# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Signing in with GitHub over HTTP: the redirects, the ticket, and the second factor still owed."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("cryptography")

from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.accounts.oidc import OidcManager
from noust.core.accounts.oidc_client import OidcClient, OidcHttp, set_client
from noust.core.store import NoustStore
from noust.web.auth import SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.github_login_support import FakeGitHub

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
def github() -> Iterator[FakeGitHub]:
    hub = FakeGitHub()
    hub.add_user(
        "maria-l",
        583231,
        name="Maria Lopez",
        emails=[{"email": "maria@acme.test", "primary": True, "verified": True}],
    )
    set_client(OidcClient(OidcHttp(opener=hub.opener)))
    yield hub
    set_client(None)


@pytest.fixture
def client(sandbox: Path, github: FakeGitHub) -> TestClient:
    app = create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=5000, auth_policy=AuthPolicy()
        )
    )
    return TestClient(app, base_url=BASE, client=("testclient", 50000))


def accounts() -> AccountManager:
    return get_token_manager().accounts


def make_account(username: str, role: str = "viewer", **kwargs: Any) -> list[str]:
    account = accounts().create(username, role, password=PASSWORD, **kwargs)
    secret = accounts().begin_totp(account.id)
    codes = accounts().confirm_totp(account.id, totp.totp_now(secret))
    assert codes is not None
    return codes


def configure(github: FakeGitHub, **changes: Any) -> None:
    OidcManager(accounts().store, accounts=accounts()).configure(
        "github",
        {"kind": "github", "client_id": github.client_id, "label": "GitHub", **changes},
        client_secret=github.client_secret,
    )


def target(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def test_the_sign_in_page_offers_the_button(client: TestClient, github: FakeGitHub) -> None:
    make_account("root-admin", "admin")
    configure(github)

    choices = client.get("/api/auth/oidc/providers").json()

    assert choices["providers"] == [{"name": "github", "label": "GitHub", "protocol": "oidc"}]


def test_the_button_leaves_for_github_and_the_answer_comes_back_with_a_ticket(
    client: TestClient, github: FakeGitHub
) -> None:
    make_account("root-admin", "admin")
    make_account("maria", person_ref="maria@acme.test")
    configure(github, link_by_email=True)

    started = client.get(
        "/api/auth/oidc/start",
        params={"provider": "github", "next": "/apps"},
        follow_redirects=False,
    )
    assert started.status_code == 302
    assert started.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    callback = github.authorize(started.headers["location"], "maria-l")
    arrived = client.get(target(callback), follow_redirects=False)

    assert arrived.status_code == 303, arrived.text
    assert "sso" in parse_qs(urlsplit(arrived.headers["location"]).query)


def test_the_ticket_still_asks_for_nousts_own_second_factor(
    client: TestClient, github: FakeGitHub
) -> None:
    make_account("root-admin", "admin")
    make_account("maria", person_ref="maria@acme.test")
    configure(github, link_by_email=True)
    started = client.get(
        "/api/auth/oidc/start", params={"provider": "github"}, follow_redirects=False
    )
    callback = github.authorize(started.headers["location"], "maria-l")
    arrived = client.get(target(callback), follow_redirects=False)
    ticket = parse_qs(urlsplit(arrived.headers["location"]).query)["sso"][0]

    done = client.post("/api/auth/oidc/complete", json={"ticket": ticket})

    assert done.status_code == 200, done.text
    step = done.json()["login"]
    assert step["success"] is False and step["second_factor"]["methods"][0] == "totp"
    assert "gho_" not in done.text


def test_a_person_github_vouches_for_but_nobody_linked_is_sent_back_with_a_reason(
    client: TestClient, github: FakeGitHub
) -> None:
    make_account("root-admin", "admin")
    configure(github)
    started = client.get(
        "/api/auth/oidc/start", params={"provider": "github"}, follow_redirects=False
    )
    callback = github.authorize(started.headers["location"], "maria-l")

    arrived = client.get(target(callback), follow_redirects=False)

    location = urlsplit(arrived.headers["location"])
    assert location.path == "/login"
    assert parse_qs(location.query)["sso_error"] == ["refused"]


def test_the_admin_sees_the_provider_with_its_kind_and_never_the_secret(
    client: TestClient, github: FakeGitHub
) -> None:
    make_account("root-admin", "admin")
    configure(github)
    token = get_token_manager().generate_master_token()
    session = client.post("/api/auth/login", json={"token": token})

    listed = client.get("/api/auth/oidc/config")

    assert session.status_code == 200
    assert listed.status_code == 200
    (provider,) = listed.json()["providers"]
    assert provider["kind"] == "github" and provider["client_secret_set"] is True
    assert github.client_secret not in listed.text
