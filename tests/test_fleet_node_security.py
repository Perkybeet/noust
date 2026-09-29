# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A node's side of the fleet: what a central's token may do, from where, for whom.

The fleet token is the one credential that speaks for somebody else, so these
tests attack exactly that: present it from the network instead of the tunnel,
through a reverse proxy on the node itself, with a forged actor, with a
scope it should not have, on the endpoints that manage the node's own
credentials, and on an elevated action the central never confirmed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.web.auth import (
    CSRF_HEADER_NAME,
    FLEET_ACTOR_HEADER,
    FLEET_ACTOR_SCOPE_HEADER,
    FLEET_ELEVATED_HEADER,
    SCOPE_RANK,
    actor_label,
    fleet_refusal,
    scope_satisfies,
)
from noust.web.server import create_app, get_token_manager
from tests.test_web_auth import make_config, read_audit

#: The tunnel's end: sshd connects to the console from loopback.
TUNNEL = ("127.0.0.1", 50000)


@pytest.fixture
def config_path(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Point the configuration file into the sandbox.

    Args:
        sandbox: Per-test temporary directory.
        monkeypatch: Patching helper.

    Returns:
        The configuration path.
    """
    path = sandbox / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    return path


@pytest.fixture
def app(sandbox: Path, runner: FakeRunner, config_path: Path) -> Any:
    """
    Returns:
        A node's application.
    """
    return create_app(make_config(sandbox))


@pytest.fixture
def fleet_token(app: Any) -> str:
    """
    Returns:
        A fleet token named ``fleet-nas``, as ``noust fleet authorize`` mints it.
    """
    return str(get_token_manager().create_fleet_token("fleet-nas")["token"])


def client_from(app: Any, peer: tuple[str, int] = TUNNEL) -> TestClient:
    """
    Args:
        app: The application.
        peer: The TCP peer the requests come from.

    Returns:
        A client connecting from that peer.
    """
    return TestClient(app, client=peer)


def fleet_headers(token: str, **extra: str) -> dict[str, str]:
    """
    Args:
        token: The fleet token.
        **extra: More headers.

    Returns:
        What a central sends.
    """
    return {"Authorization": f"Bearer {token}", **extra}


def audit_of(sandbox: Path, action: str) -> list[dict[str, Any]]:
    """
    Args:
        sandbox: Per-test temporary directory.
        action: The audit action.

    Returns:
        The entries with that action.
    """
    return [entry for entry in read_audit(sandbox) if entry["action"] == action]


class TestOnlyFromTheTunnel:
    """A fleet token is accepted from loopback, and only as the tunnel delivers it."""

    def test_from_the_tunnel_it_is_an_admin(self, app: Any, fleet_token: str) -> None:
        response = client_from(app).get("/api/audit", headers=fleet_headers(fleet_token))

        assert response.status_code == 200, response.text

    def test_from_ipv6_loopback_too(self, app: Any, fleet_token: str) -> None:
        response = client_from(app, ("::1", 50000)).get(
            "/api/auth/verify", headers=fleet_headers(fleet_token)
        )

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize("peer", ["203.0.113.9", "10.0.0.2", "testclient"])
    def test_from_anywhere_else_it_is_refused_and_audited(
        self, app: Any, fleet_token: str, sandbox: Path, peer: str
    ) -> None:
        response = client_from(app, (peer, 50000)).get(
            "/api/auth/verify", headers=fleet_headers(fleet_token)
        )

        assert response.status_code == 401
        body = response.json()
        assert body["error"] == "fleet_origin"
        assert "loopback" in body["detail"]
        entries = audit_of(sandbox, "auth.fleet")
        assert entries and entries[-1]["result"] == "denied"

    def test_a_refusal_is_not_counted_as_a_guess(self, app: Any, fleet_token: str) -> None:
        # Real tokens from the wrong place must not lock the address out: a
        # misrouted central would otherwise lock out its own tunnel address.
        client = client_from(app, ("203.0.113.9", 50000))
        for _ in range(8):
            assert (
                client.get("/api/auth/verify", headers=fleet_headers(fleet_token)).status_code
                == 401
            )

        master = get_token_manager().generate_master_token()
        assert client.get("/api/auth/verify", headers=fleet_headers(master)).status_code == 200

    @pytest.mark.parametrize(
        "header",
        [
            ("X-Forwarded-For", "198.51.100.7"),
            ("X-Real-IP", "198.51.100.7"),
            ("Forwarded", "for=1.2.3.4"),
        ],
    )
    def test_through_a_reverse_proxy_on_the_node_it_is_refused(
        self, app: Any, fleet_token: str, header: tuple[str, str]
    ) -> None:
        response = client_from(app).get(
            "/api/auth/verify", headers=fleet_headers(fleet_token, **{header[0]: header[1]})
        )

        assert response.status_code == 401
        assert "reverse proxy" in response.json()["detail"]

    def test_the_session_probe_refuses_it_from_elsewhere_too(
        self, app: Any, fleet_token: str
    ) -> None:
        response = client_from(app, ("203.0.113.9", 50000)).get(
            "/api/auth/session", headers=fleet_headers(fleet_token)
        )

        assert response.status_code == 401

    def test_a_raw_fleet_scope_satisfies_nothing(self) -> None:
        for required in SCOPE_RANK:
            assert not scope_satisfies("fleet", required)


class TestActor:
    """``X-Noust-Actor`` names the central's operator, for a fleet token only."""

    def test_a_mutation_is_audited_on_behalf_of_the_actor(
        self, app: Any, fleet_token: str, sandbox: Path
    ) -> None:
        client_from(app).post(
            "/api/apps",
            json={},
            headers=fleet_headers(fleet_token, **{FLEET_ACTOR_HEADER: "token:ci"}),
        )

        entries = audit_of(sandbox, "api.post")
        assert entries[-1]["actor"] == "fleet-nas on behalf of token:ci"

    def test_without_an_actor_it_is_the_token(
        self, app: Any, fleet_token: str, sandbox: Path
    ) -> None:
        client_from(app).post("/api/apps", json={}, headers=fleet_headers(fleet_token))

        assert audit_of(sandbox, "api.post")[-1]["actor"] == "token:fleet-nas"

    @pytest.mark.parametrize(
        "actor", ["", "a b", "x" * 65, "evil\u00e9".encode(), "-leading", "a;b", "a/b", "a,b"]
    )
    def test_a_malformed_actor_is_refused(
        self, app: Any, fleet_token: str, actor: str | bytes
    ) -> None:
        headers: dict[str, str | bytes] = {
            "Authorization": f"Bearer {fleet_token}",
            FLEET_ACTOR_HEADER: actor,
        }
        response = client_from(app).get("/api/auth/verify", headers=headers)

        assert response.status_code == 400
        assert FLEET_ACTOR_HEADER in response.json()["detail"]

    def test_on_any_other_credential_it_is_ignored(self, app: Any, sandbox: Path) -> None:
        issued = get_token_manager().create_api_token("ci", "admin")
        client_from(app).post(
            "/api/apps",
            json={},
            headers={"Authorization": f"Bearer {issued['token']}", FLEET_ACTOR_HEADER: "someone"},
        )

        assert audit_of(sandbox, "api.post")[-1]["actor"] == "token:ci"

    def test_the_label_names_both(self) -> None:
        payload = {
            "fleet": True,
            "token_name": "fleet-nas",
            "sid": "token:fleet-nas",
            "on_behalf_of": "master",
        }

        assert actor_label(payload) == "fleet-nas on behalf of master"


class TestActorScope:
    """The fleet token is narrowed to the scope of the operator it acts for."""

    def test_a_read_actor_cannot_read_what_needs_admin(self, app: Any, fleet_token: str) -> None:
        response = client_from(app).get(
            "/api/audit", headers=fleet_headers(fleet_token, **{FLEET_ACTOR_SCOPE_HEADER: "read"})
        )

        assert response.status_code == 403

    def test_a_read_actor_cannot_write(self, app: Any, fleet_token: str) -> None:
        response = client_from(app).post(
            "/api/apps",
            json={},
            headers=fleet_headers(fleet_token, **{FLEET_ACTOR_SCOPE_HEADER: "read"}),
        )

        assert response.status_code == 403

    def test_a_scope_above_admin_is_not_a_scope(self, app: Any, fleet_token: str) -> None:
        response = client_from(app).get(
            "/api/auth/verify",
            headers=fleet_headers(fleet_token, **{FLEET_ACTOR_SCOPE_HEADER: "fleet"}),
        )

        assert response.status_code == 400


class TestRefusedOperations:
    """The node's own credentials, security settings and enrollment are not the central's."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/auth/tokens"),
            ("POST", "/api/auth/tokens"),
            ("DELETE", "/api/auth/tokens/1"),
            ("POST", "/api/auth/2fa/enroll"),
            ("POST", "/api/auth/2fa/disable"),
            ("GET", "/api/auth/sessions"),
            ("POST", "/api/auth/sessions/revoke-all"),
            ("DELETE", "/api/auth/sessions/abcdef"),
            ("POST", "/api/auth/elevate"),
            ("POST", "/api/auth/logout"),
            ("POST", "/api/auth/ws-ticket"),
            ("PUT", "/api/config/web"),
            ("PUT", "/api/config"),
            ("GET", "/api/nodes"),
            ("DELETE", "/api/nodes/web-2"),
        ],
    )
    def test_refused_and_audited(
        self, app: Any, fleet_token: str, sandbox: Path, method: str, path: str
    ) -> None:
        response = client_from(app).request(
            method,
            path,
            json={},
            headers=fleet_headers(fleet_token, **{FLEET_ELEVATED_HEADER: "1"}),
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"] == "forbidden"
        entries = audit_of(sandbox, "auth.fleet")
        assert entries[-1]["resource"] == path

    @pytest.mark.parametrize("key", ["web.ip_whitelist", "web", "fleet.anything", "central.role"])
    def test_patching_a_security_setting_is_refused(
        self, app: Any, fleet_token: str, key: str
    ) -> None:
        response = client_from(app).patch(
            "/api/config",
            json={"path": key, "value": []},
            headers=fleet_headers(fleet_token, **{FLEET_ELEVATED_HEADER: "1"}),
        )

        assert response.status_code == 403

    def test_patching_any_other_setting_is_not(self, app: Any, fleet_token: str) -> None:
        response = client_from(app).patch(
            "/api/config",
            json={"path": "notifications.enabled", "value": True},
            headers=fleet_headers(fleet_token, **{FLEET_ELEVATED_HEADER: "1"}),
        )

        assert response.status_code == 200, response.text

    def test_who_it_is_stays_readable(self, app: Any, fleet_token: str) -> None:
        response = client_from(app).get("/api/auth/verify", headers=fleet_headers(fleet_token))

        assert response.status_code == 200

    def test_the_table(self) -> None:
        assert fleet_refusal("GET", "/api/auth/session") is None
        assert fleet_refusal("GET", "/api/apps") is None
        assert fleet_refusal("DELETE", "/api/apps/shop.example.com") is None
        assert fleet_refusal("GET", "/api/nodesmith") is None
        assert fleet_refusal("GET", "/api/authors") is None
        assert fleet_refusal("GET", "/api/auth") is not None
        assert fleet_refusal("GET", "/ws/nodes/web-2/logs/x") is not None
        assert fleet_refusal("PATCH", "/api/config", "monitor.enabled") is None
        assert fleet_refusal("PATCH", "/api/config", " web.host") is not None

    def test_a_node_stream_through_the_node_is_refused(self, app: Any, fleet_token: str) -> None:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client_from(app).websocket_connect(
                "/ws/nodes/web-2/jobs", headers=fleet_headers(fleet_token)
            ):
                pass

        assert refused.value.code == 4403


class TestElevation:
    """A fleet token is exempt from the node's sudo window, not from the central's."""

    def test_without_the_centrals_word_an_elevated_action_is_refused(
        self, app: Any, fleet_token: str
    ) -> None:
        response = client_from(app).delete(
            "/api/apps/shop.example.com", headers=fleet_headers(fleet_token)
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert "central" in response.json()["detail"]

    def test_with_it_the_action_runs(self, app: Any, fleet_token: str) -> None:
        response = client_from(app).delete(
            "/api/apps/shop.example.com",
            headers=fleet_headers(fleet_token, **{FLEET_ELEVATED_HEADER: "1"}),
        )

        # Past sudo mode: the application simply does not exist.
        assert response.status_code == 404, response.text

    def test_the_header_means_nothing_to_a_session(self, app: Any) -> None:
        client = client_from(app, ("testclient", 50000))
        master = get_token_manager().generate_master_token()
        login = client.post("/api/auth/login", json={"token": master})
        client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]

        response = client.delete("/api/apps/shop.example.com", headers={FLEET_ELEVATED_HEADER: "1"})

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"


class TestWebSockets:
    """The fleet token opens the node's streams from the tunnel, and only there."""

    def test_from_the_tunnel_with_a_bearer_header(self, app: Any, fleet_token: str) -> None:
        with client_from(app).websocket_connect(
            "/ws/jobs", headers=fleet_headers(fleet_token)
        ) as ws:
            ws.close()

    def test_from_elsewhere_it_is_refused(self, app: Any, fleet_token: str) -> None:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client_from(app, ("203.0.113.9", 50000)).websocket_connect(
                "/ws/jobs", headers=fleet_headers(fleet_token)
            ):
                pass

        assert refused.value.code == 4401


class TestSelfRevocation:
    """Removing a node from a central ends the central's token on the node."""

    def test_a_fleet_token_revokes_itself(self, app: Any, fleet_token: str, sandbox: Path) -> None:
        client = client_from(app)

        response = client.post("/api/auth/fleet/revoke", headers=fleet_headers(fleet_token))

        assert response.status_code == 200, response.text
        assert response.json()["revoked"] == "fleet-nas"
        assert client.get("/api/auth/verify", headers=fleet_headers(fleet_token)).status_code == 401
        assert audit_of(sandbox, "auth.token.revoke")[-1]["actor"] == "token:fleet-nas"

    def test_no_other_credential_may_use_it(self, app: Any) -> None:
        issued = get_token_manager().create_api_token("ci", "admin")

        response = client_from(app).post(
            "/api/auth/fleet/revoke", headers={"Authorization": f"Bearer {issued['token']}"}
        )

        assert response.status_code == 403


class TestARetiredFleetTokenLocksNobodyOut:
    """
    A central still presenting a revoked or expired fleet token arrives from
    loopback, the same address as the operator's own SSH-tunnelled console.
    Counted against 127.0.0.1 it would lock the operator out for fifteen
    minutes; it is counted under the token instead, and audited.
    """

    @staticmethod
    def _retire(app: Any, fleet_token: str) -> None:
        response = client_from(app).post(
            "/api/auth/fleet/revoke", headers=fleet_headers(fleet_token)
        )
        assert response.status_code == 200, response.text

    def test_a_revoked_one_does_not_lock_out_loopback(
        self, app: Any, fleet_token: str, sandbox: Path
    ) -> None:
        from noust.web.server import get_brute_force

        self._retire(app, fleet_token)
        client = client_from(app)
        for _ in range(8):
            response = client.get("/api/auth/verify", headers=fleet_headers(fleet_token))
            assert response.status_code == 401

        protection = get_brute_force()
        assert not protection.is_locked(TUNNEL[0])
        assert protection.is_locked("fleet:fleet-nas")
        master = get_token_manager().generate_master_token()
        assert client.get("/api/auth/verify", headers=fleet_headers(master)).status_code == 200
        denied = [e for e in audit_of(sandbox, "auth.credential") if e["result"] == "denied"]
        assert denied and "fleet token fleet-nas" in denied[-1]["detail"]

    def test_nor_through_a_handshake_or_an_unauthenticated_path(
        self, app: Any, fleet_token: str
    ) -> None:
        from noust.web.server import get_brute_force

        self._retire(app, fleet_token)
        client = client_from(app)
        for _ in range(4):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws/jobs/x", headers=fleet_headers(fleet_token)):
                    pass
            client.get("/api/does-not-exist", headers=fleet_headers(fleet_token))
            client.get("/api/auth/session", headers=fleet_headers(fleet_token))

        assert not get_brute_force().is_locked(TUNNEL[0])

    def test_an_expired_one_neither(self, app: Any, fleet_token: str) -> None:
        from noust.web.server import get_brute_force

        manager = get_token_manager()
        with manager.sessions._lock, manager.sessions._conn:
            manager.sessions._conn.execute(
                "UPDATE api_tokens SET expires_at = 1 WHERE name = 'fleet-nas'"
            )
        client = client_from(app)
        for _ in range(8):
            client.get("/api/auth/verify", headers=fleet_headers(fleet_token))

        assert not get_brute_force().is_locked(TUNNEL[0])

    def test_a_guess_from_loopback_still_counts_there(self, app: Any) -> None:
        from noust.web.server import get_brute_force

        client = client_from(app)
        guess = "noust_" + "tok_" + "G" * 43
        for _ in range(8):
            client.get("/api/auth/verify", headers=fleet_headers(guess))

        assert get_brute_force().is_locked(TUNNEL[0])

    def test_a_retired_token_of_another_scope_still_counts(self, app: Any) -> None:
        # Only a central's token is exempt from the address: an operator's own
        # revoked token is an ordinary failure where it comes from.
        from noust.web.server import get_brute_force

        manager = get_token_manager()
        issued = manager.create_api_token("ci", "admin")
        manager.revoke_api_token(int(issued["id"]))
        client = client_from(app)
        for _ in range(8):
            client.get("/api/auth/verify", headers=fleet_headers(str(issued["token"])))

        assert get_brute_force().is_locked(TUNNEL[0])
