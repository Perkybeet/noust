# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A database Noust does not manage, as the Databases screen shows it (4.1, item 103).

It is not a kind of database: it is a database whose Noust state is ``not_added`` and whose action
is ``add_to_noust``. Where it runs (its server) is a separate field, so neither is ever a word that
means both. What is pinned over HTTP: the state and the actions on every database, the route that
adds one database, the refusal of one that is already Noust's with a code, and the default server
of an engine being where a database named by its bare engine is made.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from noust.managers.database.base import BaseDatabaseManager
from tests.support.web_databases_api import (  # noqa: F401
    _capture_queued_jobs,
    anonymous,
    app,
    client,
    elevate,
    engines,
)

# A fixture imported from the support module is found by pytest in this module's namespace.
# ruff: noqa: F811


@pytest.fixture
def db(engines: dict[str, type[BaseDatabaseManager]]) -> type[BaseDatabaseManager]:
    return engines["postgresql"]


def listed(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/databases/databases")
    assert response.status_code == 200, response.text
    return {item["name"]: item for item in response.json()["databases"]}


class TestTheStateAndTheAction:
    def test_a_database_noust_does_not_manage_says_so_and_offers_to_add_it(
        self, client: TestClient, db
    ) -> None:
        appdb = listed(client)["appdb"]

        assert appdb["tracked"] is False
        assert appdb["noust_state"] == "not_added"
        assert appdb["actions"] == ["add_to_noust"]
        # Where it runs is its own field: the key of its server.
        assert appdb["engine"] == "postgresql"

    def test_one_noust_manages_offers_nothing_to_add(self, client: TestClient, db) -> None:
        created = client.post(
            "/api/databases/databases", json={"name": "shop", "engine": "postgresql"}
        )
        assert created.status_code == 200, created.text

        shop = listed(client)["shop"]

        assert (shop["noust_state"], shop["actions"]) == ("added", [])
        assert created.json()["noust_state"] == "added"


class TestAddingOne:
    def test_it_becomes_noust_s(self, client: TestClient, db) -> None:
        response = client.post("/api/databases/databases/postgresql/appdb/adopt")

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["name"], body["tracked"], body["noust_state"]) == ("appdb", True, "added")
        assert body["actions"] == []
        assert listed(client)["appdb"]["tracked"] is True

    def test_adding_all_of_an_engines_is_still_there(self, client: TestClient, db) -> None:
        response = client.post("/api/databases/databases/adopt", json={"engine": "postgresql"})

        assert response.status_code == 200, response.text
        assert response.json()["adopted"] == ["postgresql/appdb"]

    def test_one_that_is_already_noust_s_is_a_409_with_a_code(self, client: TestClient, db) -> None:
        client.post("/api/databases/databases/postgresql/appdb/adopt")

        again = client.post("/api/databases/databases/postgresql/appdb/adopt")

        assert again.status_code == 409
        assert again.json()["coded"] == {
            "code": "databases.already_added",
            "params": {"name": "appdb", "engine": "postgresql"},
        }

    def test_a_database_the_engine_does_not_hold_is_a_404(self, client: TestClient, db) -> None:
        def absent(self, name):  # type: ignore[no-untyped-def]
            from noust.core.exceptions import DatabaseNotFoundError

            raise DatabaseNotFoundError(f"Database '{name}' not found")

        db.get_database_info = absent  # type: ignore[method-assign]

        assert client.post("/api/databases/databases/postgresql/nope/adopt").status_code == 404

    def test_it_is_a_database_manage_and_a_resource_of_a_team(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.registry import route_map
        from noust.web.permissions.scopes import RESOURCE, rule_for

        template = "/api/databases/databases/{engine}/{name}/adopt"
        assert route_map()[("POST", template)] == Permission.DATABASES_MANAGE
        rule = rule_for("POST", template)
        assert rule is not None and rule.scoping == RESOURCE
