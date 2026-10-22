# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The names the console speaks (cookies, CSRF header, WebSocket subprotocols, token prefixes).

From 4.0 the console writes ``noust_session``, ``noust_csrf`` and ``X-Noust-CSRF`` and
answers ``noust.auth`` on a stream. What lives in people's scripts and CI secrets keeps
working: the old CSRF header, the old subprotocols and the old token prefix are read. The
old cookies are not: the console asks to sign in once, and expires them so they do not stay.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import legacy_names
from noust.web.auth import (
    API_TOKEN_PREFIXES,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SecurityConfig,
)
from noust.web.server import create_app, get_token_manager
from noust.web.websockets.router import WS_SUBPROTOCOL, WS_TOKEN_PREFIX
from tests.test_web_auth import build_client


@pytest.fixture
def app(tmp_path: Path, runner: object) -> FastAPI:
    """
    Args:
        tmp_path: Per-test temporary directory.
        runner: The fake command runner.

    Returns:
        The application.
    """
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


def browser(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        A client with no cookies.
    """
    return TestClient(app, client=("testclient", 50000), follow_redirects=False)


def signed_in(app: FastAPI) -> tuple[TestClient, dict[str, str]]:
    """
    Args:
        app: The application.

    Returns:
        A client signed in with the access token, and the body the sign-in answered.
    """
    client = browser(app)
    response = client.post(
        "/api/auth/login", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text
    return client, response.json()


def test_the_names_are_noust_s() -> None:
    assert SESSION_COOKIE_NAME == "noust_session"
    assert CSRF_COOKIE_NAME == "noust_csrf"
    assert CSRF_HEADER_NAME == "X-Noust-CSRF"
    assert WS_SUBPROTOCOL == "noust.auth"
    assert WS_TOKEN_PREFIX == "noust.token."
    assert API_TOKEN_PREFIXES == ("noust_tok_", legacy_names.API_TOKEN_PREFIX)


def test_signing_in_sets_the_new_cookies_and_only_those(app: FastAPI) -> None:
    client, _ = signed_in(app)

    assert client.cookies.get("noust_session")
    assert client.cookies.get("noust_csrf")
    assert client.cookies.get(legacy_names.SESSION_COOKIE) is None
    assert client.cookies.get(legacy_names.CSRF_COOKIE) is None


def test_signing_in_expires_the_cookies_of_before(app: FastAPI) -> None:
    """A browser signed in under 3.x keeps two dead cookies otherwise, for ever."""
    client = browser(app)

    response = client.post(
        "/api/auth/login", json={"token": get_token_manager().generate_master_token()}
    )

    cleared = [
        h
        for h in response.headers.get_list("set-cookie")
        if h.startswith(legacy_names.SESSION_COOKIE + "=")
    ]
    assert cleared, response.headers.get_list("set-cookie")
    assert "Max-Age=0" in cleared[0] or "expires=" in cleared[0].lower()


def test_signing_out_expires_both_generations_of_cookies(app: FastAPI) -> None:
    client, body = signed_in(app)

    response = client.post("/api/auth/logout", headers={CSRF_HEADER_NAME: body["csrf_token"]})

    names = {h.split("=", 1)[0] for h in response.headers.get_list("set-cookie")}
    assert {
        "noust_session",
        "noust_csrf",
        legacy_names.SESSION_COOKIE,
        legacy_names.CSRF_COOKIE,
    } <= names


def test_a_session_cookie_of_before_is_not_read(app: FastAPI) -> None:
    """The browser asks to sign in again once: the server does not look at the old name."""
    client, _ = signed_in(app)
    token = client.cookies["noust_session"]

    old = browser(app)
    old.cookies.set(legacy_names.SESSION_COOKIE, token)

    assert client.get("/api/apps").status_code == 200
    assert old.get("/api/apps").status_code == 401


def test_the_csrf_header_has_two_spellings(app: FastAPI) -> None:
    """A script that signs in with cookies and hard-codes the old header keeps working."""
    client, body = signed_in(app)

    new = client.post("/api/auth/ws-ticket", headers={"X-Noust-CSRF": body["csrf_token"]})
    old = client.post("/api/auth/ws-ticket", headers={legacy_names.CSRF_HEADER: body["csrf_token"]})
    wrong = client.post("/api/auth/ws-ticket", headers={"X-Noust-CSRF": "not-it"})
    none = client.post("/api/auth/ws-ticket")

    assert new.status_code == 200, new.text
    assert old.status_code == 200, old.text
    assert wrong.status_code == 403
    assert none.status_code == 403


def test_the_session_names_what_the_console_has_to_send(app: FastAPI) -> None:
    client, _ = signed_in(app)

    body = client.get("/api/auth/session").json()

    assert body["csrf_header"] == "X-Noust-CSRF"
    assert body["csrf_cookie"] == "noust_csrf"


def test_cross_origin_requests_may_send_either_csrf_header(tmp_path: Path, runner: object) -> None:
    app = create_app(
        SecurityConfig(
            state_dir=tmp_path / "state",
            enable_cors=True,
            cors_origins=["https://panel.example.com"],
        )
    )
    for header in ("x-noust-csrf", legacy_names.CSRF_HEADER.lower()):
        response = browser(app).options(
            "/api/apps",
            headers={
                "Origin": "https://panel.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": header,
            },
        )
        assert response.status_code == 200, (header, response.text)


class TestStreams:
    """A stream is opened with the token in the subprotocol; both spellings are accepted."""

    def test_the_new_subprotocol_is_answered_in_kind(self, sandbox: Path) -> None:
        client = build_client(sandbox)
        token = get_token_manager().generate_master_token()

        with client.websocket_connect(
            "/ws/events", subprotocols=["noust.auth", f"noust.token.{token}"]
        ) as ws:
            assert ws.accepted_subprotocol == "noust.auth"
            assert ws.receive_json()["type"] == "connected"

    def test_the_old_subprotocol_is_answered_in_kind(self, sandbox: Path) -> None:
        """A client that offers only the old name fails the handshake if it is answered the new."""
        client = build_client(sandbox)
        token = get_token_manager().generate_master_token()

        with client.websocket_connect(
            "/ws/events",
            subprotocols=[legacy_names.WS_SUBPROTOCOL, f"{legacy_names.WS_TOKEN_PREFIX}{token}"],
        ) as ws:
            assert ws.accepted_subprotocol == legacy_names.WS_SUBPROTOCOL
            assert ws.receive_json()["type"] == "connected"


class TestApiTokens:
    """Tokens issued before 3.0 are in other people's scripts and keep working."""

    def test_a_token_of_before_signs_in(self, app: FastAPI) -> None:
        import secrets
        import time

        manager = get_token_manager()
        token = legacy_names.API_TOKEN_PREFIX + secrets.token_urlsafe(32)
        manager.sessions.create_api_token(
            "old-script", manager._hash_token(token), "admin", time.time(), None, created_by="test"
        )

        response = browser(app).get(
            "/api/auth/session", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.json()["authenticated"] is True
