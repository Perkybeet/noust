# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``/api/tunnels``.

The rules of tunnels are pinned in ``tests/test_tunnels.py``. What this module owns: that no
token is in any response, that creating, routing and deleting ask for sudo mode while looking
does not, that a conflicting record is an outcome the console can confirm and not an error,
and the statuses the console branches on.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.records import ManagedDns
from noust.managers.tunnels import TunnelManager
from noust.web.api import tunnels as tunnels_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth
from tests.dns_support import FakeTunnelProvider

BEARER = {"sid": "token", "scope": "admin", "source": "bearer", "type": "api_token"}
COOKIE = {"sid": "s1", "scope": "admin", "source": "cookie", "type": "session"}
APP = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(App(domain=APP, app_path="/srv/shop", port=3000))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def provider() -> FakeTunnelProvider:
    return FakeTunnelProvider("example.com")


@pytest.fixture(autouse=True)
def wired(
    tmp_path: Path,
    store: NoustStore,
    provider: FakeTunnelProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    DnsStore(store).save_account(DnsAccount(name="cf", kind="cloudflare", automatic=True))
    monkeypatch.setattr(DnsAccounts, "provider", lambda self, name: provider)
    secrets = SecretStore(root=tmp_path / "state" / "secrets")
    systemd = tmp_path / "systemd"
    systemd.mkdir()
    runner = FakeRunner()
    runner.script(("systemctl", "is-active"), stdout="active\n")

    def manager() -> TunnelManager:
        accounts = DnsAccounts(store=store, secrets=secrets)
        return TunnelManager(
            accounts=accounts,
            store=store,
            runner=runner,
            secrets=secrets,
            systemd_dir=systemd,
            which=lambda program: "/usr/bin/cloudflared",
            dns=lambda: ManagedDns(accounts=accounts, store=store, label="srv"),
            serves_tls=lambda app: False,
        )

    monkeypatch.setattr(tunnels_api, "TunnelManager", manager)


def build(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(tunnels_api.router, prefix="/api/tunnels")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


@pytest.fixture
def client() -> TestClient:
    return build(BEARER)


def test_nothing_created_and_what_this_server_offers(client: TestClient) -> None:
    response = client.get("/api/tunnels")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tunnels"] == []
    assert body["support"]["available"] is True
    assert body["support"]["accounts"] == ["cf"]


def test_creating_a_tunnel_never_returns_its_token(
    client: TestClient, provider: FakeTunnelProvider
) -> None:
    response = client.post("/api/tunnels", json={"name": "home"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["tunnel"]["state"] == "ready"
    assert body["tunnel"]["target"] == "tun-1.cfargotunnel.com"
    assert body["tunnel"]["active"] == "active"
    assert provider.token not in response.text
    assert provider.token not in client.get("/api/tunnels").text


def test_routing_a_name_through_it_and_taking_it_away(
    client: TestClient, provider: FakeTunnelProvider
) -> None:
    client.post("/api/tunnels", json={"name": "home"})

    added = client.post("/api/tunnels/home/routes", json={"hostname": APP, "app_domain": APP})

    assert added.status_code == 201, added.text
    body = added.json()
    assert body["route"]["service"] == "http://localhost:80"
    assert body["dns"]["outcome"] == "created"
    assert [r.get("hostname") for r in body["ingress"]] == [APP, None]
    listed = client.get("/api/tunnels").json()["tunnels"][0]
    assert [r["hostname"] for r in listed["routes"]] == [APP]

    removed = client.delete(f"/api/tunnels/home/routes/{APP}")

    assert removed.status_code == 200, removed.text
    assert removed.json()["dns"]["outcome"] == "deleted"
    assert client.get("/api/tunnels").json()["tunnels"][0]["routes"] == []


def test_a_record_in_the_way_is_an_outcome_to_confirm_not_an_error(
    client: TestClient, provider: FakeTunnelProvider
) -> None:
    client.post("/api/tunnels", json={"name": "home"})
    other = provider.seed("example.com", APP, "CNAME", "elsewhere.example.net")

    first = client.post("/api/tunnels/home/routes", json={"hostname": APP, "app_domain": APP})

    assert first.status_code == 201, first.text
    dns = first.json()["dns"]
    assert dns["outcome"] == "conflict" and dns["record_id"] == other.id
    assert dns["current"] == "elsewhere.example.net"

    second = client.post(
        "/api/tunnels/home/routes",
        json={
            "hostname": APP,
            "app_domain": APP,
            "replace_record": dns["record_id"],
            "replace_current": dns["current"],
        },
    )

    assert second.json()["dns"]["outcome"] == "replaced"


def test_deleting_a_tunnel(client: TestClient, provider: FakeTunnelProvider) -> None:
    client.post("/api/tunnels", json={"name": "home"})
    client.post("/api/tunnels/home/routes", json={"hostname": APP, "app_domain": APP})

    response = client.delete("/api/tunnels/home")

    assert response.status_code == 200, response.text
    assert [item["outcome"] for item in response.json()["released"]] == ["deleted"]
    assert client.get("/api/tunnels").json()["tunnels"] == []
    assert provider.tunnels == {}


def test_creating_routing_and_deleting_ask_for_sudo_mode_and_looking_does_not() -> None:
    client = build(COOKIE)

    assert client.get("/api/tunnels").status_code == 200
    for response in (
        client.post("/api/tunnels", json={"name": "home"}),
        client.post("/api/tunnels/home/routes", json={"hostname": APP, "app_domain": APP}),
        client.delete(f"/api/tunnels/home/routes/{APP}"),
        client.delete("/api/tunnels/home"),
    ):
        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"


def test_a_server_without_the_connector_is_a_409_with_where_to_get_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tunnels_api.TunnelManager

    def without() -> TunnelManager:
        manager = original()
        manager._which = lambda program: None
        return manager

    monkeypatch.setattr(tunnels_api, "TunnelManager", without)

    response = client.post("/api/tunnels", json={"name": "home"})

    assert response.status_code == 409
    assert response.json()["detail"] == "cloudflared is not installed"
    assert "https://pkg.cloudflare.com/" in response.json()["hint"]


def test_an_unknown_tunnel_and_an_unknown_application_are_404s(client: TestClient) -> None:
    client.post("/api/tunnels", json={"name": "home"})

    assert client.delete("/api/tunnels/nope").status_code == 404
    assert (
        client.post(
            "/api/tunnels/nope/routes", json={"hostname": APP, "app_domain": APP}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/tunnels/home/routes", json={"hostname": APP, "app_domain": "ghost.example.com"}
        ).status_code
        == 404
    )


def test_a_service_a_tunnel_does_not_carry_is_a_400(client: TestClient) -> None:
    client.post("/api/tunnels", json={"name": "home"})

    response = client.post(
        "/api/tunnels/home/routes",
        json={"hostname": "x.example.com", "service": "ssh://localhost:22"},
    )

    assert response.status_code == 400
    assert "not a service a tunnel can reach" in response.json()["detail"]


def test_a_name_another_tunnel_carries_is_a_409(client: TestClient) -> None:
    client.post("/api/tunnels", json={"name": "home"})
    client.post("/api/tunnels", json={"name": "office"})
    client.post(
        "/api/tunnels/home/routes",
        json={"hostname": "x.example.com", "service": "http://localhost:1"},
    )

    response = client.post(
        "/api/tunnels/office/routes",
        json={"hostname": "x.example.com", "service": "http://localhost:1"},
    )

    assert response.status_code == 409
    assert json.loads(response.text)["detail"].endswith("carried by the tunnel home")
