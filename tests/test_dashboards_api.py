# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The panels of the console's Home over HTTP: ``/api/auth/dashboards``.

What is pinned here is what the API adds to the store of panels (``tests/test_account_dashboards.py``):
every principal that is an account keeps its own, the master token and a central's fleet token have no
account to keep them for (400 and a refusal), a central never forwards the route to a node, and what
a person sends is judged before it is stored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.web.auth import fleet_refusal
from tests.support.teams_support import World, build_world

ROUTE = "/api/auth/dashboards"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    """Two teams and their people, served by the console."""
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def layout(*ids: str) -> dict[str, Any]:
    return {
        "version": 1,
        "widgets": [
            {
                "id": widget,
                "type": "needs-attention",
                "x": 0,
                "y": index * 2,
                "w": 12,
                "h": 2,
                "settings": {},
            }
            for index, widget in enumerate(ids)
        ],
    }


def body(**changes: Any) -> dict[str, Any]:
    return {"name": "This server", "scope": "server", "layout": layout("a"), **changes}


class TestKeepingPanels:
    def test_an_account_with_nothing_saved_gets_an_empty_list_and_the_limits(
        self, world: World
    ) -> None:
        response = world.client.get(ROUTE, headers=world.as_("alice"))

        assert response.status_code == 200
        assert response.json() == {
            "dashboards": [],
            "limits": {
                "dashboards": 20,
                "widgets": 40,
                "columns": 12,
                "document_bytes": 32768,
                "settings_bytes": 2048,
            },
        }

    def test_a_panel_is_created_by_putting_it_under_an_id_and_read_back(self, world: World) -> None:
        put = world.client.put(f"{ROUTE}/main", json=body(), headers=world.as_("alice"))
        listed = world.client.get(ROUTE, headers=world.as_("alice")).json()["dashboards"]

        assert put.status_code == 200, put.text
        saved = put.json()
        assert saved["id"] == "main" and saved["name"] == "This server" and saved["position"] == 0
        assert saved["scope"] == "server" and saved["layout"] == layout("a")
        assert saved["created_at"] > 0 and saved["updated_at"] >= saved["created_at"]
        assert listed == [saved]

    def test_putting_it_again_changes_it_and_keeps_its_place(self, world: World) -> None:
        for name in ("one", "two"):
            world.client.put(f"{ROUTE}/{name}", json=body(name=name), headers=world.as_("alice"))

        changed = world.client.put(
            f"{ROUTE}/one",
            json=body(name="Renamed", scope="fleet", layout=layout("x", "y")),
            headers=world.as_("alice"),
        )

        assert changed.status_code == 200
        assert changed.json()["position"] == 0 and changed.json()["scope"] == "fleet"
        ids = [
            p["id"]
            for p in world.client.get(ROUTE, headers=world.as_("alice")).json()["dashboards"]
        ]
        assert ids == ["one", "two"]

    def test_each_account_keeps_its_own_and_the_same_id_is_not_shared(self, world: World) -> None:
        world.client.put(f"{ROUTE}/main", json=body(name="Alice's"), headers=world.as_("alice"))
        world.client.put(f"{ROUTE}/main", json=body(name="Dana's"), headers=world.as_("dana"))

        alice = world.client.get(ROUTE, headers=world.as_("alice")).json()["dashboards"]
        dana = world.client.get(ROUTE, headers=world.as_("dana")).json()["dashboards"]
        bob = world.client.get(ROUTE, headers=world.as_("bob")).json()["dashboards"]

        assert [p["name"] for p in alice] == ["Alice's"]
        assert [p["name"] for p in dana] == ["Dana's"]
        assert bob == []

    @pytest.mark.parametrize("who", ["admin", "alice", "dana", "omar", "sec", "aud"])
    def test_every_role_keeps_its_own_panels(self, world: World, who: str) -> None:
        # It is the person's own business, like the team they work in: no permission but being someone.
        response = world.client.put(f"{ROUTE}/main", json=body(), headers=world.as_(who))

        assert response.status_code == 200, response.text

    def test_the_master_token_has_no_account_to_keep_them_for(self, world: World) -> None:
        for response in (
            world.client.get(ROUTE, headers=world.master),
            world.client.put(f"{ROUTE}/main", json=body(), headers=world.master),
            world.client.delete(f"{ROUTE}/main", headers=world.master),
            world.client.post(f"{ROUTE}/order", json={"order": []}, headers=world.master),
        ):
            assert response.status_code == 400, response.text
            assert "account" in response.json()["detail"].lower()

    def test_an_anonymous_request_is_refused(self, world: World) -> None:
        assert world.client.get(ROUTE).status_code == 401

    def test_a_central_never_forwards_it_and_a_fleet_token_never_reaches_it(self) -> None:
        # Under /api/auth, which a node refuses from a central and the central does not proxy: a
        # panel is the operator's own, one per person across the fleet.
        for method in ("GET", "PUT", "DELETE", "POST"):
            assert fleet_refusal(method, ROUTE) is not None
            assert fleet_refusal(method, f"{ROUTE}/main") is not None
            assert fleet_refusal(method, f"{ROUTE}/order") is not None


class TestWhatIsJudged:
    def test_a_layout_that_is_not_valid_is_a_400_that_names_the_field(self, world: World) -> None:
        bad = layout("a")
        bad["widgets"][0]["w"] = 13

        response = world.client.put(
            f"{ROUTE}/main", json=body(layout=bad), headers=world.as_("alice")
        )

        assert response.status_code == 400
        assert "layout.widgets[0].w" in response.json()["fields"]
        assert world.client.get(ROUTE, headers=world.as_("alice")).json()["dashboards"] == []

    @pytest.mark.parametrize(
        ("changes", "field"),
        [
            ({"name": ""}, "name"),
            ({"name": "a\nb"}, "name"),
            ({"scope": "node"}, "scope"),
            ({"layout": {"version": 2, "widgets": []}}, "layout.version"),
            ({"layout": {"version": 1, "widgets": [], "extra": 1}}, "layout.extra"),
        ],
    )
    def test_each_field_is_judged_and_named(
        self, world: World, changes: dict[str, Any], field: str
    ) -> None:
        response = world.client.put(
            f"{ROUTE}/main", json=body(**changes), headers=world.as_("alice")
        )

        assert response.status_code == 400
        assert field in response.json()["fields"]

    @pytest.mark.parametrize("dashboard", ["Main", "a_b", "order", "x" * 41, "a.b"])
    def test_an_id_that_is_not_valid_is_refused(self, world: World, dashboard: str) -> None:
        response = world.client.put(f"{ROUTE}/{dashboard}", json=body(), headers=world.as_("alice"))

        assert response.status_code == 400, response.text
        assert world.client.get(ROUTE, headers=world.as_("alice")).json()["dashboards"] == []

    def test_a_body_that_leaves_a_field_out_is_refused(self, world: World) -> None:
        response = world.client.put(
            f"{ROUTE}/main", json={"name": "x", "scope": "server"}, headers=world.as_("alice")
        )

        assert response.status_code == 422

    def test_twenty_panels_and_not_a_twenty_first(self, world: World) -> None:
        for number in range(20):
            made = world.client.put(f"{ROUTE}/p{number}", json=body(), headers=world.as_("alice"))
            assert made.status_code == 200, made.text

        over = world.client.put(f"{ROUTE}/more", json=body(), headers=world.as_("alice"))
        still = world.client.put(f"{ROUTE}/p3", json=body(name="Fine"), headers=world.as_("alice"))

        assert over.status_code == 400 and "20" in over.json()["detail"]
        assert still.status_code == 200
        assert len(world.client.get(ROUTE, headers=world.as_("alice")).json()["dashboards"]) == 20

    def test_a_document_past_the_size_is_refused_by_the_server_whatever_the_client_thinks(
        self, world: World
    ) -> None:
        settings = {f"k{i}": "x" * 190 for i in range(4)}
        widgets = [
            {"id": f"w{i}", "type": "t", "x": 0, "y": i, "w": 1, "h": 1, "settings": settings}
            for i in range(40)
        ]

        response = world.client.put(
            f"{ROUTE}/big",
            json=body(layout={"version": 1, "widgets": widgets}),
            headers=world.as_("alice"),
        )

        assert response.status_code == 400 and "layout" in response.json()["fields"]


class TestDeletingAndOrdering:
    def test_a_panel_is_deleted_and_the_rest_stay(self, world: World) -> None:
        for name in ("a", "b", "c"):
            world.client.put(f"{ROUTE}/{name}", json=body(name=name), headers=world.as_("alice"))

        response = world.client.delete(f"{ROUTE}/b", headers=world.as_("alice"))

        assert response.status_code == 200
        assert [p["id"] for p in response.json()["dashboards"]] == ["a", "c"]

    def test_a_panel_that_is_not_there_is_a_404_and_one_of_another_account_is_not_there(
        self, world: World
    ) -> None:
        world.client.put(f"{ROUTE}/main", json=body(), headers=world.as_("alice"))

        missing = world.client.delete(f"{ROUTE}/nowhere", headers=world.as_("alice"))
        theirs = world.client.delete(f"{ROUTE}/main", headers=world.as_("dana"))

        assert missing.status_code == theirs.status_code == 404
        assert missing.json()["error"] == theirs.json()["error"] == "not_found"
        assert len(world.client.get(ROUTE, headers=world.as_("alice")).json()["dashboards"]) == 1

    def test_the_order_given_is_the_order_kept(self, world: World) -> None:
        for name in ("a", "b", "c"):
            world.client.put(f"{ROUTE}/{name}", json=body(name=name), headers=world.as_("alice"))

        response = world.client.post(
            f"{ROUTE}/order", json={"order": ["c", "a", "b"]}, headers=world.as_("alice")
        )

        assert response.status_code == 200
        assert [p["id"] for p in response.json()["dashboards"]] == ["c", "a", "b"]
        listed = world.client.get(ROUTE, headers=world.as_("alice")).json()["dashboards"]
        assert [p["id"] for p in listed] == ["c", "a", "b"]

    @pytest.mark.parametrize("order", [["a"], ["a", "b", "x"], ["a", "a", "b"], []])
    def test_an_order_that_does_not_name_every_panel_once_is_refused(
        self, world: World, order: list[str]
    ) -> None:
        for name in ("a", "b"):
            world.client.put(f"{ROUTE}/{name}", json=body(name=name), headers=world.as_("alice"))

        response = world.client.post(
            f"{ROUTE}/order", json={"order": order}, headers=world.as_("alice")
        )

        assert response.status_code == 400 and "order" in response.json()["fields"]
        listed = world.client.get(ROUTE, headers=world.as_("alice")).json()["dashboards"]
        assert [p["id"] for p in listed] == ["a", "b"]

    def test_the_panels_of_another_account_cannot_be_ordered(self, world: World) -> None:
        world.client.put(f"{ROUTE}/a", json=body(), headers=world.as_("alice"))

        response = world.client.post(
            f"{ROUTE}/order", json={"order": ["a"]}, headers=world.as_("dana")
        )

        assert response.status_code == 400


class TestTheRoutesAreClassified:
    def test_each_declares_a_permission_and_a_scope(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.registry import permission_for_route
        from noust.web.permissions.scopes import OPEN, rule_for

        for method, template in (
            ("GET", ROUTE),
            ("PUT", f"{ROUTE}/{{dashboard_id}}"),
            ("DELETE", f"{ROUTE}/{{dashboard_id}}"),
            ("POST", f"{ROUTE}/order"),
        ):
            assert permission_for_route(method, template) == Permission.SELF
            rule = rule_for(method, template)
            assert rule is not None and rule.scoping == OPEN
