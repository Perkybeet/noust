# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_accounts.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt cheap; the rules under test do not depend on its cost."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own, forgotten afterwards so no account leaks on."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def build(sandbox: Path, policy: AuthPolicy | None = None, **overrides: Any) -> TestClient:
    """
    Create the console with its state in the sandbox.

    Args:
        sandbox: Per-test directory.
        policy: The sign-in policy to use.
        **overrides: Other security configuration.

    Returns:
        A client for it.
    """
    config = SecurityConfig(
        state_dir=sandbox / "state",
        rate_limit_requests=5000,
        auth_policy=policy or AuthPolicy(),
        **overrides,
    )
    return TestClient(create_app(config), client=("testclient", 50000))


def accounts() -> AccountManager:
    """The account manager the console uses."""
    return get_token_manager().accounts


def make_account(
    username: str, role: str, *, mfa: bool = True, person_ref: str | None = None
) -> list[str]:
    """
    Create an account, with an authenticator unless told otherwise.

    Args:
        username: Its name.
        role: Its role.
        mfa: Whether to enrol an authenticator.
        person_ref: The e-mail of the person it belongs to.

    Returns:
        Its backup codes, one per sign-in or confirmation the test needs.
    """
    manager = accounts()
    account = manager.create(username, role, password=PASSWORD, person_ref=person_ref)
    if not mfa:
        return []
    secret = manager.begin_totp(account.id)
    codes = manager.confirm_totp(account.id, totp.totp_now(secret))
    assert codes is not None
    return codes


def sign_in(client: TestClient, username: str, code: str | None) -> dict[str, Any]:
    """
    Sign in as an account and return the body.

    Args:
        client: The client, whose cookie jar keeps the session.
        username: The account.
        code: A backup code, or None for an account without a second factor.

    Returns:
        The login response.
    """
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD, "totp_code": code}
    )
    assert response.status_code == 200, response.text
    return response.json()


def master_sign_in(client: TestClient) -> tuple[str, str]:
    """
    Sign in with the master token.

    Args:
        client: The client.

    Returns:
        The master token and the CSRF token of the session.
    """
    token = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    return token, response.json()["csrf_token"]


def elevate_master(client: TestClient, token: str, csrf: str) -> None:
    """Confirm a master token session for sudo mode."""
    response = client.post(
        "/api/auth/elevate", json={"token": token}, headers={CSRF_HEADER_NAME: csrf}
    )
    assert response.status_code == 200, response.text


def read_audit(sandbox: Path) -> list[dict[str, Any]]:
    """Every audit entry the console wrote."""
    import json

    path = sandbox / "state" / "web-audit.log"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
