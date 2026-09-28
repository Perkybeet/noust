# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The public hooks site cannot be used to reach, or starve, the console.

``wasm web expose-hooks`` puts the internet in front of ``/hooks/`` through an
nginx proxy on this machine, so every delivery reaches the console from
127.0.0.1 - the same address as the operator's SSH tunnel. Two things follow:

- A path that nginx matched as ``/hooks/...`` only after normalising it
  (``/api/x/../../hooks/y``) used to be forwarded raw and routed to the API,
  where its junk bearer token locked 127.0.0.1 out. nginx now forwards the
  normalised path, and the console refuses a dot segment before any
  credential is looked at.
- A flood of deliveries spent 127.0.0.1's anonymous budget, and an address
  over that budget got no credential check, so the operator's own signed-in
  requests were refused with 429. Deliveries now have a budget of their own,
  and a valid credential is always counted against its own budget.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from wasm.web import auth
from wasm.web.auth import SecurityConfig
from wasm.web.server import create_app, get_brute_force, get_token_manager


def build(sandbox: Path, **overrides: object) -> Any:
    """
    Create an application with small budgets.

    Args:
        sandbox: Per-test temporary directory.
        **overrides: Security configuration overrides.

    Returns:
        The ASGI application.
    """
    params: dict[str, object] = {
        "state_dir": sandbox / "state",
        "rate_limit_requests": 3,
        "rate_limit_authenticated_requests": 10,
        "rate_limit_window": 60,
    }
    params.update(overrides)
    return create_app(SecurityConfig(**params))  # type: ignore[arg-type]


def client_for(app: Any) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        A client whose every request comes from 127.0.0.1, like nginx's and a tunnel's.
    """
    return TestClient(app, client=("127.0.0.1", 50000), follow_redirects=False)


def bearer(token: str) -> dict[str, str]:
    """
    Args:
        token: A credential.

    Returns:
        The header that presents it.
    """
    return {"Authorization": f"Bearer {token}"}


def spy_on_credential_checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """
    Record every credential the server checks, still checking it.

    Args:
        monkeypatch: Patching helper.

    Returns:
        The credentials checked, in order.
    """
    checked: list[str] = []
    real = auth.check_credential

    def spy(credential: str, client_ip: str) -> dict[str, object] | None:
        checked.append(credential)
        return real(credential, client_ip)

    monkeypatch.setattr(auth, "check_credential", spy)
    return checked


def raw_request(app: Any, path: str, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """
    Send one GET with a path exactly as given, dot segments included.

    HTTP clients normalise ``..`` away before sending, which is precisely
    what a proxy forwarding the raw request line does not do.

    Args:
        app: The ASGI application.
        path: The path, verbatim.
        headers: Request headers.

    Returns:
        The status and the JSON body.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1")]
        + [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8080),
    }
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(body or b"{}")


@pytest.mark.parametrize(
    "path",
    [
        "/api/metrics/../../hooks/x",
        "/hooks/../api/auth/verify",
        "/hooks/./github",
        "/api/..",
    ],
)
def test_a_path_with_dot_segments_is_refused_before_any_credential_is_checked(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """No client sends one; a proxy forwarding a raw request line does."""
    app = build(sandbox, rate_limit_requests=1000, max_failed_attempts=1)
    checked = spy_on_credential_checks(monkeypatch)

    status, body = raw_request(app, path, bearer("wasm_guess"))

    assert status == 400
    assert body["error"] == "validation_error"
    assert checked == []
    assert get_brute_force().get_attempts_remaining("127.0.0.1") == 1


def test_a_dot_inside_a_segment_is_not_a_dot_segment(sandbox: Path) -> None:
    """A domain in a hook path is not refused for its dots."""
    app = build(sandbox)

    status, _ = raw_request(app, "/hooks/deploy/shop.example.com", {})

    assert status != 400


def test_forge_deliveries_do_not_spend_the_tunnels_anonymous_budget(sandbox: Path) -> None:
    """Anyone on the internet can POST to /hooks/github; the sign-in page stays up."""
    client = client_for(build(sandbox))

    hooks = [client.post("/hooks/github", content=b"{}").status_code for _ in range(5)]
    console = [client.get("/api/auth/session").status_code for _ in range(3)]

    assert hooks[3:] == [429, 429]
    assert console == [200, 200, 200]


def test_the_tunnel_does_not_spend_the_forges_budget(sandbox: Path) -> None:
    """The other way round: a busy anonymous tunnel does not drop deliveries."""
    client = client_for(build(sandbox))

    for _ in range(4):
        client.get("/api/auth/session")
    delivery = client.post("/hooks/github", content=b"{}")

    assert delivery.status_code != 429
    assert delivery.headers["X-RateLimit-Remaining"] == "2"


def test_a_valid_credential_is_served_when_the_address_is_over_the_anonymous_budget(
    sandbox: Path,
) -> None:
    """The operator behind 127.0.0.1 is counted by credential, never by address."""
    client = client_for(build(sandbox))
    token = get_token_manager().generate_master_token()
    for _ in range(4):
        client.get("/api/auth/session")

    response = client.get("/api/auth/verify", headers=bearer(token))

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "10"


def test_a_flood_of_guesses_over_the_anonymous_budget_is_bounded_by_the_lockout(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checking a credential over budget costs at most the attempts the lockout allows."""
    client = client_for(build(sandbox, max_failed_attempts=3, lockout_duration=60))
    for _ in range(3):
        client.get("/api/auth/session")
    checked = spy_on_credential_checks(monkeypatch)

    statuses = [
        client.get("/api/auth/verify", headers=bearer(f"wasm_guess{n}")).status_code
        for n in range(10)
    ]

    assert set(statuses) == {429}
    assert len(checked) == 3
    assert get_brute_force().is_locked("127.0.0.1")
