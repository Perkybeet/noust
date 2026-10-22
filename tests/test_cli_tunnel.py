# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust tunnel``.

The manager decides (``tests/test_tunnels.py``). Pinned here is the translation: the flags that
become the request, what is said for each outcome of a route's DNS (and the command that
confirms replacing a conflicting record), a removal that asks first, and a refusal that is
exit code 1 with the operation's own words.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.cli.commands import tunnel as tunnel_cli
from noust.core.exceptions import PrerequisiteError
from noust.core.logger import Logger
from noust.core.tunnel_store import Tunnel, TunnelRoute
from noust.managers.tunnels import RouteResult, TunnelResult, TunnelSupport

TUNNEL = Tunnel("home", "cf", "acc-1", "tun-1", "noust-tunnel-home", "ready")
ROUTE = TunnelRoute("home", "shop.example.com", "http://localhost:80", "shop.example.com")


class Fake:
    """A manager that records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.dns: dict[str, Any] = {"outcome": "created", "content": "tun-1.cfargotunnel.com"}
        self.refusal: Exception | None = None
        self.records = self
        self.support_answer = TunnelSupport(True, "/usr/bin/cloudflared", False, ("cf",))
        self.rehearsal = False

    def routes(self, name: str) -> list[TunnelRoute]:
        return [ROUTE]

    def all(self) -> list[Tunnel]:
        return [TUNNEL]

    def active(self, tunnel: Tunnel) -> str:
        return "active"

    def support(self) -> TunnelSupport:
        return self.support_answer

    def create(self, *args: Any, **kwargs: Any) -> TunnelResult:
        self.calls.append(("create", args, kwargs))
        if self.refusal is not None:
            raise self.refusal
        return TunnelResult(TUNNEL, rehearsal=self.rehearsal)

    def add_route(self, *args: Any, **kwargs: Any) -> RouteResult:
        self.calls.append(("add_route", args, kwargs))
        return RouteResult(TUNNEL, ROUTE, ({"service": "http_status:404"},), self.dns)

    def remove_route(self, *args: Any, **kwargs: Any) -> RouteResult:
        self.calls.append(("remove_route", args, kwargs))
        outcome = "skipped" if kwargs.get("keep_dns") else "deleted"
        return RouteResult(TUNNEL, ROUTE, (), {"outcome": outcome})

    def delete(self, *args: Any, **kwargs: Any) -> TunnelResult:
        self.calls.append(("delete", args, kwargs))
        return TunnelResult(TUNNEL, ("a record stayed",), rehearsal=self.rehearsal)

    def repair(self) -> list[str]:
        self.calls.append(("repair", (), {}))
        return ["half"]


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> Fake:
    fake = Fake()
    monkeypatch.setattr(tunnel_cli, "TunnelManager", lambda: fake)
    return fake


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def invoke(args: list[str], input_text: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, args, input=input_text)


def test_creating_names_the_account_and_says_how_to_route(manager: Fake, log: list[str]) -> None:
    result = invoke(
        ["tunnel", "create", "home", "--account", "cf", "--cloudflare-account", "acc-9"]
    )

    assert result.exit_code == 0, result.output
    name, args, kwargs = manager.calls[-1]
    assert (name, args) == ("create", ("home",))
    assert kwargs["account"] == "cf" and kwargs["cloudflare_account"] == "acc-9"
    assert kwargs["actor"]
    assert any("noust tunnel route add home HOSTNAME" in line for line in log)


def test_a_server_that_cannot_run_one_is_told_what_to_install(
    manager: Fake, log: list[str]
) -> None:
    manager.refusal = PrerequisiteError(
        "cloudflared is not installed", details="Install it from https://pkg.cloudflare.com/"
    )

    assert main(["tunnel", "create", "home"]) == 1
    assert any("https://pkg.cloudflare.com/" in line for line in log)


def test_the_check_says_what_is_missing_or_that_all_is_there(manager: Fake, log: list[str]) -> None:
    assert invoke(["tunnel", "check"]).exit_code == 0
    assert any("A tunnel can run here" in line for line in log)

    manager.support_answer = TunnelSupport(
        False, None, False, (), "cloudflared is not installed", "Install it"
    )
    result = invoke(["tunnel", "check", "--json"])

    assert json.loads(result.output)["problem"] == "cloudflared is not installed"


def test_the_list_shows_the_connector_and_what_each_tunnel_carries(manager: Fake) -> None:
    result = invoke(["tunnel", "list", "--json"])

    (row,) = json.loads(result.output)["tunnels"]
    assert row["active"] == "active" and row["routes"][0]["hostname"] == "shop.example.com"
    assert "token" not in result.output


def test_a_route_becomes_the_request(manager: Fake, log: list[str]) -> None:
    result = invoke(
        [
            "tunnel",
            "route",
            "add",
            "home",
            "shop.example.com",
            "--app",
            "shop.example.com",
            "--no-dns",
        ]
    )

    assert result.exit_code == 0, result.output
    name, args, kwargs = manager.calls[-1]
    assert (name, args) == ("add_route", ("home", "shop.example.com"))
    assert kwargs["app_domain"] == "shop.example.com" and kwargs["dns"] is False
    assert kwargs["service"] is None


def test_a_service_can_be_named_instead_of_an_application(manager: Fake) -> None:
    invoke(
        ["tunnel", "route", "add", "home", "x.example.com", "--service", "http://localhost:3001"]
    )

    _, _, kwargs = manager.calls[-1]
    assert kwargs["service"] == "http://localhost:3001" and kwargs["app_domain"] is None


def test_a_conflicting_record_is_named_with_the_command_that_replaces_it(
    manager: Fake, log: list[str]
) -> None:
    manager.dns = {
        "outcome": "conflict",
        "record_id": "rec-7",
        "current": "elsewhere.example.net",
        "detail": "x.example.com already has a record",
    }

    invoke(["tunnel", "route", "add", "home", "shop.example.com", "--app", "shop.example.com"])

    text = " ".join(log)
    assert "--replace-record rec-7 --replace-current elsewhere.example.net" in text


def test_a_name_in_a_zone_nobody_holds_is_told_the_record_to_make(
    manager: Fake, log: list[str]
) -> None:
    manager.dns = {
        "outcome": "manual",
        "detail": "no account holds the zone",
        "content": "tun-1.cfargotunnel.com",
    }

    invoke(
        ["tunnel", "route", "add", "home", "shop.example.com", "--service", "http://localhost:1"]
    )

    text = " ".join(log)
    assert "CNAME shop.example.com -> tun-1.cfargotunnel.com" in text and "proxied" in text


def test_the_confirmation_of_a_record_is_passed_through(manager: Fake) -> None:
    invoke(
        [
            "tunnel",
            "route",
            "add",
            "home",
            "shop.example.com",
            "--service",
            "http://localhost:1",
            "--replace-record",
            "rec-7",
            "--replace-current",
            "elsewhere.example.net",
        ]
    )

    _, _, kwargs = manager.calls[-1]
    assert kwargs["replace_record"] == "rec-7"
    assert kwargs["replace_current"] == "elsewhere.example.net"


def test_removing_a_route_can_leave_the_dns_alone(manager: Fake, log: list[str]) -> None:
    result = invoke(["tunnel", "route", "remove", "home", "shop.example.com", "--keep-dns"])

    assert result.exit_code == 0, result.output
    name, args, kwargs = manager.calls[-1]
    assert (name, args) == ("remove_route", ("home", "shop.example.com"))
    assert kwargs["keep_dns"] is True
    assert not any("its record" in line for line in log)

    invoke(["tunnel", "route", "remove", "home", "shop.example.com"])

    assert any("its record was deleted" in line for line in log)


def test_deleting_a_tunnel_asks_unless_told_not_to(manager: Fake, log: list[str]) -> None:
    declined = invoke(["tunnel", "remove", "home"], input_text="n\n")
    assert declined.exit_code == 0
    assert manager.calls == []

    confirmed = invoke(["tunnel", "remove", "home", "--yes"])

    assert confirmed.exit_code == 0, confirmed.output
    assert [call[0] for call in manager.calls] == ["delete"]
    assert any("a record stayed" in line for line in log)


def test_repair_names_what_it_finished(manager: Fake, log: list[str]) -> None:
    result = invoke(["tunnel", "repair"])

    assert result.exit_code == 0, result.output
    assert any("Put right: half" in line for line in log)
