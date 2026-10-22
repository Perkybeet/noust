# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust app policy route``.

What a route is and how it is rendered is pinned in ``tests/test_site_routes.py``. Pinned here
is the translation: a flag becomes a field of the route, a route at a prefix that already has
one replaces it, the others stay, and a refusal is exit code 1 in the operation's own words.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.deployers import access
from noust.managers.site_policy import SitePolicy, validated

APP = "example.com"


def view(*routes: dict[str, Any]) -> access.AccessView:
    return access.AccessView(
        app=APP,
        policy=validated(SitePolicy(), {"routes": list(routes)}),
        users=("ana",),
        webserver="nginx",
    )


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


@pytest.fixture
def current(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The routes the application has, which a test may fill; set_policy records what is asked."""
    routes: list[dict[str, Any]] = []
    monkeypatch.setattr(access, "describe", lambda *a, **k: view(*routes))
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    return routes


@pytest.fixture
def set_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    asked: list[dict[str, Any]] = []

    def fake(domain: str, changes: dict[str, Any], **kwargs: Any) -> access.AccessView:
        asked.append(changes)
        return view(*changes["routes"])

    monkeypatch.setattr(access, "set_policy", fake)
    return asked


def invoke(args: list[str]) -> Result:
    return CliRunner().invoke(root_cli, ["app", "policy", "route", *args])


def test_a_prefix_is_given_a_password_and_a_header(
    current: list[Any], set_calls: list[Any], log: list[str]
) -> None:
    result = invoke(["add", APP, "/admin", "--password", "--header", "X-Robots-Tag: noindex"])

    assert result.exit_code == 0, result.output
    (route,) = set_calls[0]["routes"]
    assert (route["path"], route["kind"], route["auth"]) == ("/admin", "site", "password")
    assert route["headers"] == {"X-Robots-Tag": "noindex"}


def test_a_prefix_is_opened(current: list[Any], set_calls: list[Any]) -> None:
    result = invoke(["add", APP, "/hooks/", "--open"])

    assert result.exit_code == 0, result.output
    assert set_calls[0]["routes"][0]["path"] == "/hooks"
    assert set_calls[0]["routes"][0]["auth"] == "open"


def test_a_prefix_is_handed_to_a_port_with_or_without_its_prefix(
    current: list[Any], set_calls: list[Any]
) -> None:
    invoke(["add", APP, "/api", "--proxy-port", "4000", "--strip"])
    invoke(["add", APP, "/blog", "--proxy-port", "5000"])

    api, blog = (calls["routes"][-1] for calls in set_calls)
    assert (api["kind"], api["port"], api["strip"]) == ("proxy", 4000, True)
    assert (blog["port"], blog["strip"]) == (5000, False)


def test_a_prefix_is_redirected(current: list[Any], set_calls: list[Any]) -> None:
    result = invoke(
        [
            "add",
            APP,
            "/docs",
            "--redirect-to",
            "https://docs.example.org/",
            "--code",
            "308",
            "--drop-path",
        ]
    )

    assert result.exit_code == 0, result.output
    route = set_calls[0]["routes"][0]
    assert (route["kind"], route["to"], route["code"], route["keep_path"]) == (
        "redirect",
        "https://docs.example.org/",
        308,
        False,
    )


def test_a_route_at_the_same_prefix_replaces_the_old_and_the_others_stay(
    current: list[Any], set_calls: list[Any]
) -> None:
    current.append({"path": "/admin", "auth": "password"})
    current.append({"path": "/hooks", "auth": "open"})

    invoke(["add", APP, "/admin", "--open"])

    routes = {route["path"]: route for route in set_calls[0]["routes"]}
    assert set(routes) == {"/admin", "/hooks"}
    assert routes["/admin"]["auth"] == "open"


def test_a_prefix_cannot_be_both_handed_over_and_redirected(
    current: list[Any], set_calls: list[Any]
) -> None:
    result = invoke(["add", APP, "/x", "--proxy-port", "4000", "--redirect-to", "/y"])

    assert result.exit_code == 2
    assert set_calls == []


def test_a_header_without_a_colon_is_a_usage_error(
    current: list[Any], set_calls: list[Any]
) -> None:
    result = invoke(["add", APP, "/x", "--header", "X-A"])

    assert result.exit_code == 2
    assert set_calls == []


def test_a_route_the_rules_refuse_is_exit_one_with_the_reason(
    current: list[Any], set_calls: list[Any], log: list[str]
) -> None:
    assert main(["app", "policy", "route", "add", APP, "/x y", "--open"]) == 1
    assert any("not a path to route" in line for line in log)


def test_a_route_is_removed_and_a_missing_one_is_said(
    current: list[Any], set_calls: list[Any], log: list[str]
) -> None:
    current.extend([{"path": "/admin", "auth": "password"}, {"path": "/hooks", "auth": "open"}])

    done = invoke(["remove", APP, "/admin"])

    assert done.exit_code == 0, done.output
    assert [route["path"] for route in set_calls[0]["routes"]] == ["/hooks"]
    assert main(["app", "policy", "route", "remove", APP, "/nope"]) == 1
    assert any("is not a route" in line for line in log)


def test_the_routes_are_listed_as_a_table_and_as_json(current: list[Any]) -> None:
    current.extend(
        [
            {"path": "/api", "kind": "proxy", "port": 4000, "strip": True},
            {"path": "/docs", "kind": "redirect", "to": "/new", "code": 302},
        ]
    )

    plain = invoke(["list", APP])
    as_json = invoke(["list", APP, "--json"])

    assert plain.exit_code == 0 and "/api" in plain.output
    assert [route["path"] for route in json.loads(as_json.output)] == ["/docs", "/api"] or [
        route["path"] for route in json.loads(as_json.output)
    ] == ["/api", "/docs"]


def test_a_rehearsal_changes_nothing(current: list[Any], set_calls: list[Any]) -> None:
    result = CliRunner().invoke(
        root_cli, ["--dry-run", "app", "policy", "route", "add", APP, "/admin", "--password"]
    )

    assert result.exit_code == 0, result.output
    assert set_calls == []


def test_a_refusal_from_the_operation_is_exit_one(
    current: list[Any], monkeypatch: pytest.MonkeyPatch, log: list[str]
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise ValidationError("api: port 22 is SSH's", field="routes")

    monkeypatch.setattr(access, "set_policy", refuse)

    assert main(["app", "policy", "route", "add", APP, "/api", "--proxy-port", "22"]) == 1
    assert any("SSH's" in line for line in log)
