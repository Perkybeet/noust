# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_self_backup.py`` that other tests build on."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from noust.core.ens import envelope
from noust.core.runner import SubprocessRunner, set_runner
from noust.core.store import App, NoustStore
from noust.managers.self_backup import SelfBackupManager
from tests.support.database_backup_support import RcloneRunner

PASSPHRASE = "correct horse battery"


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
