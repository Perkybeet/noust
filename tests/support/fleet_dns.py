# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fake servers that run Noust DNS, for the tests of the fleet action that sets a cluster up."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.support.fleet_views_support import SCHEMA_31, Node

KEY = "ZmFrZWZha2VmYWtlZmFrZWZha2VmYWtlZmFrZWZha2U="

#: What a server with Noust DNS offers beyond a 4.0 one.
DNS_PATHS: dict[str, Any] = {
    "/api/dns/noust/server": {"get": {}, "post": {}},
    "/api/dns/noust/server/key": {"get": {"x-noust-requires-elevation": True}},
    "/api/dns/noust/server/secondaries": {"post": {}},
}

#: The schema of a server that has it.
SCHEMA_DNS: dict[str, Any] = {**SCHEMA_31, **DNS_PATHS}


@dataclass
class DnsNode:
    """
    A fake server's Noust DNS: answers its status, takes a setup as a job, hands out its key.

    Attributes:
        node: The fake server.
        listen: The addresses it will answer on once set up.
        calls: ``(method, path)`` of every request, in order.
        bodies: The body of every request, by ``(method, path)``.
        fail_setup: The error the setup job ends with, if it fails.
        role: Its role once set up.
    """

    node: Node
    listen: list[str] = field(default_factory=lambda: ["203.0.113.10"])
    calls: list[tuple[str, str]] = field(default_factory=list)
    bodies: dict[tuple[str, str], list[Any]] = field(default_factory=dict)
    fail_setup: str | None = None
    role: str | None = None
    hostname: str | None = None
    secondaries: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.node.schema = dict(SCHEMA_DNS)
        base = "/api/dns/noust/server"
        self.node.handlers[("GET", base)] = self._status
        self.node.handlers[("POST", base)] = self._setup
        self.node.handlers[("GET", f"{base}/key")] = self._key
        self.node.handlers[("POST", f"{base}/secondaries")] = self._secondary

    def _record(self, request: httpx.Request) -> Any:
        key = (request.method, request.url.path)
        self.calls.append(key)
        body = json.loads(request.content) if request.content else None
        self.bodies.setdefault(key, []).append(body)
        return body

    def _status(self, request: httpx.Request) -> httpx.Response:
        self._record(request)
        configured = self.role is not None
        return httpx.Response(
            200,
            json={
                "engine": "knot",
                "installed": configured,
                "version": "3.4.6" if configured else None,
                "running": configured,
                "configured": configured,
                "role": self.role,
                "hostname": self.hostname,
                "listen": self.listen if configured else [],
                "primary_address": None,
                "zones": 0,
                "secondaries": [{"address": a} for a in self.secondaries],
                "candidates": self.listen,
                "firewall": {},
                "foreign_zones": [],
                "warnings": [],
            },
        )

    def _setup(self, request: httpx.Request) -> httpx.Response:
        body = self._record(request)
        if self.fail_setup:
            return self.node.queue(status="failed", error=self.fail_setup)
        self.role = body["role"]
        self.hostname = body.get("hostname")
        if body.get("listen"):
            self.listen = list(body["listen"])
        return self.node.queue(result={"configured": True, "role": self.role})

    def _key(self, request: httpx.Request) -> httpx.Response:
        self._record(request)
        return httpx.Response(
            200, json={"name": "noust-xfr", "algorithm": "hmac-sha256", "secret": KEY}
        )

    def _secondary(self, request: httpx.Request) -> httpx.Response:
        body = self._record(request)
        self.secondaries.append(body["address"])
        return httpx.Response(
            201, json={"address": body["address"], "hostname": body.get("hostname")}
        )
