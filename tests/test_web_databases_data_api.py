# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The data explorer, the row editor, the console's second version and the
database metrics, over HTTP.

What is defended at this layer: reads need ``databases.read`` only (a viewer
may browse and run read-only SQL); a row edit and a write need
``databases.write`` and sudo mode, and a write needs ``root_equivalent``
under the ens-medium profile; history and saved queries are the operator's
own; exports are files; the shapes the console builds on.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.managers.database.postgres import NULL_MARKER, PostgresManager
from noust.managers.database.redis import RedisManager
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.database_data_support import SqlRunner, StubConfig, installed_sql_runner


@pytest.fixture
def sql_runner() -> Iterator[SqlRunner]:
    """
    Yields:
        The SQL runner, installed over a fresh store.
    """
    yield from installed_sql_runner()


CATALOG = json.dumps(
    {
        "schemas": ["public"],
        "relations": [
            {
                "schema": "public",
                "name": "users",
                "kind": "table",
                "rows_estimate": 2,
                "size_bytes": 8192,
            },
            {"schema": "public", "name": "odd/name", "kind": "view"},
        ],
    }
)
STRUCTURE = json.dumps(
    {
        "columns": [
            {"name": "id", "type": "integer", "base": "int4", "category": "N", "nullable": False},
            {"name": "email", "type": "text", "base": "text", "category": "S"},
            {"name": "avatar", "type": "bytea", "base": "bytea", "category": "U"},
        ],
        "primary_key": ["id"],
        "indexes": [{"name": "users_pkey", "unique": True, "primary": True, "columns": ["id"]}],
        "constraints": [{"name": "users_pkey", "type": "p", "definition": "PRIMARY KEY (id)"}],
    }
)
PAGE = json.dumps(
    {
        "rows": [
            [[1, "a@x", {"bytes": 3, "hex": "010203"}], [False], ["1"]],
            [[2, None, None], [False], ["2"]],
        ]
    }
)


@pytest.fixture
def wired(sql_runner: SqlRunner, monkeypatch: pytest.MonkeyPatch) -> SqlRunner:
    """
    Stand real engine managers, over the SQL runner, behind the service.

    Args:
        sql_runner: The runner.
        monkeypatch: Patching helper.

    Returns:
        The runner, answering the explorer's queries.
    """
    import noust.managers.database.service as db_service

    sql_runner.only_knows("psql", "redis-cli")
    sql_runner.answer("'relations'", CATALOG)
    sql_runner.answer("'primary_key'", STRUCTURE)
    sql_runner.answer("'rows', coalesce", PAGE)

    def get(name: str, verbose: bool = False) -> Any:
        if name in ("postgresql", "pg", "postgres"):
            manager = PostgresManager()
            manager.config = StubConfig()
            return manager
        if name == "redis":
            return RedisManager()
        return None

    monkeypatch.setattr(db_service, "get_db_manager", get)
    monkeypatch.setattr(
        db_service,
        "DatabaseRegistry",
        SimpleNamespace(list_engines=lambda: ["postgresql", "redis"]),
    )
    return sql_runner


@pytest.fixture
def app(tmp_path: Path, wired: SqlRunner) -> FastAPI:
    """
    Args:
        tmp_path: Per-test temporary directory.
        wired: The wired engines.

    Returns:
        The application.
    """
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        A master-token session carrying the CSRF header.
    """
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    response = signed_in.post(
        "/api/auth/login", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


def bearer(app: FastAPI, name: str, scope: str, **kwargs: Any) -> TestClient:
    """
    Args:
        app: The application.
        name: The token's name.
        scope: Its scope.
        **kwargs: More create_api_token arguments.

    Returns:
        A client presenting a fresh API token.
    """
    token = get_token_manager().create_api_token(name, scope=scope, **kwargs)["token"]
    reader = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    reader.headers["Authorization"] = f"Bearer {token}"
    return reader


def elevate(client: TestClient) -> None:
    """
    Args:
        client: A master-token session.
    """
    response = client.post(
        "/api/auth/elevate", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text


BASE = "/api/databases/databases/postgresql/shop"


# ============================================================ reading


def test_schemas_and_relations(client: TestClient) -> None:
    schemas = client.get(f"{BASE}/schemas")
    relations = client.get(f"{BASE}/relations", params={"kind": "table"})

    assert schemas.status_code == 200, schemas.text
    assert schemas.json() == {"schemas": [{"name": "public", "relations": 2}]}
    assert relations.json()["relations"] == [
        {
            "schema": "public",
            "name": "users",
            "kind": "table",
            "rows_estimate": 2,
            "size_bytes": 8192,
        }
    ]


def test_a_relation_is_named_by_query_parameters_whatever_its_name(client: TestClient) -> None:
    response = client.get(f"{BASE}/relation", params={"schema": "public", "relation": "odd/name"})

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "odd/name"


def test_the_structure(client: TestClient) -> None:
    body = client.get(f"{BASE}/relation", params={"schema": "public", "relation": "users"}).json()

    assert body["editable"] is True
    assert body["primary_key"] == ["id"]
    assert [column["kind"] for column in body["columns"]] == ["numeric", "text", "binary"]
    assert body["constraints"][0]["type"] == "primary_key"


def test_a_page_of_rows_is_typed(client: TestClient) -> None:
    response = client.get(
        f"{BASE}/rows",
        params=[
            ("schema", "public"),
            ("relation", "users"),
            ("limit", "1"),
            ("filter", "id:gt:0"),
            ("order", "id:asc"),
        ],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"] == [
        {"cells": [1, "a@x", {"bytes": 3, "hex": "010203"}], "truncated": [], "key": {"id": "1"}}
    ]
    assert body["pagination"] == "keyset"
    assert body["has_more"] is True
    assert body["next_cursor"]
    assert body["editable"] is True


def test_an_unknown_relation_is_404(client: TestClient) -> None:
    response = client.get(f"{BASE}/relation", params={"schema": "public", "relation": "nope"})

    assert response.status_code == 404, response.text


def test_a_bad_filter_is_400(client: TestClient) -> None:
    response = client.get(
        f"{BASE}/rows",
        params={"schema": "public", "relation": "users", "filter": "id:drop:1"},
    )

    assert response.status_code == 400, response.text


def test_a_viewer_may_browse(app: FastAPI) -> None:
    viewer = bearer(app, "viewer", "read")

    response = viewer.get(f"{BASE}/rows", params={"schema": "public", "relation": "users"})

    assert response.status_code == 200, response.text


# ============================================================ editing


def test_a_row_edit_needs_sudo_mode(client: TestClient, wired: SqlRunner) -> None:
    response = client.patch(
        f"{BASE}/rows",
        json={
            "schema": "public",
            "relation": "users",
            "key": {"id": "1"},
            "values": {"email": "b"},
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"] == "elevation_required"
    assert not wired.sent("UPDATE")


def test_a_viewer_may_not_edit(app: FastAPI) -> None:
    viewer = bearer(app, "viewer", "read")

    response = viewer.post(
        f"{BASE}/rows/delete", json={"schema": "public", "relation": "users", "key": {"id": "1"}}
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"] == "permission_denied"


def test_an_elevated_session_edits_one_row(client: TestClient, wired: SqlRunner) -> None:
    wired.answer(
        "UPDATE", json.dumps({"before": {"id": 1, "email": "a"}, "after": {"id": 1, "email": "b"}})
    )
    elevate(client)

    response = client.patch(
        f"{BASE}/rows",
        json={
            "schema": "public",
            "relation": "users",
            "key": {"id": "1"},
            "values": {"email": "b"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "update"
    assert body["schema"] == "public"
    assert body["before"] == {"id": 1, "email": "a"}
    assert body["key"] == {"id": "1"}


def test_a_missing_row_is_404(client: TestClient, wired: SqlRunner) -> None:
    wired.answer(
        "DELETE FROM", stderr="ERROR:  noust: expected exactly one row, found 0\n", exit_code=3
    )
    elevate(client)

    response = client.post(
        f"{BASE}/rows/delete", json={"schema": "public", "relation": "users", "key": {"id": "9"}}
    )

    assert response.status_code == 404, response.text


def test_row_edits_are_marked_as_needing_elevation_in_the_schema(client: TestClient) -> None:
    paths = client.get("/api/openapi.json").json()["paths"]

    rows = paths["/api/databases/databases/{engine}/{name}/rows"]
    assert rows["patch"].get("x-noust-requires-elevation") is True
    assert rows["post"].get("x-noust-requires-elevation") is True
    assert rows["get"].get("x-noust-permission") == "databases.read"
    assert rows["patch"].get("x-noust-permission") == "databases.write"


# ============================================================ console


def test_a_viewer_may_run_a_read_but_not_a_write(app: FastAPI, wired: SqlRunner) -> None:
    viewer = bearer(app, "viewer", "read")
    body = {"engine": "postgresql", "database": "shop", "query": "SELECT 1"}

    read = viewer.post("/api/databases/query", json=body)
    write = viewer.post("/api/databases/query", json={**body, "mode": "write"})

    assert read.status_code == 200, read.text
    assert write.status_code == 403, write.text
    assert write.json()["error"] == "permission_denied"


@pytest.mark.parametrize("ens", [False, True])
def test_a_write_needs_root_equivalent_under_the_ens_profile(
    monkeypatch: pytest.MonkeyPatch, ens: bool
) -> None:
    """A principal with databases.write but not root_equivalent writes only outside ENS."""
    import noust.web.api.databases.query as query_api
    from noust.web.permissions.enforce import PermissionDenied

    payload = {"type": "session", "permissions": ["databases.read", "databases.write"]}
    elevations: list[Any] = []
    monkeypatch.setattr(query_api, "load_policy", lambda: SimpleNamespace(ens=ens))
    monkeypatch.setattr(query_api, "ensure_elevated", lambda request, session: elevations.append(1))

    if ens:
        with pytest.raises(PermissionDenied) as caught:
            query_api.ensure_may_write(None, payload)  # type: ignore[arg-type]
        assert caught.value.permission == "root_equivalent"
        assert not elevations
    else:
        query_api.ensure_may_write(None, payload)  # type: ignore[arg-type]
        assert elevations == [1]


def test_the_console_returns_nulls_and_its_timeout(client: TestClient, wired: SqlRunner) -> None:
    wired.answer("SELECT a", f'a,b\n{NULL_MARKER},""\n')

    response = client.post(
        "/api/databases/query",
        json={"engine": "postgresql", "database": "shop", "query": "SELECT a, b", "timeout_s": 5},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [[None, ""]]
    assert response.json()["timeout_s"] == 5


def test_an_unsupported_timeout_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/databases/query",
        json={"engine": "postgresql", "database": "shop", "query": "SELECT 1", "timeout_s": 600},
    )

    assert response.status_code == 422, response.text


def test_explain_analyze_needs_sudo_mode(client: TestClient, wired: SqlRunner) -> None:
    body = {"engine": "postgresql", "database": "shop", "query": "DELETE FROM t", "analyze": True}

    refused = client.post("/api/databases/query/explain", json=body)
    wired.answer("EXPLAIN (ANALYZE", '[{"Plan": {"Node Type": "Delete"}}]')
    elevate(client)
    allowed = client.post("/api/databases/query/explain", json=body)

    assert refused.status_code == 403
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["format"] == "json"
    assert allowed.json()["plan"][0]["Plan"]["Node Type"] == "Delete"


def test_an_export_is_a_file(client: TestClient, wired: SqlRunner) -> None:
    wired.answer("SELECT id", "id\n1\n2\n")

    response = client.post(
        "/api/databases/query/export",
        json={"engine": "postgresql", "database": "shop", "query": "SELECT id FROM t"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-noust-rows"] == "2"
    assert response.text == "id\n1\n2\n"


def test_history_and_saved_queries_are_the_operators_own(app: FastAPI, client: TestClient) -> None:
    client.post(
        "/api/databases/query",
        json={"engine": "postgresql", "database": "shop", "query": "SELECT 1"},
    )
    saved = client.post(
        "/api/databases/console/saved",
        json={"name": "one", "engine": "postgres", "database": "shop", "query": "SELECT 1"},
    )
    viewer = bearer(app, "viewer", "read")

    assert saved.status_code == 201, saved.text
    assert saved.json()["engine"] == "postgresql"
    mine = client.get("/api/databases/console/history", params={"database": "shop"}).json()
    assert [entry["statement"] for entry in mine["entries"]] == ["SELECT 1"]
    assert viewer.get("/api/databases/console/history").json() == {"entries": []}
    assert viewer.get("/api/databases/console/saved").json() == {"queries": []}
    assert viewer.delete(f"/api/databases/console/saved/{saved.json()['id']}").status_code == 404
    duplicate = client.post(
        "/api/databases/console/saved",
        json={"name": "one", "engine": "postgresql", "database": "shop", "query": "SELECT 2"},
    )
    assert duplicate.status_code == 409, duplicate.text
    updated = client.put(
        f"/api/databases/console/saved/{saved.json()['id']}",
        json={"name": "two", "engine": "postgresql", "query": "SELECT 2"},
    )
    assert updated.json()["name"] == "two" and updated.json()["database"] == ""
    assert client.delete(f"/api/databases/console/saved/{saved.json()['id']}").status_code == 200
    assert client.delete("/api/databases/console/history").json()["success"] is True


# ============================================================ metrics


def test_database_metrics(client: TestClient, wired: SqlRunner) -> None:
    wired.answer(
        "'tables', coalesce",
        json.dumps(
            {
                "size_bytes": 1024,
                "server_connections": 3,
                "max_connections": 100,
                "stat": {
                    "connections": 1,
                    "xact": 5,
                    "blks_hit": 9,
                    "blks_read": 1,
                    "deadlocks": 0,
                },
                "tables": [
                    {"schema": "public", "name": "users", "size_bytes": 512, "rows_estimate": 2}
                ],
            }
        ),
    )

    response = client.get(f"{BASE}/metrics")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cache_hit_ratio"] == 90.0
    assert body["tables"][0]["schema"] == "public"
    assert body["series"]["size"] == "db.postgresql.shop.size"


def test_slow_queries_say_how_to_enable_them(client: TestClient, wired: SqlRunner) -> None:
    wired.answer("'preloaded'", json.dumps({"schema": None, "preloaded": False}))

    body = client.get(f"{BASE}/slow-queries").json()

    assert body["available"] is False
    assert body["reason"] == "not_loaded"


# ============================================================ Redis


def test_redis_keys(client: TestClient, wired: SqlRunner) -> None:
    wired.answer("\\x53\\x43\\x41\\x4e", '"0","k"\n')
    wired.answer("\\x54\\x59\\x50\\x45", '"string"\n-1\n48\n')

    response = client.get("/api/databases/databases/redis/0/keys", params={"match": "k*"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "keys": [{"key": "k", "hex": None, "type": "string", "ttl": None, "memory": 48}],
        "cursor": "0",
        "done": True,
        "read_only_enforced": True,
    }
