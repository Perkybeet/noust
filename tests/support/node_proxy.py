# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_node_proxy.py`` that other tests build on."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.websockets import WebSocketDisconnect

from noust.core.exceptions import NodeError, NodeUnreachableError
from noust.core.store import NodeRecord
from noust.fleet.client import NodeClient
from noust.web.api.openapi import ELEVATION_EXTENSION

#: The token the central holds for the node.
FLEET_TOKEN = "noust_tok_the-fleet-token"


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
