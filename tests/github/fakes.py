# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A fake GitHub for the GitHub App tests.

A real HTTP server on 127.0.0.1, in a thread, answering the few API routes
WASM calls with canned JSON and recording every request, so the client's
urllib code, its headers and its error handling are exercised for real. The
JWT signature comes from a faked ``openssl`` answer: real processes are
blocked in the suite.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: What the faked openssl answers: a hex signature of three bytes.
FAKE_SIGNATURE_HEX = "0a0b0c"

APP_ID = 4242
INSTALLATION_ID = 77


@dataclass
class Recorded:
    """One request the fake received."""

    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeGitHub:
    """
    Canned answers by method and path pattern, and the requests received.

    Attributes:
        routes: ``(method, regex) -> (status, json body)``; later entries win.
        requests: Every request, in order.
    """

    routes: list[tuple[str, str, int, Any]] = field(default_factory=list)
    requests: list[Recorded] = field(default_factory=list)
    base_url: str = ""

    def on(self, method: str, pattern: str, body: Any = None, status: int = 200) -> FakeGitHub:
        """
        Answer requests matching a method and a path regex.

        Args:
            method: HTTP method.
            pattern: Regex the path (with query) must fully match.
            body: JSON to answer.
            status: HTTP status to answer.

        Returns:
            This fake, for chaining.
        """
        self.routes.append((method, pattern, status, body))
        return self

    def answer(self, method: str, path: str) -> tuple[int, Any]:
        """
        Find the answer to a request.

        Args:
            method: HTTP method.
            path: Path with query.

        Returns:
            Status and JSON body; 404 when nothing matches.
        """
        for route_method, pattern, status, body in reversed(self.routes):
            if route_method == method and re.fullmatch(pattern, path):
                return status, body
        return 404, {"message": "Not Found"}

    def paths(self, method: str | None = None) -> list[str]:
        """
        List the paths requested.

        Args:
            method: Only this method's.

        Returns:
            The paths, query strings stripped.
        """
        return [r.path.split("?")[0] for r in self.requests if method in (None, r.method)]


def start_server(fake: FakeGitHub) -> ThreadingHTTPServer:
    """
    Serve a fake on an ephemeral loopback port, in a thread.

    Args:
        fake: The fake to serve; its ``base_url`` is set.

    Returns:
        The server, to shut down.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    fake.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    return server


def _handler(fake: FakeGitHub) -> type[BaseHTTPRequestHandler]:
    """
    Build the request handler class bound to a fake.

    Args:
        fake: The fake answering.

    Returns:
        The handler class.
    """

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            fake.requests.append(
                Recorded(
                    method=self.command,
                    path=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=json.loads(raw) if raw else None,
                )
            )
            status, body = fake.answer(self.command, self.path)
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _serve
        do_POST = _serve
        do_PATCH = _serve
        do_DELETE = _serve

        def log_message(self, format: str, *args: Any) -> None:
            """Keep the test output quiet."""

    return Handler


def token_route(
    fake: FakeGitHub,
    token: str = "ghs_installation",  # noqa: S107 - a fake
    expires: str = "2099-01-01T00:00:00Z",
) -> None:
    """
    Answer the installation token exchange.

    Args:
        fake: The fake.
        token: The token to hand out.
        expires: Its expiry.
    """
    fake.on(
        "POST",
        r"/app/installations/\d+/access_tokens",
        {"token": token, "expires_at": expires},
        status=201,
    )
