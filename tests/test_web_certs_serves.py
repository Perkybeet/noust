# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/certs`` says which names each certificate presents.

A certificate of its own and the shared one are told apart by what they serve; the listing is
held to what the caller may see, because a wildcard or an upload can be presented by
applications of more than one team.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, NoustStore
from noust.managers.cert_manager import CertificateInfo, CertManager
from noust.web.api import certs as certs_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store with one application and a name that holds a certificate of its own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="example.com", app_type="nodejs", port=3000))
    instance.add_domain("example.com", "admin.example.com", "alias")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def client(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client for the certificates router, presenting an admin Bearer credential."""
    monkeypatch.setattr(
        CertManager,
        "list_certificates",
        lambda self: [
            CertificateInfo(name="example.com", domains=["example.com"], expiry="2026-12-01"),
            CertificateInfo(
                name="admin.example.com", domains=["admin.example.com"], expiry="2026-12-01"
            ),
        ],
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(certs_api.router, prefix="/api/certs")
    app.dependency_overrides[require_auth] = lambda: {
        "sid": "token",
        "scope": "admin",
        "source": "bearer",
        "type": "api_token",
    }
    return TestClient(app)


def test_each_certificate_lists_the_names_it_presents(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        certs_api,
        "names_served_by_certificate",
        lambda: {"example.com": ["example.com"], "admin.example.com": ["admin.example.com"]},
    )

    body = client.get("/api/certs").json()

    serves = {entry["domain"]: entry["serves"] for entry in body["certificates"]}
    assert serves == {"example.com": ["example.com"], "admin.example.com": ["admin.example.com"]}


def test_a_certificate_nothing_presents_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(certs_api, "names_served_by_certificate", lambda: {})

    body = client.get("/api/certs").json()

    assert all(entry["serves"] == [] for entry in body["certificates"])


def test_a_name_the_caller_may_not_see_is_left_out(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        certs_api, "names_served_by_certificate", lambda: {"example.com": ["example.com", "x.org"]}
    )

    class Scope:
        def sees_domain(self, name: str | None) -> bool:
            return name != "x.org"

    monkeypatch.setattr(certs_api, "scoped_store", lambda: Scope())

    names = certs_api._serves()

    assert names == {"example.com": ["example.com"]}


def test_one_certificate_carries_them_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        certs_api,
        "names_served_by_certificate",
        lambda: {"admin.example.com": ["admin.example.com"]},
    )
    monkeypatch.setattr(
        CertManager,
        "get_cert_info",
        lambda self, domain: CertificateInfo(
            name="admin.example.com", domains=["admin.example.com"], expiry="2026-12-01"
        ),
    )

    body = client.get("/api/certs/admin.example.com").json()

    assert body["serves"] == ["admin.example.com"]
