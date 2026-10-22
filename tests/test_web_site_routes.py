# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The routes of a site over the API: how they are sent, answered and held to a caller's teams.

A route that proxies a prefix to a local port can reach anything listening on the
machine, so a caller that sees only some teams may name only the ports of the
applications it sees (the same refusal a server-wide route gives). The rules of a
route and their rendering are pinned in ``tests/test_site_routes.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, NoustStore
from noust.deployers import access
from noust.managers.site_policy import SitePolicy, validated
from noust.web.api import site_policy as policy_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth
from tests.teams_support import World, build_world

ROUTES = [
    {"path": "/admin", "auth": "password"},
    {"path": "/api", "kind": "proxy", "port": 4000, "strip": True},
    {"path": "/docs", "kind": "redirect", "to": "https://docs.example.org/", "code": 308},
]


def _view(**changes: Any) -> access.AccessView:
    return access.AccessView(
        app="example.com",
        policy=validated(SitePolicy(), changes),
        users=("ana",),
        webserver="nginx",
    )


@pytest.fixture
def client(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    store.create_app(App(domain="example.com", app_path="/var/www/apps/example.com", port=3000))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(policy_api.router, prefix="/api/apps")
    app.dependency_overrides[require_auth] = lambda: {
        "sid": "token",
        "scope": "admin",
        "source": "bearer",
        "type": "api_token",
    }
    yield TestClient(app)
    NoustStore.reset_instance()


def test_routes_are_sent_whole_and_answered_with_every_field(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def fake(domain: str, changes: dict[str, Any], **kwargs: Any) -> access.AccessView:
        seen.append(changes)
        return _view(routes=changes["routes"])

    monkeypatch.setattr(access, "set_policy", fake)

    response = client.put("/api/apps/example.com/policy", json={"routes": ROUTES})

    assert response.status_code == 200, response.text
    (changes,) = seen
    assert [route["path"] for route in changes["routes"]] == ["/admin", "/api", "/docs"]
    assert changes["routes"][1]["port"] == 4000
    answered = response.json()["policy"]["routes"]
    assert {route["path"] for route in answered} == {"/admin", "/api", "/docs"}
    api = next(route for route in answered if route["path"] == "/api")
    assert (api["kind"], api["strip"], api["port"], api["auth"]) == ("proxy", True, 4000, "inherit")


def test_a_request_without_routes_does_not_touch_them(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        access, "set_policy", lambda domain, changes, **kw: seen.append(changes) or _view()
    )

    client.put("/api/apps/example.com/policy", json={"noindex": True})

    assert seen == [{"noindex": True}]


def test_an_empty_list_clears_them(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        access, "set_policy", lambda domain, changes, **kw: seen.append(changes) or _view()
    )

    client.put("/api/apps/example.com/policy", json={"routes": []})

    assert seen == [{"routes": []}]


# -- teams ------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def _put(world: World, who: str, routes: list[dict[str, Any]]) -> Any:
    return world.client.put(
        "/api/apps/shop.acme.example/policy",
        json={"routes": routes},
        headers=world.as_(who),
    )


def test_a_team_member_may_hand_a_prefix_to_an_application_it_sees(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "set_policy", lambda *a, **k: _view())

    response = _put(world, "alice", [{"path": "/api", "kind": "proxy", "port": 3000}])

    assert response.status_code == 200, response.text


def test_a_team_member_may_not_hand_a_prefix_to_a_port_no_visible_application_has(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    reached: list[Any] = []
    monkeypatch.setattr(access, "set_policy", lambda *a, **k: reached.append(a) or _view())

    response = _put(world, "alice", [{"path": "/api", "kind": "proxy", "port": 8080}])

    assert response.status_code == 403
    assert "instance_scope_required" in response.text
    assert reached == []


def test_the_server_administrator_may_name_any_port(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "set_policy", lambda *a, **k: _view())

    response = _put(world, "admin", [{"path": "/api", "kind": "proxy", "port": 8080}])

    assert response.status_code == 200, response.text


def test_a_redirect_or_a_password_route_is_a_teams_own(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "set_policy", lambda *a, **k: _view())

    response = _put(
        world,
        "alice",
        [{"path": "/admin", "auth": "password"}, {"path": "/o", "kind": "redirect", "to": "/n"}],
    )

    assert response.status_code == 200, response.text
