# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_permission_routes.py`` that other tests build on."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from noust.core import totp
from noust.web.server import get_token_manager

PASSWORD = "correct horse battery staple"


def signed_in(app: Any, role: str) -> tuple[TestClient, str]:
    """
    Create an account of a role and sign it in.

    Args:
        app: The application.
        role: The role.

    Returns:
        A client holding the session, and its CSRF token.
    """
    manager = get_token_manager().accounts
    username = f"{role}-user"
    account = manager.create(username, role, password=PASSWORD)
    secret = manager.begin_totp(account.id)
    codes = manager.confirm_totp(account.id, totp.totp_now(secret))
    assert codes is not None
    client = TestClient(app, client=("testclient", 50000))
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD, "totp_code": codes[0]}
    )
    assert response.status_code == 200, response.text
    return client, response.json()["csrf_token"]
