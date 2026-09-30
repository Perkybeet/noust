# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database dumps on a backup destination: sending, listing, fetching, restoring.

The destinations are the ones application backups already use (rclone, secrets
in the environment, an optional crypt wrapper), driven here through a fake
rclone that answers out of a directory. What is pinned:

- a dump is sent with a sidecar that says who sent it and its SHA-256, and the
  upload is checked before it counts;
- retention on a destination deletes only what this server sent *and* a
  schedule made: another server's dumps and a dump sent by hand stay;
- an encrypted destination is verified by size (a crypt remote reports no
  hash) and the digest is what a download is checked against;
- a remote restore downloads, checks the digest, checks the dump and only then
  touches the database, and cleans its download up whatever happens;
- a folder is a validated path: a request cannot reach outside it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import (
    BackupError,
    DatabaseBackupError,
    DatabaseNotFoundError,
    ValidationError,
)
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.backup_destination_files import DestinationFileManager, file_sha256
from noust.managers.backup_manager import server_id
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.service import DatabaseService
from tests.database_backup_support import (
    PG_ARCHIVE,
    PG_LISTING,
    RcloneRunner,
    age,
    make_engine,
    make_service,
)
from tests.test_database_backups import FakeScheduler, add_destination

FOLDER = Path("wasm-backups") / "databases" / "postgresql" / "shop"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    """Force a fresh store singleton per test."""
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The fake rclone runner, installed as the process-wide one.
    """
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def dumps_dir(tmp_path: Path) -> Path:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The engine's dump directory.
    """
    path = tmp_path / "dumps"
    path.mkdir()
    return path


@pytest.fixture
def engine(dumps_dir: Path) -> type[BaseDatabaseManager]:
    """
    Args:
        dumps_dir: The engine's dump directory.

    Returns:
        A PostgreSQL fake with one database.
    """
    return make_engine(dumps_dir)


@pytest.fixture
def service(
    tmp_path: Path, engine: type[BaseDatabaseManager], runner: RcloneRunner
) -> DatabaseService:
    """
    Args:
        tmp_path: Per-test temporary directory.
        engine: The fake engine.
        runner: The installed fake runner.

    Returns:
        A service over the fake engine.
    """
    return make_service(tmp_path, [engine])


@pytest.fixture
def backups(service: DatabaseService, runner: RcloneRunner) -> DatabaseBackups:
    """
    Args:
        service: The service over the fake engine.
        runner: The fake runner.

    Returns:
        The backups class with the destination "nas" configured.
    """
    add_destination(runner)
    return DatabaseBackups(
        service,
        scheduler=FakeScheduler(),  # type: ignore[arg-type]
        destinations=DestinationFileManager(runner=runner),
    )


@pytest.fixture
def audited_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Record what reaches the audit trail.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        One dict per event: ``event``, ``target``, ``outcome`` and ``details``.
    """
    events: list[dict[str, Any]] = []

    def record(event: str, **kwargs: Any) -> None:
        events.append({"event": event, **kwargs})

    monkeypatch.setattr("noust.core.audit.record", record)
    return events


def remote_dir(runner: RcloneRunner, remote: str = "nas") -> Path:
    """
    Args:
        runner: The fake rclone.
        remote: The remote's name (``nascrypt`` for an encrypted destination).

    Returns:
        Where a database's dumps are on that remote.
    """
    return runner.root / remote / FOLDER


# ================================================================== sending


class TestPush:
    """A dump goes out with its sidecar, and the upload is checked."""

    def test_a_dump_is_sent_with_a_sidecar_that_says_who_and_what(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        taken = backups.dump("postgresql", "shop")

        summary = backups.push("postgresql", taken.name, "nas")

        folder = remote_dir(runner)
        assert (folder / taken.name).read_bytes() == PG_ARCHIVE
        sidecar = json.loads((folder / f"{taken.name}.json").read_text())
        assert sidecar["name"] == taken.name
        assert sidecar["sha256"] == file_sha256(taken.info.path)
        assert sidecar["origin"] == server_id()
        assert sidecar["scheduled"] is False
        assert sidecar["database"] == "shop"
        assert sidecar["format"] == "custom"
        assert summary["verified_by"] == "md5"
        assert summary["file"] == taken.name

    def test_where_it_went_is_recorded_on_the_dump(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        taken = backups.dump("postgresql", "shop")

        backups.push("postgresql", taken.name, "nas")

        (view,) = backups.list_dumps("postgresql", "shop")
        (copy,) = view.to_dict()["destinations"]
        assert copy["destination"] == "nas"
        assert copy["folder"] == "databases/postgresql/shop"
        assert copy["verified_by"] == "md5"
        assert copy["pushed_at"]

    def test_pushing_again_replaces_the_record_and_does_not_duplicate_it(
        self, backups: DatabaseBackups
    ) -> None:
        taken = backups.dump("postgresql", "shop")

        backups.push("postgresql", taken.name, "nas")
        backups.push("postgresql", taken.name, "nas")

        (view,) = backups.list_dumps("postgresql", "shop")
        assert len(view.to_dict()["destinations"]) == 1

    def test_a_dump_that_was_never_checked_is_checked_first(
        self, backups: DatabaseBackups, dumps_dir: Path, runner: RcloneRunner
    ) -> None:
        (dumps_dir / "postgresql-shop-20250101_010101.dump").write_bytes(PG_ARCHIVE)

        backups.push("postgresql", "postgresql-shop-20250101_010101.dump", "nas")

        assert runner.ran("pg_restore", "--list")
        (view,) = backups.list_dumps("postgresql", "shop")
        assert view.to_dict()["verify_status"] == "ok"

    def test_a_dump_that_fails_its_check_is_not_sent(
        self, backups: DatabaseBackups, dumps_dir: Path, runner: RcloneRunner
    ) -> None:
        (dumps_dir / "postgresql-shop-20250101_010101.dump").write_bytes(PG_ARCHIVE)
        runner.script(("pg_restore", "--list"), stderr="pg_restore: error: bad", exit_code=1)

        with pytest.raises(DatabaseBackupError) as refused:
            backups.push("postgresql", "postgresql-shop-20250101_010101.dump", "nas")

        assert "failed its check" in str(refused.value)
        assert "pg_restore: error: bad" in str(refused.value)
        assert not [call for call in runner.calls if call[:2] == ("rclone", "copyto")]

    def test_a_refusing_destination_says_what_rclone_said(
        self, backups: DatabaseBackups, runner: RcloneRunner, audited_events: list[dict[str, Any]]
    ) -> None:
        taken = backups.dump("postgresql", "shop")
        runner.refusing["nas"] = "Failed to copyto: AccessDenied: Access Denied (403)"

        with pytest.raises(BackupError) as refused:
            backups.push("postgresql", taken.name, "nas")

        assert "AccessDenied: Access Denied (403)" in str(refused.value)
        (view,) = backups.list_dumps("postgresql", "shop")
        assert view.to_dict()["destinations"] == []
        assert audited_events[-1]["event"] == "db.backup.push"
        assert audited_events[-1]["outcome"] == "failure"

    def test_a_destination_that_does_not_exist_is_refused(self, backups: DatabaseBackups) -> None:
        taken = backups.dump("postgresql", "shop")

        with pytest.raises(ValidationError) as refused:
            backups.push("postgresql", taken.name, "nowhere")

        assert refused.value.field == "destination"

    def test_an_encrypted_destination_goes_through_the_crypt_wrapper_and_is_checked_by_size(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        add_destination(runner, "vault", encrypted=True)
        taken = backups.dump("postgresql", "shop")

        summary = backups.push("postgresql", taken.name, "vault")

        assert summary["verified_by"] == "size"
        assert (
            runner.root / "vaultcrypt" / "databases" / "postgresql" / "shop" / taken.name
        ).is_file()
        # The crypt passphrases travelled in the environment, never in argv.
        copy = next(
            c for c in runner.calls if c[:2] == ("rclone", "copyto") and "vaultcrypt:" in c[3]
        )
        assert not any("password" in part.lower() for part in copy)


class TestRemoteRetention:
    """A server deletes what it sent and a schedule made, and nothing else."""

    def push_scheduled(
        self, backups: DatabaseBackups, runner: RcloneRunner, days: int, **limits: Any
    ) -> str:
        taken = backups.dump("postgresql", "shop", origin="scheduled")
        backups.push("postgresql", taken.name, "nas", scheduled=True, **limits)
        age(remote_dir(runner) / taken.name, days)
        return taken.name

    def test_beyond_the_count_is_deleted_with_its_sidecar(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        names = [self.push_scheduled(backups, runner, days) for days in (30, 20, 10)]
        taken = backups.dump("postgresql", "shop", origin="scheduled")

        summary = backups.push("postgresql", taken.name, "nas", scheduled=True, retention_count=2)

        assert sorted(summary["retention_deleted"]) == sorted(names[:2])
        remaining = sorted(p.name for p in remote_dir(runner).iterdir())
        assert remaining == sorted([names[2], f"{names[2]}.json", taken.name, f"{taken.name}.json"])

    def test_by_age(self, backups: DatabaseBackups, runner: RcloneRunner) -> None:
        old = self.push_scheduled(backups, runner, 90)
        recent = self.push_scheduled(backups, runner, 2)
        taken = backups.dump("postgresql", "shop", origin="scheduled")

        summary = backups.push("postgresql", taken.name, "nas", scheduled=True, retention_days=30)

        assert summary["retention_deleted"] == [old]
        assert (remote_dir(runner) / recent).is_file()

    def test_a_dump_sent_by_hand_is_never_deleted(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        by_hand = backups.dump("postgresql", "shop")
        backups.push("postgresql", by_hand.name, "nas", scheduled=False)
        age(remote_dir(runner) / by_hand.name, 500)
        taken = backups.dump("postgresql", "shop", origin="scheduled")

        summary = backups.push(
            "postgresql", taken.name, "nas", scheduled=True, retention_count=1, retention_days=7
        )

        assert summary["retention_deleted"] == []
        assert (remote_dir(runner) / by_hand.name).is_file()

    def test_another_servers_dumps_are_never_deleted(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        folder = remote_dir(runner)
        folder.mkdir(parents=True)
        (folder / "postgresql-shop-20240101_000000.dump").write_bytes(b"theirs")
        (folder / "postgresql-shop-20240101_000000.dump.json").write_text(
            json.dumps(
                {
                    "name": "postgresql-shop-20240101_000000.dump",
                    "origin": "somebody-else",
                    "scheduled": True,
                }
            )
        )
        age(folder / "postgresql-shop-20240101_000000.dump", 800)
        taken = backups.dump("postgresql", "shop", origin="scheduled")

        summary = backups.push(
            "postgresql", taken.name, "nas", scheduled=True, retention_count=1, retention_days=7
        )

        assert summary["retention_deleted"] == []
        assert (folder / "postgresql-shop-20240101_000000.dump").is_file()

    def test_a_file_with_no_sidecar_is_never_deleted(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        folder = remote_dir(runner)
        folder.mkdir(parents=True)
        (folder / "mystery.dump").write_bytes(b"who knows")
        age(folder / "mystery.dump", 900)
        taken = backups.dump("postgresql", "shop", origin="scheduled")

        backups.push("postgresql", taken.name, "nas", scheduled=True, retention_count=1)

        assert (folder / "mystery.dump").is_file()


# ============================================================== listing


class TestRemoteListing:
    """What a destination holds, whoever put it there."""

    def test_the_dumps_of_a_database_with_who_sent_them(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        taken = backups.dump("postgresql", "shop", origin="scheduled")
        backups.push("postgresql", taken.name, "nas", scheduled=True)
        other = remote_dir(runner) / "postgresql-shop-20240101_000000.dump"
        other.write_bytes(b"theirs")
        (remote_dir(runner) / f"{other.name}.json").write_text(
            json.dumps({"name": other.name, "origin": "somebody-else", "sha256": "ab" * 32})
        )

        dumps = backups.remote_dumps("postgresql", "shop", "nas")

        by_name = {dump["name"]: dump for dump in dumps}
        assert by_name[taken.name]["own"] is True
        assert by_name[taken.name]["scheduled"] is True
        assert by_name[taken.name]["local"] is True
        assert by_name[taken.name]["format"] == "custom"
        assert by_name[other.name]["own"] is False
        assert by_name[other.name]["local"] is False
        assert by_name[other.name]["sha256"] == "ab" * 32

    def test_a_database_with_no_remote_dumps_lists_nothing(self, backups: DatabaseBackups) -> None:
        assert backups.remote_dumps("postgresql", "shop", "nas") == []

    def test_the_databases_a_destination_holds_for_an_engine(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        taken = backups.dump("postgresql", "shop")
        backups.push("postgresql", taken.name, "nas")
        (runner.root / "nas" / "wasm-backups" / "databases" / "postgresql" / "blog").mkdir()

        assert backups.remote_databases("postgresql", "nas") == ["blog", "shop"]

    def test_a_folder_cannot_climb_out_of_the_destination(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        files = backups.destinations

        for folder in ("databases/../../etc", "../x", "databases//..", "", "a b"):
            with pytest.raises(BackupError):
                files.list_files("nas", folder)

    def test_a_file_name_is_one_path_segment(self, backups: DatabaseBackups) -> None:
        with pytest.raises(BackupError):
            backups.destinations.delete_file("nas", "databases/postgresql/shop", "../../x")


class TestRemoteDelete:
    """A copy on a destination is deleted with its sidecar, and forgotten."""

    def test_the_copy_and_its_sidecar_go_and_the_record_forgets_it(
        self, backups: DatabaseBackups, runner: RcloneRunner, audited_events: list[dict[str, Any]]
    ) -> None:
        taken = backups.dump("postgresql", "shop")
        backups.push("postgresql", taken.name, "nas")

        backups.delete_remote("postgresql", "shop", taken.name, "nas")

        assert list(remote_dir(runner).iterdir()) == []
        (view,) = backups.list_dumps("postgresql", "shop")
        assert view.to_dict()["destinations"] == []
        assert taken.info.path.is_file()
        last = audited_events[-1]
        assert last["event"] == "db.backup.delete"
        assert last["details"]["destination"] == "nas"

    def test_deleting_what_is_already_gone_is_not_an_error(self, backups: DatabaseBackups) -> None:
        backups.delete_remote("postgresql", "shop", "postgresql-shop-20250101_010101.dump", "nas")


# ================================================================ restoring


class TestRestoreRemote:
    """Download, check the digest, check the dump, then and only then touch the database."""

    @staticmethod
    def pushed(backups: DatabaseBackups) -> str:
        taken = backups.dump("postgresql", "shop")
        backups.push("postgresql", taken.name, "nas")
        return taken.name

    def test_a_dump_that_is_only_on_the_destination_restores_as_a_new_database(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager], dumps_dir: Path
    ) -> None:
        name = self.pushed(backups)
        (dumps_dir / name).unlink()

        outcome = backups.restore_remote(
            "postgresql", "shop", "nas", name, new_name="shop_from_nas"
        )

        assert outcome.database == "shop_from_nas"
        assert ("load", "shop_from_nas", name) in engine.state["calls"]  # type: ignore[attr-defined]
        assert backups.service.store.get_database("shop_from_nas", "postgresql") is not None
        # The download is cleaned up, and is never mistaken for a dump.
        assert not (dumps_dir / ".remote-staging").exists() or not list(
            (dumps_dir / ".remote-staging").iterdir()
        )
        assert [v.name for v in backups.list_dumps("postgresql")] == []

    def test_a_replace_takes_the_safety_copy_first(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        name = self.pushed(backups)

        outcome = backups.restore_remote("postgresql", "shop", "nas", name, drop_existing=True)

        assert outcome.safety_copy is not None
        assert outcome.replaced is True
        kinds = {v.name: v.to_dict()["kind"] for v in backups.list_dumps("postgresql")}
        assert kinds[outcome.safety_copy.name] == "safety"

    def test_a_download_that_does_not_match_its_digest_restores_nothing(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager], runner: RcloneRunner
    ) -> None:
        name = self.pushed(backups)
        (remote_dir(runner) / name).write_bytes(PG_ARCHIVE + b" rotted on the destination")

        with pytest.raises(BackupError) as refused:
            backups.restore_remote("postgresql", "shop", "nas", name, new_name="copy")

        assert "Checksum mismatch" in str(refused.value)
        assert not [c for c in engine.state["calls"] if c[0] in ("load", "create")]  # type: ignore[attr-defined]

    def test_a_download_that_fails_its_check_restores_nothing(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager], runner: RcloneRunner
    ) -> None:
        name = self.pushed(backups)
        runner.script(("pg_restore", "--list"), stderr="pg_restore: error: bad", exit_code=1)

        with pytest.raises(DatabaseBackupError) as refused:
            backups.restore_remote("postgresql", "shop", "nas", name, new_name="copy")

        assert "nothing was restored" in str(refused.value)
        assert "pg_restore: error: bad" in str(refused.value)
        assert not [c for c in engine.state["calls"] if c[0] in ("load", "create")]  # type: ignore[attr-defined]

    def test_a_dump_that_is_not_on_the_destination_says_so(self, backups: DatabaseBackups) -> None:
        with pytest.raises(BackupError, match="not found"):
            backups.restore_remote(
                "postgresql", "shop", "nas", "postgresql-shop-20250101_010101.dump"
            )

    def test_a_download_with_no_sidecar_still_restores_after_its_check(
        self, backups: DatabaseBackups, runner: RcloneRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        folder = remote_dir(runner)
        folder.mkdir(parents=True)
        (folder / "postgresql-shop-20240101_000000.dump").write_bytes(PG_ARCHIVE)

        outcome = backups.restore_remote(
            "postgresql", "shop", "nas", "postgresql-shop-20240101_000000.dump", new_name="old"
        )

        assert outcome.database == "old"

    def test_the_download_is_removed_even_when_the_restore_fails(
        self,
        backups: DatabaseBackups,
        engine: type[BaseDatabaseManager],
        dumps_dir: Path,
    ) -> None:
        name = self.pushed(backups)
        engine.state["fail_load"] = "pg_restore: error: boom"  # type: ignore[attr-defined]

        with pytest.raises(DatabaseBackupError):
            backups.restore_remote("postgresql", "shop", "nas", name, drop_existing=True)

        staging = dumps_dir / ".remote-staging"
        assert not staging.exists() or list(staging.iterdir()) == []

    def test_restoring_a_local_dump_that_does_not_exist_is_a_404(
        self, backups: DatabaseBackups
    ) -> None:
        with pytest.raises(DatabaseNotFoundError):
            backups.restore("postgresql", "shop", "postgresql-shop-20250101_010101.dump")
