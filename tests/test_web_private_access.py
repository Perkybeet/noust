# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A private network over the API and the command line.

What a private site is and how it comes out is pinned in ``tests/test_private_access.py``. Here: who
may see and change it (an application's team does, another team does not see it at all, and the
certificate that joins the server's own is the server's), that the caller's address goes to the
operation as the operator's own, and that the flags become the same call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.deployers import private_access
from noust.managers.private_networks import PrivateNetwork, PrivateNetworks
from tests.teams_support import World, build_world

APP = "shop.acme.example"
TAILNET = PrivateNetwork(
    name="tailscale",
    kind="tailscale",
    interface="tailscale0",
    cidrs=("100.64.0.0/10", "fd7a:115c:a1e0::/48"),
    addresses=("100.101.102.103",),
    up=True,
    dns_name="web.tail1234.ts.net",
    tailnet="example.org",
    cert_domains=("web.tail1234.ts.net",),
)


def status(**changes: Any) -> private_access.PrivateStatus:
    fields: dict[str, Any] = {
        "app": APP,
        "private": False,
        "networks": (),
        "allow_ips": (),
        "public_ranges": (),
        "applicable": True,
        "reason": "",
        "available": (TAILNET,),
        "tailnet_name": TAILNET.dns_name,
        "tailnet_certificate": "",
    }
    fields.update(changes)
    return private_access.PrivateStatus(**fields)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


# -- the API -----------------------------------------------------------------------


def test_the_networks_are_listed_for_anybody_who_can_read_applications(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(private_access, "networks", lambda runner=None: [TAILNET])

    response = world.client.get("/api/private-networks", headers=world.as_("alice"))

    assert response.status_code == 200, response.text
    (network,) = response.json()["networks"]
    assert (network["name"], network["kind"], network["up"]) == ("tailscale", "tailscale", True)
    assert network["cert_domains"] == ["web.tail1234.ts.net"]


def test_an_application_says_whether_it_is_private_and_where_it_could_be(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        private_access,
        "status",
        lambda domain, **k: status(
            private=True,
            networks=("tailscale",),
            allow_ips=TAILNET.cidrs,
            tailnet_certificate="tailnet-web.tail1234.ts.net",
        ),
    )

    response = world.client.get(f"/api/apps/{APP}/private", headers=world.as_("alice"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["private"] is True and body["networks"] == ["tailscale"]
    assert body["available"][0]["name"] == "tailscale"
    assert body["tailnet_certificate"] == "tailnet-web.tail1234.ts.net"


def test_another_teams_application_is_not_there_to_ask_about(world: World) -> None:
    for method, path in (
        ("get", f"/api/apps/{APP}/private"),
        ("put", f"/api/apps/{APP}/private"),
        ("delete", f"/api/apps/{APP}/private"),
    ):
        response = getattr(world.client, method)(path, headers=world.as_("bob"))
        assert response.status_code == 404, (method, response.text)


def test_the_members_of_the_team_make_it_private_and_their_address_is_the_operators(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def enable(domain: str, **kwargs: Any) -> private_access.PrivateStatus:
        seen.append({"domain": domain, **kwargs})
        return status(private=True, networks=("tailscale",), allow_ips=TAILNET.cidrs)

    monkeypatch.setattr(private_access, "enable", enable)

    response = world.client.put(
        f"/api/apps/{APP}/private",
        json={"network": "tailscale", "cidrs": ["10.8.0.0/24"]},
        headers=world.as_("alice"),
    )

    assert response.status_code == 200, response.text
    (call,) = seen
    assert call["domain"] == APP and call["network"] == "tailscale"
    assert call["cidrs"] == ["10.8.0.0/24"] and call["accept_lockout"] is False
    assert call["operator_addresses"] == ["testclient"]


def test_a_viewer_reads_but_does_not_change(world: World) -> None:
    response = world.client.put(
        f"/api/apps/{APP}/private", json={"network": "tailscale"}, headers=world.as_("dana")
    )

    assert response.status_code == 403


def test_a_refusal_of_the_operation_is_a_400_in_its_own_words(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(domain: str, **kwargs: Any) -> Any:
        raise ValidationError("203.0.113.0/24 is a public network", field="cidrs")

    monkeypatch.setattr(private_access, "enable", refuse)

    response = world.client.put(
        f"/api/apps/{APP}/private", json={"cidrs": ["203.0.113.0/24"]}, headers=world.as_("alice")
    )

    assert response.status_code in (400, 422)
    assert "public network" in response.text


def test_making_it_public_again_goes_through_the_same_operation(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(private_access, "disable", lambda domain, **k: seen.append(k) or status())

    response = world.client.delete(
        f"/api/apps/{APP}/private?accept_lockout=true", headers=world.as_("alice")
    )

    assert response.status_code == 200, response.text
    assert seen[0]["accept_lockout"] is True and seen[0]["operator_addresses"] == ["testclient"]


def test_the_tailnets_certificate_joins_the_servers_and_a_team_may_not_ask_for_it(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    issued: list[str] = []

    def certificate(domain: str, **kwargs: Any) -> private_access.TailnetCertificate:
        issued.append(domain)
        return private_access.TailnetCertificate(
            name="tailnet-web.tail1234.ts.net",
            hostname="web.tail1234.ts.net",
            not_after="2027-01-01T00:00:00+00:00",
            applications=(domain,),
        )

    monkeypatch.setattr(private_access, "tailnet_certificate", certificate)

    refused = world.client.post(f"/api/apps/{APP}/private/certificate", headers=world.as_("alice"))
    allowed = world.client.post(f"/api/apps/{APP}/private/certificate", headers=world.as_("admin"))

    assert refused.status_code == 403
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["hostname"] == "web.tail1234.ts.net"
    assert issued == [APP]


# -- the command line --------------------------------------------------------------


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(domain: str, **kwargs: Any) -> private_access.PrivateStatus:
        calls.append({"domain": domain, **kwargs})
        return status(private=True, networks=("tailscale",), allow_ips=TAILNET.cidrs)

    monkeypatch.setattr(private_access, "enable", fake)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    return calls


def invoke(*args: str) -> Any:
    return CliRunner().invoke(root_cli, ["app", "policy", "private", *args])


def test_the_networks_are_listed_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PrivateNetworks, "discover", lambda self: [TAILNET])

    result = invoke("networks", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["dns_name"] == "web.tail1234.ts.net"


def test_the_flags_become_the_operation_and_the_ssh_address_is_the_operators(
    enabled: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.9 50000 192.0.2.1 22")

    result = invoke(
        "enable", "shop.example.com", "--network", "tailscale", "--cidr", "10.8.0.0/24",
        "--accept-lockout",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    (call,) = enabled
    assert call["domain"] == "shop.example.com" and call["network"] == "tailscale"
    assert call["cidrs"] == ("10.8.0.0/24",) and call["accept_lockout"] is True
    assert call["operator_addresses"] == ["203.0.113.9"]


def test_a_rehearsal_changes_nothing(enabled: list[dict[str, Any]]) -> None:
    result = CliRunner().invoke(
        root_cli,
        ["--dry-run", "app", "policy", "private", "enable", "shop.example.com", "--network", "x"],
    )

    assert result.exit_code == 0, result.output
    assert enabled == []


def test_the_answer_is_shown_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        private_access,
        "status",
        lambda domain, **k: status(private=True, networks=("tailscale",), allow_ips=TAILNET.cidrs),
    )

    result = invoke("show", "shop.example.com", "--json")

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["private"] is True and body["networks"] == ["tailscale"]
