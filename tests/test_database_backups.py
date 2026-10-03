# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database backups (3.1, M6): what turns a dump into a backup.

What is pinned here is what an operator learns about on the worst day when it
is not true:

- a dump is hashed and checked the moment it is taken, by the engine's own
  reading of it, and a dump that fails says why in the tool's own words;
- retention only ever deletes dumps a schedule made, keeps the newest, and
  applies by count and by age;
- a policy run checks the new dump *before* it sends or deletes anything, so a
  dump that does not restore can neither replace a good remote copy nor push
  an old one out;
- a restore test loads into a temporary database and drops it, and never
  touches a database it did not make;
- the safety copy a restore takes is kept and is never retention's to delete;
- every change is on the audit trail, and a failed run reaches the operator
  with the database as its subject.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import (
    BackupError,
    DatabaseBackupError,
    DatabaseNotFoundError,
    ValidationError,
)
from noust.core.notifications.context import NotificationContext
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.database.backup_verify import (
    check_dump,
    restore_test,
    temporary_name,
)
from noust.managers.database.backups import DatabaseBackups, remote_folder
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.service import DatabaseService
from noust.managers.retention import select_expired
from tests.database_backup_support import (
    MYSQL_DUMP,
    MYSQL_DUMP_CUT,
    PG_ARCHIVE,
    PG_LISTING,
    PG_PLAIN,
    PG_PLAIN_CUT,
    RcloneRunner,
    age,
    make_engine,
    make_service,
)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    """Force a fresh store singleton per test; see test_backup_destinations.py."""
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    """
    A runner that answers rclone from a directory, with pg_restore content to list.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The runner, installed as the process-wide one.
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
        A PostgreSQL fake with one database, ``shop``, writing custom archives.
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
        runner: The fake runner, installed.

    Returns:
        A service over the fake engine and an isolated store.
    """
    return make_service(tmp_path, [engine])


class FakeScheduler:
    """Stands in for the timer installer: records what it was asked, touches no unit."""

    def __init__(self) -> None:
        self.installed: dict[tuple[str, str], str] = {}
        self.removed: list[tuple[str, str]] = []

    def install_database_timer(self, engine: str, database: str, schedule: str) -> str:
        self.installed[(engine, database)] = schedule
        return f"noust-backup-db-{engine}-{database}"

    def remove_database_timer(self, engine: str, database: str) -> bool:
        self.removed.append((engine, database))
        self.installed.pop((engine, database), None)
        return True

    def database_timer_state(self, engine: str, database: str) -> dict[str, Any]:
        installed = (engine, database) in self.installed
        return {"installed": installed, "next_run": "Tue 2026-09-30 02:00:00 UTC", "last_run": None}


@pytest.fixture
def backups(service: DatabaseService, runner: RcloneRunner) -> DatabaseBackups:
    """
    Args:
        service: The service over the fake engine.
        runner: The fake runner, which the destination manager runs rclone through.

    Returns:
        The backups class, with a scheduler that only records.
    """
    return DatabaseBackups(
        service,
        scheduler=FakeScheduler(),  # type: ignore[arg-type]
        destinations=DestinationFileManager(runner=runner),
    )


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
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


def add_destination(runner: RcloneRunner, name: str = "nas", *, encrypted: bool = False) -> None:
    """
    Create a backup destination the fake rclone answers for.

    Args:
        runner: The fake runner.
        name: The destination's name.
        encrypted: Wrap it in a crypt remote.
    """
    DestinationFileManager(runner=runner).add(
        name,
        "sftp",
        {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t"},
        encrypted=encrypted,
    )


# ============================================================== retention


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


class TestRetention:
    """The one rule, for the directory and for every destination."""

    def test_by_count_keeps_the_newest_n(self) -> None:
        entries = [(f"d{i}", days_ago(i)) for i in range(5)]

        assert select_expired(entries, count=2, days=None, now=NOW) == ["d2", "d3", "d4"]

    def test_by_age_deletes_what_is_older(self) -> None:
        entries = [("new", days_ago(1)), ("old", days_ago(40)), ("older", days_ago(90))]

        assert select_expired(entries, count=None, days=30, now=NOW) == ["old", "older"]

    def test_either_limit_is_enough(self) -> None:
        entries = [("a", days_ago(1)), ("b", days_ago(2)), ("c", days_ago(50))]

        assert select_expired(entries, count=2, days=30, now=NOW) == ["c"]
        assert select_expired(entries, count=1, days=30, now=NOW) == ["b", "c"]

    def test_the_newest_is_never_deleted_even_when_it_is_older_than_the_limit(self) -> None:
        # A timer that stopped must not leave a database with no dump at all.
        entries = [("only", days_ago(400)), ("older", days_ago(500))]

        assert select_expired(entries, count=None, days=30, now=NOW) == ["older"]

    def test_no_limits_delete_nothing(self) -> None:
        entries = [(f"d{i}", days_ago(i * 100)) for i in range(5)]

        assert select_expired(entries, count=None, days=None, now=NOW) == []

    def test_an_empty_list_is_fine(self) -> None:
        assert select_expired([], count=3, days=3, now=NOW) == []

    def test_naive_moments_read_as_utc(self) -> None:
        entries = [("a", datetime(2026, 9, 28, 12, 0)), ("b", datetime(2026, 7, 1, 12, 0))]

        assert select_expired(entries, count=None, days=30, now=NOW) == ["b"]


# ============================================================ verification


class TestDumpIsChecked:
    """A dump is hashed and read the way the engine's own tool reads it."""

    def test_a_dump_is_recorded_hashed_and_checked(self, backups: DatabaseBackups) -> None:
        view = backups.dump("postgresql", "shop")

        assert view.record is not None
        assert view.record.origin == "manual"
        assert view.record.verify_status == "ok"
        assert view.record.verify_method == "pg_restore --list"
        assert len(view.record.sha256 or "") == 64
        assert view.to_dict()["verify_status"] == "ok"

    def test_a_dump_that_fails_its_check_says_so_in_the_tools_words(
        self, backups: DatabaseBackups, runner: RcloneRunner, dumps_dir: Path
    ) -> None:
        runner.script(
            ("pg_restore", "--list"),
            stderr="pg_restore: error: unsupported version (1.99) in file header",
            exit_code=1,
        )

        with pytest.raises(DatabaseBackupError) as failure:
            backups.dump("postgresql", "shop")

        assert "unsupported version (1.99) in file header" in str(failure.value)
        # The file is evidence: it stays, marked failed.
        kept = backups.list_dumps("postgresql", "shop")
        assert len(kept) == 1
        assert kept[0].to_dict()["verify_status"] == "failed"
        assert "unsupported version" in kept[0].to_dict()["verify_detail"]

    def test_an_archive_that_lists_nothing_is_not_a_dump(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        runner.script(("pg_restore", "--list"), stdout="; only comments\n")

        with pytest.raises(DatabaseBackupError, match="failed its check"):
            backups.dump("postgresql", "shop")

    def test_a_listed_dump_that_was_never_checked_reads_as_unverified(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        (dumps_dir / "postgresql-shop-20250101_010101.dump").write_bytes(PG_ARCHIVE)

        (view,) = backups.list_dumps("postgresql", "shop")

        assert view.to_dict()["verify_status"] == "unverified"
        assert view.to_dict()["kind"] == "unknown"
        assert view.to_dict()["destinations"] == []

    def test_verifying_records_the_evidence_on_the_dump(self, backups: DatabaseBackups) -> None:
        taken = backups.dump("postgresql", "shop")

        view = backups.verify("postgresql", taken.name)

        assert view.record is not None
        assert view.record.verified_at
        assert view.record.verify_detail == "1 objects listed."

    def test_a_dump_that_changed_on_disk_since_it_was_taken_fails(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        taken = backups.dump("postgresql", "shop")
        (dumps_dir / taken.name).write_bytes(PG_ARCHIVE + b"tampered")

        view = backups.verify("postgresql", taken.name)

        assert view.record is not None
        assert view.record.verify_status == "failed"
        assert "changed on disk" in (view.record.verify_detail or "")

    def test_verifying_a_dump_that_does_not_exist_is_a_404(self, backups: DatabaseBackups) -> None:
        with pytest.raises(DatabaseNotFoundError):
            backups.verify("postgresql", "postgresql-shop-20250101_010101.dump")

    def test_a_path_is_not_a_dump_name(self, backups: DatabaseBackups) -> None:
        with pytest.raises(ValidationError):
            backups.verify("postgresql", "../../etc/passwd")


class TestChecksPerEngine:
    """Each engine's dump is read the way its own tool reads it."""

    @staticmethod
    def check(tmp_path: Path, runner: RcloneRunner, **engine_args: Any) -> tuple[bool, str, str]:
        directory = tmp_path / f"engine-dumps-{secrets.token_hex(3)}"
        directory.mkdir()
        cls = make_engine(directory, **engine_args)
        manager = cls()
        info = manager.backup("shop")
        outcome = check_dump(manager, info.path)
        return outcome.ok, outcome.method, outcome.detail

    def test_postgresql_plain_whole(self, tmp_path: Path, runner: RcloneRunner) -> None:
        ok, method, _ = self.check(tmp_path, runner, suffix=".sql", content=PG_PLAIN)

        assert (ok, method) == (True, "trailer")

    def test_postgresql_plain_cut_short(self, tmp_path: Path, runner: RcloneRunner) -> None:
        ok, method, detail = self.check(tmp_path, runner, suffix=".sql", content=PG_PLAIN_CUT)

        assert (ok, method) == (False, "trailer")
        assert "cut short" in detail

    def test_postgresql_plain_with_a_psql_meta_command_is_refused(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        hostile = b"-- PostgreSQL database dump\n\\! id\n-- PostgreSQL database dump complete\n"

        ok, method, _ = self.check(tmp_path, runner, suffix=".sql", content=hostile)

        assert (ok, method) == (False, "psql script check")

    def test_postgresql_gzipped_is_decompressed_and_read(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        runner.script(("gzip", "-dc"), stdout=PG_PLAIN.decode())

        ok, method, _ = self.check(tmp_path, runner, suffix=".sql.gz", content=b"gz bytes")

        assert (ok, method) == (True, "trailer")
        assert runner.ran("gzip", "-dc")

    def test_a_corrupt_gzip_fails_with_gzips_words(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        runner.script(("gzip", "-dc"), stderr="gzip: stdin: unexpected end of file", exit_code=1)

        ok, method, detail = self.check(tmp_path, runner, suffix=".sql.gz", content=b"cut")

        assert (ok, method) == (False, "gzip -dc")
        assert "unexpected end of file" in detail

    def test_mysql_whole(self, tmp_path: Path, runner: RcloneRunner) -> None:
        ok, method, _ = self.check(
            tmp_path, runner, engine="mysql", suffix=".sql", content=MYSQL_DUMP
        )

        assert (ok, method) == (True, "trailer")

    def test_mysql_cut_short(self, tmp_path: Path, runner: RcloneRunner) -> None:
        ok, _, detail = self.check(
            tmp_path, runner, engine="mysql", suffix=".sql", content=MYSQL_DUMP_CUT
        )

        assert ok is False
        assert "Dump completed" in detail

    def test_mongodb_is_listed_by_tar(self, tmp_path: Path, runner: RcloneRunner) -> None:
        runner.script(("tar", "-tzf"), stdout="shop/\nshop/users.bson\nshop/users.metadata.json\n")

        ok, method, detail = self.check(
            tmp_path, runner, engine="mongodb", suffix=".tar.gz", content=b"tar bytes"
        )

        assert (ok, method) == (True, "tar -tzf")
        assert "3 entries" in detail

    def test_mongodb_archive_tar_cannot_read_fails_verbatim(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        runner.script(("tar", "-tzf"), stderr="tar: Unexpected EOF in archive", exit_code=2)

        ok, _, detail = self.check(
            tmp_path, runner, engine="mongodb", suffix=".tar.gz", content=b"cut"
        )

        assert ok is False
        assert "Unexpected EOF in archive" in detail

    def test_redis_uses_redis_check_rdb_when_it_is_installed(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        runner.only_knows("rclone", "redis-check-rdb")
        runner.script(
            ("redis-check-rdb",), stdout="[offset 0] Checking RDB file\n\\o/ RDB looks OK! \\o/\n"
        )

        ok, method, detail = self.check(
            tmp_path, runner, engine="redis", suffix=".rdb", content=b"REDIS0011"
        )

        assert (ok, method) == (True, "redis-check-rdb")
        assert "RDB looks OK" in detail

    def test_redis_without_the_tool_checks_the_header(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        ok, method, _ = self.check(
            tmp_path, runner, engine="redis", suffix=".rdb", content=b"REDIS0011"
        )
        bad_ok, bad_method, _ = self.check(
            tmp_path, runner, engine="redis", suffix=".rdb", content=b"not a snapshot"
        )

        assert (ok, method) == (True, "header")
        assert (bad_ok, bad_method) == (False, "header")

    def test_an_empty_dump_fails_whatever_the_engine(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        ok, method, detail = self.check(tmp_path, runner, content=b"")

        assert (ok, method) == (False, "size")
        assert "0 bytes" in detail


# ============================================================ restore test


class TestRestoreTest:
    """Loading a dump into a temporary database, and dropping it."""

    def test_the_temporary_database_is_loaded_looked_at_and_dropped(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        taken = backups.dump("postgresql", "shop")

        view = backups.verify("postgresql", taken.name, restore=True)

        assert view.record is not None
        assert view.record.restore_test_status == "ok"
        assert "7 tables" in (view.record.restore_test_detail or "")
        assert "dropped afterwards" in (view.record.restore_test_detail or "")
        calls = engine.state["calls"]  # type: ignore[attr-defined]
        created = [call[1] for call in calls if call[0] == "create"]
        dropped = [call[1] for call in calls if call[0] == "drop"]
        assert len(created) == 1
        assert created == dropped
        assert created[0].startswith("noust_verify_")
        # Nothing that existed was touched.
        assert list(engine.state["dbs"]) == ["shop"]  # type: ignore[attr-defined]

    def test_a_dump_that_does_not_load_fails_with_the_loaders_words_and_still_drops(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        taken = backups.dump("postgresql", "shop")
        engine.state["fail_load"] = "pg_restore: error: could not execute query: relation exists"  # type: ignore[attr-defined]

        view = backups.verify("postgresql", taken.name, restore=True)

        assert view.record is not None
        assert view.record.restore_test_status == "failed"
        assert "could not execute query: relation exists" in (view.record.restore_test_detail or "")
        assert list(engine.state["dbs"]) == ["shop"]  # type: ignore[attr-defined]

    def test_a_dump_that_failed_its_check_is_not_test_restored(
        self, backups: DatabaseBackups, runner: RcloneRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        taken = backups.dump("postgresql", "shop")
        runner.script(("pg_restore", "--list"), stderr="pg_restore: error: bad", exit_code=1)

        view = backups.verify("postgresql", taken.name, restore=True)

        assert view.record is not None
        assert view.record.verify_status == "failed"
        assert view.record.restore_test_status is None
        assert not [c for c in engine.state["calls"] if c[0] == "create"]  # type: ignore[attr-defined]

    def test_a_leftover_temporary_database_is_reported_not_hidden(
        self,
        backups: DatabaseBackups,
        engine: type[BaseDatabaseManager],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        taken = backups.dump("postgresql", "shop")

        def stuck(self: Any, name: str, force: bool = False) -> None:
            raise DatabaseBackupError("cannot drop", details="database is being accessed")

        monkeypatch.setattr(engine, "drop_database", stuck)

        view = backups.verify("postgresql", taken.name, restore=True)

        assert view.record is not None
        assert view.record.restore_test_status == "failed"
        assert "still there" in (view.record.restore_test_detail or "")
        assert "Drop it by hand" in (view.record.restore_test_detail or "")

    def test_the_name_is_generated_and_a_taken_one_is_never_used(
        self, dumps_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager = make_engine(dumps_dir)()
        monkeypatch.setattr(type(manager), "database_exists", lambda self, name: True)
        drops: list[str] = []
        monkeypatch.setattr(
            type(manager), "drop_database", lambda self, n, force=False: drops.append(n)
        )
        path = manager.backup("shop").path

        result = restore_test(manager, path)

        assert result.ok is False
        assert "already exists" in result.detail
        assert drops == []
        assert temporary_name().startswith("noust_verify_")
        assert len(temporary_name()) == len("noust_verify_") + 8

    def test_a_redis_snapshot_cannot_be_test_restored(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        directory = tmp_path / "redis-dumps"
        directory.mkdir()
        cls = make_engine(
            directory, engine="redis", suffix=".rdb", content=b"REDIS0011", databases=("0",)
        )
        backups = DatabaseBackups(
            make_service(tmp_path, [cls]),
            scheduler=FakeScheduler(),  # type: ignore[arg-type]
            destinations=DestinationFileManager(runner=runner),
        )
        taken = backups.dump("redis", "0")

        with pytest.raises(DatabaseBackupError, match="cannot be test-restored"):
            backups.verify("redis", taken.name, restore=True)


# ================================================================ policies


class TestPolicy:
    """A policy per database, and the timer that runs it."""

    def test_setting_a_policy_installs_the_timer_and_stores_the_row(
        self, backups: DatabaseBackups, audited: list[dict[str, Any]]
    ) -> None:
        view = backups.set_policy(
            "postgresql", "shop", schedule="daily", retention_count=5, retention_days=14
        )

        assert backups.scheduler.installed == {("postgresql", "shop"): "*-*-* 02:00:00"}  # type: ignore[attr-defined]
        data = view.to_dict()
        assert data["configured"] is True
        assert data["schedule"] == "*-*-* 02:00:00"
        assert (data["retention_count"], data["retention_days"]) == (5, 14)
        assert data["timer"]["next_run"] == "Tue 2026-09-30 02:00:00 UTC"
        assert [e["event"] for e in audited] == ["db.backup.policy"]
        assert audited[0]["target"] == "db:postgresql/shop"

    def test_a_database_with_no_policy_says_so(self, backups: DatabaseBackups) -> None:
        data = backups.get_policy("postgresql", "shop").to_dict()

        assert data["configured"] is False
        assert data["timer"] == {}

    def test_saving_again_replaces_it_and_keeps_what_the_last_run_recorded(
        self, backups: DatabaseBackups
    ) -> None:
        backups.set_policy("postgresql", "shop", schedule="daily")
        backups.run_policy("postgresql", "shop")

        view = backups.set_policy("postgresql", "shop", schedule="weekly")

        data = view.to_dict()
        assert data["schedule"] == "Mon *-*-* 02:00:00"
        assert data["last_status"] == "ok"
        assert len(backups.list_policies()) == 1

    def test_a_disabled_policy_keeps_its_settings_and_has_no_timer(
        self, backups: DatabaseBackups
    ) -> None:
        backups.set_policy("postgresql", "shop", retention_count=9)

        view = backups.set_policy("postgresql", "shop", retention_count=9, enabled=False)

        assert backups.scheduler.installed == {}  # type: ignore[attr-defined]
        assert ("postgresql", "shop") in backups.scheduler.removed  # type: ignore[attr-defined]
        data = view.to_dict()
        assert data["enabled"] is False
        assert data["retention_count"] == 9
        assert data["timer"] == {}

    def test_removing_a_policy_removes_the_timer_and_keeps_the_dumps(
        self, backups: DatabaseBackups, audited: list[dict[str, Any]]
    ) -> None:
        backups.set_policy("postgresql", "shop")
        taken = backups.dump("postgresql", "shop")
        audited.clear()

        assert backups.remove_policy("postgresql", "shop") is True

        assert backups.get_policy("postgresql", "shop").policy is None
        assert [v.name for v in backups.list_dumps("postgresql")] == [taken.name]
        assert [e["event"] for e in audited] == ["db.backup.policy.remove"]
        assert backups.remove_policy("postgresql", "shop") is False

    @pytest.mark.parametrize(
        ("arguments", "field"),
        [
            ({"retention_count": 0}, "retention_count"),
            ({"retention_days": 99999}, "retention_days"),
            ({"destinations": [{"name": "nowhere"}]}, "destinations"),
            ({"dump_format": "directory"}, "dump_format"),
        ],
    )
    def test_an_unusable_value_is_a_validation_error_naming_the_field(
        self, backups: DatabaseBackups, arguments: dict[str, Any], field: str
    ) -> None:
        with pytest.raises(ValidationError) as refused:
            backups.set_policy("postgresql", "shop", **arguments)

        assert refused.value.field == field
        assert backups.scheduler.installed == {}  # type: ignore[attr-defined]

    def test_a_calendar_the_scheduler_refuses_is_refused(self, backups: DatabaseBackups) -> None:
        with pytest.raises(BackupError):
            backups.set_policy("postgresql", "shop", schedule="daily\nExecStart=/bin/evil")

    def test_a_database_that_does_not_exist_is_a_404(self, backups: DatabaseBackups) -> None:
        with pytest.raises(DatabaseNotFoundError):
            backups.set_policy("postgresql", "ghost")

    def test_destinations_must_exist_and_keep_their_own_retention(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        add_destination(runner)

        view = backups.set_policy(
            "postgresql",
            "shop",
            destinations=[{"name": "nas", "retention_count": 4, "retention_days": 60}],
        )

        assert view.to_dict()["destinations"] == [
            {
                "name": "nas",
                "retention_count": 4,
                "retention_days": 60,
                "exists": True,
                "encrypted": False,
            }
        ]

    def test_a_policy_says_which_destinations_are_encrypted_and_which_are_gone(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        add_destination(runner, "nas")
        add_destination(runner, "vault", encrypted=True)
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}, {"name": "vault"}])
        DestinationFileManager(runner=runner).remove("nas", force=True, key_saved=True)
        # ``force`` drops the reference; put it back as an operator editing the store would.
        policy = backups.records.policy("postgresql", "shop")
        assert policy is not None
        policy.destinations = [{"name": "nas"}, {"name": "vault"}]
        backups.records.save_policy(policy)

        states = {
            d["name"]: (d["exists"], d["encrypted"])
            for d in backups.get_policy("postgresql", "shop").to_dict()["destinations"]
        }

        assert states == {"nas": (False, False), "vault": (True, True)}

    def test_removing_a_destination_a_policy_names_is_refused_until_forced(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        add_destination(runner)
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}])
        manager = DestinationFileManager(runner=runner)

        with pytest.raises(BackupError) as refused:
            manager.remove("nas")

        assert "postgresql/shop" in str(refused.value)
        assert manager.get("nas") is not None

        manager.remove("nas", force=True)

        assert manager.get("nas") is None
        assert backups.get_policy("postgresql", "shop").to_dict()["destinations"] == []

    def test_a_destination_named_twice_is_refused(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        add_destination(runner)

        with pytest.raises(ValidationError):
            backups.set_policy(
                "postgresql", "shop", destinations=[{"name": "nas"}, {"name": "nas"}]
            )

    def test_a_redis_snapshot_cannot_be_asked_to_test_restore(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        directory = tmp_path / "redis-dumps"
        directory.mkdir()
        cls = make_engine(
            directory, engine="redis", suffix=".rdb", content=b"REDIS0011", databases=("0",)
        )
        backups = DatabaseBackups(
            make_service(tmp_path, [cls]),
            scheduler=FakeScheduler(),  # type: ignore[arg-type]
            destinations=DestinationFileManager(runner=runner),
        )

        with pytest.raises(ValidationError) as refused:
            backups.set_policy("redis", "0", verify_restore=True)

        assert refused.value.field == "verify_restore"

    def test_the_databases_without_a_policy_are_listed(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["dbs"]["blog"] = {"owner": "app", "tables": 1}  # type: ignore[attr-defined]
        backups.set_policy("postgresql", "shop")

        assert backups.unprotected() == [{"engine": "postgresql", "database": "blog"}]

    def test_a_disabled_policy_does_not_protect(self, backups: DatabaseBackups) -> None:
        backups.set_policy("postgresql", "shop", enabled=False)

        assert backups.unprotected() == [{"engine": "postgresql", "database": "shop"}]


# ============================================================== policy run


def set_up_history(backups: DatabaseBackups, dumps_dir: Path) -> dict[str, Path]:
    """
    Lay down a history of dumps to prune: five scheduled, one manual, one safety copy.

    Args:
        backups: The backups class.
        dumps_dir: The dump directory.

    Returns:
        The files by role.
    """
    files: dict[str, Path] = {}
    for index, days in enumerate((50, 40, 30, 20, 10)):
        view = backups.dump("postgresql", "shop", origin="scheduled")
        age(view.info.path, days)
        files[f"scheduled-{index}"] = view.info.path
    manual = backups.dump("postgresql", "shop")
    age(manual.info.path, 400)
    files["manual"] = manual.info.path
    safety = backups.dump("postgresql", "shop", origin="manual")
    record = safety.record
    assert record is not None
    record.origin = "safety"
    backups.records.save_dump(record)
    age(safety.info.path, 500)
    files["safety"] = safety.info.path
    return files


class TestRunPolicy:
    """Dump, check, send, prune: in that order, and stopping when a step is not sure."""

    def test_a_run_takes_a_scheduled_dump_checks_it_and_notes_how_it_went(
        self, backups: DatabaseBackups
    ) -> None:
        backups.set_policy("postgresql", "shop")

        result = backups.run_policy("postgresql", "shop")

        assert result["dump"]["kind"] == "scheduled"
        assert result["dump"]["verify_status"] == "ok"
        policy = backups.get_policy("postgresql", "shop").to_dict()
        assert policy["last_status"] == "ok"
        assert policy["last_dump"] == result["dump"]["name"]
        assert policy["last_error"] is None
        assert policy["last_success_at"]

    def test_retention_deletes_only_what_a_schedule_made_and_keeps_the_newest(
        self, backups: DatabaseBackups, dumps_dir: Path, audited: list[dict[str, Any]]
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        backups.set_policy("postgresql", "shop", retention_count=3, retention_days=None)

        result = backups.run_policy("postgresql", "shop")

        surviving = {view.name: view.to_dict()["kind"] for view in backups.list_dumps("postgresql")}
        # The new dump and the two newest scheduled ones: three.
        assert (
            sorted(kind for kind in surviving.values() if kind == "scheduled") == ["scheduled"] * 3
        )
        assert files["scheduled-4"].name in surviving
        assert files["scheduled-3"].name in surviving
        for role in ("scheduled-0", "scheduled-1", "scheduled-2"):
            assert not files[role].exists(), role
        # A dump taken by hand and a restore's safety copy are never retention's, however old.
        assert files["manual"].name in surviving
        assert files["safety"].name in surviving
        assert sorted(result["local_deleted"]) == sorted(
            files[role].name for role in ("scheduled-0", "scheduled-1", "scheduled-2")
        )
        deleted = [e for e in audited if e["event"] == "db.backup.delete"]
        assert {e["details"]["reason"] for e in deleted} == {"retention"}

    def test_retention_by_age_alone(self, backups: DatabaseBackups, dumps_dir: Path) -> None:
        files = set_up_history(backups, dumps_dir)
        backups.set_policy("postgresql", "shop", retention_count=None, retention_days=25)

        backups.run_policy("postgresql", "shop")

        assert not files["scheduled-0"].exists()  # 50 days
        assert not files["scheduled-1"].exists()  # 40
        assert not files["scheduled-2"].exists()  # 30
        assert files["scheduled-3"].exists()  # 20
        assert files["scheduled-4"].exists()  # 10

    def test_a_dump_that_fails_its_check_stops_everything_before_anything_is_sent_or_deleted(
        self,
        backups: DatabaseBackups,
        dumps_dir: Path,
        runner: RcloneRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        add_destination(runner)
        files = set_up_history(backups, dumps_dir)
        backups.set_policy(
            "postgresql",
            "shop",
            retention_count=1,
            destinations=[{"name": "nas", "retention_count": 1}],
        )
        runner.script(
            ("pg_restore", "--list"),
            stderr="pg_restore: error: could not read from input file: end of file",
            exit_code=1,
        )
        sent: list[Any] = []
        monkeypatch.setattr("noust.managers.database.backups.deliver", sent.append)

        with pytest.raises(DatabaseBackupError) as failure:
            backups.run_policy("postgresql", "shop", notify=True)

        assert "could not read from input file: end of file" in str(failure.value)
        # Nothing was sent, and no old dump was pushed out to make room for a bad one.
        assert not [call for call in runner.calls if call[:2] == ("rclone", "copyto")]
        assert all(path.exists() for path in files.values())
        policy = backups.get_policy("postgresql", "shop").to_dict()
        assert policy["last_status"] == "failed"
        assert "end of file" in policy["last_error"]
        assert len(sent) == 1

    def test_a_dump_that_does_not_restore_stops_the_run_too(
        self,
        backups: DatabaseBackups,
        runner: RcloneRunner,
        engine: type[BaseDatabaseManager],
    ) -> None:
        add_destination(runner)
        backups.set_policy(
            "postgresql", "shop", verify_restore=True, destinations=[{"name": "nas"}]
        )
        engine.state["fail_load"] = 'psql: ERROR: type "vector" does not exist'  # type: ignore[attr-defined]

        with pytest.raises(DatabaseBackupError, match="did not restore into a temporary database"):
            backups.run_policy("postgresql", "shop")

        assert not [call for call in runner.calls if call[:2] == ("rclone", "copyto")]
        assert "vector" in backups.get_policy("postgresql", "shop").to_dict()["last_error"]

    def test_a_restore_test_that_passes_is_part_of_the_evidence(
        self, backups: DatabaseBackups
    ) -> None:
        backups.set_policy("postgresql", "shop", verify_restore=True)

        result = backups.run_policy("postgresql", "shop")

        assert result["restore_test"] == "ok"
        assert result["dump"]["restore_test_status"] == "ok"

    def test_a_failing_destination_does_not_stop_the_others_and_fails_the_run(
        self, backups: DatabaseBackups, runner: RcloneRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_destination(runner, "nas")
        add_destination(runner, "cloud")
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}, {"name": "cloud"}])
        runner.refusing["nas"] = "Failed to copyto: AccessDenied: Access Denied (403)"
        sent: list[Any] = []
        monkeypatch.setattr("noust.managers.database.backups.deliver", sent.append)

        with pytest.raises(BackupError) as failure:
            backups.run_policy("postgresql", "shop", notify=True)

        assert "could not be sent to: nas" in str(failure.value)
        assert "The local dump was kept" in str(failure.value)
        cloud = list(
            (runner.root / "cloud" / "wasm-backups" / "databases" / "postgresql" / "shop").iterdir()
        )
        assert len(cloud) == 2  # the dump and its sidecar
        assert len(backups.list_dumps("postgresql", "shop")) == 1
        assert backups.get_policy("postgresql", "shop").to_dict()["last_status"] == "failed"
        assert len(sent) == 1  # one warning, for nas

    def test_with_no_policy_row_the_dump_is_still_taken(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        result = backups.run_policy("postgresql", "shop")

        assert result["policy_missing"] is True
        assert result["dump"]["verify_status"] == "ok"
        assert result["local_deleted"] == []

    def test_an_engine_that_is_down_is_a_failed_run_the_operator_hears_about(
        self,
        backups: DatabaseBackups,
        engine: type[BaseDatabaseManager],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backups.set_policy("postgresql", "shop")
        engine.state["running"] = False  # type: ignore[attr-defined]
        sent: list[Any] = []
        monkeypatch.setattr("noust.managers.database.backups.deliver", sent.append)

        with pytest.raises(Exception, match="is not running"):
            backups.run_policy("postgresql", "shop", notify=True)

        assert len(sent) == 1
        policy = backups.get_policy("postgresql", "shop").to_dict()
        assert policy["last_status"] == "failed"
        assert "is not running" in policy["last_error"]

    def test_a_console_job_does_not_announce_what_the_job_manager_will(
        self, backups: DatabaseBackups, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner
    ) -> None:
        sent: list[Any] = []
        monkeypatch.setattr("noust.managers.database.backups.deliver", sent.append)
        runner.script(("pg_restore", "--list"), stderr="bad", exit_code=1)

        with pytest.raises(DatabaseBackupError):
            backups.run_policy("postgresql", "shop")

        assert sent == []

    def test_a_success_announces_the_heartbeat_when_asked_to(
        self, backups: DatabaseBackups, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[Any] = []
        monkeypatch.setattr("noust.managers.database.backups.deliver", sent.append)
        backups.set_policy("postgresql", "shop")

        backups.run_policy("postgresql", "shop", notify=True)

        (build,) = sent
        notification = build(NotificationContext("en", "web-1", ""))
        assert notification.kind == "backup_success"
        assert notification.subject == "postgresql/shop"

    def test_a_failure_reaches_the_operator_with_the_database_as_its_subject(
        self, backups: DatabaseBackups, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner
    ) -> None:
        sent: list[Any] = []
        monkeypatch.setattr("noust.managers.database.backups.deliver", sent.append)
        runner.script(("pg_restore", "--list"), stderr="pg_restore: error: bad header", exit_code=1)

        with pytest.raises(DatabaseBackupError):
            backups.run_policy("postgresql", "shop", notify=True)

        (build,) = sent
        notification = build(NotificationContext("es", "web-1", "https://console.example.com"))
        assert notification.kind == "backup_failed"
        assert notification.subject == "postgresql/shop"
        assert notification.domain is None
        assert notification.console_link is not None
        assert notification.console_link.url.endswith("/databases/postgresql/shop")
        assert notification.excerpt is not None
        assert "bad header" in "\n".join(notification.excerpt.lines)


# ================================================================ restores


class TestRestore:
    """A restore never destroys without a copy, and the copy is kept as what it is."""

    def test_the_safety_copy_is_recorded_as_such_and_never_deleted_by_retention(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        taken = backups.dump("postgresql", "shop", origin="scheduled")

        outcome = backups.restore("postgresql", "shop", taken.name, drop_existing=True)

        assert outcome.safety_copy is not None
        by_name = {view.name: view.to_dict() for view in backups.list_dumps("postgresql")}
        assert by_name[outcome.safety_copy.name]["kind"] == "safety"
        assert len(by_name[outcome.safety_copy.name]["sha256"]) == 64
        backups.set_policy("postgresql", "shop", retention_count=1, retention_days=None)
        age(outcome.safety_copy, 900)
        backups.run_policy("postgresql", "shop")
        assert outcome.safety_copy.exists()

    def test_only_the_newest_safety_copies_are_kept_per_database(
        self, backups: DatabaseBackups, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(DatabaseBackups, "safety_copies_kept", lambda self: 2)
        taken = backups.dump("postgresql", "shop", origin="scheduled")
        copies = []
        for days in (40, 30, 20, 10):
            outcome = backups.restore("postgresql", "shop", taken.name, drop_existing=True)
            assert outcome.safety_copy is not None
            age(outcome.safety_copy, days)
            copies.append(outcome.safety_copy)

        # Pruned after each restore: the two newest safety copies are left,
        # the dump restored from is not a safety copy and stays.
        assert [copy.exists() for copy in copies] == [False, False, True, True]
        assert (backups.list_dumps("postgresql")[0].info.path.parent / taken.name).exists()

    def test_the_newest_safety_copy_is_never_deleted_whatever_the_setting(
        self, backups: DatabaseBackups, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(DatabaseBackups, "safety_copies_kept", lambda self: 0)
        taken = backups.dump("postgresql", "shop", origin="scheduled")

        first = backups.restore("postgresql", "shop", taken.name, drop_existing=True)
        assert first.safety_copy is not None
        age(first.safety_copy, 5)
        second = backups.restore("postgresql", "shop", taken.name, drop_existing=True)

        assert second.safety_copy is not None and second.safety_copy.exists()
        assert not first.safety_copy.exists()

    def test_as_a_new_database_touches_nothing_that_exists(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        taken = backups.dump("postgresql", "shop")
        before = dict(engine.state["dbs"]["shop"])  # type: ignore[attr-defined]

        outcome = backups.restore("postgresql", "shop", taken.name, new_name="shop_copy")

        assert outcome.database == "shop_copy"
        assert engine.state["dbs"]["shop"] == before  # type: ignore[attr-defined]
        assert ("load", "shop_copy", taken.name) in engine.state["calls"]  # type: ignore[attr-defined]
        assert backups.service.store.get_database("shop_copy", "postgresql") is not None

    def test_a_name_that_exists_is_refused_and_nothing_is_loaded(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        taken = backups.dump("postgresql", "shop")

        with pytest.raises(Exception, match="already exists"):
            backups.restore("postgresql", "shop", taken.name, new_name="shop")

        assert not [c for c in engine.state["calls"] if c[0] == "load"]  # type: ignore[attr-defined]

    def test_a_failed_restore_puts_the_database_back_and_says_both_things(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        taken = backups.dump("postgresql", "shop")
        engine.state["fail_load"] = "pg_restore: error: unexpected EOF"  # type: ignore[attr-defined]

        with pytest.raises(DatabaseBackupError) as failure:
            backups.restore("postgresql", "shop", taken.name, drop_existing=True)

        assert "unexpected EOF" in str(failure.value)
        assert "safety copy" in str(failure.value).lower()

    def test_redis_cannot_restore_as_a_new_database(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        directory = tmp_path / "redis-dumps"
        directory.mkdir()
        cls = make_engine(
            directory, engine="redis", suffix=".rdb", content=b"REDIS0011", databases=("0",)
        )
        backups = DatabaseBackups(
            make_service(tmp_path, [cls]),
            scheduler=FakeScheduler(),  # type: ignore[arg-type]
            destinations=DestinationFileManager(runner=runner),
        )
        taken = backups.dump("redis", "0")

        with pytest.raises(ValidationError) as refused:
            backups.restore("redis", "0", taken.name, new_name="copy")
        with pytest.raises(ValidationError):
            backups.suggest_name("redis", "0")

        assert refused.value.field == "new_name"

    def test_the_suggested_name_says_what_it_is_and_is_free(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        name = backups.suggest_name("postgresql", "shop", "postgresql-shop-20260928_020000.dump")
        assert name == "shop_restored_20260928"

        engine.state["dbs"][name] = {"owner": None, "tables": 0}  # type: ignore[attr-defined]
        assert (
            backups.suggest_name("postgresql", "shop", "postgresql-shop-20260928_020000.dump")
            == "shop_restored_20260928_2"
        )

    def test_the_suggested_name_fits_the_engines_limit(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        long_name = "a" * 63
        engine.state["dbs"][long_name] = {"owner": None, "tables": 0}  # type: ignore[attr-defined]

        name = backups.suggest_name("postgresql", long_name, "postgresql-x-20260928_020000.dump")

        assert len(name) <= 63
        assert name.endswith("_restored_20260928")


# ============================================================== listing


class TestListing:
    """What the console's table is built from."""

    def test_a_dump_carries_everything_the_table_shows(self, backups: DatabaseBackups) -> None:
        taken = backups.dump("postgresql", "shop", origin="scheduled")

        (view,) = backups.list_dumps("postgresql", "shop")
        data = view.to_dict()

        assert data["name"] == taken.name
        assert data["engine"] == "postgresql"
        assert data["database"] == "shop"
        assert data["format"] == "custom"
        assert data["kind"] == "scheduled"
        assert data["verify_status"] == "ok"
        assert data["size"] == len(PG_ARCHIVE)
        assert data["age_seconds"] >= 0
        assert data["destinations"] == []

    def test_dumps_of_other_databases_are_not_in_the_listing(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["dbs"]["blog"] = {"owner": "app", "tables": 1}  # type: ignore[attr-defined]
        backups.dump("postgresql", "shop")
        backups.dump("postgresql", "blog")

        assert [v.info.database for v in backups.list_dumps("postgresql", "blog")] == ["blog"]
        assert len(backups.list_dumps("postgresql")) == 2

    def test_deleting_a_dump_removes_the_file_and_the_record(
        self, backups: DatabaseBackups, audited: list[dict[str, Any]]
    ) -> None:
        taken = backups.dump("postgresql", "shop")
        audited.clear()

        backups.delete_dump("postgresql", taken.name)

        assert backups.list_dumps("postgresql") == []
        assert backups.records.dump("postgresql", taken.name) is None
        assert [e["event"] for e in audited] == ["db.backup.delete"]
        assert audited[0]["details"]["reason"] == "operator"

    def test_the_remote_folder_of_a_database_and_of_a_redis_instance(self) -> None:
        assert remote_folder("postgresql", "shop") == "databases/postgresql/shop"
        assert remote_folder("redis", "3") == "databases/redis/instance"


# ---------------------------------------------------------------------------
# A load beside a database is isolated from every other database
# ---------------------------------------------------------------------------


def _isolation_seen(
    engine: type[BaseDatabaseManager], monkeypatch: pytest.MonkeyPatch
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(engine, "_check_backup", lambda self, path, **kwargs: seen.append(kwargs))
    return seen


def test_a_restore_test_loads_isolated(
    backups: DatabaseBackups, engine: type[BaseDatabaseManager], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dump that switches database must not reach production from a test."""
    seen = _isolation_seen(engine, monkeypatch)
    taken = backups.dump("postgresql", "shop")

    backups.verify("postgresql", taken.name, restore=True)

    assert seen and all(kwargs.get("isolated") is True for kwargs in seen)


def test_a_restore_as_a_new_database_loads_isolated(
    backups: DatabaseBackups, engine: type[BaseDatabaseManager], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _isolation_seen(engine, monkeypatch)
    taken = backups.dump("postgresql", "shop")

    backups.restore("postgresql", "shop", taken.name, new_name="shop_copy")

    assert seen == [{"isolated": True}]


def test_a_restore_over_the_database_itself_is_not_isolated(
    backups: DatabaseBackups, engine: type[BaseDatabaseManager], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _isolation_seen(engine, monkeypatch)
    taken = backups.dump("postgresql", "shop")

    backups.restore("postgresql", "shop", taken.name)

    assert seen == [{}]


# ---------------------------------------------------------------------------
# A restore the console did not live to finish
# ---------------------------------------------------------------------------


def _safety_records(backups: DatabaseBackups) -> list[str]:
    return [
        record.file_name
        for record in backups.records.dumps("postgresql", "shop")
        if record.origin == "safety"
    ]


def test_the_safety_copy_is_recorded_before_the_database_is_dropped(
    backups: DatabaseBackups, engine: type[BaseDatabaseManager], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recorded after the load, a restart mid-restore left it unknown to Noust."""
    taken = backups.dump("postgresql", "shop")
    seen_at_drop: list[list[str]] = []
    original = engine.drop_database

    def drop(self: Any, name: str, force: bool = False) -> None:
        seen_at_drop.append(_safety_records(backups))
        original(self, name, force=force)

    monkeypatch.setattr(engine, "drop_database", drop)

    outcome = backups.restore("postgresql", "shop", taken.name, drop_existing=True)

    assert outcome.safety_copy is not None
    assert seen_at_drop == [[outcome.safety_copy.name]]


def test_an_interrupted_restore_is_reported_with_the_command_to_put_it_back(
    backups: DatabaseBackups, engine: type[BaseDatabaseManager], monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.managers.database.backups import reconcile_interrupted_restores

    taken = backups.dump("postgresql", "shop")

    def killed(self: Any, database: str, backup_path: Path, **kwargs: Any) -> None:
        # What the process sees of a restart: it stops, and nothing after runs.
        raise SystemExit(143)

    monkeypatch.setattr(engine, "_load_backup", killed)
    with pytest.raises(SystemExit):
        backups.restore("postgresql", "shop", taken.name, drop_existing=True)
    (safety,) = _safety_records(backups)

    found = reconcile_interrupted_restores(backups.service.store)

    assert len(found) == 1
    assert found[0].target == "shop"
    assert (
        f"noust db restore shop {backups.service.dump_path('postgresql', safety)}"
        in found[0].message
    )
    assert "--engine postgresql --drop" in found[0].message
    # Reported once: the journal entry is gone.
    assert reconcile_interrupted_restores(backups.service.store) == []


def test_a_restore_that_finishes_leaves_nothing_to_reconcile(
    backups: DatabaseBackups,
) -> None:
    from noust.managers.database.backups import reconcile_interrupted_restores

    taken = backups.dump("postgresql", "shop")

    backups.restore("postgresql", "shop", taken.name, drop_existing=True)

    assert reconcile_interrupted_restores(backups.service.store) == []


class TestAnyEngineErrorAfterTheDropPutsTheDatabaseBack:
    """
    Not only the loader's failure: the database is gone from the drop on.

    Only a failed load used to trigger the put-back. A create that failed
    after the drop (an owner that no longer exists, a credential that could
    not be read) left no database and an error that did not name the copy.
    """

    def manager(self, dumps_dir: Path, *, fail: str) -> BaseDatabaseManager:
        from noust.core.exceptions import DatabaseError

        cls = make_engine(dumps_dir)
        real_create = cls.create_database
        real_drop = cls.drop_database
        creates: list[str] = []

        def create(self, name, owner=None, encoding=None, **kwargs):  # type: ignore[no-untyped-def]
            creates.append(name)
            if fail == "create" and len(creates) == 1:
                raise DatabaseError(
                    f"Failed to create database '{name}'", details='role "gone" does not exist'
                )
            return real_create(self, name, owner=owner, encoding=encoding, **kwargs)

        def drop(self, name, force=False):  # type: ignore[no-untyped-def]
            if fail == "drop":
                raise DatabaseError(f"Failed to drop database '{name}'", details="in use")
            return real_drop(self, name, force=force)

        cls.create_database = create  # type: ignore[method-assign]
        cls.drop_database = drop  # type: ignore[method-assign]
        return cls()

    def dump(self, tmp_path: Path) -> Path:
        path = tmp_path / "incoming.dump"
        path.write_bytes(PG_ARCHIVE)
        return path

    def test_a_create_that_fails_after_the_drop_is_put_back(
        self, dumps_dir: Path, tmp_path: Path
    ) -> None:
        manager = self.manager(dumps_dir, fail="create")

        with pytest.raises(DatabaseBackupError) as failure:
            manager.restore("shop", self.dump(tmp_path), drop_existing=True)

        state = type(manager).state  # type: ignore[attr-defined]
        assert "its previous contents were put back" in failure.value.message
        assert 'role "gone" does not exist' in (failure.value.details or "")
        loads = [call for call in state["calls"] if call[0] == "load"]
        (put_back,) = loads
        assert put_back[2] in (failure.value.details or "")
        assert "shop" in state["dbs"]

    def test_a_drop_that_fails_names_the_safety_copy_and_changes_nothing(
        self, dumps_dir: Path, tmp_path: Path
    ) -> None:
        manager = self.manager(dumps_dir, fail="drop")

        with pytest.raises(DatabaseBackupError) as failure:
            manager.restore("shop", self.dump(tmp_path), drop_existing=True)

        state = type(manager).state  # type: ignore[attr-defined]
        assert "before anything was changed" in failure.value.message
        assert "The safety copy" in (failure.value.details or "")
        assert not [call for call in state["calls"] if call[0] == "load"]
