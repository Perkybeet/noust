# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust self-backup``: Noust's own state in one encrypted file (3.4, checklist H-27).

Over real openssl, because the whole point is a file nobody opens without its
passphrase. What is pinned here is what an operator learns on the worst day:

- a bundle holds the store (a consistent snapshot, rows still in the WAL
  included), the configuration, the secrets, the certificates with their
  links and the units Noust wrote with which of them were enabled, and leaves
  out what regenerates;
- it holds what is needed to read its own secrets except the passphrase, which
  nothing stores in it or passes on a command line;
- it is proven restorable without restoring it, and a bundle that is altered,
  damaged or from a newer Noust is refused before anything changes;
- a restore overlays this server's own directories (never the paths the bundle
  came from) atomically, through no link, keeps a bundle of what it replaces,
  migrates the store, enables the units and starts what was running;
- a schedule keeps its passphrase in the secret store, proves each bundle
  before sending it, and its retention deletes only its own.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from noust.core import sealing
from noust.core.ens import envelope
from noust.core.ens.envelope import EnvelopeError
from noust.core.exceptions import BackupError, ValidationError
from noust.core.runner import SubprocessRunner, set_runner
from noust.core.secrets import SecretStore
from noust.core.store import SCHEMA_VERSION, App, NoustStore, get_store
from noust.managers import self_restore
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.backup_failures import BackupFailureLog
from noust.managers.self_backup import LABEL, SECRET_NAME, SelfBackupManager
from noust.managers.self_restore import SelfRestore
from tests.database_backup_support import RcloneRunner, age
from tests.notifications_support import failure_id_of

PASSPHRASE = "correct horse battery"
pytestmark = pytest.mark.allow_subprocess


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


class Runner(RcloneRunner):
    """Real openssl, rclone answered from a directory, everything else scripted and recorded."""

    real = SubprocessRunner()

    def run(self, argv: Any, **kwargs: Any) -> Any:
        if argv and argv[0] == "openssl":
            self.calls.append(tuple(str(a) for a in argv))
            self.envs.append(kwargs.get("env"))
            return self.real.run(argv, **kwargs)
        return super().run(argv, **kwargs)


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[Runner]:
    fake = Runner(tmp_path / "remote")
    fake.script(("systemctl", "is-active", "--quiet", "noust-web.service"), exit_code=0)
    fake.script(("systemctl", "is-active", "--quiet", "noust-monitor.service"), exit_code=3)
    fake.script(("systemctl", "is-active", "--quiet", "noust-previews.service"), exit_code=3)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


class NoRoles:
    """A database service whose engines have no roles of their own to dump."""

    def manager(self, engine: str) -> Any:
        return object()


@dataclass
class Server:
    """One server's directories, and the database connection it keeps open."""

    config: Path
    state: Path
    letsencrypt: Path
    units: Path
    bundles: Path
    held: list[sqlite3.Connection] = field(default_factory=list)

    def manager(self, runner: Runner, **extra: Any) -> SelfBackupManager:
        # A test server is isolated: the web server of the machine running the tests is not its own.
        extra.setdefault("webservers", {})
        # ...and so are its database servers: no engine of the machine running the tests is asked.
        extra.setdefault("database_service", lambda: NoRoles())
        return SelfBackupManager(
            runner=runner,
            sources={"config": self.config, "state": self.state},
            letsencrypt=self.letsencrypt,
            systemd_dir=self.units,
            directory=self.bundles,
            **extra,
        )


def server(base: Path, name: str) -> Server:
    root = base / name
    made = Server(
        config=root / "etc-noust",
        state=root / "var-lib-noust",
        letsencrypt=root / "etc-letsencrypt",
        units=root / "systemd",
        bundles=root / "backups" / "self",
    )
    for directory in (made.config, made.state, made.units):
        directory.mkdir(parents=True)
    return made


def origin(base: Path) -> Server:
    """A server with state worth keeping: a store with an app, secrets, certificates, units."""
    made = server(base, "origin")
    (made.config / "panel-tls").mkdir()
    (made.config / "config.yaml").write_text("web:\n  port: 8443\n")
    (made.config / "panel-tls" / "panel.key").write_text("PRIVATE KEY")
    key = made.state / "secrets" / "fleet" / "nodes" / "web-1"
    key.mkdir(parents=True)
    (key / "id_ed25519").write_text("NODE SSH KEY")
    (made.state / "incidents" / "old").mkdir(parents=True)
    (made.state / "incidents" / "old" / "big.log").write_text("evidence")
    for regenerable in ("metrics.db", "observations.db"):
        connection = sqlite3.connect(made.state / regenerable)
        connection.execute("CREATE TABLE t (x)")
        connection.commit()
        connection.close()
    NoustStore.reset_instance()
    store = NoustStore(made.state / "noust.db")
    store.create_app(App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop"))
    store.close()
    NoustStore.reset_instance()
    # A change that is still in the write-ahead log when the backup is taken.
    connection = sqlite3.connect(made.state / "noust.db")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("UPDATE apps SET branch = 'only-in-the-wal'")
    connection.commit()
    # Open still, so the write-ahead log is not checkpointed away before the backup.
    made.held.append(connection)

    archive = made.letsencrypt / "archive" / "shop.example.com"
    archive.mkdir(parents=True)
    (archive / "fullchain1.pem").write_text("CERT")
    (archive / "privkey1.pem").write_text("CERT KEY")
    live = made.letsencrypt / "live" / "shop.example.com"
    live.mkdir(parents=True)
    (live / "fullchain.pem").symlink_to("../../archive/shop.example.com/fullchain1.pem")
    (live / "evil.pem").symlink_to("/etc/passwd")
    (made.letsencrypt / "renewal").mkdir()
    (made.letsencrypt / "renewal" / "shop.example.com.conf").write_text("renew")

    marker = "# Generated by Noust\n[Service]\nExecStart=/bin/true\n"
    (made.units / "shop-example-com.service").write_text(marker)
    (made.units / "noust-web.service").write_text(marker)
    (made.units / "noust-backup-shop-example-com.timer").write_text(marker)
    (made.units / "legacy.service").write_text("# Generated by WASM\n")
    (made.units / "hand-written.service").write_text("[Service]\nExecStart=/bin/false\n")
    (made.units / "shop-example-com.service.d").mkdir()
    (made.units / "shop-example-com.service.d" / "limits.conf").write_text(
        "[Service]\nMemoryMax=1G\n"
    )
    for target, unit in (
        ("multi-user.target.wants", "shop-example-com.service"),
        ("multi-user.target.wants", "noust-web.service"),
        ("timers.target.wants", "noust-backup-shop-example-com.timer"),
    ):
        wants = made.units / target
        wants.mkdir(exist_ok=True)
        (wants / unit).symlink_to(f"../{unit}")
    return made


@pytest.fixture
def old(tmp_path: Path) -> Iterator[Server]:
    made = origin(tmp_path)
    yield made
    for connection in made.held:
        connection.close()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


def unpack(bundle: Path, into: Path, runner: Runner) -> Path:
    archive = into / "bundle.tar.gz"
    envelope.open_envelope(bundle, archive, PASSPHRASE, runner=runner)
    with tarfile.open(archive) as tar:
        tar.extractall(into / "x", filter="data")
    return into / "x" / "noust-self"


# =============================================================== the bundle


class TestCreate:
    def test_it_is_an_owner_only_envelope_with_a_signed_sidecar(
        self, old: Server, runner: Runner, audited: list[dict[str, Any]]
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)

        assert made.path.parent == old.bundles
        assert stat.S_IMODE(made.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(old.bundles.stat().st_mode) == 0o700
        assert envelope.read_header(made.path)["label"] == LABEL
        raw = made.path.read_bytes()
        assert b"NODE SSH KEY" not in raw and b"only-in-the-wal" not in raw
        sidecar = json.loads(made.path.with_name(f"{made.path.name}.json").read_text())
        assert sidecar["sha256"] == made.sha256 and sidecar["mac"]
        assert sidecar["scheduled"] is False and sidecar["kind"] == "manual"
        event = next(e for e in audited if e["event"] == "self_backup.create")
        assert event["details"]["sha256"] == made.sha256
        assert PASSPHRASE not in json.dumps(event, default=str)

    def test_the_passphrase_is_never_on_a_command_line_nor_in_the_environment(
        self, old: Server, runner: Runner
    ) -> None:
        old.manager(runner).create(PASSPHRASE)

        openssl = [call for call in runner.calls if call[0] == "openssl"]
        assert openssl
        assert not any(PASSPHRASE in word for call in openssl for word in call)
        assert all(PASSPHRASE not in str(env) for env in runner.envs if env)

    def test_it_holds_the_store_the_config_the_secrets_the_certificates_and_the_units(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)

        paths_in = {entry["path"] for entry in made.manifest["files"]}
        assert {
            "config/config.yaml",
            "config/panel-tls/panel.key",
            "state/noust.db",
            "state/secrets/fleet/nodes/web-1/id_ed25519",
            "letsencrypt/archive/shop.example.com/privkey1.pem",
            "letsencrypt/renewal/shop.example.com.conf",
            "units/shop-example-com.service",
            "units/noust-web.service",
            "units/legacy.service",
            "units/shop-example-com.service.d/limits.conf",
        } <= paths_in
        assert made.manifest["contains"] == {
            "store": True,
            "secrets": True,
            "seal_header": False,
            "certificates": True,
            "units": 3,
            "webservers": [],
            "pg_globals": 0,
        }

    def test_what_regenerates_and_what_is_not_noust_s_is_left_out(
        self, old: Server, runner: Runner
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)

        paths_in = {entry["path"] for entry in made.manifest["files"]}
        assert not {p for p in paths_in if p.endswith(("metrics.db", "observations.db"))}
        assert not any("incidents" in p for p in paths_in)
        assert "units/hand-written.service" not in paths_in
        skipped = {entry["path"]: entry["reason"] for entry in made.manifest["skipped"]}
        assert skipped["state/metrics.db"] == "excluded"
        assert skipped["letsencrypt/live/shop.example.com/evil.pem"] == "link out of the tree"

    def test_the_store_is_a_consistent_snapshot_with_what_was_in_the_wal(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)

        root = unpack(made.path, tmp_path / "check", runner)
        rows = sqlite3.connect(root / "state" / "noust.db").execute("SELECT branch FROM apps")
        assert rows.fetchall() == [("only-in-the-wal",)]
        assert not list((root / "state").glob("noust.db-*"))

    def test_the_links_of_certbots_live_folders_are_recorded_and_the_unit_enablement_too(
        self, old: Server, runner: Runner
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)

        assert made.manifest["links"] == [
            {
                "path": "letsencrypt/live/shop.example.com/fullchain.pem",
                "target": "../../archive/shop.example.com/fullchain1.pem",
            }
        ]
        assert sorted(made.manifest["enabled_units"]) == [
            "noust-backup-shop-example-com.timer",
            "noust-web.service",
            "shop-example-com.service",
        ]

    def test_it_holds_what_is_needed_to_read_a_sealed_store_except_its_passphrase(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        sealing.seal_store(old.state / "secrets", "sealing passphrase", runner=runner)
        made = old.manager(runner).create(PASSPHRASE)

        assert made.manifest["contains"]["seal_header"] is True
        root = unpack(made.path, tmp_path / "check", runner)
        text = (root / "state" / "secrets" / "fleet" / "nodes" / "web-1" / "id_ed25519").read_text()
        assert "NODE SSH KEY" not in text
        header = (root / "state" / "secrets" / sealing.SEAL_FILE).read_text()
        assert "sealing passphrase" not in header and "NODE SSH KEY" not in header

    def test_a_short_passphrase_is_refused_before_anything_is_written(
        self, old: Server, runner: Runner
    ) -> None:
        with pytest.raises(EnvelopeError, match="at least"):
            old.manager(runner).create("short")

        assert not list(old.bundles.glob("*.enc"))

    def test_two_in_the_same_second_do_not_overwrite_each_other(
        self, old: Server, runner: Runner
    ) -> None:
        manager = old.manager(runner)

        first = manager.create(PASSPHRASE)
        second = manager.create(PASSPHRASE)

        assert first.path != second.path and first.path.exists() and second.path.exists()

    def test_a_pre_restore_copy_says_so_in_its_name(self, old: Server, runner: Runner) -> None:
        made = old.manager(runner).create(PASSPHRASE, kind="pre-restore")

        assert made.path.name.endswith("-prerestore.tar.gz.enc")


# =============================================================== the listing


class TestListing:
    def test_bundles_are_listed_newest_first_with_what_the_sidecar_says(
        self, old: Server, runner: Runner
    ) -> None:
        manager = old.manager(runner)
        first = manager.create(PASSPHRASE)
        second = manager.create(PASSPHRASE, kind="scheduled")

        listed = manager.entries()

        assert [e.name for e in listed] == [second.path.name, first.path.name]
        assert listed[0].scheduled is True and listed[1].kind == "manual"
        assert listed[0].sha256 == second.sha256 and listed[0].sidecar_mac == "valid"

    def test_what_is_not_a_bundle_is_not_listed(self, old: Server, runner: Runner) -> None:
        manager = old.manager(runner)
        manager.create(PASSPHRASE)
        (old.bundles / "noust-self-x-1.tar.gz.enc").write_text("not an envelope")
        (old.bundles / "other.tar.gz.enc").write_bytes(b"x")

        assert len(manager.entries()) == 1

    def test_a_bundle_is_found_by_name_or_path_and_nothing_else(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        manager = old.manager(runner)
        made = manager.create(PASSPHRASE)

        assert manager.resolve(made.path.name) == made.path
        assert manager.resolve(str(made.path)) == made.path
        with pytest.raises(BackupError, match="not the name"):
            manager.resolve("passwd")
        with pytest.raises(BackupError, match="No bundle"):
            manager.resolve("../../etc/passwd.tar.gz.enc")
        with pytest.raises(BackupError, match="No bundle"):
            manager.resolve("noust-self-nope-20260101T000000Z.tar.gz.enc")
        elsewhere = tmp_path / "elsewhere.tar.gz.enc"
        elsewhere.write_text("x")
        with pytest.raises(BackupError, match="not a Noust bundle"):
            manager.resolve(str(elsewhere))

    def test_an_envelope_of_something_else_is_refused(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        plain = tmp_path / "plain"
        plain.write_text("x")
        other = tmp_path / "other.tar.gz.enc"
        envelope.seal_file(plain, other, PASSPHRASE, runner=runner, label="noust-central-backup")

        with pytest.raises(BackupError, match="envelope of something else"):
            old.manager(runner).resolve(str(other))

    def test_deleting_removes_the_bundle_and_its_sidecar_and_only_here(
        self, old: Server, runner: Runner, tmp_path: Path, audited: list[dict[str, Any]]
    ) -> None:
        manager = old.manager(runner)
        made = manager.create(PASSPHRASE)
        copy = tmp_path / made.path.name
        copy.write_bytes(made.path.read_bytes())

        with pytest.raises(BackupError, match="is not in"):
            manager.delete(str(copy))
        assert manager.delete(made.path.name) is True

        assert not made.path.exists() and not made.path.with_name(f"{made.path.name}.json").exists()
        assert any(e["event"] == "self_backup.delete" for e in audited)


# ================================================================= verifying


class TestVerify:
    def test_a_good_bundle_is_proven_restorable(
        self, old: Server, runner: Runner, audited: list[dict[str, Any]]
    ) -> None:
        manager = old.manager(runner)
        made = manager.create(PASSPHRASE)

        report = manager.verify(made.path.name, PASSPHRASE)

        assert report["ok"] is True and report["restorable_here"] is True
        assert report["files"] == len(made.manifest["files"])
        assert report["schema_version"] == made.manifest["schema_version"]
        assert report["host"] and report["noust_version"]
        assert audited[-1]["event"] == "self_backup.verify"

    def test_a_wrong_passphrase_opens_nothing(self, old: Server, runner: Runner) -> None:
        manager = old.manager(runner)
        made = manager.create(PASSPHRASE)

        with pytest.raises(EnvelopeError, match="passphrase"):
            manager.verify(made.path.name, "another passphrase")

    def test_a_changed_byte_is_refused(self, old: Server, runner: Runner) -> None:
        manager = old.manager(runner)
        made = manager.create(PASSPHRASE)
        data = bytearray(made.path.read_bytes())
        data[-5] ^= 0xFF
        made.path.write_bytes(bytes(data))

        with pytest.raises(EnvelopeError, match="integrity"):
            manager.verify(made.path.name, PASSPHRASE)

    def test_a_bundle_from_a_newer_noust_cannot_be_restored_here(
        self, old: Server, runner: Runner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager = old.manager(runner)
        made = manager.create(PASSPHRASE)
        from noust.managers import self_backup

        monkeypatch.setattr(self_backup, "SCHEMA_VERSION", SCHEMA_VERSION - 1)

        report = manager.verify(made.path.name, PASSPHRASE)

        assert report["ok"] is True and report["restorable_here"] is False
        assert f"schema v{SCHEMA_VERSION}" in report["warnings"][0]

    def test_a_file_that_does_not_match_its_digest_is_a_problem(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        manager = old.manager(runner)
        forged = forge(
            tmp_path, runner, {"config/config.yaml": ("changed", "0" * 64)}, with_store=old
        )

        report = manager.verify(str(forged), PASSPHRASE)

        assert report["ok"] is False
        assert "config/config.yaml does not match its SHA-256" in report["problems"]

    def test_the_verification_is_recorded_on_the_schedule(
        self, old: Server, runner: Runner
    ) -> None:
        manager = old.manager(runner, scheduler=FakeTimers())
        manager.set_schedule(passphrase=PASSPHRASE)
        made = manager.create(PASSPHRASE)

        manager.verify(made.path.name, PASSPHRASE)

        assert manager.records.get().last_verify_status == "ok"  # type: ignore[union-attr]


# ================================================================= restoring


def forge(
    base: Path,
    runner: Runner,
    entries: dict[str, tuple[str, str | None]],
    *,
    with_store: Server,
    links: list[dict[str, str]] | None = None,
    extra_members: dict[str, str] | None = None,
    sources: dict[str, str] | None = None,
) -> Path:
    """
    Build a bundle by hand, to put in front of a restore what a real one never holds.

    Args:
        base: A scratch directory.
        runner: The runner.
        entries: Path inside the bundle to (its text, the digest the manifest claims; None
            for the true one).
        with_store: A server whose store is taken as the bundle's.
        links: Links the manifest declares.
        extra_members: Files in the archive that the manifest does not mention.
        sources: Directories the manifest says it came from, besides config and state.

    Returns:
        The forged bundle.
    """
    import hashlib

    stage = base / "forge" / "noust-self"
    stage.mkdir(parents=True)
    files: list[dict[str, Any]] = []
    snapshot = stage / "state"
    snapshot.mkdir()
    connection = sqlite3.connect(with_store.state / "noust.db")
    writer = sqlite3.connect(snapshot / "noust.db")
    connection.backup(writer)
    writer.close()
    connection.close()
    files.append(
        {
            "path": "state/noust.db",
            "size": (snapshot / "noust.db").stat().st_size,
            "sha256": hashlib.sha256((snapshot / "noust.db").read_bytes()).hexdigest(),
        }
    )
    for relative, (text, digest) in entries.items():
        target = stage / relative
        if ".." not in relative:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        files.append(
            {
                "path": relative,
                "size": len(text),
                "sha256": digest or hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    for relative, text in (extra_members or {}).items():
        (stage / relative).parent.mkdir(parents=True, exist_ok=True)
        (stage / relative).write_text(text)
    manifest = {
        "format": LABEL,
        "version": 1,
        "created_at": "2026-10-06T00:00:00+00:00",
        "host": "forged",
        "noust_version": "3.4.0",
        "schema_version": SCHEMA_VERSION,
        "sources": {"config": "/somewhere/else", "state": "/elsewhere/still", **(sources or {})},
        "files": files,
        "links": links or [],
        "skipped": [],
        "enabled_units": [],
        "contains": {"store": True, "secrets": False, "seal_header": False},
    }
    (stage / "MANIFEST.json").write_text(json.dumps(manifest))
    archive = base / "forged.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stage, arcname="noust-self")
    out = base / "noust-self-forged-20260101T000000Z.tar.gz.enc"
    envelope.seal_file(archive, out, PASSPHRASE, runner=runner, label=LABEL)
    return out


class FakeTimers:
    """Stands in for the timer installer: records, touches no unit."""

    def __init__(self) -> None:
        self.installed: str | None = None

    def install_self_backup_timer(self, schedule: str, *, options: Any = None) -> str:
        self.installed = schedule
        return "noust-backup-self"

    def remove_self_backup_timer(self) -> bool:
        was, self.installed = self.installed is not None, None
        return was

    def self_backup_timer_state(self) -> dict[str, Any]:
        return {"installed": self.installed is not None, "next_run": "soon", "last_run": None}


class TestRestore:
    def restore(
        self, runner: Runner, bundle: Path, new: Server, **kwargs: Any
    ) -> self_restore.RestoreReport:
        return SelfRestore(new.manager(runner), restart=kwargs.pop("restart", True)).restore(
            str(bundle), PASSPHRASE, **kwargs
        )

    def test_a_fresh_server_gets_everything_back_in_its_own_places(
        self, old: Server, runner: Runner, tmp_path: Path, audited: list[dict[str, Any]]
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        new = server(tmp_path, "new")

        report = self.restore(runner, made.path, new)

        assert (new.config / "config.yaml").read_text() == "web:\n  port: 8443\n"
        assert (new.config / "panel-tls" / "panel.key").read_text() == "PRIVATE KEY"
        key = new.state / "secrets" / "fleet" / "nodes" / "web-1" / "id_ed25519"
        assert key.read_text() == "NODE SSH KEY" and stat.S_IMODE(key.stat().st_mode) == 0o600
        link = new.letsencrypt / "live" / "shop.example.com" / "fullchain.pem"
        assert link.is_symlink() and link.read_text() == "CERT"
        assert not (new.letsencrypt / "live" / "shop.example.com" / "evil.pem").exists()
        unit = new.units / "shop-example-com.service"
        assert unit.is_file() and stat.S_IMODE(unit.stat().st_mode) == 0o644
        assert (new.units / "shop-example-com.service.d" / "limits.conf").is_file()
        assert not (new.units / "hand-written.service").exists()
        assert report.files == len(made.manifest["files"]) and report.links == 1
        assert report.safety_copy is None
        assert any(e["event"] == "self_backup.restore" for e in audited)

    def test_the_store_comes_back_whole_and_opens_at_this_version(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        new = server(tmp_path, "new")
        # What a fresh server may already have: a store's write-ahead files.
        (new.state / "noust.db-wal").write_bytes(b"stale write-ahead log")
        (new.state / "noust.db-shm").write_bytes(b"stale index")

        report = self.restore(runner, made.path, new)

        store = get_store()
        assert store.db_path == new.state / "noust.db"
        app = store.get_app("shop.example.com")
        assert app is not None and app.branch == "only-in-the-wal"
        assert report.schema_after == SCHEMA_VERSION and report.schema_before == SCHEMA_VERSION
        for name, stale in (
            ("noust.db-wal", b"stale write-ahead log"),
            ("noust.db-shm", b"stale index"),
        ):
            leftover = new.state / name
            assert not leftover.exists() or leftover.read_bytes() != stale

    def test_units_are_enabled_the_timers_started_and_what_ran_started_again(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        new = server(tmp_path, "new")
        runner.calls.clear()

        report = self.restore(runner, made.path, new)

        ran = [c for c in runner.calls if c[0] == "systemctl"]
        assert ("systemctl", "daemon-reload") in ran
        assert ("systemctl", "enable", "shop-example-com.service") in ran
        assert ("systemctl", "enable", "noust-backup-shop-example-com.timer") in ran
        assert ("systemctl", "stop", "noust-web.service") in ran
        assert ("systemctl", "start", "noust-web.service") in ran
        assert ("systemctl", "start", "noust-backup-shop-example-com.timer") in ran
        # The application's unit is enabled, not started: restore-server decides that.
        assert ("systemctl", "start", "shop-example-com.service") not in ran
        # What was not running is not started.
        assert ("systemctl", "start", "noust-monitor.service") not in ran
        assert report.stopped == ["noust-web"]
        # Stopped before a file is written, started after.
        assert ran.index(("systemctl", "stop", "noust-web.service")) < ran.index(
            ("systemctl", "daemon-reload")
        )

    def test_without_restart_nothing_is_started(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        new = server(tmp_path, "new")
        runner.calls.clear()

        self.restore(runner, made.path, new, restart=False)

        assert not [c for c in runner.calls if c[:2] == ("systemctl", "start")]

    def test_a_server_that_holds_state_is_not_overwritten_unasked(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        busy = origin(tmp_path / "busy")
        (busy.config / "config.yaml").write_text("mine: true\n")

        with pytest.raises(BackupError, match="already holds Noust state"):
            self.restore(runner, made.path, busy)

        assert (busy.config / "config.yaml").read_text() == "mine: true\n"
        assert not list(busy.bundles.glob("*prerestore*"))

    def test_with_force_what_was_there_is_kept_in_a_bundle_first(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        busy = origin(tmp_path / "busy")
        (busy.config / "config.yaml").write_text("mine: true\n")

        report = self.restore(runner, made.path, busy, force=True)

        assert report.safety_copy and report.safety_copy.endswith("-prerestore.tar.gz.enc")
        assert (busy.config / "config.yaml").read_text() == "web:\n  port: 8443\n"
        kept = unpack(busy.bundles / report.safety_copy, tmp_path / "kept", runner)
        assert (kept / "config" / "config.yaml").read_text() == "mine: true\n"

    def test_a_wrong_passphrase_changes_nothing(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        new = server(tmp_path, "new")

        with pytest.raises(EnvelopeError):
            SelfRestore(new.manager(runner)).restore(str(made.path), "not the passphrase")

        assert list(new.config.iterdir()) == []
        assert not [c for c in runner.calls if c[:2] == ("systemctl", "stop")]

    def test_a_bundle_of_a_newer_noust_changes_nothing(
        self, old: Server, runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        new = server(tmp_path, "new")
        monkeypatch.setattr(self_restore, "SCHEMA_VERSION", SCHEMA_VERSION - 1)

        with pytest.raises(BackupError, match=f"schema v{SCHEMA_VERSION}") as failure:
            self.restore(runner, made.path, new)

        assert "Nothing was changed" in failure.value.details
        assert list(new.config.iterdir()) == []
        assert not [c for c in runner.calls if c[:2] == ("systemctl", "stop")]

    def test_a_path_that_climbs_is_refused_before_anything_is_written(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        new = server(tmp_path, "new")
        (new.config / "x").write_text("mine")
        (tmp_path / "outside").mkdir()
        forged = forge(
            tmp_path / "f",
            runner,
            {"config/../marker": ("EVIL", None)},
            with_store=old,
            extra_members={"config/placeholder": "dir", "marker": "EVIL"},
        )

        with pytest.raises(BackupError, match="names a path it must not"):
            self.restore(runner, forged, new)

        assert (new.config / "x").read_text() == "mine"
        assert not (new.config.parent / "marker").exists()

    def test_nothing_is_written_through_a_symbolic_link_on_this_server(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        new = server(tmp_path, "new")
        (new.config / "panel-tls").symlink_to(outside)
        made = old.manager(runner).create(PASSPHRASE)

        with pytest.raises(BackupError, match="symbolic link"):
            self.restore(runner, made.path, new)

        assert list(outside.iterdir()) == []

    def test_a_link_in_the_bundle_that_leaves_its_tree_is_not_made(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        new = server(tmp_path, "new")
        forged = forge(
            tmp_path / "f",
            runner,
            {"letsencrypt/archive/a/pem": ("PEM", None)},
            with_store=old,
            links=[
                {"path": "letsencrypt/live/a/pem", "target": "../../../../etc/passwd"},
                {"path": "letsencrypt/live/a/ok", "target": "../../archive/a/pem"},
            ],
            sources={"letsencrypt": "/etc/letsencrypt"},
        )

        report = self.restore(runner, forged, new)

        assert (new.letsencrypt / "archive" / "a" / "pem").read_text() == "PEM"
        assert (new.letsencrypt / "live" / "a" / "ok").is_symlink()
        assert not (new.letsencrypt / "live" / "a" / "pem").exists()
        assert {"path": "letsencrypt/live/a/pem", "reason": "a link that leaves its tree"} in (
            report.skipped
        )

    def test_a_failure_part_way_starts_the_services_again_and_names_the_safety_copy(
        self, old: Server, runner: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        busy = origin(tmp_path / "busy")

        def broken(*args: Any, **kwargs: Any) -> None:
            raise BackupError("the disk is full")

        monkeypatch.setattr(SelfRestore, "_put_back", broken)
        runner.calls.clear()

        with pytest.raises(BackupError) as failure:
            self.restore(runner, made.path, busy, force=True)

        assert "the disk is full" in str(failure.value)
        assert "prerestore" in failure.value.details
        assert ("systemctl", "start", "noust-web.service") in runner.calls

    def test_the_secrets_of_a_sealed_server_open_with_their_own_passphrase_after_a_restore(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        (old.state / "secrets" / "fleet" / "token").write_text("FLEET TOKEN")
        sealing.seal_store(old.state / "secrets", "sealing passphrase", runner=runner)
        made = old.manager(runner).create(PASSPHRASE)
        new = server(tmp_path, "new")

        report = self.restore(runner, made.path, new)

        secrets_dir = new.state / "secrets"
        assert sealing.is_sealed(secrets_dir)
        sealing.lock(secrets_dir)
        sealing.unlock(secrets_dir, "sealing passphrase")
        assert SecretStore(root=secrets_dir, runner=runner).read("fleet/token") == "FLEET TOKEN"
        assert any("sealed" in note for note in report.notes)

    def test_a_bundle_of_another_data_directory_goes_to_this_servers_own(
        self, old: Server, runner: Runner, tmp_path: Path
    ) -> None:
        made = old.manager(runner).create(PASSPHRASE)
        assert made.manifest["sources"]["state"] == str(old.state)
        new = server(tmp_path, "new")

        self.restore(runner, made.path, new)

        assert (new.state / "secrets").is_dir()
        assert not old.state.joinpath("secrets").samefile(new.state / "secrets")


# ================================================================= the schedule


class TestSchedule:
    @pytest.fixture
    def manager(self, old: Server, runner: Runner) -> SelfBackupManager:
        return old.manager(
            runner,
            scheduler=FakeTimers(),  # type: ignore[arg-type]
            destinations=DestinationFileManager(runner=runner),
        )

    def add_destination(self, runner: Runner, name: str = "nas") -> None:
        DestinationFileManager(runner=runner).add(
            name, "sftp", {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t"}
        )

    def test_it_stores_the_passphrase_in_the_secret_store_and_installs_the_timer(
        self, manager: SelfBackupManager, audited: list[dict[str, Any]]
    ) -> None:
        view = manager.set_schedule(
            schedule="weekly", retention_count=4, retention_days=21, passphrase=PASSPHRASE
        )

        assert view["configured"] is True and view["passphrase_stored"] is True
        assert view["schedule"] == "Mon *-*-* 02:00:00"
        assert view["timer"]["installed"] is True
        assert manager.secrets.read(SECRET_NAME) == PASSPHRASE
        event = next(e for e in audited if e["event"] == "self_backup.schedule")
        assert PASSPHRASE not in json.dumps(event, default=str)
        assert PASSPHRASE not in json.dumps(view, default=str)

    def test_a_schedule_with_no_passphrase_anywhere_is_refused(
        self, manager: SelfBackupManager
    ) -> None:
        with pytest.raises(ValidationError, match="needs a passphrase"):
            manager.set_schedule()

        assert manager.records.get() is None

    def test_changing_the_calendar_keeps_the_passphrase(self, manager: SelfBackupManager) -> None:
        manager.set_schedule(passphrase=PASSPHRASE)

        view = manager.set_schedule(schedule="hourly")

        assert view["passphrase_stored"] is True
        assert manager.stored_passphrase() == PASSPHRASE

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"retention_count": 0},
            {"retention_days": 99999},
            {"schedule": "daily\nExecStart=/bin/evil"},
            {"destinations": [{"name": "nowhere"}]},
            {"passphrase": "short"},
        ],
    )
    def test_an_unusable_schedule_is_refused_and_installs_no_timer(
        self, manager: SelfBackupManager, kwargs: dict[str, Any]
    ) -> None:
        kwargs.setdefault("passphrase", PASSPHRASE)

        with pytest.raises((ValidationError, BackupError)):
            manager.set_schedule(**kwargs)

        assert manager._scheduler.installed is None  # type: ignore[union-attr]
        assert manager.records.get() is None

    def test_removing_it_removes_the_timer_and_leaves_the_bundles_and_the_passphrase(
        self, manager: SelfBackupManager
    ) -> None:
        manager.set_schedule(passphrase=PASSPHRASE)
        manager.create(PASSPHRASE)

        assert manager.remove_schedule() is True

        assert manager._scheduler.installed is None  # type: ignore[union-attr]
        assert len(manager.entries()) == 1 and manager.stored_passphrase() == PASSPHRASE
        assert manager.remove_schedule() is False

    def test_a_run_takes_proves_sends_and_records(
        self, manager: SelfBackupManager, runner: Runner, tmp_path: Path
    ) -> None:
        self.add_destination(runner)
        manager.set_schedule(
            passphrase=PASSPHRASE, destinations=[{"name": "nas", "retention_count": 3}]
        )

        result = manager.run_schedule()

        (entry,) = manager.entries()
        assert entry.scheduled is True and entry.name == result["file"]
        assert result["verification"]["ok"] is True
        remote = (
            tmp_path / "remote" / "nas" / "noust-backups" / "self-backups" / manager.host_slug()
        )
        assert (remote / entry.name).is_file()
        sidecar = json.loads((remote / f"{entry.name}.json").read_text())
        assert sidecar["scheduled"] is True and sidecar["sha256"] == entry.sha256
        assert entry.remote_copies[0]["destination"] == "nas"
        schedule = manager.records.get()
        assert schedule is not None and schedule.last_status == "ok"
        assert schedule.last_file == entry.name

    def test_what_is_sent_is_encrypted_by_a_passphrase_that_is_not_sent(
        self, manager: SelfBackupManager, runner: Runner, tmp_path: Path
    ) -> None:
        self.add_destination(runner)
        manager.set_schedule(passphrase=PASSPHRASE, destinations=[{"name": "nas"}])

        manager.run_schedule()

        for sent in (tmp_path / "remote").rglob("*"):
            if sent.is_file():
                assert PASSPHRASE.encode() not in sent.read_bytes()
                assert b"NODE SSH KEY" not in sent.read_bytes()

    def test_a_bundle_that_does_not_verify_is_not_sent(
        self,
        manager: SelfBackupManager,
        runner: Runner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.add_destination(runner)
        manager.set_schedule(passphrase=PASSPHRASE, destinations=[{"name": "nas"}])
        monkeypatch.setattr(
            manager,
            "verify",
            lambda name, passphrase: {"ok": False, "problems": ["state/noust.db is damaged"]},
        )

        with pytest.raises(BackupError, match="failed its verification") as failure:
            manager.run_schedule()

        assert "state/noust.db is damaged" in failure.value.details
        assert not list((tmp_path / "remote").rglob("*.enc"))
        schedule = manager.records.get()
        assert schedule is not None and schedule.last_status == "failed"

    def test_one_destination_failing_does_not_stop_the_others(
        self, manager: SelfBackupManager, runner: Runner, tmp_path: Path
    ) -> None:
        self.add_destination(runner, "nas")
        self.add_destination(runner, "cloud")
        runner.refusing["nas"] = "connection refused"
        manager.set_schedule(
            passphrase=PASSPHRASE, destinations=[{"name": "nas"}, {"name": "cloud"}]
        )

        with pytest.raises(BackupError) as failure:
            manager.run_schedule()

        assert "nas" in str(failure.value) and "connection refused" in failure.value.details
        (entry,) = manager.entries()
        assert [c["destination"] for c in entry.remote_copies] == ["cloud"]
        schedule = manager.records.get()
        assert schedule is not None and schedule.last_status == "failed"

    def test_without_a_stored_passphrase_a_run_says_how_to_set_it_again(
        self, manager: SelfBackupManager
    ) -> None:
        manager.set_schedule(passphrase=PASSPHRASE)
        manager.secrets.delete(SECRET_NAME)

        with pytest.raises(ValidationError, match="No passphrase is stored") as failure:
            manager.run_schedule()

        assert "noust self-backup schedule set" in failure.value.details
        assert manager.records.get().last_status == "failed"  # type: ignore[union-attr]

    def test_retention_deletes_only_scheduled_bundles_and_never_the_newest(
        self, manager: SelfBackupManager
    ) -> None:
        manual = manager.create(PASSPHRASE)
        safety = manager.create(PASSPHRASE, kind="pre-restore")
        scheduled = [manager.create(PASSPHRASE, kind="scheduled") for _ in range(3)]
        for index, bundle in enumerate(scheduled):
            age(bundle.path, 3 - index)
        for kept in (manual, safety):
            age(kept.path, 900)

        deleted = manager.apply_retention(retention_count=1, retention_days=None)

        assert sorted(deleted) == sorted(b.path.name for b in scheduled[:2])
        names = {e.name for e in manager.entries()}
        assert {manual.path.name, safety.path.name, scheduled[2].path.name} <= names

    def test_a_failure_is_announced_when_asked(
        self, manager: SelfBackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager.set_schedule(passphrase=PASSPHRASE)
        manager.secrets.delete(SECRET_NAME)
        sent: list[Any] = []
        monkeypatch.setattr(
            "noust.core.notifier.Notifier.notify",
            lambda self, notification: sent.append(notification),
        )

        with pytest.raises(ValidationError):
            manager.run_schedule(notify=True)

        assert len(sent) == 1 and sent[0].kind == "backup_failed"

    def test_the_notices_of_a_failed_run_link_to_all_that_was_said(
        self,
        manager: SelfBackupManager,
        runner: Runner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.add_destination(runner, "nas")
        runner.refusing["nas"] = "connection refused " * 100
        manager.set_schedule(passphrase=PASSPHRASE, destinations=[{"name": "nas"}])
        sent: list[Any] = []
        monkeypatch.setattr(
            "noust.core.notifier.Notifier.notify",
            lambda self, notification: sent.append(notification),
        )

        with pytest.raises(BackupError):
            manager.run_schedule(notify=True)

        (notice,) = [n for n in sent if n.code == "backup.upload_failed"]
        failure_id = failure_id_of(notice)
        stored = BackupFailureLog().get(failure_id)
        assert stored is not None and (stored.kind, stored.destination) == ("upload", "nas")
        assert stored.subject == "Noust"
        assert stored.output.count("connection refused") == 100

    def test_a_run_that_could_not_take_the_bundle_links_to_its_report(
        self, manager: SelfBackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager.set_schedule(passphrase=PASSPHRASE)
        sent: list[Any] = []
        monkeypatch.setattr(
            "noust.core.notifier.Notifier.notify",
            lambda self, notification: sent.append(notification),
        )

        def broken(*args: Any, **kwargs: Any) -> Any:
            raise BackupError("disk full", details="no room for the bundle")

        monkeypatch.setattr(manager, "create", broken)

        with pytest.raises(BackupError):
            manager.run_schedule(notify=True)

        (notice,) = [n for n in sent if n.code == "backup.failed"]
        failure_id = failure_id_of(notice)
        stored = BackupFailureLog().get(failure_id)
        assert stored is not None and "no room for the bundle" in stored.output

    def test_what_is_on_a_destination_is_listed_and_fetched_back_checked(
        self, manager: SelfBackupManager, runner: Runner, tmp_path: Path
    ) -> None:
        self.add_destination(runner)
        manager.set_schedule(passphrase=PASSPHRASE, destinations=[{"name": "nas"}])
        result = manager.run_schedule()

        listed = manager.remote_entries("nas")
        hosts = manager.remote_hosts("nas")
        fetched = manager.download("nas", result["file"], hosts[0], tmp_path / "in")

        assert [e["name"] for e in listed] == [result["file"]] and listed[0]["scheduled"] is True
        assert hosts == [manager.host_slug()]
        assert fetched.read_bytes() == (manager.directory / result["file"]).read_bytes()
        assert os.access(fetched, os.R_OK)
        with pytest.raises(BackupError, match="not the name"):
            manager.download("nas", "../../x", hosts[0], tmp_path / "in")


GB = 1024**3


class TestRetentionBySize:
    """Noust's own state is kept by size as well as by number and age (checklist H-04)."""

    @pytest.fixture
    def manager(self, old: Server, runner: Runner) -> SelfBackupManager:
        return old.manager(
            runner,
            scheduler=FakeTimers(),  # type: ignore[arg-type]
            destinations=DestinationFileManager(runner=runner),
        )

    def scheduled(self, manager: SelfBackupManager, count: int) -> list[Any]:
        bundles = [manager.create(PASSPHRASE, kind="scheduled") for _ in range(count)]
        for index, bundle in enumerate(bundles):
            age(bundle.path, count - index)
        return bundles

    def test_the_newest_that_fit_stay_and_a_bundle_by_hand_is_never_touched(
        self, manager: SelfBackupManager
    ) -> None:
        manual = manager.create(PASSPHRASE)
        age(manual.path, 900)
        bundles = self.scheduled(manager, 4)
        one = bundles[0].path.stat().st_size

        deleted = manager.apply_retention(
            retention_count=None, retention_days=None, retention_bytes=one * 2 + one // 2
        )

        assert sorted(deleted) == sorted(b.path.name for b in bundles[:2])
        names = {e.name for e in manager.entries()}
        assert {manual.path.name, bundles[2].path.name, bundles[3].path.name} <= names

    def test_the_newest_stays_whatever_the_budget(self, manager: SelfBackupManager) -> None:
        bundles = self.scheduled(manager, 2)

        deleted = manager.apply_retention(
            retention_count=None, retention_days=None, retention_bytes=1
        )

        assert deleted == [bundles[0].path.name]

    def test_the_schedule_stores_the_size_and_shows_it_in_gigabytes(
        self, manager: SelfBackupManager
    ) -> None:
        view = manager.set_schedule(passphrase=PASSPHRASE, retention_gb=2.5)

        assert view["retention_gb"] == 2.5
        assert "retention_bytes" not in view
        stored = manager.records.get()
        assert stored is not None and stored.retention_bytes == int(2.5 * GB)
        assert manager.set_schedule(passphrase=PASSPHRASE)["retention_gb"] is None

    @pytest.mark.parametrize("bad", [0, -1, 1_000_001, "5", True])
    def test_a_size_that_is_not_one_is_refused_and_installs_no_timer(
        self, manager: SelfBackupManager, bad: Any
    ) -> None:
        with pytest.raises(ValidationError, match="retention_gb"):
            manager.set_schedule(passphrase=PASSPHRASE, retention_gb=bad)

        assert manager._scheduler.installed is None  # type: ignore[union-attr]

    def test_a_destination_has_a_size_of_its_own_and_a_run_applies_both(
        self,
        manager: SelfBackupManager,
        runner: Runner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        DestinationFileManager(runner=runner).add(
            "nas", "sftp", {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t"}
        )
        view = manager.set_schedule(
            passphrase=PASSPHRASE,
            retention_gb=1e-9,
            destinations=[{"name": "nas", "retention_count": 3, "retention_gb": 20}],
        )
        assert view["destinations"][0]["retention_gb"] == 20.0
        seen: dict[str, Any] = {}
        real = manager.push

        def spy(name: str, destination: str, **kwargs: Any) -> dict[str, Any]:
            seen.update(kwargs)
            return real(name, destination, **kwargs)

        monkeypatch.setattr(manager, "push", spy)
        old_bundle = manager.create(PASSPHRASE, kind="scheduled")
        age(old_bundle.path, 3)

        result = manager.run_schedule()

        assert seen["retention_gb"] == 20.0 and seen["retention_count"] == 3
        # The new bundle is over a one-byte budget by itself and stays; the old one goes.
        assert old_bundle.path.name in result["local_deleted"]
        assert result["file"] not in result["local_deleted"]
