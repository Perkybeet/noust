# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PostgreSQL extensions of a database (3.4, C-10): the manager, ``noust db extension`` and the API.

The engine is an in-memory PostgreSQL that offers a list of extensions and loads or drops them
per database, and records every statement it was sent. What is defended:

- **the name is the server's, never the caller's text**: an extension the server does not offer
  is refused, saying where one comes from, before any statement is sent; and what reaches the
  statement is the name from the list, quoted;
- **it is only PostgreSQL's**, and never PostgreSQL's own databases;
- **everything that changes is audited**, and a team does not load an extension that is not
  trusted: that one runs the extension's script with the superuser's rights.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noust.web.api.databases.extensions as extensions_module
from noust.cli.app import cli as root_cli
from noust.cli.commands import db as db_cli
from noust.core import audit
from noust.core.exceptions import (
    DatabaseNotFoundError,
    DatabaseQueryError,
    ValidationError,
)
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.extensions import SEPARATOR, Extensions
from noust.managers.database.service import DatabaseService
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.test_database_service import FakeEngine

OFFERED = {
    # name: (default version, trusted, requires, comment)
    "pg_trgm": ("1.6", True, (), "text similarity measurement and index searching"),
    "vector": ("0.8.0", True, (), "vector data type and ivfflat and hnsw access methods"),
    "postgis": ("3.5.2", False, (), "PostGIS geometry and geography spatial types"),
    "postgis_topology": ("3.5.2", False, ("postgis",), "PostGIS topology spatial types"),
    "plpython3u": ("1.0", False, (), "PL/Python3U untrusted procedural language"),
}


class ExtensionEngine(FakeEngine):
    """A PostgreSQL that offers extensions and loads them per database."""

    def get_version(self) -> str | None:
        return "17.2"

    def _server_version_num(self) -> int:
        return 170002

    @staticmethod
    def _escape_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def run_sql(self, database: str, sql: str, *, read_only: bool, timeout_s: int | None = None):
        loaded: dict[str, str] = self.state["extensions"].setdefault(database, {})
        self.state["statements"].append((database, sql))
        if self.state.get("refuse"):
            raise DatabaseQueryError("PostgreSQL refused a statement", output=self.state["refuse"])
        if sql.startswith("SELECT e.name"):
            rows = []
            for name, (version, trusted, requires, comment) in OFFERED.items():
                columns = [
                    name,
                    version,
                    loaded.get(name, ""),
                    comment,
                    "true" if trusted else "false",
                    ",".join(requires),
                ]
                rows.append(SEPARATOR.join(columns))
            return "\n".join(rows) + "\n"
        created = re.fullmatch(r'CREATE EXTENSION IF NOT EXISTS "([^"]+)"( CASCADE)?;', sql)
        if created:
            loaded[created.group(1)] = OFFERED[created.group(1)][0]
            for needed in OFFERED[created.group(1)][2]:
                if created.group(2):
                    loaded[needed] = OFFERED[needed][0]
            return ""
        dropped = re.fullmatch(r'DROP EXTENSION "([^"]+)"( CASCADE)?;', sql)
        if dropped:
            loaded.pop(dropped.group(1), None)
            return ""
        raise AssertionError(f"unexpected statement: {sql!r}")


class OtherEngine(FakeEngine):
    ENGINE_NAME = "mysql"


@pytest.fixture
def state(tmp_path: Path) -> dict[str, Any]:
    return {
        "dbs": {"shop_db": {"owner": "shop_user", "rows": "live"}, "postgres": {"owner": None}},
        "users": {"shop_user": "x", "postgres": "x"},
        "profiles": {},
        "calls": [],
        "dumps": 0,
        "fail_load": False,
        "extensions": {},
        "statements": [],
    }


@pytest.fixture
def service(tmp_path: Path, state: dict[str, Any]) -> Iterator[DatabaseService]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    backups = tmp_path / "dumps"

    def resolve(name: str) -> BaseDatabaseManager | None:
        if name in ("postgresql", "pg"):
            return ExtensionEngine(state, backups)
        if name == "mysql":
            return OtherEngine(state, backups)
        return None

    yield DatabaseService(
        store=store,
        secrets=SecretStore(root=tmp_path / "secrets"),
        resolve=resolve,
        engines=lambda: ["postgresql", "mysql"],
    )
    store.close()
    NoustStore.reset_instance()


@pytest.fixture
def extensions(service: DatabaseService) -> Extensions:
    return Extensions(service)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def record(event: str, **kwargs: Any) -> None:
        events.append({"event": event, **kwargs})

    monkeypatch.setattr(audit, "record", record)
    return events


class TestWhatTheServerOffers:
    def test_it_is_the_servers_own_list_with_what_is_loaded(
        self, extensions: Extensions, state: dict[str, Any]
    ) -> None:
        state["extensions"]["shop_db"] = {"pg_trgm": "1.6"}

        found = extensions.available("postgresql", "shop_db")

        assert found.server_version == "17.2"
        by_name = {item.name: item for item in found.extensions}
        assert set(by_name) == set(OFFERED)
        assert by_name["pg_trgm"].installed_version == "1.6" and by_name["pg_trgm"].installed
        assert by_name["vector"].installed_version is None
        assert by_name["vector"].trusted and not by_name["postgis"].trusted
        assert by_name["postgis_topology"].requires == ("postgis",)
        assert "PostGIS" in by_name["postgis"].comment

    def test_where_more_come_from_depends_on_where_the_engine_runs(
        self, extensions: Extensions
    ) -> None:
        found = extensions.available("postgresql", "shop_db")

        assert "postgresql-17-<extension>" in found.source

    def test_only_postgresql_has_extensions(self, extensions: Extensions) -> None:
        with pytest.raises(ValidationError) as caught:
            extensions.available("mysql", "shop_db")

        assert "Extensions are PostgreSQL's" in caught.value.message

    def test_postgresqls_own_databases_take_none(self, extensions: Extensions) -> None:
        with pytest.raises(ValidationError) as caught:
            extensions.available("postgresql", "template1")

        assert caught.value.field == "database"

    def test_a_database_that_is_not_there_is_not_found(self, extensions: Extensions) -> None:
        with pytest.raises(DatabaseNotFoundError):
            extensions.available("postgresql", "nope")


class TestLoading:
    def test_the_name_in_the_statement_is_the_servers_quoted(
        self, extensions: Extensions, state: dict[str, Any]
    ) -> None:
        info = extensions.add("postgresql", "shop_db", "vector")

        assert info.installed_version == "0.8.0"
        assert state["statements"][-2] == (
            "shop_db",
            'CREATE EXTENSION IF NOT EXISTS "vector";',
        )

    def test_the_needs_are_loaded_only_when_asked(
        self, extensions: Extensions, state: dict[str, Any]
    ) -> None:
        extensions.add("postgresql", "shop_db", "postgis_topology", cascade=True)

        assert set(state["extensions"]["shop_db"]) == {"postgis_topology", "postgis"}
        assert any(sql.endswith(" CASCADE;") for _db, sql in state["statements"])

    def test_an_extension_the_server_does_not_offer_is_refused_before_any_statement(
        self, extensions: Extensions, state: dict[str, Any]
    ) -> None:
        state["statements"].clear()

        with pytest.raises(ValidationError) as caught:
            extensions.add("postgresql", "shop_db", "timescaledb")

        assert "no extension called timescaledb" in caught.value.message
        assert "Install the extension's package" in caught.value.details
        assert all(sql.startswith("SELECT") for _db, sql in state["statements"]), "nothing written"

    @pytest.mark.parametrize("name", ['x"; DROP DATABASE shop_db; --', "a b", "", "x" * 64, "ñ"])
    def test_text_that_cannot_be_a_name_never_reaches_a_statement(
        self, extensions: Extensions, state: dict[str, Any], name: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            extensions.add("postgresql", "shop_db", name)

        assert caught.value.field == "name"
        assert state["statements"] == []

    def test_loading_is_audited(
        self, extensions: Extensions, recorded: list[dict[str, Any]]
    ) -> None:
        extensions.add("postgresql", "shop_db", "pg_trgm")

        [event] = [item for item in recorded if item["event"] == "db.extension"]
        assert event["target"] == "db:postgresql/shop_db"
        assert event["details"] == {"extension": "pg_trgm", "action": "add", "version": "1.6"}

    def test_what_postgresql_refuses_is_said_in_its_own_words(
        self, extensions: Extensions, state: dict[str, Any]
    ) -> None:
        extensions.available("postgresql", "shop_db")
        state["refuse"] = 'ERROR: could not open extension control file "postgis.control"'

        with pytest.raises(DatabaseQueryError) as caught:
            extensions.add("postgresql", "shop_db", "pg_trgm")

        assert "postgis.control" in (caught.value.output or "")


class TestUnloading:
    def test_it_drops_what_is_loaded(self, extensions: Extensions, state: dict[str, Any]) -> None:
        extensions.add("postgresql", "shop_db", "vector")

        info = extensions.remove("postgresql", "shop_db", "vector")

        assert not info.installed and state["extensions"]["shop_db"] == {}
        assert state["statements"][-2][1] == 'DROP EXTENSION "vector";'

    def test_cascade_is_only_sent_when_asked(
        self, extensions: Extensions, state: dict[str, Any]
    ) -> None:
        extensions.add("postgresql", "shop_db", "vector")

        extensions.remove("postgresql", "shop_db", "vector", cascade=True)

        assert state["statements"][-2][1] == 'DROP EXTENSION "vector" CASCADE;'

    def test_an_extension_that_is_not_loaded_has_nothing_to_remove(
        self, extensions: Extensions
    ) -> None:
        with pytest.raises(DatabaseNotFoundError) as caught:
            extensions.remove("postgresql", "shop_db", "vector")

        assert "is not loaded in shop_db" in caught.value.message

    def test_removing_is_audited(
        self, extensions: Extensions, recorded: list[dict[str, Any]]
    ) -> None:
        extensions.add("postgresql", "shop_db", "vector")

        extensions.remove("postgresql", "shop_db", "vector")

        events = [item for item in recorded if item["event"] == "db.extension"]
        assert [item["details"]["action"] for item in events] == ["add", "remove"]


# ===================================================================== the command


@pytest.fixture
def cli_world(
    service: DatabaseService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> DatabaseService:
    monkeypatch.setattr(db_cli, "_service", lambda logger: service)
    return service


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["db", "extension", *args], input=input)


class TestTheCommand:
    def test_list_shows_what_is_loaded_and_all_shows_what_is_offered(
        self, cli_world: DatabaseService, state: dict[str, Any]
    ) -> None:
        state["extensions"]["shop_db"] = {"pg_trgm": "1.6"}

        loaded = run("list", "shop_db", "-e", "postgresql")
        offered = run("list", "shop_db", "-e", "postgresql", "--all")

        assert loaded.exit_code == 0, loaded.output
        assert "pg_trgm" in loaded.output and "postgis" not in loaded.output
        assert "postgis" in offered.output and "plpython3u" in offered.output

    def test_list_says_how_to_see_more_when_nothing_is_loaded(
        self, cli_world: DatabaseService
    ) -> None:
        result = run("list", "shop_db", "-e", "postgresql")

        assert result.exit_code == 0, result.output
        assert "no extension loaded" in result.output and "--all" in result.output

    def test_the_list_is_json_for_scripts(self, cli_world: DatabaseService) -> None:
        body = json.loads(run("list", "shop_db", "-e", "postgresql", "--json").output)

        assert body["engine"] == "postgresql" and len(body["extensions"]) == len(OFFERED)

    def test_add_loads_it(self, cli_world: DatabaseService, state: dict[str, Any]) -> None:
        result = run("add", "shop_db", "pg_trgm", "-e", "postgresql")

        assert result.exit_code == 0, result.output
        assert state["extensions"]["shop_db"] == {"pg_trgm": "1.6"}

    def test_remove_asks_first(self, cli_world: DatabaseService, state: dict[str, Any]) -> None:
        run("add", "shop_db", "pg_trgm", "-e", "postgresql")

        refused = run("remove", "shop_db", "pg_trgm", "-e", "postgresql", input="n\n")
        done = run("remove", "shop_db", "pg_trgm", "-e", "postgresql", "--yes")

        assert refused.exit_code != 0 and done.exit_code == 0, done.output
        assert state["extensions"]["shop_db"] == {}

    def test_a_rehearsal_sends_nothing(
        self, cli_world: DatabaseService, state: dict[str, Any]
    ) -> None:
        result = CliRunner().invoke(
            root_cli,
            ["--dry-run", "db", "extension", "add", "shop_db", "pg_trgm", "-e", "postgresql"],
        )

        assert result.exit_code == 0, result.output
        assert state["statements"] == []


# ========================================================================== the API


@pytest.fixture
def app(service: DatabaseService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(extensions_module, "service", lambda session=None, logger=None: service)
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


def sign_in(app: FastAPI, *, elevate: bool) -> TestClient:
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = client.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200, login.text
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    if elevate:
        assert client.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return client


URL = "/api/databases/databases/postgresql/shop_db/extensions"


class TestTheApi:
    def test_the_list_marks_what_is_loaded_and_what_a_team_may_load(
        self, app: FastAPI, state: dict[str, Any]
    ) -> None:
        state["extensions"]["shop_db"] = {"vector": "0.8.0"}
        client = sign_in(app, elevate=False)

        response = client.get(URL)

        assert response.status_code == 200, response.text
        by_name = {item["name"]: item for item in response.json()["extensions"]}
        assert by_name["vector"]["installed"] is True and by_name["vector"]["trusted"] is True
        assert by_name["postgis"]["installed"] is False and by_name["postgis"]["trusted"] is False
        assert response.json()["server_version"] == "17.2"

    def test_loading_asks_for_sudo_mode(self, app: FastAPI, state: dict[str, Any]) -> None:
        client = sign_in(app, elevate=False)

        response = client.post(URL, json={"name": "vector"})

        assert response.status_code in (401, 403)
        assert state["extensions"].get("shop_db", {}) == {}

    def test_it_loads_and_unloads(self, app: FastAPI, state: dict[str, Any]) -> None:
        client = sign_in(app, elevate=True)

        added = client.post(URL, json={"name": "vector"})
        removed = client.delete(f"{URL}/vector")

        assert added.status_code == 200 and added.json()["installed"] is True, added.text
        assert removed.status_code == 200 and removed.json()["installed"] is False, removed.text

    def test_an_extension_the_server_does_not_offer_is_a_400_saying_where_one_comes_from(
        self, app: FastAPI
    ) -> None:
        client = sign_in(app, elevate=True)

        response = client.post(URL, json={"name": "timescaledb"})

        assert response.status_code == 400
        assert "no extension called timescaledb" in response.text

    def test_a_team_does_not_load_an_extension_that_is_not_trusted(
        self, app: FastAPI, state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.permissions.enforce import PermissionDenied

        def refuse(permission: str) -> None:
            raise PermissionDenied("instance_scope_required", permission, "server only", "ask")

        monkeypatch.setattr(extensions_module, "require_instance_scope", refuse)
        client = sign_in(app, elevate=True)

        trusted = client.post(URL, json={"name": "vector"})
        untrusted = client.post(URL, json={"name": "plpython3u"})

        assert trusted.status_code == 200, trusted.text
        assert untrusted.status_code == 403
        assert "plpython3u" not in state["extensions"]["shop_db"]

    def test_mysql_has_no_extensions(self, app: FastAPI) -> None:
        client = sign_in(app, elevate=False)

        response = client.get("/api/databases/databases/mysql/shop_db/extensions")

        assert response.status_code == 400
        assert "PostgreSQL" in response.text
