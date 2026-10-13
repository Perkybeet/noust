# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server for the API client to talk to, without a network.

Two stand-ins for the wire: :class:`Wire`, scripted answers keyed by method and
path (to pin what the client does with a retry, a redirect or a 502), and
:func:`bridge`, which carries the client's requests into a console served by
``TestClient`` so a remote command runs against the real routes, permissions
and scopes.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import Message
from typing import Any


class Answer:
    """What the wire gives back for one request: a status, headers and a JSON body."""

    def __init__(
        self, status: int = 200, body: Any = None, headers: Mapping[str, str] | None = None
    ) -> None:
        """
        Args:
            status: The HTTP status.
            body: A JSON-serialisable body, or text/bytes sent as they are.
            headers: Response headers.
        """
        self.status = status
        self.payload = (
            body
            if isinstance(body, bytes)
            else (body if isinstance(body, str) else json.dumps(body)).encode()
            if body is not None
            else b""
        )
        message = Message()
        for key, value in {"Content-Type": "application/json", **(headers or {})}.items():
            message[key] = value
        self.headers = message

    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, limit: int = -1) -> bytes:
        """
        Args:
            limit: Most bytes to read.

        Returns:
            The body.
        """
        return self.payload if limit < 0 else self.payload[:limit]


@dataclass
class Wire:
    """
    A scripted server: answers are queued per route, and every request is kept.

    Attributes:
        routes: ``(METHOD, path)`` to the answers to give, in order; the last
            one repeats.
        seen: Every request received, as ``(method, url, headers, body)``.
    """

    routes: dict[tuple[str, str], list[Answer | Exception]] = field(default_factory=dict)
    seen: list[tuple[str, str, dict[str, str], Any]] = field(default_factory=list)

    def on(self, method: str, path: str, *answers: Answer | Exception) -> Wire:
        """
        Args:
            method: The HTTP method.
            path: The path (with query) to answer.
            *answers: What to answer, in order.

        Returns:
            Itself, to chain.
        """
        self.routes[(method.upper(), path)] = list(answers)
        return self

    def __call__(self, request: urllib.request.Request, timeout: float) -> Answer:
        """
        Args:
            request: What the client sent.
            timeout: Its timeout (ignored).

        Returns:
            The queued answer.

        Raises:
            urllib.error.HTTPError: For an answer of 400 or more, as the real
                opener raises it.
        """
        parts = request.full_url.split("/", 3)
        path = "/" + parts[3] if len(parts) > 3 else "/"
        headers = {key.lower(): value for key, value in request.header_items()}
        body = json.loads(request.data) if request.data else None
        self.seen.append((request.get_method(), path, headers, body))
        queue = self.routes.get((request.get_method(), path))
        if queue is None:
            raise AssertionError(f"unexpected request {request.get_method()} {path}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        if item.status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, item.status, "error", item.headers, io.BytesIO(item.payload)
            )
        return item


def bridge(client: Any) -> Callable[[urllib.request.Request, float], Answer]:
    """
    Carry an API client's requests into a console served by ``TestClient``.

    Args:
        client: A ``TestClient`` of the console.

    Returns:
        An opener for :class:`~noust.client.api.ApiClient`.
    """

    def opener(request: urllib.request.Request, timeout: float) -> Answer:
        parts = request.full_url.split("/", 3)
        path = "/" + parts[3] if len(parts) > 3 else "/"
        response = client.request(
            request.get_method(),
            path,
            content=request.data,
            headers=dict(request.header_items()),
        )
        answer = Answer(
            response.status_code,
            response.content,
            {k: v for k, v in response.headers.items() if k.lower() != "content-length"},
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                response.status_code,
                "error",
                answer.headers,
                io.BytesIO(response.content),
            )
        return answer

    return opener
