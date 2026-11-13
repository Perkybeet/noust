# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server of a fleet, as the central's assistant meets it: a small console of its own that records what it is asked (item 171).

What the tests of the assistant with a fleet share: the node answers the few routes the assistant reads and
acts on, says what the central called it with (the headers it received), and can pretend to run an older
Noust that does not have the routes added since.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from noust.web.api.openapi import ELEVATION_EXTENSION

__all__ = ["FleetNode"]

#: What the node's logs say when a test does not say otherwise.
DEFAULT_LOGS = "node says: out of memory\nrestart counter 5\n"


class FleetNode:
    """
    A node: its routes, and a record of every call it received.

    Attributes:
        seen: What it was asked: method, path and the headers, in order.
        logs: The journal it answers by application; the default text for one it is not told about.
        diagnosis: What its diagnosis says, as the API's own document.
        older: Whether it runs a Noust that has no diagnosis: the route is neither in its schema nor answered.
        app: The FastAPI application to serve.
    """

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []
        self.logs: dict[str, str] = {}
        self.diagnosis: dict[str, Any] = {}
        self.older = False
        self.app = FastAPI()
        node = self

        def note(request: Request) -> None:
            node.seen.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "headers": {k.lower(): v for k, v in request.headers.items()},
                }
            )

        def missing() -> Response:
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        @self.app.get("/api/openapi.json")
        def schema() -> Response:
            paths: dict[str, Any] = {
                "/api/apps/{domain}/logs": {"get": {}},
                "/api/apps/{domain}/restart": {"post": {}},
                "/api/apps/{domain}/limits": {"patch": {ELEVATION_EXTENSION: True}},
            }
            if not node.older:
                paths["/api/apps/{domain}/diagnose"] = {"get": {}}
            return JSONResponse(
                {
                    "openapi": "3.1.0",
                    "info": {"title": "node", "version": "4.3.0"},
                    "paths": paths,
                }
            )

        @self.app.get("/api/apps/{domain}/logs")
        def read_logs(domain: str, request: Request) -> Response:
            note(request)
            return JSONResponse(
                {"domain": domain, "logs": node.logs.get(domain, DEFAULT_LOGS), "lines": 100}
            )

        @self.app.get("/api/apps/{domain}/diagnose")
        def diagnose(domain: str, request: Request) -> Response:
            note(request)
            if node.older:
                return missing()
            return JSONResponse(
                {
                    "domain": domain,
                    "verdict": "down",
                    "probable_cause": "",
                    "checks": [],
                    **node.diagnosis,
                }
            )

        @self.app.post("/api/apps/{domain}/restart")
        def restart(domain: str, request: Request) -> Response:
            note(request)
            return JSONResponse(
                {"success": True, "message": f"Application restarted: {domain}", "domain": domain}
            )

        @self.app.patch("/api/apps/{domain}/limits")
        def limits(domain: str, request: Request) -> Response:
            note(request)
            return JSONResponse({"domain": domain, "memory_max_mb": 512})
