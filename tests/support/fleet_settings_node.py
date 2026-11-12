# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A fake node that answers the way a 4.1 node does to the fleet's settings.

It keeps what it holds of each setting and what it says it manages, answers the routes the
reconciler uses, and refuses what the real node refuses: a write that is not signed as a push of
the fleet's settings is a 403 with the node's own code, the way ``noust.web.auth.fleet_sync``
answers it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.support.fleet_views_support import Node

#: The routes a 4.1 node offers for the fleet's settings, as its OpenAPI schema lists them.
SETTINGS_SCHEMA: dict[str, dict[str, Any]] = {
    "/api/auth/fleet/self": {"get": {}},
    "/api/managed-settings": {"get": {}},
    "/api/managed-settings/{resource}/{name}": {"put": {}, "delete": {}},
    "/api/notifications/channels": {"get": {}},
    "/api/notifications/channels/{name}": {"put": {"x-noust-requires-elevation": True}},
    "/api/alerts/rules": {"get": {}},
    "/api/alerts/rules/{name}": {"put": {}},
    "/api/backup-destinations": {"get": {}, "post": {"x-noust-requires-elevation": True}},
    "/api/backup-destinations/{name}": {"put": {"x-noust-requires-elevation": True}},
    "/api/registries": {"get": {}, "post": {"x-noust-requires-elevation": True}},
    "/api/registries/{name}": {"patch": {"x-noust-requires-elevation": True}},
    "/api/variables/server": {"get": {}},
    "/api/variables/server/{name}": {"put": {"x-noust-requires-elevation": True}},
    "/api/notifications/console-url": {"get": {}, "put": {"x-noust-requires-elevation": True}},
}

#: The kinds of setting a 4.1 or 4.2 node says it can be managed for (``GET /api/managed-settings``).
KINDS_OF_A_4_2_NODE: tuple[str, ...] = (
    "channel",
    "alert_rule",
    "backup_destination",
    "registry",
    "variable",
    "console_link",
)

#: What a node of 4.3 adds: the route the server policy is set by, and the kind that says so.
POLICY_PATH = "/api/config/policy"
POLICY_SCHEMA: dict[str, dict[str, Any]] = {
    POLICY_PATH: {"get": {}, "put": {"x-noust-requires-elevation": True}},
}

#: What a node answers of its policy when its operator never touched it.
DEFAULT_POLICY: dict[str, Any] = {
    "updates_check": True,
    "ssl_email": "",
    "backup_max_per_app": 10,
    "delivery_max_minutes": 60,
}

_LISTS = {
    "/api/notifications/channels": ("channel", "items"),
    "/api/alerts/rules": ("alert_rule", "items"),
    "/api/backup-destinations": ("backup_destination", "destinations"),
    "/api/registries": ("registry", "logins"),
    "/api/variables/server": ("variable", "variables"),
}

_ITEM_PATHS = [
    (re.compile(r"/api/notifications/channels/([^/]+)"), "channel", ("PUT",)),
    (re.compile(r"/api/alerts/rules/([^/]+)"), "alert_rule", ("PUT",)),
    (re.compile(r"/api/backup-destinations/([^/]+)"), "backup_destination", ("PUT",)),
    (re.compile(r"/api/registries/([^/]+)"), "registry", ("PATCH",)),
    (re.compile(r"/api/variables/server/([^/]+)"), "variable", ("PUT",)),
]
_CREATES = {
    "/api/backup-destinations": "backup_destination",
    "/api/registries": "registry",
}
_MANAGED = re.compile(r"/api/managed-settings/([^/]+)/([^/]+)")
_CONSOLE_URL = "/api/notifications/console-url"

#: The blocks of a channel that a PUT replaces when given and keeps when it leaves them out.
_CHANNEL_BLOCKS = ("events", "route", "quiet", "flood", "summary")


@dataclass
class Signed:
    """One request the node took as a push, with who it said it was."""

    method: str
    path: str
    actor: str
    elevated: str
    scope: str
    body: Any


@dataclass
class ManagedNode:
    """
    What a fake node holds, and what it says it manages.

    Attributes:
        node: The fake node whose answers this wraps.
        central: The central, as the node knows it.
        held: ``(kind, name)`` to the value the node holds of it.
        rows: ``(kind, name)`` to the row ``GET /api/managed-settings`` gives.
        writes: Every push it took.
        refusals: ``(METHOD, path)`` to the body of a 400 to answer it with.
        ceiling: The level ``GET /api/auth/fleet/self`` publishes.
    """

    node: Node
    central: str = "nas"
    held: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    rows: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    writes: list[Signed] = field(default_factory=list)
    refusals: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    ceiling: str = "admin"
    unsigned: int = 0
    kinds: list[str] = field(default_factory=lambda: list(KINDS_OF_A_4_2_NODE))

    def __post_init__(self) -> None:
        self._offers_policy = False
        self.node.schema.update(SETTINGS_SCHEMA)
        self._fallback = self.node.handler
        self.node.handler = self.handle  # type: ignore[method-assign]

    # -- what a test sets up ---------------------------------------------------------

    def offer_policy(self) -> None:
        """Make this a node of 4.3: it has the policy route and says it can be managed for it."""
        self.node.schema.update(POLICY_SCHEMA)
        if "policy" not in self.kinds:
            self.kinds.append("policy")
        self._offers_policy = True

    def hold(self, kind: str, name: str, **value: Any) -> None:
        """The node's own operator made this setting."""
        self.held[(kind, name)] = dict(value) or {"type": "slack"}

    def edit_by_hand(self, kind: str, name: str, **value: Any) -> None:
        """The node's own operator changed a setting the fleet manages, and the node noticed."""
        self.held[(kind, name)] = {**self.held.get((kind, name), {}), **value}
        if (kind, name) in self.rows:
            self.rows[(kind, name)]["state"] = "overridden"

    def ask_back(self, kind: str, name: str) -> None:
        """The node's operator asked the central to put a setting back."""
        self.rows[(kind, name)]["restore_requested_at"] = "2026-10-28T10:00:00+00:00"

    # -- the node's answers ----------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one request."""
        method, path = request.method, request.url.path
        if self.node.gate is not None:
            self.node.gate.wait(10)
        if self.node.down:
            raise httpx.ConnectError("Connection refused", request=request)
        if request.headers.get("Authorization") != f"Bearer {self.node.token}":
            return self._fallback(request)
        if path == "/api/auth/fleet/self":
            self.node.requests.append(request)
            return httpx.Response(
                200, json={"level": self.ceiling, "fleet": True, "central": self.central}
            )
        if path == "/api/managed-settings" and method == "GET":
            self.node.requests.append(request)
            return httpx.Response(
                200, json={"items": list(self.rows.values()), "kinds": list(self.kinds)}
            )
        managed = _MANAGED.fullmatch(path)
        if managed and method in ("PUT", "DELETE"):
            return self._managed(request, managed.group(1), managed.group(2))
        if method == "GET" and path == _CONSOLE_URL:
            self.node.requests.append(request)
            held = self.held.get(("console_link", "console"))
            return httpx.Response(200, json={"url": held["url"] if held else ""})
        if method == "GET" and path == POLICY_PATH and self._offers_policy:
            self.node.requests.append(request)
            held = self.held.get(("policy", "general"))
            return httpx.Response(
                200, json={**DEFAULT_POLICY, **(held or {}), "customized": held is not None}
            )
        if method == "GET" and path in _LISTS:
            self.node.requests.append(request)
            kind, key = _LISTS[path]
            items = [
                {
                    "name": n,
                    "default": False,
                    "configured": True,
                    "backend": v.get("backend"),
                    # A channel lists the blocks it has, as the real route does.
                    **{b: v[b] for b in _CHANNEL_BLOCKS if b in v},
                }
                for (k, n), v in self.held.items()
                if k == kind
            ]
            return httpx.Response(200, json={key: items})
        written = self._typed(request)
        if written is not None:
            return written
        return self._fallback(request)

    def _signed(self, request: httpx.Request) -> Signed:
        body: Any = None
        if request.content:
            body = json.loads(request.content)
        return Signed(
            method=request.method,
            path=request.url.path,
            actor=request.headers.get("X-Noust-Actor", ""),
            elevated=request.headers.get("X-Noust-Elevated", ""),
            scope=request.headers.get("X-Noust-Actor-Scope", ""),
            body=body,
        )

    def _is_a_push(self, signed: Signed) -> bool:
        return signed.actor.startswith("fleet-sync on behalf of ") and signed.elevated.startswith(
            "change:"
        )

    def _typed(self, request: httpx.Request) -> httpx.Response | None:
        method, path = request.method, request.url.path
        kind = _CREATES.get(path) if method == "POST" else None
        name: str | None = None
        if path == _CONSOLE_URL and method == "PUT":
            kind, name = "console_link", "console"
        if path == POLICY_PATH and method == "PUT" and self._offers_policy:
            kind, name = "policy", "general"
        if kind is None:
            for pattern, item_kind, methods in _ITEM_PATHS:
                found = pattern.fullmatch(path)
                if found and method in methods:
                    kind, name = item_kind, found.group(1)
                    break
        if kind is None:
            return None
        self.node.requests.append(request)
        signed = self._signed(request)
        if not self._is_a_push(signed):
            self.unsigned += 1
            return httpx.Response(
                403, json={"error": "elevation_required", "detail": "Confirm it's you"}
            )
        refused = self.refusals.get((method, path))
        if refused is not None:
            return httpx.Response(400, json=refused)
        body = dict(signed.body or {})
        if name is None:
            name = str(body.pop("name"))
        self.writes.append(signed)
        self.held[(kind, name)] = {**self.held.get((kind, name), {}), **body}
        return httpx.Response(200, json={"name": name})

    def _managed(self, request: httpx.Request, kind: str, name: str) -> httpx.Response:
        self.node.requests.append(request)
        signed = self._signed(request)
        if not self._is_a_push(signed):
            return httpx.Response(
                403, json={"error": "fleet_sync_required", "detail": "Only the central"}
            )
        self.writes.append(signed)
        if request.method == "DELETE":
            return httpx.Response(
                200, json={"released": self.rows.pop((kind, name), None) is not None}
            )
        if (kind, name) not in self.held:
            return httpx.Response(404, json={"error": "not_found", "detail": "No such setting"})
        version = int((signed.body or {}).get("version", 0))
        change = signed.actor.rsplit("(change ", 1)[1].rstrip(")")
        person = signed.actor.removeprefix("fleet-sync on behalf of ").split(" (change")[0]
        row = {
            "resource": kind,
            "name": name,
            "central": self.central,
            "version": version,
            "fingerprint": "f" * 16,
            "state": "managed",
            "change": change,
            "applied_by": person,
            "applied_at": "2026-10-28T10:00:00+00:00",
            "overridden_at": None,
            "restore_requested_at": None,
        }
        self.rows[(kind, name)] = row
        return httpx.Response(200, json=row)
