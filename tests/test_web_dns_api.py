# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``/api/dns`` and ``/api/apps/{domain}/dns``.

The rules of ownership are pinned in ``tests/test_dns_records.py`` and the accounts in
``tests/test_dns_accounts.py``. What this module owns: that no credential is in any
response, that adding and removing an account ask for sudo mode, that a conflict is a
409 until the operator confirms the exact record, and the shapes the console reads.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.store import App, NoustStore
from noust.managers.dns import records as records_module
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.cloudflare import CloudflareClient
from noust.web.api import dns as dns_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth
from tests.dns_support import FakeProvider

TOKEN = "cf-token-" + "x" * 31
PUBLIC = "8.8.4.4"
BEARER = {"sid": "token", "scope": "admin", "source": "bearer", "type": "api_token"}
COOKIE = {"sid": "s1", "scope": "admin", "source": "cookie", "type": "session"}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="shop.example.com", app_path="/var/www/apps/shop"))
    yield instance
    NoustStore.reset_instance()


def build(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(dns_api.router, prefix="/api/dns")
    app.include_router(dns_api.apps_router, prefix="/api/apps")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


@pytest.fixture
def client(store: NoustStore) -> TestClient:
    return build(BEARER)


@pytest.fixture
def provider(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    fake = FakeProvider("example.com")
    DnsStore(store).save_account(DnsAccount(name="cf", kind="cloudflare", automatic=True))
    monkeypatch.setattr(DnsAccounts, "provider", lambda self, name: fake)
    monkeypatch.setattr(records_module, "server_addresses", lambda: [PUBLIC])
    monkeypatch.setattr(dns_api, "server_addresses", lambda: [PUBLIC])
    return fake


class Opener:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        return io.BytesIO(json.dumps(self.payload).encode())


def test_the_catalog_and_the_accounts_are_listed_without_any_credential(
    client: TestClient, provider: FakeProvider
) -> None:
    response = client.get("/api/dns/providers")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [a["name"] for a in body["accounts"]] == ["cf"]
    assert body["accounts"][0]["native"] is True and body["accounts"][0]["label"] == "Cloudflare"
    kinds = {k["kind"]: k for k in body["kinds"]}
    assert kinds["cloudflare"]["native"] is True
    assert [f["name"] for f in kinds["cloudflare"]["fields"]] == ["token", "account_id"]
    assert kinds["digitalocean"]["package"] == "python3-certbot-dns-digitalocean"
    assert "token" not in json.dumps(body["accounts"])


def test_adding_an_account_proves_the_token_and_never_echoes_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Opener(
        {
            "success": True,
            "result": [{"id": "z1", "name": "example.com"}],
            "result_info": {"total_pages": 1},
        }
    )
    monkeypatch.setattr(
        DnsAccounts, "_client", lambda self, token: CloudflareClient(token, opener=fake)
    )

    response = client.post(
        "/api/dns/providers",
        json={
            "name": "cf",
            "kind": "cloudflare",
            "credentials": {"token": TOKEN},
            "automatic": True,
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["automatic"] is True
    assert TOKEN not in response.text


def test_adding_and_removing_an_account_ask_for_sudo_mode(
    store: NoustStore, provider: FakeProvider
) -> None:
    cookie = build(COOKIE)

    adding = cookie.post(
        "/api/dns/providers",
        json={"name": "x", "kind": "cloudflare", "credentials": {"token": TOKEN}},
    )
    removing = cookie.delete("/api/dns/providers/cf")

    assert adding.status_code == 403 and adding.json()["error"] == "elevation_required"
    assert removing.status_code == 403 and removing.json()["error"] == "elevation_required"
    assert DnsStore(store).get_account("cf") is not None


def test_a_token_that_is_refused_is_a_bad_gateway_with_cloudflares_words(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error

    def refuse(request: Any, timeout: float = 0) -> Any:
        body = json.dumps(
            {"success": False, "errors": [{"code": 9109, "message": "Invalid access token"}]}
        )
        raise urllib.error.HTTPError(request.full_url, 403, "no", {}, io.BytesIO(body.encode()))  # type: ignore[arg-type]

    monkeypatch.setattr(
        DnsAccounts, "_client", lambda self, token: CloudflareClient(token, opener=refuse)
    )

    response = client.post(
        "/api/dns/providers",
        json={"name": "cf", "kind": "cloudflare", "credentials": {"token": TOKEN}},
    )

    assert response.status_code == 502
    assert "9109: Invalid access token" in response.text


def test_an_account_is_updated_and_verified_and_removed(
    client: TestClient, provider: FakeProvider, store: NoustStore
) -> None:
    updated = client.patch(
        "/api/dns/providers/cf", json={"automatic": False, "propagation_seconds": 45}
    )
    zones = client.post("/api/dns/providers/cf/verify")
    removed = client.delete("/api/dns/providers/cf")

    assert updated.json()["automatic"] is False and updated.json()["propagation_seconds"] == 45
    assert zones.json() == [{"id": "zone-example.com", "name": "example.com"}]
    assert removed.status_code == 204
    assert DnsStore(store).get_account("cf") is None


def test_an_unknown_account_is_an_error_not_a_crash(
    client: TestClient, provider: FakeProvider
) -> None:
    assert client.patch("/api/dns/providers/ghost", json={"automatic": True}).status_code == 404
    assert (
        client.patch("/api/dns/providers/cf", json={"propagation_seconds": 9000}).status_code == 422
    )


def test_an_application_sees_what_its_names_would_point_at(
    client: TestClient, provider: FakeProvider, store: NoustStore
) -> None:
    store.add_domain("shop.example.com", "www.shop.example.com", "alias")
    store.add_domain("shop.example.com", "elsewhere.org", "redirect")

    response = client.get("/api/apps/shop.example.com/dns")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["addresses"] == [PUBLIC]
    assert {(p["hostname"], p["action"]) for p in body["plans"]} == {
        ("shop.example.com", "create"),
        ("www.shop.example.com", "create"),
    }
    assert body["unmanaged"] == ["elsewhere.org"]


def test_making_a_name_point_here_creates_the_record_and_lists_it(
    client: TestClient, provider: FakeProvider
) -> None:
    response = client.post("/api/apps/shop.example.com/dns", json={"hostname": "api.example.com"})

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "created"
    assert provider.holds("example.com", "api.example.com", "A", PUBLIC)
    listing = client.get("/api/dns/records").json()["records"]
    assert [(r["hostname"], r["owned"], r["applications"]) for r in listing] == [
        ("api.example.com", True, ["shop.example.com"])
    ]


def test_a_record_in_the_way_is_a_409_until_the_exact_record_is_confirmed(
    client: TestClient, provider: FakeProvider
) -> None:
    stale = provider.seed("example.com", "api.example.com", "A", "9.9.9.9")

    refused = client.post("/api/apps/shop.example.com/dns", json={"hostname": "api.example.com"})
    confirmed = client.post(
        "/api/apps/shop.example.com/dns",
        json={
            "hostname": "api.example.com",
            "replace_record": stale.id,
            "replace_current": "9.9.9.9",
        },
    )

    assert refused.status_code == 409
    body = refused.json()
    # The console branches on the code and confirms with the record and what it held.
    assert body["error"] == "record_conflict"
    assert (body["hostname"], body["record_id"], body["current"], body["wanted"]) == (
        "api.example.com",
        stale.id,
        "9.9.9.9",
        PUBLIC,
    )
    assert confirmed.status_code == 200 and confirmed.json()["outcome"] == "replaced"


def test_a_server_with_no_public_address_says_how_to_declare_one(
    client: TestClient, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records_module, "server_addresses", lambda: [])
    monkeypatch.setattr(dns_api, "server_addresses", lambda: [])

    response = client.post("/api/apps/shop.example.com/dns", json={"hostname": "api.example.com"})

    assert response.status_code == 400
    assert "dns.public_addresses" in response.text


def test_the_sweep_reports_what_it_released(client: TestClient, provider: FakeProvider) -> None:
    response = client.post("/api/dns/sweep")

    assert response.status_code == 200 and response.json() == {"released": []}


def test_an_unknown_application_is_a_404(client: TestClient, provider: FakeProvider) -> None:
    assert client.get("/api/apps/ghost.example.com/dns").status_code == 404
    assert (
        client.post(
            "/api/apps/ghost.example.com/dns", json={"hostname": "a.example.com"}
        ).status_code
        == 404
    )
