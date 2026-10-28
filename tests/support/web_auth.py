# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_auth.py`` that other tests build on."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from noust.core import totp
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app


def make_config(sandbox: Path, **overrides) -> SecurityConfig:
    """
    Build a security configuration whose state lives inside the sandbox.

    Args:
        sandbox: Per-test temporary directory.
        **overrides: Fields to override on the configuration.

    Returns:
        The configuration.
    """
    params: dict[str, object] = {
        "state_dir": sandbox / "state",
        "rate_limit_requests": 5000,
    }
    params.update(overrides)
    return SecurityConfig(**params)


def build_client(sandbox: Path, client_host: str = "testclient", **overrides) -> TestClient:
    """
    Create an application and a client for it.

    Args:
        sandbox: Per-test temporary directory.
        client_host: Peer address the test connects from.
        **overrides: Security configuration overrides.

    Returns:
        A test client bound to a freshly created application.
    """
    app = create_app(make_config(sandbox, **overrides))
    return TestClient(app, client=(client_host, 50000))


def login(client: TestClient, token: str, **kwargs) -> dict:
    """
    Log in and return the response body.

    Args:
        client: The test client.
        token: The master token.
        **kwargs: Extra request arguments, such as headers.

    Returns:
        The decoded JSON body.
    """
    response = client.post("/api/auth/login", json={"token": token}, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def read_audit(sandbox: Path) -> list[dict]:
    """
    Read the audit log written during a test.

    Args:
        sandbox: Per-test temporary directory.

    Returns:
        One dict per audit line.
    """
    path = sandbox / "state" / "web-audit.log"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def iter_routes(routes: list, prefix: str = "") -> list[tuple[str, object]]:
    """
    Collect every route of an application, including nested routers.

    Args:
        routes: Routes of an application or router.
        prefix: Path prefix accumulated so far.

    Returns:
        Pairs of full path and route, for HTTP and WebSocket routes alike.
    """
    collected: list[tuple[str, object]] = []
    for route in routes:
        # FastAPI keeps included routers as an opaque node instead of flattening.
        context = getattr(route, "include_context", None)
        if context is not None:
            collected.extend(iter_routes(context.included_router.routes, prefix + context.prefix))
            continue
        path = getattr(route, "path", None)
        if path is not None and not hasattr(route, "routes"):
            collected.append((prefix + path, route))
            continue
        if hasattr(route, "routes"):
            collected.extend(iter_routes(route.routes, prefix))
    return collected


def enable_totp(client: TestClient, csrf: str, master: str) -> tuple[str, list[str]]:
    """
    Enrol and confirm the second factor through the API.

    Enrolling needs sudo mode (see tests/test_web_sudo.py). The confirmation
    happens in a session of its own, signed in and elevated here and then
    dropped, so the caller's session is left exactly as unelevated as it was:
    several callers go on to test what an unelevated session may do.

    Args:
        client: A signed-in client, for the application it talks to.
        csrf: The caller's CSRF token. Unused: the caller's session is not the
            one that enrols. Kept so every call site reads the same.
        master: The master token, to sign in and confirm sudo mode with.

    Returns:
        The shared secret and the backup codes shown at confirmation.
    """
    del csrf
    operator = TestClient(client.app, client=("testclient", 50000))
    own_csrf = login(operator, master)["csrf_token"]
    headers = {CSRF_HEADER_NAME: own_csrf}
    elevated = operator.post("/api/auth/elevate", json={"token": master}, headers=headers)
    assert elevated.status_code == 200, elevated.text

    enroll = operator.post("/api/auth/2fa/enroll", headers=headers)
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"]

    confirm = operator.post(
        "/api/auth/2fa/confirm", json={"code": totp.totp_now(secret)}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    return secret, confirm.json()["backup_codes"]


def bearer(token: str) -> dict[str, str]:
    """
    Args:
        token: The credential to present.

    Returns:
        An Authorization header carrying it.
    """
    return {"Authorization": f"Bearer {token}"}


def issue_token(
    client: TestClient,
    csrf: str,
    master: str,
    name: str = "ci",
    scope: str = "read",
    expires_hours: int | None = None,
) -> dict:
    """
    Issue an API token through the API.

    Minting a token is one of the actions D5's sudo mode guards (see
    tests/test_web_sudo.py), so a cookie session has to confirm itself before
    it may - this is not what any of this helper's callers are testing, so it
    elevates first rather than making every one of them do it.

    Args:
        client: A signed-in client.
        csrf: The session's CSRF token.
        master: The master token, to confirm sudo mode with. Two-factor
            authentication is never enabled in a test that calls this.
        name: Token name.
        scope: Token scope.
        expires_hours: Optional lifetime in hours.

    Returns:
        The creation response body, the only place the token is ever clear.
    """
    elevated = client.post(
        "/api/auth/elevate", json={"token": master}, headers={CSRF_HEADER_NAME: csrf}
    )
    assert elevated.status_code == 200, elevated.text

    body: dict[str, object] = {"name": name, "scope": scope}
    if expires_hours is not None:
        body["expires_hours"] = expires_hours
    response = client.post("/api/auth/tokens", json=body, headers={CSRF_HEADER_NAME: csrf})
    assert response.status_code == 201, response.text
    return response.json()
