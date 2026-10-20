# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Importing a dump of all databases and its accounts (4.0, checklist H-18).

Over a real ``pg_dumpall`` of PostgreSQL 17 (accounts, a membership, three user databases) and an
engine that records what it is told. What is pinned is what an operator leaving another server
depends on:

- the plan says what would happen and changes nothing;
- accounts are made only when asked for and confirmed, from the data of the dump, without the
  powers the source gave them, and never over one that exists;
- every database goes through the one import (check, safety copy, way back), is loaded as its
  owner with the dump's own owners kept for the roles that exist, and one that fails does not stop
  the others and, if it was made for this, does not stay behind;
- everything a dump of one database is refused for, a cluster one is refused for too.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ConfirmationRequired, DatabaseBackupError, ValidationError
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.cluster_import import ClusterImporter
from noust.managers.database.imports import DatabaseImports
from noust.managers.database.uploads import UploadStore
from tests.database_backup_support import PG_LISTING, RcloneRunner, make_engine, make_service

FIXTURE = Path(__file__).parent / "fixtures" / "pg_dumpall" / "all17.sql"
SQL = b"-- PostgreSQL database dump\nSELECT 1;\n-- PostgreSQL database dump complete\n"


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
    """A PostgreSQL that has the server's own accounts and records every load."""
    (tmp_path / "dumps").mkdir()
    base = make_engine(tmp_path / "dumps", suffix=".sql", content=SQL, databases=())

    class Postgres(base):  # type: ignore[valid-type, misc]
        def role_names(self) -> set[str]:
            return set(type(self).state["roles"])

        def fix_owner(self, database: str, new_owner: str, *, apply: bool = False) -> Any:
            type(self).state["dbs"][database]["owner"] = new_owner
            type(self).state["handed"].append((database, new_owner))
            return type("Plan", (), {"current_owner": "x", "objects": []})()

        def create_accounts(self, script: str, *, secrets: Sequence[str] = ()) -> None:
            type(self).state["scripts"].append((script, tuple(secrets)))
            for line in script.splitlines():
                if line.startswith("CREATE ROLE"):
                    type(self).state["roles"].add(line.split('"')[1])

        def _load_backup(self, database: str, backup_path: Path, **kwargs: Any) -> None:
            state = type(self).state
            state["loaded"].append(
                {
                    "database": database,
                    "kwargs": kwargs,
                    "text": Path(backup_path).read_text(errors="replace"),
                }
            )
            failing = state["fail"].get(database)
            if failing:
                raise DatabaseBackupError("Failed to restore", details=failing)
            super()._load_backup(database, backup_path, **kwargs)

    Postgres.state["roles"] = {"postgres", "pg_monitor", "taken"}
    Postgres.state["scripts"] = []
    Postgres.state["loaded"] = []
    Postgres.state["fail"] = {}
    Postgres.state["handed"] = []
    return Postgres


@pytest.fixture
def importer(
    tmp_path: Path, engine: type[BaseDatabaseManager], runner: RcloneRunner
) -> ClusterImporter:
    service = make_service(tmp_path, [engine])
    return ClusterImporter(DatabaseImports(service, uploads=UploadStore(tmp_path / "uploads")))


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


class TestThePlan:
    def test_without_users_the_databases_are_planned_and_the_accounts_are_not(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        plan = importer.plan("postgresql", FIXTURE)

        by_name = {db.name: db for db in plan.databases}
        assert by_name["shop"].action == "create" and by_name["shop"].owner is None
        assert by_name["blog"].action == "create"
        assert (
            by_name["postgres"].action == "skip" and "server itself" in by_name["postgres"].reason
        )  # type: ignore[operator]
        assert by_name["template1"].action == "skip"
        assert plan.roles == []
        assert any("5 account(s) in the dump were not restored" in note for note in plan.ignored)
        assert engine.state["dbs"] == {}

    def test_with_users_each_account_says_what_happens_to_it(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["roles"].add("legacy")

        plan = importer.plan("postgresql", FIXTURE, restore_users=True)

        by_name = {role.name: role for role in plan.roles}
        assert by_name["shopapp"].action == "create"
        assert by_name["legacy"].action == "exists"
        assert by_name["postgres"].action == "skip"
        assert by_name["bossman"].action == "create" and by_name["bossman"].dropped == (
            "SUPERUSER",
        )
        assert plan.memberships == 1
        assert any("without it" in warning and "bossman" in warning for warning in plan.warnings)
        assert any("shopapp" in warning for warning in plan.warnings)

    def test_a_database_is_owned_by_its_account_only_when_the_account_is_there(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["roles"].add("legacy")

        plan = importer.plan("postgresql", FIXTURE, restore_users=True)

        owners = {db.name: db.owner for db in plan.databases}
        assert owners["shop"] == "shopapp" and owners["blog"] == "legacy"

    def test_a_database_that_exists_is_loaded_over_or_replaced(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["dbs"]["shop"] = {"owner": "app", "tables": 1}

        loaded = importer.plan("postgresql", FIXTURE)
        replaced = importer.plan("postgresql", FIXTURE, replace=True)

        assert {db.name: db.action for db in loaded.databases}["shop"] == "load"
        assert {db.name: db.action for db in replaced.databases}["shop"] == "replace"

    def test_only_the_databases_asked_for_are_planned_and_a_system_one_when_named(
        self, importer: ClusterImporter
    ) -> None:
        plan = importer.plan("postgresql", FIXTURE, only=["shop", "postgres", "nothing"])

        actions = {db.name: db.action for db in plan.databases}
        assert actions == {
            "template1": "skip",
            "blog": "skip",
            "postgres": "create",
            "shop": "create",
        }
        assert any("'nothing'" in note for note in plan.ignored)

    def test_a_dump_of_one_database_is_sent_to_the_other_command(
        self, importer: ClusterImporter, tmp_path: Path
    ) -> None:
        single = tmp_path / "one.sql"
        single.write_bytes(SQL)

        with pytest.raises(ValidationError) as raised:
            importer.plan("postgresql", single)

        assert "not a dump of all databases" in raised.value.message
        assert "noust db import" in (raised.value.details or "")

    def test_an_engine_without_such_a_dump_says_so(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        cache = make_engine(tmp_path / "m", engine="redis", suffix=".rdb", content=SQL)
        other = ClusterImporter(
            DatabaseImports(
                make_service(tmp_path / "svc", [cache]), uploads=UploadStore(tmp_path / "up")
            )
        )

        with pytest.raises(ValidationError, match="no dump of all databases"):
            other.plan("redis", FIXTURE)


class TestRunning:
    def test_without_users_every_user_database_is_imported_and_no_account_is_made(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        outcome = importer.run("postgresql", FIXTURE)

        assert {d.name: d.status for d in outcome.databases} == {
            "template1": "skipped",
            "blog": "created",
            "postgres": "skipped",
            "shop": "created",
        }
        assert outcome.accounts_created == [] and engine.state["scripts"] == []
        loads = {load["database"]: load for load in engine.state["loaded"]}
        assert set(loads) == {"blog", "shop"}
        assert loads["shop"]["kwargs"]["keep_owners"] is False
        assert "role_filter" not in loads["shop"]["kwargs"]

    def test_a_database_dump_is_the_part_after_its_connect(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        importer.run("postgresql", FIXTURE)

        shop = next(load for load in engine.state["loaded"] if load["database"] == "shop")
        assert shop["text"].startswith("--\n-- PostgreSQL database dump\n--\n")
        assert "CREATE TABLE public.orders" in shop["text"]
        assert "CREATE DATABASE" not in shop["text"] and "\\connect" not in shop["text"]

    def test_users_need_the_operator_to_accept_what_they_are(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        with pytest.raises(ConfirmationRequired) as raised:
            importer.run("postgresql", FIXTURE, restore_users=True)

        assert raised.value.field == "confirm_users"
        assert "account(s) will be created" in raised.value.warnings[0]
        assert engine.state["scripts"] == [] and engine.state["loaded"] == []

    def test_confirmed_the_accounts_are_made_from_data_without_the_power_they_had(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        outcome = importer.run("postgresql", FIXTURE, restore_users=True, confirm_users=True)

        assert outcome.accounts_created == ["bossman", "legacy", "reporting", "shopapp"]
        ((script, secrets),) = engine.state["scripts"]
        assert 'CREATE ROLE "bossman" WITH LOGIN INHERIT NOCREATEDB NOSUPERUSER' in script
        assert 'CREATE ROLE "postgres"' not in script
        assert 'GRANT "reporting" TO "shopapp";' in script
        assert "CONNECTION LIMIT 5" in script and "VALID UNTIL '2030-01-01 00:00:00+00'" in script
        # The hashes go through the call that keeps them out of the logs.
        assert len(secrets) == 3 and all(h.startswith("SCRAM-SHA-256$") for h in secrets)

    def test_the_databases_are_owned_by_their_accounts_and_keep_the_dumps_owners_for_roles_that_exist(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        importer.run("postgresql", FIXTURE, restore_users=True, confirm_users=True)

        assert engine.state["dbs"]["shop"]["owner"] == "shopapp"
        assert engine.state["dbs"]["blog"]["owner"] == "legacy"
        shop = next(load for load in engine.state["loaded"] if load["database"] == "shop")
        kwargs = shop["kwargs"]
        assert kwargs["keep_owners"] is True
        keep = kwargs["role_filter"]
        # Objects can go to the database's owner, whom the loader becomes; nobody else is named.
        assert keep.owners == frozenset()
        assert {"reporting", "taken"} <= keep.grantees
        assert "as_superuser" not in kwargs

    def test_a_database_that_is_replaced_is_recreated_with_the_account_that_owned_it_at_the_source(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["dbs"]["shop"] = {"owner": "postgres", "tables": 1}

        outcome = importer.run(
            "postgresql",
            FIXTURE,
            restore_users=True,
            confirm_users=True,
            replace=True,
            only=["shop"],
        )

        shop = next(d for d in outcome.databases if d.name == "shop")
        assert shop.status == "replaced"
        load = next(load for load in engine.state["loaded"] if load["database"] == "shop")
        assert load["kwargs"]["create_owner"] == "shopapp"
        assert engine.state["dbs"]["shop"]["owner"] == "shopapp"

    def test_a_database_loaded_over_is_handed_to_the_account_that_owned_it_at_the_source(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["dbs"]["shop"] = {"owner": "postgres", "tables": 1}

        outcome = importer.run(
            "postgresql", FIXTURE, restore_users=True, confirm_users=True, only=["shop"]
        )

        shop = next(d for d in outcome.databases if d.name == "shop")
        assert shop.status == "loaded"
        assert engine.state["handed"] == [("shop", "shopapp")]
        assert shop.notes and "now belongs to shopapp" in shop.notes[0]

    def test_a_database_without_the_accounts_keeps_its_owner(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["dbs"]["shop"] = {"owner": "postgres", "tables": 1}

        importer.run("postgresql", FIXTURE, replace=True, only=["shop"])

        assert engine.state["handed"] == []

    def test_an_account_that_exists_is_left_exactly_as_it_is(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["roles"].add("shopapp")

        outcome = importer.run("postgresql", FIXTURE, restore_users=True, confirm_users=True)

        assert "shopapp" not in outcome.accounts_created
        ((script, _),) = engine.state["scripts"]
        assert '"shopapp"' not in script.split("GRANT")[0]
        assert 'GRANT "reporting" TO "shopapp"' not in script

    def test_a_database_that_fails_does_not_stop_the_others_and_does_not_stay(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["fail"]["blog"] = "ERROR:  permission denied for table posts"

        outcome = importer.run("postgresql", FIXTURE)

        results = {d.name: d for d in outcome.databases}
        assert results["blog"].status == "failed"
        assert "permission denied for table posts" in (results["blog"].detail or "")
        assert results["shop"].status == "created"
        assert "blog" not in engine.state["dbs"]
        assert "shop" in engine.state["dbs"]
        assert [d.name for d in outcome.failed] == ["blog"]

    def test_a_database_that_exists_is_dumped_first_and_replaced_when_asked(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        engine.state["dbs"]["shop"] = {"owner": "app", "tables": 1}

        outcome = importer.run("postgresql", FIXTURE, replace=True, only=["shop"])

        (shop,) = [d for d in outcome.databases if d.status != "skipped"]
        assert shop.status == "replaced" and shop.safety_copy
        calls = [call[0] for call in engine.state["calls"]]
        assert calls[:3] == ["backup", "drop", "create"]

    def test_the_import_is_on_the_audit_trail(
        self,
        importer: ClusterImporter,
        audited: list[dict[str, Any]],
    ) -> None:
        importer.run("postgresql", FIXTURE, restore_users=True, confirm_users=True)

        accounts = next(e for e in audited if e["event"] == "db.import.accounts")
        assert accounts["details"]["accounts"] == "bossman,legacy,reporting,shopapp"
        done = next(e for e in audited if e["event"] == "db.import.cluster")
        assert done["details"]["created"] == 2 and done["details"]["failed"] == 0
        assert "SCRAM" not in repr(audited)

    def test_the_superuser_is_asked_for_by_name_and_confirmed(
        self, importer: ClusterImporter, engine: type[BaseDatabaseManager]
    ) -> None:
        with pytest.raises(ConfirmationRequired) as raised:
            importer.run("postgresql", FIXTURE, as_superuser=True)
        assert "COPY ... FROM PROGRAM" in raised.value.warnings[0]
        assert engine.state["loaded"] == []

        importer.run(
            "postgresql",
            FIXTURE,
            restore_users=True,
            confirm_users=True,
            as_superuser=True,
            confirm_superuser=True,
        )

        shop = next(load for load in engine.state["loaded"] if load["database"] == "shop")
        assert shop["kwargs"]["as_superuser"] is True
        # The superuser can hand objects to any account there is.
        assert "shopapp" in shop["kwargs"]["role_filter"].owners

    def test_progress_is_told(self, importer: ClusterImporter) -> None:
        steps: list[str] = []

        importer.run("postgresql", FIXTURE, on_step=steps.append)

        assert steps[0].startswith("Reading ")
        assert any(step == "Creating shop" for step in steps)


# ================================================================ MySQL and MariaDB

MYSQL_DUMP = Path(__file__).parent / "fixtures" / "mysql_dump" / "all-mariadb11.sql"
MYSQL_GRANTS = Path(__file__).parent / "fixtures" / "mysql_dump" / "grants-mariadb11.sql"


@pytest.fixture
def mysql(tmp_path: Path) -> type[BaseDatabaseManager]:
    """A MariaDB that has the server's own accounts and records every load."""
    (tmp_path / "mdumps").mkdir()
    base = make_engine(
        tmp_path / "mdumps", engine="mysql", suffix=".sql", content=SQL, databases=()
    )

    class Maria(base):  # type: ignore[valid-type, misc]
        is_mariadb = True

        def role_names(self) -> set[str]:
            return set(type(self).state["roles"])

        def create_accounts(self, script: str, *, secrets: Sequence[str] = ()) -> None:
            type(self).state["scripts"].append((script, tuple(secrets)))
            type(self).state["order"].append(("accounts" if "CREATE USER" in script else "grants",))
            for line in script.splitlines():
                if line.startswith("CREATE USER"):
                    user, host = line.split(" ")[2].split("@")
                    type(self).state["roles"].add(f"{user.strip(chr(39))}@{host.strip(chr(39))}")

        def create_database(
            self, name: str, owner: str | None = None, encoding: str | None = None, **kw: Any
        ):  # type: ignore[no-untyped-def]
            type(self).state["charsets"][name] = encoding
            return super().create_database(name, owner=owner, encoding=encoding, **kw)

        def _load_backup(self, database: str, backup_path: Path, **kwargs: Any) -> None:
            type(self).state["loaded"].append(
                {"database": database, "text": Path(backup_path).read_text(errors="replace")}
            )
            type(self).state["order"].append(("load",))
            super()._load_backup(database, backup_path, **kwargs)

    Maria.INTERNAL_USERS = frozenset({"root", "mariadb.sys"})
    Maria.state["roles"] = {"root@localhost", "mariadb.sys@localhost"}
    Maria.state["scripts"] = []
    Maria.state["loaded"] = []
    Maria.state["charsets"] = {}
    Maria.state["order"] = []
    return Maria


@pytest.fixture
def mariadb_importer(
    tmp_path: Path, mysql: type[BaseDatabaseManager], runner: RcloneRunner
) -> ClusterImporter:
    service = make_service(tmp_path / "msvc", [mysql])
    return ClusterImporter(DatabaseImports(service, uploads=UploadStore(tmp_path / "mupload")))


class TestMariaDb:
    def test_the_databases_are_planned_and_the_servers_own_schema_is_not(
        self, mariadb_importer: ClusterImporter
    ) -> None:
        plan = mariadb_importer.plan("mysql", MYSQL_DUMP)

        actions = {db.name: db.action for db in plan.databases}
        assert actions == {"blog": "create", "mysql": "skip", "shop": "create"}
        assert plan.format.scope == "cluster"
        assert any("mysql, sys" in note and "CREATE USER" in note for note in plan.ignored)

    def test_the_accounts_come_from_a_file_of_their_own(
        self, mariadb_importer: ClusterImporter
    ) -> None:
        plan = mariadb_importer.plan("mysql", MYSQL_DUMP, restore_users=True, accounts=MYSQL_GRANTS)

        by_name = {role.name: role for role in plan.roles}
        assert {n: r.action for n, r in by_name.items()} == {
            "shopapp@%": "create",
            "reader@10.0.%": "create",
            "admin2@localhost": "create",
        }
        assert "ALL PRIVILEGES ON *.*" in by_name["admin2@localhost"].dropped
        assert any("admin2@localhost" in w and "without it" in w for w in plan.warnings)

    def test_each_database_is_made_with_its_character_set_and_loaded_whole(
        self, mariadb_importer: ClusterImporter, mysql: type[BaseDatabaseManager]
    ) -> None:
        outcome = mariadb_importer.run("mysql", MYSQL_DUMP)

        assert {d.name: d.status for d in outcome.databases} == {
            "blog": "created",
            "mysql": "skipped",
            "shop": "created",
        }
        assert mysql.state["charsets"] == {"blog": "latin1", "shop": "utf8mb4"}
        shop = next(load for load in mysql.state["loaded"] if load["database"] == "shop")
        assert "CREATE TABLE `orders`" in shop["text"] and "VIEW `v_orders`" in shop["text"]

    def test_accounts_are_made_in_the_servers_own_words_with_their_hashes(
        self, mariadb_importer: ClusterImporter, mysql: type[BaseDatabaseManager]
    ) -> None:
        outcome = mariadb_importer.run(
            "mysql", MYSQL_DUMP, restore_users=True, confirm_users=True, accounts=MYSQL_GRANTS
        )

        assert outcome.accounts_created == ["admin2@localhost", "reader@10.0.%", "shopapp@%"]
        accounts, grants = mysql.state["scripts"]
        script, secrets = accounts
        assert (
            "CREATE USER 'shopapp'@'%' IDENTIFIED BY PASSWORD "
            "'*7648E52EC79D4BB879B7B913F0B8BBF8DC4AB6CC';" in script
        )
        assert "GRANT" not in script  # the privileges wait for the tables they name
        assert "GRANT SELECT, INSERT, UPDATE, DELETE ON `shop`.* TO 'shopapp'@'%';" in grants[0]
        assert "GRANT SELECT ON `shop`.`orders` TO 'reader'@'10.0.%';" in grants[0]
        assert "ALL PRIVILEGES" not in script + grants[0] and "GRANT OPTION" not in grants[0]
        assert len(secrets) == 3
        order = [kind for kind, *_ in mysql.state["order"]]
        assert order.index("accounts") < order.index("load") < order.index("grants")

    def test_an_account_that_exists_is_left_as_it_is(
        self, mariadb_importer: ClusterImporter, mysql: type[BaseDatabaseManager]
    ) -> None:
        mysql.state["roles"].add("shopapp@%")

        outcome = mariadb_importer.run(
            "mysql", MYSQL_DUMP, restore_users=True, confirm_users=True, accounts=MYSQL_GRANTS
        )

        assert "shopapp@%" not in outcome.accounts_created
        assert all("'shopapp'" not in script for script, _ in mysql.state["scripts"])

    def test_users_without_a_file_of_accounts_say_what_to_give(
        self, mariadb_importer: ClusterImporter
    ) -> None:
        plan = mariadb_importer.plan("mysql", MYSQL_DUMP, restore_users=True)

        assert plan.roles == []
        assert any("No accounts to restore" in note for note in plan.ignored)

    def test_accounts_still_need_the_operator_to_accept_them(
        self, mariadb_importer: ClusterImporter, mysql: type[BaseDatabaseManager]
    ) -> None:
        with pytest.raises(ConfirmationRequired):
            mariadb_importer.run("mysql", MYSQL_DUMP, restore_users=True, accounts=MYSQL_GRANTS)

        assert mysql.state["scripts"] == [] and mysql.state["loaded"] == []

    def test_a_single_database_import_sends_a_dump_of_several_to_the_other_command(
        self, mariadb_importer: ClusterImporter
    ) -> None:
        with pytest.raises(DatabaseBackupError) as raised:
            mariadb_importer.imports.run("mysql", "shop", MYSQL_DUMP)

        assert "not the dump of one database" in raised.value.message
        assert "import-all" in (raised.value.details or "")
