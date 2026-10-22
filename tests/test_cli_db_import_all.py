# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db import-all`` (4.0, checklist H-18).

The command line of the import of every database of a PostgreSQL and, when asked, its accounts: a
plan that changes nothing, the question before the accounts, the result per database and a status
that tells a script one failed.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.audit_policy import is_read_only
from noust.cli.commands import db as db_cli
from noust.core.exceptions import DatabaseBackupError
from noust.core.logger import Logger
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.database.base import BaseDatabaseManager
from tests.database_backup_support import PG_LISTING, RcloneRunner, make_engine

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


@pytest.fixture(autouse=True)
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from noust.core.config import Config

    backups = tmp_path / "backups"
    monkeypatch.setattr(Config, "backup_directory", property(lambda self: backups), raising=False)
    return backups


@pytest.fixture
def engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner
) -> type[BaseDatabaseManager]:
    (tmp_path / "dumps").mkdir()
    base = make_engine(tmp_path / "dumps", suffix=".sql", content=SQL, databases=())

    class Postgres(base):  # type: ignore[valid-type, misc]
        def role_names(self) -> set[str]:
            return set(type(self).state["roles"])

        def create_accounts(self, script: str, *, secrets: Sequence[str] = ()) -> None:
            type(self).state["scripts"].append(script)
            for line in script.splitlines():
                if line.startswith("CREATE ROLE"):
                    type(self).state["roles"].add(line.split('"')[1])

        def _load_backup(self, database: str, backup_path: Path, **kwargs: Any) -> None:
            if type(self).state["fail"].get(database):
                raise DatabaseBackupError(
                    "Failed to restore", details=type(self).state["fail"][database]
                )
            super()._load_backup(database, backup_path, **kwargs)

    Postgres.state["roles"] = {"postgres"}
    Postgres.state["scripts"] = []
    Postgres.state["fail"] = {}
    monkeypatch.setattr(db_cli, "get_db_manager", lambda engine, verbose=False: Postgres())
    return Postgres


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    buffer = io.StringIO()

    def factory(**kwargs: Any) -> Logger:
        return Logger(stream=buffer, **kwargs)

    monkeypatch.setattr(cli_app, "Logger", factory)
    monkeypatch.setattr(db_cli, "Logger", factory)
    return buffer


def invoke(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(cli_app.cli, ["db", "import-all", *args], input=input)


class TestPlan:
    def test_the_plan_changes_nothing_and_is_json_when_asked(
        self, engine: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(str(FIXTURE), "-e", "postgresql", "--users", "--plan", "--json")

        assert result.exit_code == 0, result.output
        plan = json.loads(result.output)
        assert {d["name"]: d["action"] for d in plan["databases"]}["shop"] == "create"
        assert any(
            role["name"] == "shopapp" and role["action"] == "create" for role in plan["roles"]
        )
        assert engine.state["dbs"] == {} and engine.state["scripts"] == []

    def test_planning_is_read_only_for_the_audit_policy(self) -> None:
        command = db_cli.cli.commands["import-all"]

        assert is_read_only(command, "db import-all", {"plan_only": True}) is True
        assert is_read_only(command, "db import-all", {"plan_only": False}) is False

    def test_a_dump_of_one_database_is_refused_with_where_to_go(
        self, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        single = tmp_path / "one.sql"
        single.write_bytes(SQL)

        result = invoke(str(single), "-e", "postgresql", "--plan")

        assert result.exit_code != 0
        assert "noust db import" in result.output


class TestRunning:
    def test_the_databases_are_imported_and_no_account_is_made_without_users(
        self, engine: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(str(FIXTURE), "-e", "postgresql", "--force")

        assert result.exit_code == 0, result.output
        assert {"shop", "blog"} <= set(engine.state["dbs"])
        assert engine.state["scripts"] == []

    def test_with_users_the_accounts_are_asked_about_and_a_no_changes_nothing(
        self, engine: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(str(FIXTURE), "-e", "postgresql", "--users", input="y\nn\n")

        assert result.exit_code == 0
        assert "account(s) will be created" in result.output
        assert engine.state["scripts"] == [] and engine.state["dbs"] == {}

    def test_with_force_the_accounts_are_made(self, engine: type[BaseDatabaseManager]) -> None:
        result = invoke(str(FIXTURE), "-e", "postgresql", "--users", "--force")

        assert result.exit_code == 0, result.output
        assert engine.state["scripts"] and "shopapp" in engine.state["roles"]
        assert engine.state["dbs"]["shop"]["owner"] == "shopapp"

    def test_only_the_databases_named_are_loaded(self, engine: type[BaseDatabaseManager]) -> None:
        result = invoke(str(FIXTURE), "-e", "postgresql", "--force", "-d", "shop")

        assert result.exit_code == 0, result.output
        assert set(engine.state["dbs"]) == {"shop"}

    def test_a_database_that_fails_is_told_and_the_status_says_so(
        self, engine: type[BaseDatabaseManager], logged: io.StringIO
    ) -> None:
        engine.state["fail"]["blog"] = "ERROR:  permission denied for table posts"

        result = invoke(str(FIXTURE), "-e", "postgresql", "--force")

        assert result.exit_code == 1
        assert "shop" in engine.state["dbs"] and "blog" not in engine.state["dbs"]
        text = logged.getvalue() + result.output
        assert "blog" in text and "permission denied for table posts" in text

    def test_json_is_the_outcome(self, engine: type[BaseDatabaseManager]) -> None:
        result = invoke(str(FIXTURE), "-e", "postgresql", "--force", "--json")

        outcome = json.loads(result.output[result.output.index("{") :])
        assert {d["name"]: d["status"] for d in outcome["databases"]}["shop"] == "created"
        assert outcome["failed"] == 0

    def test_declining_the_first_question_does_nothing(
        self, engine: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(str(FIXTURE), "-e", "postgresql", input="n\n")

        assert result.exit_code == 0
        assert engine.state["dbs"] == {}


MYSQL_DUMP = Path(__file__).parent / "fixtures" / "mysql_dump" / "all-mariadb11.sql"
MYSQL_GRANTS = Path(__file__).parent / "fixtures" / "mysql_dump" / "grants-mariadb11.sql"


@pytest.fixture
def maria(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner
) -> type[BaseDatabaseManager]:
    (tmp_path / "mdumps").mkdir()
    base = make_engine(
        tmp_path / "mdumps", engine="mysql", suffix=".sql", content=SQL, databases=()
    )

    class Maria(base):  # type: ignore[valid-type, misc]
        is_mariadb = True

        def role_names(self) -> set[str]:
            return {"root@localhost"}

        def create_accounts(self, script: str, *, secrets: Sequence[str] = ()) -> None:
            type(self).state["scripts"].append(script)

    Maria.INTERNAL_USERS = frozenset({"root"})
    Maria.state["scripts"] = []
    monkeypatch.setattr(db_cli, "get_db_manager", lambda engine, verbose=False: Maria())
    return Maria


class TestMariaDb:
    def test_the_plan_names_the_accounts_of_the_file(
        self, maria: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(
            str(MYSQL_DUMP),
            "-e",
            "mysql",
            "--users",
            "--accounts-file",
            str(MYSQL_GRANTS),
            "--plan",
            "--json",
        )

        assert result.exit_code == 0, result.output
        plan = json.loads(result.output)
        assert {r["name"] for r in plan["roles"]} == {
            "shopapp@%",
            "reader@10.0.%",
            "admin2@localhost",
        }
        assert maria.state["dbs"] == {} and maria.state["scripts"] == []

    def test_the_accounts_and_then_their_privileges_are_made(
        self, maria: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(
            str(MYSQL_DUMP),
            "-e",
            "mysql",
            "--users",
            "--accounts-file",
            str(MYSQL_GRANTS),
            "--force",
        )

        assert result.exit_code == 0, result.output
        accounts, grants = maria.state["scripts"]
        assert "CREATE USER 'shopapp'@'%'" in accounts and "GRANT" not in accounts
        assert "GRANT SELECT ON `shop`.`orders` TO 'reader'@'10.0.%';" in grants
        assert {"blog", "shop"} <= set(maria.state["dbs"])

    def test_without_the_file_nothing_is_said_to_be_made(
        self, maria: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(str(MYSQL_DUMP), "-e", "mysql", "--users", "--force")

        assert result.exit_code == 0, result.output
        assert maria.state["scripts"] == []
