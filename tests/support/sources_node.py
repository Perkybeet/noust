# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A fake node that answers the way a 4.1 node does about the repositories its central serves it.

It holds the leases it is given (the token too, so a test can tell which one it was handed), takes
the events a central forwards, and lets a test say what each answer is: the jobs an event queued,
how a job and its deployment end, what a preview looks like. A node held to ``read`` refuses the
central's writes with the node's own code, as the real one does.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.support.fleet_views_support import Node

#: The routes a 4.1 node offers for this, as its OpenAPI schema lists them.
SOURCES_SCHEMA: dict[str, dict[str, Any]] = {
    "/api/sources/leases": {"get": {}, "put": {}, "delete": {}},
    "/api/sources/events": {"post": {}},
    "/api/jobs/{job_id}": {"get": {}},
    "/api/deployments/{deployment_id}": {"get": {}},
    "/api/apps/{domain}/previews": {"get": {}},
}


@dataclass
class Received:
    """One request the node took, with who it said it was."""

    method: str
    path: str
    actor: str
    scope: str
    body: Any


@dataclass
class SourcesNode:
    """
    What a fake node holds of its central's repositories, and how it answers about them.

    Attributes:
        node: The fake node whose answers this wraps.
        central: The central, as the node knows it.
        ceiling: ``read``, ``deploy`` or ``admin``: a ``read`` node refuses the central's writes.
        leases: ``key`` to the body of the lease it holds, token included.
        puts: Every lease it was handed, in order.
        events: Every event it was forwarded, in order.
        answer: What an event is answered with (status and body), given the event's body.
        deployments: ``id`` to a deployment as ``GET /api/deployments/{id}`` shows it.
        previews: Parent domain to the previews ``GET .../previews`` lists.
        lease_status: Status to answer a lease PUT with instead of taking it.
    """

    node: Node
    central: str = "nas"
    ceiling: str = "deploy"
    leases: dict[str, dict[str, Any]] = field(default_factory=dict)
    puts: list[Received] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    events: list[Received] = field(default_factory=list)
    answer: Callable[[dict[str, Any]], tuple[int, dict[str, Any]]] | None = None
    deployments: dict[int, dict[str, Any]] = field(default_factory=dict)
    previews: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    lease_status: int | None = None

    def __post_init__(self) -> None:
        self.node.schema.update(SOURCES_SCHEMA)
        self._fallback = self.node.handler
        self.node.handler = self.handle  # type: ignore[method-assign]

    def job(
        self,
        job_id: str,
        state: str,
        *,
        deployment_id: int | None = None,
        error: str | None = None,
    ) -> None:
        """Set how a job stands, as ``GET /api/jobs/{id}`` shows it."""
        self.node.jobs[job_id] = {
            "id": job_id,
            "status": {"succeeded": "completed"}.get(state, state),
            "state": state,
            "deployment_id": deployment_id,
            "result": None,
            "error": error,
            "log": "",
        }

    def deployment(self, number: int, **fields: Any) -> None:
        """Set a deployment, as ``GET /api/deployments/{id}`` shows it."""
        self.deployments[number] = {"id": number, "status": "success", **fields}

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one request."""
        path, method = request.url.path, request.method
        if self.node.gate is not None:
            self.node.gate.wait(10)
        if self.node.down:
            raise httpx.ConnectError("Connection refused", request=request)
        if request.headers.get("Authorization") != f"Bearer {self.node.token}":
            return self._fallback(request)
        received = Received(
            method=method,
            path=path,
            actor=request.headers.get("X-Noust-Actor", ""),
            scope=request.headers.get("X-Noust-Actor-Scope", ""),
            body=json.loads(request.content) if request.content else None,
        )
        if path == "/api/sources/leases":
            return self._leases(request, received)
        if path == "/api/sources/events" and method == "POST":
            self.node.requests.append(request)
            if self.ceiling == "read":
                return self._refused()
            self.events.append(received)
            status, body = (self.answer or (lambda _event: (200, {"jobs": []})))(received.body)
            return httpx.Response(status, json=body)
        found = re.fullmatch(r"/api/deployments/(\d+)", path)
        if found and method == "GET":
            self.node.requests.append(request)
            known = self.deployments.get(int(found.group(1)))
            if known is None:
                return httpx.Response(404, json={"error": "not_found"})
            return httpx.Response(200, json=known)
        found = re.fullmatch(r"/api/apps/([^/]+)/previews", path)
        if found and method == "GET":
            self.node.requests.append(request)
            listed = self.previews.get(found.group(1), [])
            return httpx.Response(
                200,
                json={
                    "domain": found.group(1),
                    "enabled": True,
                    "previews": listed,
                    "total": len(listed),
                },
            )
        return self._fallback(request)

    def _refused(self) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": "permission_denied",
                "detail": "This server lets its central only read",
                "hint": "noust fleet access --level deploy",
            },
        )

    def _leases(self, request: httpx.Request, received: Received) -> httpx.Response:
        self.node.requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {k: v for k, v in lease.items() if k != "token"}
                        for lease in self.leases.values()
                    ]
                },
            )
        if self.ceiling == "read":
            return self._refused()
        if request.method == "PUT":
            self.puts.append(received)
            if self.lease_status is not None:
                return httpx.Response(
                    self.lease_status, json={"error": "validation_error", "detail": "no"}
                )
            body = dict(received.body or {})
            self.leases[str(body["key"])] = body
            return httpx.Response(
                200,
                json={k: v for k, v in body.items() if k != "token"} | {"central": self.central},
            )
        if request.method == "DELETE":
            key = request.url.params.get("key")
            self.deletes.append(str(key))
            return httpx.Response(
                200, json={"dropped": int(self.leases.pop(str(key), None) is not None)}
            )
        return httpx.Response(405, json={"error": "method_not_allowed"})
