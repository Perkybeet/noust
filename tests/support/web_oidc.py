# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_oidc.py`` that other tests build on."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.accounts.oidc_client import OidcClient, OidcHttp, set_client
from noust.core.store import NoustStore
from noust.web.auth import SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.support.oidc_support import Clock, FakeIdP

pytest.importorskip("cryptography")

PASSWORD = "correct horse battery staple"
BASE = "http://localhost:8080"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def idp() -> Iterator[FakeIdP]:
    clock = Clock()
    provider = FakeIdP(clock=clock)
    provider.add_user("sub-maria", email="maria@acme.test", email_verified=True, name="Maria Lopez")
    set_client(OidcClient(OidcHttp(opener=provider.opener), clock=clock))
    yield provider
    set_client(None)


@pytest.fixture
def app(sandbox: Path, idp: FakeIdP) -> Any:
    return create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=5000, auth_policy=AuthPolicy()
        )
    )


def browser(app: Any) -> TestClient:
    return TestClient(app, base_url=BASE, client=("testclient", 50000))


def accounts() -> AccountManager:
    return get_token_manager().accounts


def make_account(
    username: str, role: str = "viewer", *, mfa: bool = True, person_ref: str | None = None
) -> list[str]:
    account = accounts().create(username, role, password=PASSWORD, person_ref=person_ref)
    if not mfa:
        return []
    secret = accounts().begin_totp(account.id)
    codes = accounts().confirm_totp(account.id, totp.totp_now(secret))
    assert codes is not None
    return codes


def configure(idp: FakeIdP, **changes: Any) -> None:
    from noust.core.accounts.oidc import OidcManager

    manager = OidcManager(accounts().store, accounts=accounts())
    settings = {"issuer": idp.issuer, "client_id": idp.client_id, "label": "Acme SSO", **changes}
    manager.configure("corp", settings, client_secret=idp.client_secret)


def link(username: str, subject: str = "sub-maria") -> None:
    from noust.core.accounts.oidc import OidcManager

    OidcManager(accounts().store, accounts=accounts()).link("corp", username, subject)


def target(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def through_the_provider(
    client: TestClient,
    idp: FakeIdP,
    sub: str = "sub-maria",
    *,
    next: str | None = "/apps",
    **claims: Any,
) -> Any:
    """Click the button, approve at the provider, and arrive at the callback."""
    params = {"provider": "corp", **({"next": next} if next else {})}
    started = client.get("/api/auth/oidc/start", params=params, follow_redirects=False)
    assert started.status_code == 302, started.text
    callback = idp.authorize(started.headers["location"], sub, **claims)
    return client.get(target(callback), follow_redirects=False)


def ticket_of(response: Any) -> str:
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert "sso" in query, response.headers["location"]
    return query["sso"][0]


def error_of(response: Any) -> str:
    assert response.status_code in (303, 302), response.text
    location = urlsplit(response.headers["location"])
    assert location.path == "/login", response.headers["location"]
    return parse_qs(location.query)["sso_error"][0]


def audit_entries(sandbox: Path) -> list[dict[str, Any]]:
    path = sandbox / "state" / "web-audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def logged(sandbox: Path, action: str) -> list[dict[str, Any]]:
    return [entry for entry in audit_entries(sandbox) if entry["action"] == action]
