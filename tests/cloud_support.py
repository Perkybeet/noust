# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A fake HTTP API for the cloud provider adapters: no socket is ever opened.

``FakeApi`` is what the adapters take as their ``opener``. A test registers the
answers it wants by method and path; anything unregistered fails the test, so a
call the adapter should not make cannot pass unnoticed.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

Handler = Callable[["Call"], tuple[int, Any] | tuple[int, Any, dict[str, str]]]


@dataclass
class Call:
    """One request the adapter sent."""

    method: str
    path: str
    query: dict[str, list[str]]
    body: Any
    headers: dict[str, str]

    def q(self, name: str, default: str = "") -> str:
        """Returns: The first value of a query parameter."""
        values = self.query.get(name)
        return values[0] if values else default


class _Response(io.BytesIO):
    """A urlopen response: a context manager whose ``read`` takes a size."""

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass
class FakeApi:
    """
    A provider's API, answering from a table.

    Attributes:
        prefix: The API root path (``/v1``), removed before matching.
        calls: Every request that was sent, in order.
    """

    prefix: str = ""
    calls: list[Call] = field(default_factory=list)
    _routes: list[tuple[str, re.Pattern[str], Handler]] = field(default_factory=list)

    def on(self, method: str, pattern: str, answer: Handler | tuple[int, Any]) -> None:
        """
        Register an answer.

        Args:
            method: The HTTP method.
            pattern: A regular expression the whole path must match.
            answer: ``(status, body)`` or a function of the :class:`Call`.
        """
        handler: Handler = answer if callable(answer) else (lambda _call, a=answer: a)
        self._routes.append((method.upper(), re.compile(pattern), handler))

    def sent(self, method: str, pattern: str = ".*") -> list[Call]:
        """
        Returns:
            The requests of one method whose path matches.
        """
        regex = re.compile(pattern)
        return [c for c in self.calls if c.method == method.upper() and regex.fullmatch(c.path)]

    def __call__(self, request: Any, timeout: float | None = None) -> _Response:
        parsed = urlparse(request.full_url)
        path = parsed.path.removeprefix(self.prefix)
        raw = request.data
        call = Call(
            method=request.get_method(),
            path=path,
            query=parse_qs(parsed.query),
            body=json.loads(raw) if raw else None,
            headers={k.lower(): v for k, v in request.header_items()},
        )
        self.calls.append(call)
        for method, pattern, handler in self._routes:
            if method == call.method and pattern.fullmatch(path):
                result = handler(call)
                status, body = result[0], result[1]
                headers = result[2] if len(result) > 2 else {}  # type: ignore[misc]
                payload = b"" if body is None else json.dumps(body).encode()
                if status >= 400:
                    raise HTTPError(
                        request.full_url, status, "error", _headers(headers), io.BytesIO(payload)
                    )
                return _Response(payload)
        raise AssertionError(f"Unexpected request: {call.method} {path} {call.query}")


def _headers(values: dict[str, str]) -> Any:
    """Build the header mapping urllib attaches to an HTTPError."""
    from email.message import Message

    message = Message()
    for key, value in values.items():
        message[key] = value
    return message
