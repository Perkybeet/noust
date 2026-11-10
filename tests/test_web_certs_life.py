# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/certs`` says how each certificate is doing, on its own life (item 172).

The console used to decide from the days alone (amber under 21), which put a certificate of 45
days in warning for half its life. The server says the state, from the same calculation
``noust health`` and the monitor use, and the days stay for a console that does not know it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from noust.managers.cert_manager import CertificateInfo, CertManager
from noust.web.api import certs as certs_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth


def _cert(name: str, *, life: int | None, left: float) -> CertificateInfo:
    end = datetime.now(timezone.utc) + timedelta(days=left)
    return CertificateInfo(
        name=name,
        domains=[name],
        expiry=end.strftime("%Y-%m-%d"),
        expiry_full=end.strftime("%Y-%m-%d %H:%M:%S+00:00"),
        not_before=(end - timedelta(days=life)).isoformat(timespec="seconds") if life else None,
        not_after=end.isoformat(timespec="seconds"),
    )


@pytest.fixture
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def _client(monkeypatch: pytest.MonkeyPatch, *certs: CertificateInfo) -> TestClient:
    monkeypatch.setattr(CertManager, "list_certificates", lambda self: list(certs))
    monkeypatch.setattr(certs_api, "names_served_by_certificate", lambda: {})
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


def _by_name(client: TestClient) -> dict[str, dict[str, Any]]:
    return {entry["domain"]: entry for entry in client.get("/api/certs").json()["certificates"]}


def test_each_certificate_carries_its_state_and_the_life_that_decided_it(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(
        monkeypatch,
        _cert("long.example.com", life=90, left=25),
        _cert("mid.example.com", life=45, left=25),
        _cert("late.example.com", life=45, left=5),
        _cert("gone.example.com", life=45, left=-2),
    )

    certs = _by_name(client)

    assert certs["long.example.com"]["state"] == "renewal_pending"
    assert certs["mid.example.com"]["state"] == "healthy"
    assert certs["late.example.com"]["state"] == "critical"
    assert certs["gone.example.com"]["state"] == "expired"
    assert certs["mid.example.com"]["life_days"] == 45
    assert 0.5 < certs["mid.example.com"]["fraction_left"] < 0.6


def test_a_certificate_with_no_known_start_says_its_life_is_assumed(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(monkeypatch, _cert("old.example.com", life=None, left=50))

    entry = _by_name(client)["old.example.com"]

    assert entry["state"] == "healthy"
    assert entry["life_assumed"] is True
    assert entry["life_days"] == 90


def test_the_days_and_the_dates_are_still_there_for_a_console_that_does_not_know_the_state(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(monkeypatch, _cert("shop.example.com", life=90, left=40))

    entry = _by_name(client)["shop.example.com"]

    assert entry["days_remaining"] in (39, 40)
    assert entry["expires_on"]
    assert entry["not_before"]


def test_an_uploaded_certificate_has_a_state_like_any_other(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload = _cert("corp.example.com", life=365, left=60)
    upload.source = "custom"
    client = _client(monkeypatch, upload)

    entry = _by_name(client)["corp.example.com"]

    assert entry["state"] == "critical"
    assert entry["auto_renew"] is False
