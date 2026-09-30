"""
A central with several fake nodes, for the fleet views and actions.

:func:`tests.fleet_support.build_fleet` wires one fake node; the views and the
actions need several, each with its own answers. Every node is registered the
normal way (key, join code, tunnel through the fake ssh), and one transport
routes each request to the node whose tunnel's local port it was sent to.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from noust.fleet.aggregate import Aggregator, set_aggregator
from noust.fleet.nodes import NodeManager
from tests.fleet_support import FakeNode, Fleet, build_fleet

#: The OpenAPI paths of a 3.1 node, as far as the fleet actions look.
SCHEMA_31: dict[str, dict[str, Any]] = {
    "/api/apps": {"get": {}},
    "/api/apps/{domain}/restart": {"post": {}},
    "/api/backups": {"get": {}, "post": {}},
    "/api/backups/{backup_id}/verify": {"post": {}},
    "/api/certs/renew-all": {"post": {}},
    "/api/jobs/update": {"post": {}},
    "/api/jobs/{job_id}": {"get": {}},
    "/api/jobs/{job_id}/log": {"get": {}},
    "/api/system/version": {"get": {}},
    "/api/system/update": {"get": {}, "post": {"x-noust-requires-elevation": True}},
    "/api/server/updates/apply": {"post": {"x-noust-requires-elevation": True}},
}

#: What a 3.0 node offers of the same: no self-update, no server area.
SCHEMA_30 = {
    path: methods
    for path, methods in SCHEMA_31.items()
    if not path.startswith(("/api/system/update", "/api/server"))
}


@dataclass
class Node(FakeNode):
    """
    A fake node with its own schema, applications, jobs and handlers.

    Attributes:
        schema: Its OpenAPI paths.
        apps: Its applications.
        jobs: Its jobs, by id: status, result, error and log.
        handlers: ``(METHOD, path)`` to a function answering it.
        gate: When set, requests wait on it (a node that hangs).
    """

    schema: dict[str, Any] = field(default_factory=lambda: dict(SCHEMA_31))
    apps: list[dict[str, Any]] = field(default_factory=list)
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    handlers: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = field(
        default_factory=dict
    )
    gate: threading.Event | None = None
    down: bool = False

    def queue(
        self, result: Any = None, *, status: str = "completed", error: str | None = None
    ) -> httpx.Response:
        """
        Answer 202 with a new job that has already ended.

        Args:
            result: The job's result.
            status: How it ended.
            error: Its error.

        Returns:
            The 202 answer.
        """
        job_id = f"j{len(self.jobs) + 1:03d}"
        self.jobs[job_id] = {
            "id": job_id,
            "status": status,
            "result": result,
            "error": error,
            "log": f"[x] [INFO] step of {job_id}\n[x] [INFO] done",
        }
        return httpx.Response(
            202, json={"job_id": job_id, "status": "pending", "message": "", "job": {"id": job_id}}
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        """
        Answer one request.

        Args:
            request: The request.

        Returns:
            The response.
        """
        if self.gate is not None:
            self.gate.wait(10)
        if self.down:
            raise httpx.ConnectError("Connection refused", request=request)
        path = request.url.path
        method = request.method
        if request.headers.get("Authorization") == f"Bearer {self.token}" and not self.revoked:
            handler = self.handlers.get((method, path))
            if handler is not None:
                self.requests.append(request)
                return handler(request)
            if path == "/api/openapi.json":
                self.requests.append(request)
                return httpx.Response(200, json={"openapi": "3.1.0", "paths": self.schema})
            if path == "/api/apps" and method == "GET" and "/api/apps" not in self.responses:
                self.requests.append(request)
                return httpx.Response(200, json={"apps": self.apps, "total": len(self.apps)})
            if path.startswith("/api/jobs/") and method == "GET":
                self.requests.append(request)
                job_id = path.split("/")[3]
                job = self.jobs.get(job_id)
                if job is None:
                    return httpx.Response(404, json={"error": "not_found"})
                if path.endswith("/log"):
                    return httpx.Response(200, json={"content": job["log"], "truncated": False})
                return httpx.Response(200, json={k: v for k, v in job.items() if k != "log"})
        return super().handler(request)


@dataclass
class Central:
    """
    A central with several fake nodes.

    Attributes:
        fleet: The single-node wiring it is built on.
        nodes: Every node, by name.
        manager: A registry whose requests reach the right node.
    """

    fleet: Fleet
    nodes: dict[str, Node]
    manager: NodeManager

    @property
    def store(self) -> Any:
        """The central's store."""
        return self.fleet.store

    def _route(self, request: httpx.Request) -> httpx.Response:
        port = request.url.port
        for name, node in self.nodes.items():
            if self.fleet.tunnels.status(name)["local_port"] == port:
                return node.handler(request)
        raise httpx.ConnectError(f"nothing listens on {port}", request=request)


def build_central(
    tmp_path: Path, names: tuple[str, ...] = ("web-2", "web-3"), **versions: str
) -> Central:
    """
    Register several fake nodes on a fresh central, and a fresh aggregator.

    Args:
        tmp_path: The test's directory.
        names: The nodes.
        **versions: A node's version by name (underscores for dashes).

    Returns:
        The central.
    """
    fleet = build_fleet(tmp_path)
    nodes: dict[str, Node] = {}
    for name in names:
        version = versions.get(name.replace("-", "_"), "3.1.0")
        fleet.node.version = version
        fleet.added(name, noust_version=version)
        nodes[name] = Node(version=version)
    central = Central(fleet=fleet, nodes=nodes, manager=fleet.manager)
    central.manager = NodeManager(
        store=fleet.store,
        secrets=fleet.secrets,
        runner=fleet.runner,
        tunnels=fleet.tunnels,
        transport=httpx.MockTransport(central._route),
    )
    set_aggregator(Aggregator(clock=fleet.clock))
    return central
