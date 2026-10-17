# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Forward-auth over the API and the command line.

What the settings are and how they are rendered is pinned in ``tests/test_forward_auth.py``. Here:
that the address nginx will call is the server's to name (a member of a team may not), how the
request is translated, that the presets are served from the one table the command line reads, and
that the flags become the same change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from noust.deployers import access
from noust.managers.site_forward_auth import PRESETS
from noust.managers.site_policy import SitePolicy, validated
from tests.teams_support import World, build_world

APP = "shop.acme.example"
FORWARD = {
    "url": "http://127.0.0.1:4180/oauth2/auth",
    "sign_in": "/oauth2/start?rd=",
    "headers": ["X-Auth-Request-User"],
}


def view(**changes: Any) -> access.AccessView:
    return access.AccessView(
        app=APP, policy=validated(SitePolicy(), changes), users=(), webserver="nginx"
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def put(world: World, who: str, body: dict[str, Any]) -> Any:
    return world.client.put(f"/api/apps/{APP}/policy", json=body, headers=world.as_(who))


def test_a_member_of_a_team_may_not_name_the_address_nginx_will_call(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    reached: list[Any] = []
    monkeypatch.setattr(access, "set_policy", lambda *a, **k: reached.append(a) or view())

    response = put(world, "alice", {"forward_auth": FORWARD})

    assert response.status_code == 403
    assert "instance_scope_required" in response.text
    assert reached == []


def test_the_server_administrator_may_and_the_change_carries_what_was_sent(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Any, dict[str, Any]]] = []

    def fake(domain: str, changes: dict[str, Any], **kwargs: Any) -> access.AccessView:
        seen.append((changes, kwargs))
        return view(forward_auth=changes["forward_auth"])

    monkeypatch.setattr(access, "set_policy", fake)

    response = put(world, "admin", {"forward_auth": FORWARD, "accept_unreachable_auth": True})

    assert response.status_code == 200, response.text
    ((changes, kwargs),) = seen
    assert changes == {"forward_auth": FORWARD}
    assert kwargs["accept_unreachable_auth"] is True
    answered = response.json()["policy"]["forward_auth"]
    assert answered == FORWARD


def test_an_empty_address_turns_it_off_and_a_request_without_the_field_leaves_it(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        access, "set_policy", lambda d, changes, **k: seen.append(changes) or view()
    )

    put(world, "admin", {"forward_auth": {"url": ""}})
    put(world, "alice", {"noindex": True})

    assert seen[0] == {"forward_auth": {"url": "", "sign_in": "", "headers": []}}
    assert "forward_auth" not in seen[1]


def test_the_presets_are_served_from_the_table_the_command_line_reads(world: World) -> None:
    response = world.client.get("/api/policy/forward-auth-presets", headers=world.as_("admin"))

    assert response.status_code == 200, response.text
    served = {item["name"]: item for item in response.json()["presets"]}
    assert set(served) == set(PRESETS)
    assert served["oauth2-proxy"]["url"] == PRESETS["oauth2-proxy"]["url"]
    assert served["authentik"]["headers"] == PRESETS["authentik"]["headers"]


# -- the command line -------------------------------------------------------------


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def fake(domain: str, changes: dict[str, Any], **kwargs: Any) -> access.AccessView:
        calls.append((changes, kwargs))
        return view()

    monkeypatch.setattr(access, "set_policy", fake)
    monkeypatch.setattr(access, "describe", lambda *a, **k: view())
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    return calls


def invoke(*args: str) -> Any:
    return CliRunner().invoke(root_cli, ["app", "policy", "set", "example.com", *args])


def test_a_preset_becomes_the_services_defaults(asked: list[Any]) -> None:
    result = invoke("--forward-auth-preset", "oauth2-proxy")

    assert result.exit_code == 0, result.output
    changes, _ = asked[0]
    assert changes["forward_auth"] == {
        "url": PRESETS["oauth2-proxy"]["url"],
        "sign_in": PRESETS["oauth2-proxy"]["sign_in"],
        "headers": PRESETS["oauth2-proxy"]["headers"],
    }


def test_a_flag_overrides_what_the_preset_says(asked: list[Any]) -> None:
    result = invoke(
        "--forward-auth-preset",
        "authentik",
        "--forward-auth-url",
        "http://127.0.0.1:9100/outpost.goauthentik.io/auth/nginx",
        "--forward-auth-header",
        "X-authentik-username",
        "--accept-unreachable-auth",
    )

    assert result.exit_code == 0, result.output
    changes, kwargs = asked[0]
    assert changes["forward_auth"]["url"].startswith("http://127.0.0.1:9100/")
    assert changes["forward_auth"]["headers"] == ["X-authentik-username"]
    assert changes["forward_auth"]["sign_in"] == PRESETS["authentik"]["sign_in"]
    assert kwargs["accept_unreachable_auth"] is True


def test_an_address_of_ones_own_needs_no_preset(asked: list[Any]) -> None:
    invoke(
        "--forward-auth-url",
        "https://auth.example.org/verify",
        "--forward-auth-sign-in",
        "https://auth.example.org/?rd=",
    )

    changes, _ = asked[0]
    assert changes["forward_auth"] == {
        "url": "https://auth.example.org/verify",
        "sign_in": "https://auth.example.org/?rd=",
        "headers": [],
    }


def test_it_is_turned_off_with_its_own_flag(asked: list[Any]) -> None:
    invoke("--no-forward-auth")

    assert asked[0][0] == {"forward_auth": None}


def test_a_sign_in_alone_names_no_service(asked: list[Any]) -> None:
    result = invoke("--forward-auth-sign-in", "/oauth2/start?rd=")

    assert result.exit_code == 2
    assert asked == []


def test_turning_off_and_naming_one_at_once_is_a_usage_error(asked: list[Any]) -> None:
    result = invoke("--no-forward-auth", "--forward-auth-url", "http://127.0.0.1:4180/a")

    assert result.exit_code == 2
    assert asked == []
