# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/views``: one route per resource, with the servers of the fleet as a column and a filter.

The central is the real application with a signed-in operator; the nodes are the fakes of
:mod:`tests.support.fleet_views_support`. What is pinned: every resource answers the same
envelope; the central is one more server (``@central``); naming a server filters and never moves
the page; a Noust without a fleet answers the rows of its own and says it has no fleet; who may
ask the nodes; the servers that refuse the operator come grouped with a code, never as the JSON
they answered with; the search finds a thing on whichever server it is; and the routes this
replaces say so.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet import aggregate
from noust.fleet.aggregate import set_aggregator
from noust.fleet.models import central_name
from noust.web.jobs import JobManager
from noust.web.server import create_app, get_token_manager
from tests.support.fleet_views_support import Central, build_central
from tests.support.teams_support import PASSWORD, World, build_world
from tests.support.web_auth import make_config
from tests.support.web_central import sign_in

#: The routes of the views, with the view of the aggregator each reads.
VIEWS = {
    "apps": "apps",
    "databases": "databases",
    "sites": "sites",
    "certificates": "certificates",
    "backups": "backups",
    "cron": "cron",
    "logs": "logs",
    "alerts": "alerts",
    "activity": "activity",
}

ENVELOPE = {
    "resource",
    "generated_at",
    "partial",
    "fleet",
    "servers",
    "nodes",
    "refusals",
    "items",
}


def _wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]
) -> Iterator[Central]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet", names)
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
    monkeypatch.setattr(
        "noust.core.update_checker.UpdateChecker.enabled", classmethod(lambda cls: False)
    )
    JobManager.reset_instance()
    yield built
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    yield from _wired(tmp_path, monkeypatch, ("web-2", "web-3"))


@pytest.fixture
def lone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    yield from _wired(tmp_path, monkeypatch, ())


@pytest.fixture
def client(central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


@pytest.fixture
def lone_client(lone: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def _asked(central: Central, name: str) -> int:
    return len(central.nodes[name].requests)


class TestTheEnvelope:
    @pytest.mark.parametrize("route", sorted(VIEWS))
    def test_every_resource_answers_the_same_envelope(self, client, central, route):
        sign_in(client)

        response = client.get(f"/api/views/{route}")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == ENVELOPE
        assert body["resource"] == VIEWS[route]
        assert [node["name"] for node in body["nodes"]] == [central_name(), "web-2", "web-3"]
        assert body["fleet"] is True

    def test_the_central_is_one_server_among_the_others(self, client, central):
        sign_in(client)

        body = client.get("/api/views/apps").json()

        assert body["servers"] == [
            {"name": central_name(), "selector": "@central", "local": True},
            {"name": "web-2", "selector": "web-2", "local": False},
            {"name": "web-3", "selector": "web-3", "local": False},
        ]

    def test_a_row_says_which_server_it_is_on_and_where_its_page_is(self, client, central):
        sign_in(client)
        central.nodes["web-2"].apps = [{"domain": "shop.example.com", "status": "running"}]

        (row,) = client.get("/api/views/apps").json()["items"]

        assert (row["node"], row["local"]) == ("web-2", False)
        assert (row["page"], row["href"]) == (
            "/apps/shop.example.com",
            "/n/web-2/apps/shop.example.com",
        )

    def test_the_databases_the_sites_the_jobs_and_the_rules_of_every_server(self, client, central):
        sign_in(client)
        node = central.nodes["web-2"]
        node.responses["/api/databases/databases"] = (
            200,
            {"databases": [{"name": "shop", "engine": "postgresql"}], "total": 1, "problems": []},
        )
        node.responses["/api/sites"] = (
            200,
            {"sites": [{"name": "shop.example.com", "webserver": "nginx"}], "total": 1},
        )
        node.responses["/api/cron"] = (200, {"jobs": [{"name": "nightly"}], "total": 1})
        node.responses["/api/alerts/rules"] = (
            200,
            {"items": [{"name": "cpu", "states": []}], "metrics": [], "firing": 0},
        )

        found = {
            route: client.get(f"/api/views/{route}").json()["items"]
            for route in ("databases", "sites", "cron", "alerts")
        }

        assert [(r["node"], r["name"]) for r in found["databases"]] == [("web-2", "shop")]
        assert [(r["node"], r["name"]) for r in found["sites"]] == [("web-2", "shop.example.com")]
        assert [(r["node"], r["name"]) for r in found["cron"]] == [("web-2", "nightly")]
        assert [(r["node"], r["name"]) for r in found["alerts"]] == [("web-2", "cpu")]

    def test_the_operators_identity_is_what_each_node_is_told(self, client, central):
        sign_in(client)

        client.get("/api/views/apps")

        asked = central.nodes["web-2"].requests[-1]
        assert asked.headers["X-Noust-Actor-Scope"] == "admin"
        assert asked.headers["X-Noust-Actor"]


class TestTheServerFilter:
    def test_naming_a_server_asks_only_it(self, client, central):
        sign_in(client)
        central.nodes["web-2"].apps = [{"domain": "a.example.com"}]
        central.nodes["web-3"].apps = [{"domain": "b.example.com"}]

        body = client.get("/api/views/apps", params={"node": "web-3"}).json()

        assert [node["name"] for node in body["nodes"]] == ["web-3"]
        assert [row["domain"] for row in body["items"]] == ["b.example.com"]
        assert _asked(central, "web-2") == 0

    def test_naming_a_server_does_not_hide_the_others_from_the_filter(self, client, central):
        sign_in(client)

        body = client.get("/api/views/apps", params={"node": "web-3"}).json()

        assert body["fleet"] is True
        assert [server["selector"] for server in body["servers"]] == ["@central", "web-2", "web-3"]

    def test_this_central_is_asked_for_by_its_selector(self, client, central):
        sign_in(client)

        body = client.get("/api/views/apps", params={"node": "@central"}).json()

        assert [node["name"] for node in body["nodes"]] == [central_name()]
        assert body["nodes"][0]["local"] is True
        assert _asked(central, "web-2") == 0

    def test_several_servers_are_asked_for_by_repeating_the_filter(self, client, central):
        sign_in(client)

        body = client.get("/api/views/apps", params={"node": ["web-2", "@central"]}).json()

        assert [node["name"] for node in body["nodes"]] == [central_name(), "web-2"]

    def test_a_hub_has_no_rows_of_its_own(self, client, central, monkeypatch):
        monkeypatch.setattr(aggregate, "includes_central", lambda: False)
        sign_in(client)

        body = client.get("/api/views/apps").json()

        assert [node["name"] for node in body["nodes"]] == ["web-2", "web-3"]
        assert [server["name"] for server in body["servers"]] == ["web-2", "web-3"]


class TestWithoutAFleet:
    def test_a_noust_with_no_servers_gives_its_own_rows_and_says_it_has_no_fleet(
        self, lone_client, lone
    ):
        sign_in(lone_client)

        body = lone_client.get("/api/views/apps").json()

        assert body["fleet"] is False
        assert body["servers"] == []
        assert body["refusals"] == []
        assert [node["local"] for node in body["nodes"]] == [True]

    def test_the_selector_of_the_central_still_works_without_one(self, lone_client, lone):
        sign_in(lone_client)

        body = lone_client.get("/api/views/apps", params={"node": "@central"}).json()

        assert [node["local"] for node in body["nodes"]] == [True]


class TestWhoMayAskTheServers:
    def test_a_credential_without_the_fleet_gets_the_rows_of_this_server_only(
        self, client, central
    ):
        # A token narrows to what its owner holds, so it needs an owner that holds the fleet too.
        tokens = get_token_manager()
        owner = tokens.accounts.create("apps-only", "viewer", password=PASSWORD)
        issued = tokens.create_api_token(
            "apps-only", "read", None, owner=owner, permissions=["apps.read"]
        )
        headers = {"Authorization": f"Bearer {issued['token']}"}

        body = client.get("/api/views/apps", headers=headers).json()

        assert body["fleet"] is False
        assert body["servers"] == []
        assert [node["name"] for node in body["nodes"]] == [central_name()]
        assert _asked(central, "web-2") == 0 and _asked(central, "web-3") == 0

    def test_a_credential_that_does_not_see_every_team_never_asks_a_server(
        self, client, central, monkeypatch
    ):
        from noust.core.teams import Scope

        sign_in(client)
        monkeypatch.setattr(
            "noust.web.api.fleet.current_scope",
            lambda: Scope(team_roles={1: "viewer"}, sees_all=False, account_id=7),
        )

        body = client.get("/api/views/apps", params={"node": "web-2"}).json()

        assert body["fleet"] is False
        assert [node["name"] for node in body["nodes"]] == [central_name()]
        assert _asked(central, "web-2") == 0

    def test_a_route_asks_for_the_permission_of_the_page_it_stands_for(self, client, central):
        issued = get_token_manager().create_api_token("reader", "read")
        headers = {"Authorization": f"Bearer {issued['token']}"}

        assert client.get("/api/views/apps", headers=headers).status_code == 200
        assert client.get("/api/views/databases", headers=headers).status_code == 200
        # A viewer reads no audit trail, here or on a server.
        refused = client.get("/api/views/activity", headers=headers)
        assert refused.status_code == 403
        assert refused.json()["error"] == "permission_denied"


class TestTheServersThatRefuse:
    def _refuse(self, central: Central, *names: str) -> None:
        for name in names:
            central.nodes[name].responses["/api/audit"] = (
                403,
                {
                    "error": "permission_denied",
                    "detail": "This needs the 'audit.read' permission, which admin does not hold",
                    "hint": "Ask a security officer for an account with a role that holds it.",
                    "fields": None,
                },
            )

    def test_servers_that_refuse_the_same_thing_are_one_group_with_a_code(self, client, central):
        sign_in(client)
        self._refuse(central, "web-2", "web-3")

        body = client.get("/api/views/activity").json()

        (group,) = body["refusals"]
        assert group["servers"] == ["web-2", "web-3"]
        assert group["count"] == 2
        assert group["permission"] == "audit.read"
        assert group["reason"] == "permission"
        assert group["resource"] == "activity"
        assert group["coded"]["code"] == "fleet.refused.permission"
        assert group["coded"]["params"]["permission"] == "audit.read"
        assert group["hint"], "it says how to give what is missing"
        refused = {node["name"]: node for node in body["nodes"]}
        assert refused["web-2"]["status"] == "forbidden"
        assert refused["web-2"]["permission"] == "audit.read"
        assert refused[central_name()]["permission"] is None

    def test_what_a_person_is_shown_is_never_the_json_a_node_answered_with(self, client, central):
        sign_in(client)
        self._refuse(central, "web-2", "web-3")

        body = client.get("/api/views/activity").json()

        assert '"error"' not in json.dumps(body["nodes"])
        assert '"error"' not in json.dumps(body["refusals"])
        assert all(node["error_verbatim"] is None for node in body["nodes"])

    def test_the_servers_that_did_not_refuse_still_give_their_rows(self, client, central):
        sign_in(client)
        self._refuse(central, "web-3")
        central.nodes["web-2"].responses["/api/audit"] = (
            200,
            {"items": [{"timestamp": "2026-10-28T10:00:00+00:00", "action": "app.deploy"}]},
        )

        body = client.get("/api/views/activity").json()

        # The central's own audit trail is a server's too: its rows are there.
        assert [row["node"] for row in body["items"] if not row["local"]] == ["web-2"]
        assert body["partial"] is True
        assert body["refusals"][0]["servers"] == ["web-3"]

    def test_a_view_nobody_refused_has_no_group(self, client, central):
        sign_in(client)

        assert client.get("/api/views/apps").json()["refusals"] == []

    def test_a_server_that_is_down_is_not_a_refusal(self, client, central):
        sign_in(client)
        central.nodes["web-3"].down = True

        body = client.get("/api/views/apps").json()

        assert body["refusals"] == []
        down = next(node for node in body["nodes"] if node["name"] == "web-3")
        assert (down["status"], down["refusal"]) == ("unreachable", None)


class TestSearch:
    def _fill(self, central: Central) -> None:
        web2, web3 = central.nodes["web-2"], central.nodes["web-3"]
        web2.apps = [{"domain": "shop.example.com", "name": "shop.example.com"}]
        web2.responses["/api/databases/databases"] = (
            200,
            {"databases": [{"name": "shop", "engine": "postgresql"}], "total": 1, "problems": []},
        )
        web3.apps = [{"domain": "blog.example.com", "name": "blog.example.com"}]
        web3.responses["/api/sites"] = (
            200,
            {"sites": [{"name": "blog.example.com", "webserver": "nginx"}], "total": 1},
        )

    def test_a_thing_is_found_on_whichever_server_it_is(self, client, central):
        sign_in(client)
        self._fill(central)

        body = client.get("/api/views/search", params={"q": "shop"}).json()

        assert [(hit["kind"], hit["name"], hit["node"]) for hit in body["items"]] == [
            ("database", "shop", "web-2"),
            ("app", "shop.example.com", "web-2"),
        ]
        database = body["items"][0]
        assert database["href"] == "/n/web-2/databases/postgresql/shop"
        assert database["local"] is False
        assert body["query"] == "shop"
        assert (body["total"], body["truncated"]) == (2, False)
        assert "words" not in database

    def test_the_envelope_is_the_views(self, client, central):
        sign_in(client)

        body = client.get("/api/views/search", params={"q": "x"}).json()

        assert {"fleet", "servers", "nodes", "refusals", "partial", "generated_at"} <= set(body)
        assert body["resource"] == "index"

    def test_a_server_is_found_by_its_name_even_when_it_is_down(self, client, central):
        sign_in(client)
        central.nodes["web-3"].down = True

        body = client.get("/api/views/search", params={"q": "web-3"}).json()

        assert [(hit["kind"], hit["name"]) for hit in body["items"]] == [("server", "web-3")]
        assert body["partial"] is True

    def test_only_the_kinds_asked_for(self, client, central):
        sign_in(client)
        self._fill(central)

        body = client.get("/api/views/search", params={"q": "shop", "kind": "app"}).json()

        assert [hit["kind"] for hit in body["items"]] == ["app"]

    def test_the_server_filter_works_as_in_the_views(self, client, central):
        sign_in(client)
        self._fill(central)

        body = client.get("/api/views/search", params={"q": "example", "node": "web-3"}).json()

        assert {hit["node"] for hit in body["items"]} == {"web-3"}
        assert _asked(central, "web-2") == 0

    def test_the_answer_is_cut_at_the_limit_and_says_so(self, client, central):
        sign_in(client)
        self._fill(central)

        body = client.get("/api/views/search", params={"q": "example", "limit": 1}).json()

        assert len(body["items"]) == 1
        assert body["total"] > 1
        assert body["truncated"] is True

    @pytest.mark.parametrize("query", ["", "   "])
    def test_nothing_to_search_for_is_a_refusal_naming_the_field(self, client, central, query):
        sign_in(client)

        response = client.get("/api/views/search", params={"q": query})

        assert response.status_code in (400, 422)
        assert "q" in json.dumps(response.json())

    def test_a_kind_that_does_not_exist_is_refused(self, client, central):
        sign_in(client)

        response = client.get("/api/views/search", params={"q": "shop", "kind": "nope"})

        assert response.status_code == 422

    def test_a_server_that_refuses_a_part_is_a_group_not_a_hole(self, client, central):
        sign_in(client)
        central.nodes["web-2"].responses["/api/databases/databases"] = (
            403,
            {"error": "permission_denied", "detail": "No", "hint": None, "fields": None},
        )
        central.nodes["web-2"].apps = [{"domain": "shop.example.com"}]

        body = client.get("/api/views/search", params={"q": "shop"}).json()

        assert [hit["name"] for hit in body["items"]] == ["shop.example.com"]
        (group,) = body["refusals"]
        assert group["permission"] == "databases.read"
        assert group["servers"] == ["web-2"]


class TestTheRoutesThisReplaces:
    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ("/api/fleet/apps", "/api/views/apps"),
            ("/api/fleet/certificates", "/api/views/certificates"),
            ("/api/fleet/backups", "/api/views/backups"),
            ("/api/fleet/activity", "/api/views/activity"),
            ("/api/fleet/logs", "/api/views/logs"),
        ],
    )
    def test_the_old_route_still_answers_and_says_what_replaces_it(self, client, central, old, new):
        sign_in(client)

        response = client.get(old)

        assert response.status_code == 200, response.text
        assert response.headers["Deprecation"].startswith("@")
        assert "Sunset" in response.headers
        assert f"<{new.replace('/api/', '/api/v1/', 1)}>" in response.headers["Link"]
        assert 'rel="successor-version"' in response.headers["Link"]

    def test_the_old_route_keeps_its_envelope(self, client, central):
        sign_in(client)

        body = client.get("/api/fleet/apps").json()

        assert set(body) == {"resource", "generated_at", "partial", "nodes", "items"}

    def test_the_routes_that_have_no_twin_are_not_deprecated(self, client, central):
        sign_in(client)

        for route in ("summary", "servers", "updates", "metrics"):
            response = client.get(f"/api/fleet/{route}")
            assert response.status_code == 200
            assert "Deprecation" not in response.headers, route

    def test_the_schema_announces_each_with_its_successor(self, client, central):
        sign_in(client)
        schema: dict[str, Any] = client.get("/api/openapi.json").json()

        for old, new in (
            ("/api/fleet/apps", "/api/views/apps"),
            ("/api/fleet/activity", "/api/views/activity"),
        ):
            operation = schema["paths"][old]["get"]
            assert operation["deprecated"] is True
            assert operation["x-noust-deprecation"]["successor"] == new


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Iterator[World]:
    """The server with two teams, whose people are accounts with tokens."""
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


class TestAMemberOfOneTeam:
    def test_sees_the_rows_of_its_team_and_is_told_there_is_no_fleet(self, world: World) -> None:
        body = world.client.get("/api/views/apps", headers=world.as_("alice")).json()

        assert body["fleet"] is False
        assert body["servers"] == []
        assert [row["domain"] for row in body["items"]] == ["shop.acme.example"]
        assert all(row["local"] for row in body["items"])

    def test_another_teams_rows_are_not_there(self, world: World) -> None:
        body = world.client.get("/api/views/apps", headers=world.as_("bob")).json()

        assert [row["domain"] for row in body["items"]] == ["blog.globex.example"]

    def test_what_belongs_to_the_whole_server_is_a_refusal_of_this_server_not_a_failure(
        self, world: World
    ) -> None:
        response = world.client.get("/api/views/cron", headers=world.as_("alice"))

        assert response.status_code == 200
        body = response.json()
        (outcome,) = body["nodes"]
        assert (outcome["status"], outcome["code"]) == ("forbidden", "instance_scope_required")
        (group,) = body["refusals"]
        assert (group["reason"], group["code"], group["includes_central"]) == (
            "other",
            "instance_scope_required",
            True,
        )
        assert group["coded"] is None, "the words are the server's own"

    def test_the_search_finds_what_its_team_sees_and_nothing_else(self, world: World) -> None:
        mine = world.client.get(
            "/api/views/search", params={"q": "example", "kind": "app"}, headers=world.as_("alice")
        ).json()
        theirs = world.client.get(
            "/api/views/search", params={"q": "example", "kind": "app"}, headers=world.as_("bob")
        ).json()

        assert [hit["name"] for hit in mine["items"]] == ["shop.acme.example"]
        assert [hit["name"] for hit in theirs["items"]] == ["blog.globex.example"]

    def test_the_instance_sees_both_teams_applications(self, world: World) -> None:
        body = world.client.get("/api/views/apps", headers=world.master).json()

        assert {row["domain"] for row in body["items"]} == {
            "shop.acme.example",
            "blog.globex.example",
        }
