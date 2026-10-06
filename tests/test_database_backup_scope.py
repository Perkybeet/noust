# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a database backup policy covers (3.4, checklist H-02): all of an engine, minus what is left out.

Pinned here:

- a policy for every database follows the engine as databases come and go,
  dumps each one on its own, skips what its exclusions name and what has a
  policy of its own, and one database failing does not stop the next;
- the dump commands of PostgreSQL, MySQL/MariaDB and MongoDB leave out the
  schemas, tables and collections a policy names, each as one argument, with
  no shell;
- a name that could be read as an option, or as a path, is refused where it
  would be spelled into a command.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import BackupError, ValidationError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.database.backup_records import BackupPolicy, BackupRecords
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.scope import ALL_DATABASES, Exclusions, timer_label
from noust.managers.database.service import DatabaseService
from tests.database_backup_support import PG_LISTING, RcloneRunner, make_engine, make_service
from tests.test_database_backups import FakeScheduler
from tests.test_database_managers import StubConfig, existing_database


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def engine(tmp_path: Path) -> type[BaseDatabaseManager]:
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    return make_engine(dumps, databases=("shop", "blog", "scratch"))


@pytest.fixture
def service(
    tmp_path: Path, engine: type[BaseDatabaseManager], runner: RcloneRunner
) -> DatabaseService:
    return make_service(tmp_path, [engine])


@pytest.fixture
def backups(service: DatabaseService, runner: RcloneRunner) -> DatabaseBackups:
    return DatabaseBackups(
        service,
        scheduler=FakeScheduler(),  # type: ignore[arg-type]
        destinations=DestinationFileManager(runner=runner),
    )


@pytest.fixture
def postgres(tmp_path: Path) -> PostgresManager:
    manager = PostgresManager()
    manager.BACKUP_DIR = tmp_path / "engine-backups"
    manager.config = StubConfig({})  # type: ignore[assignment]
    return manager


@pytest.fixture
def mysql(tmp_path: Path) -> MySQLManager:
    manager = MySQLManager()
    manager.BACKUP_DIR = tmp_path / "engine-backups"
    manager.config = StubConfig({})  # type: ignore[assignment]
    return manager


@pytest.fixture
def mongodb(tmp_path: Path) -> MongoDBManager:
    manager = MongoDBManager()
    manager.BACKUP_DIR = tmp_path / "engine-backups"
    return manager


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


# ================================================================ exclusions


class TestExclusions:
    def test_names_are_checked_and_kept_once_in_order(self) -> None:
        left_out = Exclusions.of(
            schemas=["audit", "tmp", "audit"], tables=["public.sessions", "cache_*"]
        )

        assert left_out.schemas == ("audit", "tmp")
        assert left_out.tables == ("public.sessions", "cache_*")

    @pytest.mark.parametrize("name", ["--help", "-x", "a b", "a;b", "../x", "a'b", ""])
    def test_a_name_that_could_be_an_option_or_a_path_is_refused(self, name: str) -> None:
        with pytest.raises(ValidationError):
            Exclusions.of(tables=[name])

    def test_a_string_is_not_a_list(self) -> None:
        with pytest.raises(ValidationError, match="list"):
            Exclusions.of(tables="sessions")  # type: ignore[arg-type]

    def test_there_is_a_limit_to_how_many(self) -> None:
        with pytest.raises(ValidationError, match="at most"):
            Exclusions.of(tables=[f"t{i}" for i in range(101)])

    def test_the_json_round_trip_drops_what_no_longer_validates(self) -> None:
        stored = {"tables": ["good", "--bad"], "schemas": "not a list"}

        assert Exclusions.from_json(stored).tables == ("good",)
        assert Exclusions.from_json(stored).schemas == ()
        assert Exclusions.from_json(None).to_json() == {}

    def test_empty_exclusions_are_falsy_and_have_no_per_dump_part(self) -> None:
        assert not Exclusions()
        assert Exclusions(databases=("x",)).per_dump() is None
        assert Exclusions(tables=("t",), databases=("x",)).per_dump() == Exclusions(tables=("t",))

    def test_databases_only_make_sense_for_the_policy_that_covers_all(self) -> None:
        with pytest.raises(ValidationError, match="covers all"):
            Exclusions(databases=("x",)).check_for("postgresql", scope="database")
        Exclusions(databases=("x",)).check_for("postgresql", scope="all")

    def test_schemas_are_postgresql_only_and_redis_leaves_nothing_out(self) -> None:
        with pytest.raises(ValidationError, match="PostgreSQL"):
            Exclusions(schemas=("s",)).check_for("mysql", scope="database")
        with pytest.raises(ValidationError, match="Redis"):
            Exclusions(tables=("t",)).check_for("redis", scope="database")
        Exclusions(tables=("t",)).check_for("mongodb", scope="database")

    def test_the_unit_label_of_the_all_policy_is_not_a_database_name(self) -> None:
        assert timer_label(ALL_DATABASES) == "-all"
        assert timer_label("shop") == "shop"


# ============================================================ dump commands


class TestTheDumpCommandsLeaveThingsOut:
    def test_postgresql_excludes_schemas_and_tables_one_argument_each(
        self,
        postgres,
        runner: FakeRunner,
    ) -> None:
        runner.script(("runuser", "-u", "postgres", "--", "psql"), stdout="1")
        runner.script(["runuser", "-u", "postgres", "--", "pg_dump"], stdout="dump")

        postgres.backup(
            "shop",
            format="custom",
            exclude_schemas=["audit"],
            exclude_tables=["public.sessions", "cache_*"],
        )

        argv = runner.calls[-1]
        assert argv[argv.index("pg_dump") :] == (
            "pg_dump",
            "--no-password",
            "--format=custom",
            "--exclude-schema=audit",
            "--exclude-table=public.sessions",
            "--exclude-table=cache_*",
            "shop",
        )
        assert not any(" " in word for word in argv)

    def test_postgresql_without_exclusions_is_the_command_it_always_was(
        self,
        postgres,
        runner: FakeRunner,
    ) -> None:
        runner.script(("runuser", "-u", "postgres", "--", "psql"), stdout="1")
        runner.script(["runuser", "-u", "postgres", "--", "pg_dump"], stdout="dump")

        postgres.backup("shop", format="custom")

        assert runner.calls[-1] == (
            "runuser",
            "-u",
            "postgres",
            "--",
            "pg_dump",
            "--no-password",
            "--format=custom",
            "shop",
        )

    def test_mysql_ignores_the_tables_as_database_dot_table(
        self,
        mysql,
        runner: FakeRunner,
    ) -> None:
        existing_database(mysql)
        runner.script(["mysqldump"], stdout="dump")

        mysql.backup("shop", compress=False, exclude_tables=["sessions", "cache"])

        assert runner.calls[-1] == (
            "mysqldump",
            "--single-transaction",
            "--routines",
            "--triggers",
            "--events",
            "--set-gtid-purged=OFF",
            "--ignore-table=shop.sessions",
            "--ignore-table=shop.cache",
            "shop",
        )

    def test_mongodb_excludes_the_collections(
        self,
        mongodb,
        runner: FakeRunner,
        tmp_path: Path,
    ) -> None:
        existing_database(mongodb)
        archive = tmp_path / "mongo.tar.gz"
        archive.write_bytes(b"archive")

        mongodb.backup("shop", compress=True, output_path=archive, exclude_tables=["logs", "tmp"])

        dump_call = runner.calls[-2]
        assert dump_call[0] == "mongodump"
        assert "--excludeCollection=logs" in dump_call
        assert "--excludeCollection=tmp" in dump_call
        assert dump_call.index("--db") < dump_call.index("--excludeCollection=logs")


# ======================================================== the policy itself


class TestThePolicyForAllDatabases:
    def test_it_is_stored_under_the_all_name_with_its_scope_and_exclusions(
        self, backups: DatabaseBackups, audited: list[dict[str, Any]]
    ) -> None:
        view = backups.set_policy(
            "postgresql",
            ALL_DATABASES,
            exclusions=Exclusions.of(databases=["scratch"], tables=["sessions"]),
        )

        data = view.to_dict()
        assert data["database"] == ALL_DATABASES
        assert data["scope"] == "all"
        assert data["exclusions"] == {"databases": ["scratch"], "tables": ["sessions"]}
        scheduler: FakeScheduler = backups.scheduler  # type: ignore[assignment]
        assert ("postgresql", ALL_DATABASES) in scheduler.installed
        event = next(e for e in audited if e["event"] == "db.backup.policy")
        assert event["target"] == "db:postgresql/*"
        assert event["details"]["scope"] == "all"

    def test_a_policy_for_one_database_keeps_its_scope(self, backups: DatabaseBackups) -> None:
        data = backups.set_policy("postgresql", "shop").to_dict()

        assert data["scope"] == "database"
        assert data["exclusions"] == {}

    def test_it_cannot_exclude_what_the_engine_has_no_such_thing_of(
        self, backups: DatabaseBackups
    ) -> None:
        with pytest.raises(ValidationError, match="covers all"):
            backups.set_policy(
                "postgresql", "shop", exclusions=Exclusions.of(databases=["scratch"])
            )

    def test_a_policy_for_one_database_leaves_its_tables_out_of_its_dumps(
        self, backups: DatabaseBackups, runner: RcloneRunner, service: DatabaseService
    ) -> None:
        backups.set_policy(
            "postgresql", "shop", exclusions=Exclusions.of(schemas=["audit"], tables=["sessions"])
        )
        calls: list[dict[str, Any]] = []
        manager_class = type(service.manager("postgresql"))
        original = manager_class.backup

        def spy(
            self: Any, database: str, output_path: Any = None, compress: bool = True, **kw: Any
        ):
            calls.append(kw)
            return original(self, database, output_path, compress, **kw)

        manager_class.backup = spy  # type: ignore[method-assign]
        try:
            backups.run_policy("postgresql", "shop")
        finally:
            manager_class.backup = original  # type: ignore[method-assign]

        assert calls[0]["exclude_schemas"] == ["audit"]
        assert calls[0]["exclude_tables"] == ["sessions"]

    def test_an_all_policy_covers_what_nothing_else_does(self, backups: DatabaseBackups) -> None:
        assert {entry["database"] for entry in backups.unprotected("postgresql")} == {
            "shop",
            "blog",
            "scratch",
        }

        backups.set_policy(
            "postgresql", ALL_DATABASES, exclusions=Exclusions.of(databases=["scratch"])
        )

        assert backups.unprotected("postgresql") == [
            {"engine": "postgresql", "database": "scratch"}
        ]

    def test_a_disabled_all_policy_covers_nothing(self, backups: DatabaseBackups) -> None:
        backups.set_policy("postgresql", ALL_DATABASES, enabled=False)

        assert len(backups.unprotected("postgresql")) == 3

    def test_redis_has_no_all_policy(self, tmp_path: Path, runner: RcloneRunner) -> None:
        cache = make_engine(tmp_path / "cache", engine="redis", suffix=".rdb", databases=("0",))
        service = make_service(tmp_path / "svc", [cache])
        redis_backups = DatabaseBackups(
            service,
            scheduler=FakeScheduler(),  # type: ignore[arg-type]
            destinations=DestinationFileManager(runner=runner),
        )

        with pytest.raises(ValidationError, match="whole instance"):
            redis_backups.set_policy("redis", ALL_DATABASES)

    def test_a_row_cannot_pair_the_wrong_scope_with_its_name(
        self, service: DatabaseService
    ) -> None:
        records = BackupRecords(service.store)

        with pytest.raises(ValidationError, match="cannot be stored"):
            records.save_policy(
                BackupPolicy(engine="postgresql", db_name="shop", schedule="daily", scope="all")
            )
        with pytest.raises(ValidationError, match="cannot be stored"):
            records.save_policy(
                BackupPolicy(engine="postgresql", db_name=ALL_DATABASES, schedule="daily")
            )

    def test_removing_it_removes_its_timer(self, backups: DatabaseBackups) -> None:
        backups.set_policy("postgresql", ALL_DATABASES)

        assert backups.remove_policy("postgresql", ALL_DATABASES) is True

        scheduler: FakeScheduler = backups.scheduler  # type: ignore[assignment]
        assert ("postgresql", ALL_DATABASES) in scheduler.removed
        assert backups.get_policy("postgresql", ALL_DATABASES).policy is None


# ========================================================= running the policy


class TestRunningThePolicyForAllDatabases:
    def test_every_database_is_dumped_on_its_own_with_the_policy_retention(
        self, backups: DatabaseBackups
    ) -> None:
        backups.set_policy("postgresql", ALL_DATABASES, retention_count=2, retention_days=None)

        for _ in range(3):
            result = backups.run_policy("postgresql", ALL_DATABASES)

        assert set(result["databases"]) == {"shop", "blog", "scratch"}
        assert all(outcome["ok"] for outcome in result["databases"].values())
        for database in ("shop", "blog", "scratch"):
            # Retention is counted per database, never across them.
            assert len(backups.list_dumps("postgresql", database)) == 2
        assert backups.get_policy("postgresql", ALL_DATABASES).policy.last_status == "ok"

    def test_the_excluded_and_the_ones_with_a_policy_of_their_own_are_skipped(
        self, backups: DatabaseBackups
    ) -> None:
        backups.set_policy("postgresql", "shop", schedule="hourly")
        backups.set_policy(
            "postgresql", ALL_DATABASES, exclusions=Exclusions.of(databases=["scratch"])
        )

        result = backups.run_policy("postgresql", ALL_DATABASES)

        assert set(result["databases"]) == {"blog"}
        assert {(s["database"], s["reason"]) for s in result["skipped"]} == {
            ("scratch", "excluded"),
            ("shop", "has its own policy"),
        }

    def test_one_database_failing_does_not_stop_the_others_and_the_run_fails(
        self, backups: DatabaseBackups, service: DatabaseService
    ) -> None:
        backups.set_policy("postgresql", ALL_DATABASES)
        manager_class = type(service.manager("postgresql"))
        original = manager_class.backup

        def flaky(self: Any, database: str, *args: Any, **kwargs: Any):
            if database == "blog":
                from noust.core.exceptions import DatabaseBackupError

                raise DatabaseBackupError("Failed to back up 'blog'", details="disk full")
            return original(self, database, *args, **kwargs)

        manager_class.backup = flaky  # type: ignore[method-assign]
        try:
            with pytest.raises(BackupError) as failure:
                backups.run_policy("postgresql", ALL_DATABASES)
        finally:
            manager_class.backup = original  # type: ignore[method-assign]

        assert "blog" in str(failure.value)
        assert "disk full" in str(failure.value)
        assert len(backups.list_dumps("postgresql", "shop")) == 1
        assert len(backups.list_dumps("postgresql", "scratch")) == 1
        policy = backups.get_policy("postgresql", ALL_DATABASES).policy
        assert policy.last_status == "failed"
        assert "blog" in (policy.last_error or "")

    def test_a_database_created_later_is_dumped_by_the_next_run(
        self, backups: DatabaseBackups, engine: type[BaseDatabaseManager]
    ) -> None:
        backups.set_policy("postgresql", ALL_DATABASES)
        engine.state["dbs"]["later"] = {"owner": "app", "tables": 1}

        result = backups.run_policy("postgresql", ALL_DATABASES)

        assert "later" in result["databases"]

    def test_with_no_row_the_databases_are_still_dumped(self, backups: DatabaseBackups) -> None:
        # A store that moved while the timer kept firing: skipping every backup
        # because a row is missing is the worse outcome.
        result = backups.run_policy("postgresql", ALL_DATABASES)

        assert result["policy_missing"] is True
        assert set(result["databases"]) == {"shop", "blog", "scratch"}

    def test_a_single_database_run_is_what_it_always_was(self, backups: DatabaseBackups) -> None:
        backups.set_policy("postgresql", "shop")

        result = backups.run_policy("postgresql", "shop")

        assert result["database"] == "shop"
        assert result["dump"]["database"] == "shop"
        assert backups.get_policy("postgresql", "shop").policy.last_status == "ok"
