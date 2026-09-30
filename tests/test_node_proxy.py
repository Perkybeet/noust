# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central's side of the fleet: ``/api/nodes`` and the proxy to each node.

The node here is a small ASGI application served by uvicorn on a random
loopback port - the fake end of a tunnel - so every proxied request, stream
and WebSocket crosses a real socket, exactly as it does through ssh. The
central is the real application. The tunnel manager is the only fake between
them: it hands out that port instead of starting ssh.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from noust.core.exceptions import NodeError, NodeUnreachableError
from noust.core.runner import FakeRunner
from noust.core.store import NodeRecord
from noust.fleet import tunnels as tunnels_module
from noust.fleet.client import NodeClient
from noust.web import server as server_module
from noust.web.api import node_proxy
from noust.web.api import nodes as nodes_api
from noust.web.api.openapi import ELEVATION_EXTENSION
from noust.web.auth import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from noust.web.server import create_app, get_token_manager
from tests.test_web_auth import make_config, read_audit

#: The token the central holds for the node.
FLEET_TOKEN = "noust_tok_the-fleet-token"


# ------------------------------------------------------------- the node


class FakeNode:
    """
    What the node saw and what it serves.

    Attributes:
        seen: Every request the node received: method, path, query, headers, body.
        schema_fetches: How many times the central read the schema.
        handshakes: Headers of every WebSocket handshake.
    """

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []
        self.schema_fetches = 0
        self.handshakes: list[dict[str, str]] = []
        #: When set, /api/openapi.json serves this many bytes of padding
        #: instead of a real schema, to exercise the central's size cap.
        self.oversized_schema_bytes: int | None = None
        self.app = self._build()

    def _build(self) -> FastAPI:
        app = FastAPI()
        node = self

        @app.get("/api/openapi.json")
        def schema() -> Response:
            node.schema_fetches += 1
            if node.oversized_schema_bytes is not None:
                return Response(
                    content=b"[" + b"0" * node.oversized_schema_bytes + b"]",
                    media_type="application/json",
                )
            return JSONResponse(
                {
                    "openapi": "3.1.0",
                    "info": {"title": "node", "version": "3.0.0"},
                    "paths": {
                        "/api/apps": {"get": {}},
                        "/api/apps/import": {"post": {}},
                        "/api/apps/{domain}": {
                            "get": {},
                            "delete": {ELEVATION_EXTENSION: True},
                        },
                        "/api/echo/{rest}": {"get": {}, "post": {}},
                    },
                }
            )

        @app.api_route("/api/echo/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        async def echo(rest: str, request: Request) -> Response:
            body = (await request.body()).decode()
            node.seen.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "query": request.url.query,
                    "headers": {k.lower(): v for k, v in request.headers.items()},
                    "body": body,
                }
            )
            return JSONResponse(
                {"rest": rest, "body": body},
                headers={
                    "set-cookie": "node_session=stolen; Path=/",
                    "etag": '"v1"',
                    "x-node-internal": "yes",
                    "content-disposition": 'attachment; filename="x.json"',
                },
            )

        @app.api_route("/api/apps/{domain}", methods=["GET", "DELETE"])
        async def one_app(domain: str, request: Request) -> Response:
            node.seen.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "headers": {k.lower(): v for k, v in request.headers.items()},
                }
            )
            return JSONResponse(
                {"domain": domain}, status_code=202 if request.method == "DELETE" else 200
            )

        @app.get("/api/fail")
        def fail() -> Response:
            return JSONResponse(
                {
                    "error": "sitererror",
                    "detail": "nginx refused the configuration",
                    "hint": "Fix the server block",
                    "fields": None,
                    "output": "nginx: [emerg] unknown directive",
                },
                status_code=500,
            )

        @app.post("/api/server/power/reboot")
        async def reboot(request: Request) -> Response:
            node.seen.append({"method": "POST", "path": request.url.path})
            return JSONResponse(
                {
                    "id": 7,
                    "action": "reboot",
                    "scheduled_for": "2099-01-01T00:05:00+00:00",
                    "requested_by": "master",
                }
            )

        @app.delete("/api/server/power/scheduled")
        async def cancel(request: Request) -> Response:
            node.seen.append({"method": "DELETE", "path": request.url.path})
            return JSONResponse({"cancelled": True})

        @app.get("/api/revoked")
        def revoked() -> Response:
            return JSONResponse(
                {"error": "unauthorized", "detail": "Invalid or expired authentication token"},
                status_code=401,
            )

        @app.get("/api/moved")
        def moved(request: Request) -> Response:
            return RedirectResponse(f"{request.base_url}api/echo/landed", status_code=307)

        @app.get("/events")
        async def events(request: Request) -> Response:
            node.seen.append({"method": "GET", "path": "/events", "headers": dict(request.headers)})

            async def frames() -> AsyncIterator[str]:
                yield ": connected\n\n"
                for index in range(3):
                    yield f"event: machine\ndata: {json.dumps({'n': index})}\n\n"

            return StreamingResponse(frames(), media_type="text/event-stream")

        @app.websocket("/ws/echo")
        async def ws_echo(websocket: WebSocket) -> None:
            node.handshakes.append({k.lower(): v for k, v in websocket.headers.items()})
            await websocket.accept()
            await websocket.send_text(json.dumps({"query": websocket.url.query}))
            while True:
                try:
                    message = await websocket.receive_text()
                except WebSocketDisconnect:
                    return
                if message.startswith("close-"):
                    await websocket.close(code=int(message.split("-", 1)[1]), reason="node closed")
                    return
                await websocket.send_text(f"echo:{message}")

        @app.websocket("/ws/refuse")
        async def ws_refuse(websocket: WebSocket) -> None:
            await websocket.close(code=1008)

        return app


class ServedNode:
    """A fake node listening on a random loopback port, in a thread."""

    def __init__(self, node: FakeNode) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = int(self.sock.getsockname()[1])
        config = uvicorn.Config(node.app, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.sock]}, daemon=True
        )

    def __enter__(self) -> ServedNode:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the fake node did not start")
            time.sleep(0.01)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.sock.close()


# ------------------------------------------------------------ the fleet


class FakeTunnels:
    """The tunnel manager, handing out the fake node's port instead of ssh's."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.error: NodeUnreachableError | None = None
        self.closed_all = False
        self.leases = 0
        self.leased = 0

    def endpoint(self, node: str) -> tuple[str, int]:
        if self.error is not None:
            raise self.error
        return "127.0.0.1", self.port

    def hold(self, node: str) -> tuple[tuple[str, int], Any]:
        endpoint = self.endpoint(node)
        self.leases += 1
        self.leased += 1

        def release() -> None:
            self.leases -= 1

        return endpoint, release

    def status(self, node: str) -> dict[str, Any]:
        return {
            "open": True,
            "local_port": self.port,
            "since": None,
            "last_error": None,
            "failures": 0,
        }

    def close(self, node: str) -> None:
        return None

    def close_all(self) -> None:
        self.closed_all = True


class FakeSecrets:
    """Where the node's token is kept."""

    def read(self, name: str) -> str:
        return FLEET_TOKEN


class FakeManager:
    """The node registry, over the fake tunnels and the real ``NodeClient``."""

    def __init__(self, tunnels: FakeTunnels) -> None:
        self.tunnels = tunnels
        self.records = {
            "web-2": NodeRecord(
                name="web-2",
                ssh_host="web2.example.com",
                ssh_port=22,
                ssh_user="root",
                host_key="web2.example.com ssh-ed25519 AAAA",
                console_port=8080,
                version="3.0.0",
                status="reachable",
                created_at="2026-09-29T00:00:00+00:00",
            )
        }
        self.removed: list[tuple[str, bool]] = []

    def get(self, name: str) -> NodeRecord:
        if name not in self.records:
            raise NodeError(f"No node named {name} is registered on this central")
        return self.records[name]

    def list(self) -> list[NodeRecord]:
        return list(self.records.values())

    def client(self, name: str, *, timeout: float = 30.0) -> NodeClient:
        return NodeClient(
            name,
            tunnels=self.tunnels,  # type: ignore[arg-type]
            secrets=FakeSecrets(),  # type: ignore[arg-type]
            store=self,  # type: ignore[arg-type]
        )

    # The store's side NodeClient reads and records a node's status on.
    def get_node(self, name: str) -> NodeRecord | None:
        return self.records.get(name)

    def set_node_status(self, name: str, status: str, *, version: str | None = None) -> bool:
        if name not in self.records:
            return False
        self.records[name] = replace(self.records[name], status=status)
        return True

    def add(self, name: str, *, ssh_target: str, join_code: str) -> NodeRecord:
        if join_code == "blocked":
            raise NodeError(
                "This central cannot register a node yet",
                details="Turn on two-factor authentication first.",
            )
        if join_code == "unreachable":
            raise NodeUnreachableError(
                "ssh could not connect to web3.example.com",
                details="ssh: connect to host web3.example.com port 22: Connection refused",
            )
        record = replace(self.records["web-2"], name=name, ssh_host=ssh_target)
        self.records[name] = record
        return record

    def remove(self, name: str, *, revoke: bool = True) -> list[str]:
        self.get(name)
        self.removed.append((name, revoke))
        del self.records[name]
        return ["On the node, 'noust fleet deauthorize --name nas' removes this central's key."]

    def test(self, name: str) -> dict[str, Any]:
        return {"reachable": True, "status": "reachable", "version": "3.0.0", "latency_ms": 1.0}

    def central_public_key(self, name: str) -> str:
        return "ssh-ed25519 AAAAC3Nza noust-central@nas"

    def authorize_command(self, name: str) -> str:
        return "noust fleet authorize --central-key 'ssh-ed25519 AAAAC3Nza noust-central@nas' --name nas"


@pytest.fixture(scope="module")
def fake_node() -> Iterator[tuple[FakeNode, int]]:
    """
    Yields:
        The fake node and its port, for the whole module.
    """
    node = FakeNode()
    with ServedNode(node) as served:
        yield node, served.port


@pytest.fixture
def node(fake_node: tuple[FakeNode, int]) -> FakeNode:
    """
    Returns:
        The fake node, with what it saw forgotten.
    """
    fake, _ = fake_node
    fake.seen.clear()
    fake.handshakes.clear()
    fake.schema_fetches = 0
    fake.oversized_schema_bytes = None
    node_proxy.node_schemas.forget()
    return fake


@pytest.fixture
def manager(fake_node: tuple[FakeNode, int], monkeypatch: pytest.MonkeyPatch) -> FakeManager:
    """
    Returns:
        The registry the central's API uses.
    """
    fake = FakeManager(FakeTunnels(fake_node[1]))
    monkeypatch.setattr(nodes_api, "node_manager", lambda: fake)
    monkeypatch.setattr(nodes_api, "tunnels", lambda: fake.tunnels)
    return fake


@pytest.fixture
def central(sandbox: Path, runner: FakeRunner, manager: FakeManager, node: FakeNode) -> TestClient:
    """
    Returns:
        A client of the central, not signed in.
    """
    app = create_app(make_config(sandbox))
    return TestClient(app, client=("testclient", 50000))


@pytest.fixture
def master(central: TestClient) -> dict[str, str]:
    """
    Returns:
        Headers carrying the central's master token.
    """
    return {"Authorization": f"Bearer {get_token_manager().generate_master_token()}"}


def token_headers(scope: str, name: str) -> dict[str, str]:
    """
    Args:
        scope: The API token's scope.
        name: Its name.

    Returns:
        Headers carrying a new API token of the central.
    """
    issued = get_token_manager().create_api_token(name, scope)
    return {"Authorization": f"Bearer {issued['token']}"}


def sign_in(central: TestClient) -> str:
    """
    Sign the client in with a cookie session.

    Args:
        central: The client.

    Returns:
        The master token, for elevating later.
    """
    master_token = get_token_manager().generate_master_token()
    response = central.post("/api/auth/login", json={"token": master_token})
    assert response.status_code == 200, response.text
    central.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return master_token


# ------------------------------------------------------------ /api/nodes


class TestRegistry:
    def test_list_with_tunnel_status(self, central: TestClient, master: dict[str, str]) -> None:
        response = central.get("/api/nodes", headers=master)

        assert response.status_code == 200
        (item,) = response.json()["items"]
        assert item["name"] == "web-2"
        assert item["status"] == "reachable"
        assert item["tunnel"]["open"] is True

    def test_one_and_unknown(self, central: TestClient, master: dict[str, str]) -> None:
        assert (
            central.get("/api/nodes/web-2", headers=master).json()["ssh_host"] == "web2.example.com"
        )
        missing = central.get("/api/nodes/web-9", headers=master)
        assert missing.status_code == 404
        assert missing.json()["error"] == "not_found"

    def test_adding_needs_sudo_mode(self, central: TestClient) -> None:
        sign_in(central)

        response = central.post(
            "/api/nodes", json={"name": "web-3", "ssh_target": "web3.example.com", "join_code": "x"}
        )

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_adding(self, central: TestClient, master: dict[str, str], sandbox: Path) -> None:
        response = central.post(
            "/api/nodes",
            json={"name": "web-3", "ssh_target": "root@web3.example.com", "join_code": "code"},
            headers=master,
        )

        assert response.status_code == 201, response.text
        assert response.json()["name"] == "web-3"
        assert any(
            e["action"] == "fleet.node.add" and e["resource"] == "node:web-3"
            for e in read_audit(sandbox)
        )

    def test_a_policy_blocker_is_a_400_with_its_sentences(
        self, central: TestClient, master: dict[str, str]
    ) -> None:
        response = central.post(
            "/api/nodes",
            json={"name": "web-3", "ssh_target": "web3.example.com", "join_code": "blocked"},
            headers=master,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "This central cannot register a node yet"
        assert response.json()["hint"] == "Turn on two-factor authentication first."

    def test_an_unreachable_node_is_a_502_with_ssh_verbatim(
        self, central: TestClient, master: dict[str, str]
    ) -> None:
        response = central.post(
            "/api/nodes",
            json={"name": "web-3", "ssh_target": "web3.example.com", "join_code": "unreachable"},
            headers=master,
        )

        assert response.status_code == 502
        body = response.json()
        assert body["error"] == "node_unreachable"
        assert body["output"] == "ssh: connect to host web3.example.com port 22: Connection refused"

    def test_removing(
        self, central: TestClient, master: dict[str, str], manager: FakeManager
    ) -> None:
        response = central.delete("/api/nodes/web-2?revoke=false", headers=master)

        assert response.status_code == 200
        assert response.json()["name"] == "web-2"
        assert manager.removed == [("web-2", False)]

    def test_testing(self, central: TestClient, master: dict[str, str]) -> None:
        response = central.post("/api/nodes/web-2/test", headers=master)

        assert response.status_code == 200
        assert response.json()["reachable"] is True

    def test_the_key_is_admin_only_and_audited(
        self, central: TestClient, master: dict[str, str], sandbox: Path
    ) -> None:
        response = central.get("/api/nodes/web-5/key", headers=master)
        assert response.status_code == 200
        assert response.json()["authorize_command"].startswith("noust fleet authorize")
        assert any(e["action"] == "fleet.node.key" for e in read_audit(sandbox))

        reader = token_headers("read", "dashboard")
        assert central.get("/api/nodes/web-5/key", headers=reader).status_code == 403


# ------------------------------------------------------------ the proxy


def proxied(central: TestClient, method: str, path: str, **kwargs: Any) -> Any:
    """
    Args:
        central: The central's client.
        method: HTTP method.
        path: The node's path after ``/api/``.
        **kwargs: Request arguments.

    Returns:
        The central's response.
    """
    return central.request(method, f"/api/nodes/web-2/api/{path}", **kwargs)


class TestForwarding:
    def test_query_body_and_the_fleet_headers(
        self, central: TestClient, master: dict[str, str], node: FakeNode
    ) -> None:
        response = proxied(
            central,
            "POST",
            "echo/a/b?x=1&y=two%20words",
            content=b'{"hello": "node"}',
            headers={**master, "content-type": "application/json", "if-none-match": '"v0"'},
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"rest": "a/b", "body": '{"hello": "node"}'}
        (seen,) = node.seen
        assert seen["path"] == "/api/echo/a/b"
        assert seen["query"] == "x=1&y=two%20words"
        assert seen["headers"]["authorization"] == f"Bearer {FLEET_TOKEN}"
        assert seen["headers"]["x-noust-actor"] == "master"
        assert seen["headers"]["x-noust-actor-scope"] == "admin"
        # The master token is exempt from sudo mode on the central.
        assert seen["headers"]["x-noust-elevated"] == "1"
        assert seen["headers"]["content-type"] == "application/json"
        assert seen["headers"]["if-none-match"] == '"v0"'

    def test_a_reboot_asked_through_the_central_is_expected_until_cancelled(
        self, central: TestClient, master: dict[str, str], node: FakeNode, manager: FakeManager
    ) -> None:
        from noust.core.store import get_store
        from noust.fleet import expected

        get_store().save_node(manager.records["web-2"])

        response = proxied(central, "POST", "server/power/reboot", json={}, headers=master)

        assert response.status_code == 200, response.text
        assert response.json()["scheduled_for"] == "2099-01-01T00:05:00+00:00"
        outage = expected.current("web-2")
        assert outage is not None
        assert outage.due_at == "2099-01-01T00:05:00+00:00"
        assert outage.requested_by == "master"

        cancelled = proxied(central, "DELETE", "server/power/scheduled", headers=master)

        assert cancelled.status_code == 200, cancelled.text
        assert expected.current("web-2") is None

    def test_the_centrals_own_credentials_stay_on_the_central(
        self, central: TestClient, node: FakeNode
    ) -> None:
        sign_in(central)

        response = proxied(
            central, "POST", "echo/x", json={}, headers={"X-Forwarded-For": "1.2.3.4"}
        )

        assert response.status_code == 200, response.text
        headers = node.seen[-1]["headers"]
        assert "cookie" not in headers
        assert CSRF_HEADER_NAME.lower() not in headers
        assert "x-forwarded-for" not in headers
        assert headers["authorization"] == f"Bearer {FLEET_TOKEN}"
        # A cookie session that has not confirmed is not vouched for.
        assert "x-noust-elevated" not in headers
        assert SESSION_COOKIE_NAME not in json.dumps(headers)

    def test_response_headers_are_filtered(
        self, central: TestClient, master: dict[str, str]
    ) -> None:
        response = proxied(central, "GET", "echo/x", headers=master)

        assert "node_session" not in response.headers.get("set-cookie", "")
        assert "x-node-internal" not in response.headers
        assert response.headers["etag"] == '"v1"'
        assert response.headers["content-disposition"] == 'attachment; filename="x.json"'
        # The central's own hardening still applies.
        assert "Content-Security-Policy" in response.headers

    def test_a_node_error_passes_through_verbatim(
        self, central: TestClient, master: dict[str, str]
    ) -> None:
        response = proxied(central, "GET", "fail", headers=master)

        assert response.status_code == 500
        assert response.json()["output"] == "nginx: [emerg] unknown directive"
        assert response.json()["error"] == "sitererror"

    def test_a_node_401_is_never_the_operators_401(
        self, central: TestClient, master: dict[str, str]
    ) -> None:
        response = proxied(central, "GET", "revoked", headers=master)

        assert response.status_code == 502
        assert response.json()["error"] == "node_refused"
        assert "Invalid or expired" in response.json()["output"]

    def test_a_node_401_is_recorded_and_the_node_left_alone(
        self, central: TestClient, master: dict[str, str], node: FakeNode, manager: FakeManager
    ) -> None:
        proxied(central, "GET", "revoked", headers=master)
        assert manager.records["web-2"].status == "refused"
        seen = len(node.seen)

        # Every later call stops here: a revoked token presented again would
        # only be another failure in the node's audit log.
        response = proxied(central, "GET", "echo/x", headers=master)
        events = central.get("/api/nodes/web-2/events", headers=master)

        assert response.status_code == 502
        assert response.json()["error"] == "node_refused"
        assert "noust node test web-2" in response.json()["output"]
        assert events.status_code == 502
        assert len(node.seen) == seen

    def test_a_redirect_stays_behind_the_proxy(
        self, central: TestClient, master: dict[str, str]
    ) -> None:
        response = proxied(central, "GET", "moved", headers=master, follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "/api/nodes/web-2/api/echo/landed"

    def test_a_dead_tunnel_is_a_502(
        self, central: TestClient, master: dict[str, str], manager: FakeManager
    ) -> None:
        manager.tunnels.error = NodeUnreachableError(
            "The tunnel to web-2 failed", details="Host key verification failed."
        )

        response = proxied(central, "GET", "echo/x", headers=master)

        assert response.status_code == 502
        assert response.json()["error"] == "node_unreachable"
        assert response.json()["output"] == "Host key verification failed."

    def test_a_closed_port_is_a_502(
        self, central: TestClient, master: dict[str, str], manager: FakeManager
    ) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            manager.tunnels.port = int(probe.getsockname()[1])

        response = proxied(central, "GET", "echo/x", headers=master)

        assert response.status_code == 502
        assert response.json()["error"] == "node_unreachable"

    def test_unknown_node(self, central: TestClient, master: dict[str, str]) -> None:
        assert central.get("/api/nodes/nope/api/apps", headers=master).status_code == 404

    def test_what_no_node_accepts_is_refused_here(
        self, central: TestClient, master: dict[str, str], node: FakeNode
    ) -> None:
        for method, path in (("POST", "auth/tokens"), ("POST", "auth/login"), ("GET", "nodes")):
            response = proxied(central, method, path, json={}, headers=master)
            assert response.status_code == 403, path
        assert node.seen == []

    def test_every_mutation_is_audited_on_the_central(
        self, central: TestClient, master: dict[str, str], sandbox: Path
    ) -> None:
        proxied(central, "POST", "echo/x", json={}, headers=master)

        entry = [e for e in read_audit(sandbox) if e["action"] == "api.post"][-1]
        assert entry["resource"] == "/api/nodes/web-2/api/echo/x"
        assert entry["actor"] == "master"


class TestScopes:
    def test_a_read_token_reads_as_read(self, central: TestClient, node: FakeNode) -> None:
        reader = token_headers("read", "dashboard")

        response = proxied(central, "GET", "echo/x", headers=reader)

        assert response.status_code == 200
        assert node.seen[-1]["headers"]["x-noust-actor-scope"] == "read"
        assert node.seen[-1]["headers"]["x-noust-actor"] == "token:dashboard"

    @pytest.mark.parametrize("scope", ["read", "deploy"])
    def test_only_admin_mutates(self, central: TestClient, node: FakeNode, scope: str) -> None:
        response = proxied(
            central, "POST", "echo/x", json={}, headers=token_headers(scope, f"t-{scope}")
        )

        assert response.status_code == 403
        assert node.seen == []

    def test_a_deploy_token_clears_the_proxy_for_a_deploy_scoped_path(
        self, central: TestClient, node: FakeNode
    ) -> None:
        # /api/jobs/update is a deploy-scope path locally; through the proxy
        # it is /api/nodes/web-2/api/jobs/update, and must be judged the
        # same way. The fake node has no such route, so reaching it (a 404
        # from the node, not a 403 from the central) is what proves the
        # scope table matched on the path after the /api/nodes/{node}
        # prefix, not before it.
        response = proxied(
            central, "POST", "jobs/update", json={}, headers=token_headers("deploy", "deployer")
        )

        assert response.status_code == 404, response.text
        assert node.seen == []


class TestElevation:
    def test_an_elevated_operation_needs_the_centrals_sudo_mode(
        self, central: TestClient, node: FakeNode
    ) -> None:
        master_token = sign_in(central)

        refused = proxied(central, "DELETE", "apps/shop.example.com")
        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"
        assert node.seen == []

        assert central.post("/api/auth/elevate", json={"token": master_token}).status_code == 200
        allowed = proxied(central, "DELETE", "apps/shop.example.com")

        assert allowed.status_code == 202, allowed.text
        assert node.seen[-1]["headers"]["x-noust-elevated"] == "1"

    def test_a_plain_read_of_the_same_path_does_not(
        self, central: TestClient, node: FakeNode
    ) -> None:
        sign_in(central)

        assert proxied(central, "GET", "apps/shop.example.com").status_code == 200

    def test_the_schema_is_cached_per_node_version(
        self, central: TestClient, master: dict[str, str], node: FakeNode, manager: FakeManager
    ) -> None:
        for _ in range(3):
            proxied(central, "GET", "echo/x", headers=master)
        assert node.schema_fetches == 1

        manager.records["web-2"] = replace(manager.records["web-2"], version="3.0.1")
        proxied(central, "GET", "echo/x", headers=master)

        assert node.schema_fetches == 2

    def test_a_schema_over_the_size_cap_is_a_clean_502_not_a_memory_blowout(
        self, central: TestClient, master: dict[str, str], node: FakeNode
    ) -> None:
        node.oversized_schema_bytes = node_proxy._MAX_SCHEMA_BYTES + 1

        response = proxied(central, "GET", "echo/x", headers=master)

        assert response.status_code == 502
        assert response.json()["error"] == "node_unreachable"
        assert node.seen == []

    def test_the_cap_has_ample_headroom_over_nousts_own_schema(self) -> None:
        # Noust's own openapi.json (committed at panel/openapi.json) is what
        # a node this size actually serves; the cap must clear it many times
        # over, not merely exceed it.
        openapi = Path(__file__).resolve().parents[1] / "panel" / "openapi.json"
        assert node_proxy._MAX_SCHEMA_BYTES >= openapi.stat().st_size * 4

    def test_matching_follows_the_nodes_route_order(self) -> None:
        schema = node_proxy.compile_schema(
            {
                "paths": {
                    "/api/apps/import": {"post": {}},
                    "/api/apps/{domain}": {"post": {ELEVATION_EXTENSION: True}, "get": {}},
                    "/api/apps/{domain}/releases/{release}/activate": {"post": {}},
                }
            },
            "3.0.0",
        )

        assert not schema.requires_elevation("POST", "/api/apps/import")
        assert schema.requires_elevation("POST", "/api/apps/shop.example.com")
        assert not schema.requires_elevation("GET", "/api/apps/shop.example.com")
        assert not schema.requires_elevation("POST", "/api/apps/a/releases/b/activate")
        assert not schema.requires_elevation("POST", "/api/apps/a/b")

    def test_a_trailing_or_doubled_slash_matches_the_same_as_the_plain_path(self) -> None:
        schema = node_proxy.compile_schema(
            {"paths": {"/api/apps/{domain}": {"delete": {ELEVATION_EXTENSION: True}}}},
            "3.0.0",
        )

        assert schema.requires_elevation("DELETE", "/api/apps/x")
        assert schema.requires_elevation("DELETE", "/api/apps/x/")
        assert schema.requires_elevation("DELETE", "/api//apps/x")
        assert schema.requires_elevation("DELETE", "/api/apps/x//")


class TestEvents:
    def test_the_nodes_stream_is_relayed(
        self, central: TestClient, master: dict[str, str], node: FakeNode
    ) -> None:
        with central.stream(
            "GET", "/api/nodes/web-2/events", headers={**master, "Last-Event-ID": "7"}
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())

        assert ": connected" in body
        assert body.count("event: machine") == 3
        seen = node.seen[-1]["headers"]
        assert seen["authorization"] == f"Bearer {FLEET_TOKEN}"
        assert seen["last-event-id"] == "7"

    def test_a_dead_tunnel_is_a_502(
        self, central: TestClient, master: dict[str, str], manager: FakeManager
    ) -> None:
        manager.tunnels.error = NodeUnreachableError(
            "The tunnel to web-2 failed", details="timeout"
        )

        response = central.get("/api/nodes/web-2/events", headers=master)

        assert response.status_code == 502

    def test_the_stream_leases_the_tunnel_while_it_runs(
        self, central: TestClient, master: dict[str, str], manager: FakeManager
    ) -> None:
        before = manager.tunnels.leased
        # The test client runs the stream to its end before handing it over,
        # so what is observable is that a lease was taken and returned.
        with central.stream("GET", "/api/nodes/web-2/events", headers=master) as response:
            assert response.status_code == 200
            "".join(response.iter_text())

        assert manager.tunnels.leased == before + 1
        assert manager.tunnels.leases == 0

    def test_a_plain_call_takes_no_lease(
        self, central: TestClient, master: dict[str, str], manager: FakeManager
    ) -> None:
        before = manager.tunnels.leased
        proxied(central, "GET", "echo/x", headers=master)
        assert manager.tunnels.leased == before


class TestWebSockets:
    def test_a_round_trip(
        self, central: TestClient, master: dict[str, str], node: FakeNode, manager: FakeManager
    ) -> None:
        leased = manager.tunnels.leased
        with central.websocket_connect(
            "/ws/nodes/web-2/echo?ticket=central-only&lines=5", headers=master
        ) as ws:
            first = json.loads(ws.receive_text())
            ws.send_text("hello")
            assert ws.receive_text() == "echo:hello"

        assert first == {"query": "lines=5"}
        (handshake,) = node.handshakes
        assert handshake["authorization"] == f"Bearer {FLEET_TOKEN}"
        # The socket held the tunnel for its whole life, and gave it back.
        assert manager.tunnels.leased == leased + 1
        assert manager.tunnels.leases == 0
        assert handshake["x-noust-actor"] == "master"
        assert "cookie" not in handshake

    def test_the_nodes_close_code_reaches_the_browser(
        self, central: TestClient, master: dict[str, str], node: FakeNode
    ) -> None:
        with central.websocket_connect("/ws/nodes/web-2/echo", headers=master) as ws:
            ws.receive_text()
            ws.send_text("close-4003")
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()

        assert closed.value.code == 4003

    def test_the_subprotocol_is_answered_here(self, central: TestClient, node: FakeNode) -> None:
        token = get_token_manager().generate_master_token()
        with central.websocket_connect(
            "/ws/nodes/web-2/echo", subprotocols=["wasm.auth", f"wasm.token.{token}"]
        ) as ws:
            assert ws.accepted_subprotocol == "wasm.auth"
            ws.receive_text()

        assert "sec-websocket-protocol" not in node.handshakes[-1]

    def test_an_unknown_node(self, central: TestClient, master: dict[str, str]) -> None:
        with central.websocket_connect("/ws/nodes/nope/echo", headers=master) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()

        assert closed.value.code == 4404

    def test_an_unreachable_node(
        self, central: TestClient, master: dict[str, str], manager: FakeManager
    ) -> None:
        manager.tunnels.error = NodeUnreachableError(
            "The tunnel to web-2 failed", details="refused"
        )

        with central.websocket_connect("/ws/nodes/web-2/echo", headers=master) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()

        assert closed.value.code == 4502

    def test_anonymous_is_refused(self, central: TestClient) -> None:
        with pytest.raises(WebSocketDisconnect) as refused:
            with central.websocket_connect("/ws/nodes/web-2/echo"):
                pass

        assert refused.value.code == 4401

    def test_a_refused_handshake_says_so(
        self, central: TestClient, master: dict[str, str], node: FakeNode
    ) -> None:
        with central.websocket_connect("/ws/nodes/web-2/refuse", headers=master) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()

        assert closed.value.code == 4403

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_the_legacy_client_debian_ships_relays_too(
        self,
        central: TestClient,
        master: dict[str, str],
        node: FakeNode,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # websockets 10.x, as Debian 12 and Ubuntu 24.04 ship it, has no
        # websockets.asyncio; a None entry makes the import fail the same way.
        monkeypatch.setitem(sys.modules, "websockets.asyncio.client", None)

        with central.websocket_connect("/ws/nodes/web-2/echo", headers=master) as ws:
            ws.receive_text()
            ws.send_text("old")
            assert ws.receive_text() == "echo:old"
            ws.send_text("close-4001")
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()

        assert closed.value.code == 4001
        assert node.handshakes[-1]["authorization"] == f"Bearer {FLEET_TOKEN}"

    def test_the_relay_maps_close_codes(self) -> None:
        assert node_proxy.relayable_close_code(4003) == 4003
        assert node_proxy.relayable_close_code(1000) == 1000
        assert node_proxy.relayable_close_code(1005) == 1000
        assert node_proxy.relayable_close_code(1006) == 1011
        assert node_proxy.relayable_close_code(None) == 1011

    def test_an_unexpected_relay_error_is_logged_not_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        asyncio.gather(..., return_exceptions=True) in _pump's finally block
        collects every task's outcome so cancelling the others cannot raise
        past it; that must not mean a real bug in the relay (anything but
        the ConnectionClosed each task already handles, or the
        CancelledError cancelling its siblings causes) vanishes silently.
        """

        class BoomUpstream:
            close_code = None
            close_reason = None

            def __aiter__(self) -> BoomUpstream:
                return self

            async def __anext__(self) -> str:
                raise RuntimeError("boom from the node")

            async def send(self, message: str | bytes) -> None:
                pass

            async def close(self, code: int = 1000, reason: str = "") -> None:
                pass

        class _State:
            name = "CONNECTED"

        class HangingBrowserSocket:
            client_state = _State()

            async def receive(self) -> dict[str, Any]:
                await asyncio.sleep(60)
                raise AssertionError("never reached")

            async def accept(self) -> None:
                pass

            async def close(self, code: int = 1000, reason: str = "") -> None:
                pass

        async def exercise() -> None:
            await node_proxy._pump(
                cast(WebSocket, HangingBrowserSocket()),
                cast(node_proxy.NodeSocket, BoomUpstream()),
                {},
            )

        with caplog.at_level("WARNING", logger="noust.web.api.node_proxy"):
            asyncio.run(exercise())

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        # Only the one genuine failure is logged, not the two siblings
        # asyncio.CancelledError leaves behind when it cancels them.
        assert len(warnings) == 1
        record = warnings[0]
        exc_text = str(record.exc_info[1]) if record.exc_info else ""
        assert "boom from the node" in (record.getMessage() + exc_text)


def test_stopping_the_server_closes_every_tunnel(monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.core import sealing
    from tests.test_web_shutdown import _quiet_lifespan

    _quiet_lifespan(monkeypatch)
    removed: list[Path] = []
    monkeypatch.setattr(sealing, "remove_plaintext_copies", removed.append)
    fake = FakeTunnels(1)
    tunnels_module.set_tunnels(fake)  # type: ignore[arg-type]
    try:

        async def exercise() -> None:
            async with server_module.lifespan(FastAPI()):
                assert fake.closed_all is False

        asyncio.run(exercise())
    finally:
        tunnels_module.set_tunnels(None)

    assert fake.closed_all is True
    # The decrypted copies a sealed store handed ssh go with the process.
    assert len(removed) == 1


class TestForwardedIdentityNeverWidens:
    """Security review 3.1, finding 4: a narrowed token is not its owner on a node."""

    def payload(self, permissions: frozenset[str], role: str = "admin") -> dict[str, Any]:
        from noust.web.permissions.roles import legacy_scope

        return {
            "type": "api_token",
            "sid": "token:ci",
            "token_name": "ci",
            "role": role,
            "owner_account_id": 1,
            "permissions": permissions,
            "scope": legacy_scope(permissions),
            "elevation_exempt": True,
            "ip": "127.0.0.1",
        }

    def test_a_viewer_token_of_an_admin_is_a_viewer_on_the_node(self) -> None:
        from noust.web.permissions.roles import VIEWER

        identity = node_proxy.forwarded_identity(self.payload(VIEWER))
        assert identity["actor_role"] == "viewer"
        assert identity["actor_scope"] == "read"

    def test_a_token_missing_one_admin_permission_is_not_admin(self) -> None:
        from noust.web.permissions.roles import ADMIN

        identity = node_proxy.forwarded_identity(self.payload(ADMIN - {"root_equivalent"}))
        assert identity["actor_role"] == "operator"

    def test_a_full_admin_is_still_admin(self) -> None:
        from noust.web.permissions.roles import ADMIN

        assert node_proxy.forwarded_identity(self.payload(ADMIN))["actor_role"] == "admin"
