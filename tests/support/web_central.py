# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_central.py`` that other tests build on."""

from __future__ import annotations

from fastapi.testclient import TestClient

from noust.web.auth import CSRF_HEADER_NAME
from noust.web.server import get_token_manager


def sign_in(client: TestClient) -> str:
    master = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": master})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return master


def elevate(client: TestClient, master: str) -> None:
    response = client.post("/api/auth/elevate", json={"token": master})
    assert response.status_code == 200, response.text
