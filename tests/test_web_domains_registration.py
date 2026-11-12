# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/apps/{domain}/domains`` says when the registration of each name expires (item 173).

Nothing is asked when it does: it reads what the monitor's daily look kept
(:mod:`noust.core.domain_registrations`). A name nobody looked at has no ``registration``, which is
what a server on which ``monitor.domain_expiry`` is off, or a 4.2 server, looks like to the console.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.domain_registrations import NO_RDAP, OK, Registration, RegistrationStore
from noust.core.store import App, NoustStore
from noust.web.api import domains as domains_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth


@pytest.fixture
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="shop.example.com", app_path="/var/www/apps/shop"))
    instance.add_domain("shop.example.com", "tienda.example.es", "alias")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def client(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(domains_api, "bindings", lambda app: [])
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(domains_api.router, prefix="/api/apps")
    app.dependency_overrides[require_auth] = lambda: {
        "sid": "token",
        "scope": "admin",
        "source": "bearer",
        "type": "api_token",
    }
    return TestClient(app)


def kept(store: NoustStore) -> None:
    now = datetime.now(timezone.utc)
    records = RegistrationStore(store)
    records.save(
        Registration(
            domain="example.com",
            status=OK,
            expires_at=(now + timedelta(days=40, hours=1)).isoformat(timespec="seconds"),
            rdap_base="https://rdap.verisign.com/com/v1/",
            checked_at=now.isoformat(timespec="seconds"),
            next_check_at=(now + timedelta(days=1)).isoformat(timespec="seconds"),
        )
    )
    records.remember_registrable("shop.example.com", "example.com", now)
    records.save(
        Registration(
            domain="example.es",
            status=NO_RDAP,
            expires_at=None,
            rdap_base=None,
            checked_at=now.isoformat(timespec="seconds"),
            next_check_at=(now + timedelta(days=30)).isoformat(timespec="seconds"),
            detail="This registry does not publish the expiry (the registry of .es has no RDAP service).",
        )
    )
    records.remember_registrable("tienda.example.es", "example.es", now)


def names(client: TestClient) -> dict[str, dict[str, Any]]:
    body = client.get("/api/apps/shop.example.com/domains").json()
    return {entry["domain"]: entry for entry in body["domains"]}


def test_each_name_carries_what_its_registry_said(client: TestClient, store: NoustStore) -> None:
    kept(store)

    entries = names(client)

    shop = entries["shop.example.com"]["registration"]
    assert shop["domain"] == "example.com"
    assert shop["status"] == "ok"
    assert shop["days_left"] == 40
    assert shop["registry"] == "rdap.verisign.com"
    assert shop["expires_at"].startswith(
        (datetime.now(timezone.utc) + timedelta(days=40)).strftime("%Y-%m-%d")[:7]
    )


def test_a_registry_that_publishes_no_expiry_says_so(client: TestClient, store: NoustStore) -> None:
    kept(store)

    registration = names(client)["tienda.example.es"]["registration"]

    assert registration["status"] == "no_rdap"
    assert registration["expires_at"] is None
    assert registration["days_left"] is None
    assert "does not publish the expiry" in registration["detail"]


def test_a_name_nobody_looked_at_has_no_registration(client: TestClient) -> None:
    entries = names(client)

    assert entries["shop.example.com"]["registration"] is None
    assert entries["tienda.example.es"]["registration"] is None


def test_nothing_is_asked_to_read_it(
    client: TestClient, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept(store)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("reading the domains asked a registry")

    monkeypatch.setattr("noust.managers.domain_registration.DomainRegistrations.check", refuse)
    monkeypatch.setattr(
        "noust.managers.domain_registration.DomainRegistrations.check_names", refuse
    )

    assert names(client)["shop.example.com"]["registration"] is not None


def test_a_store_that_cannot_be_read_leaves_the_registration_out_and_the_list_whole(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    def broken(self: Any, name: str) -> None:
        raise sqlite3.OperationalError("no such table")

    monkeypatch.setattr(RegistrationStore, "for_name", broken)

    entries = names(client)

    assert set(entries) == {"shop.example.com", "tienda.example.es"}
    assert entries["shop.example.com"]["registration"] is None
