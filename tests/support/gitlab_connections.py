# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_gitlab_connections.py`` that other tests build on."""

from __future__ import annotations

import io
import json
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from noust.core.fs import RealFileSystem
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore

HOST = "git.example.com"
TOKEN = "glpat-" + "A" * 20
CLIENT_SECRET = "gloas-" + "B" * 20


class FakeResponse:
    """What urlopen returns: readable, and a context manager."""

    def __init__(self, body: object) -> None:
        self._raw = json.dumps(body).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._raw if size < 0 else self._raw[:size]


class FakeGitLab:
    """A GitLab that answers by path, records what it was asked, and can fail."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.routes: dict[tuple[str, str], Any] = {}
        self.failing: dict[tuple[str, str], int] = {}

    def route(self, method: str, path: str, body: Any) -> None:
        self.routes[(method, path)] = body

    def fail(self, method: str, path: str, status: int) -> None:
        self.failing[(method, path)] = status

    def __call__(self, request: Any, timeout: float = 0) -> FakeResponse:
        parts = urllib.parse.urlsplit(request.full_url)
        path = parts.path
        key = (request.get_method(), path)
        self.requests.append(
            {
                "method": key[0],
                "url": request.full_url,
                "path": path,
                "query": dict(urllib.parse.parse_qsl(parts.query)),
                "headers": {k.lower(): v for k, v in request.header_items()},
                "form": dict(urllib.parse.parse_qsl(request.data.decode())) if request.data else {},
            }
        )
        if key in self.failing:
            raise HTTPError(request.full_url, self.failing[key], "denied", {}, io.BytesIO(b"{}"))
        if key not in self.routes:
            raise HTTPError(request.full_url, 404, "no route", {}, io.BytesIO(b"{}"))
        body = self.routes[key]
        return FakeResponse(body(request) if callable(body) else body)

    def calls(self, path: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"] == path]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "secrets", fs=RealFileSystem())


@pytest.fixture
def gitlab() -> FakeGitLab:
    fake = FakeGitLab()
    fake.route("GET", "/api/v4/user", {"id": 7, "username": "ana"})
    return fake


@pytest.fixture
def clock() -> list[float]:
    return [1_000_000.0]


@pytest.fixture(autouse=True)
def audited(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr("noust.core.audit.record", lambda *a, **k: seen.append((a, k)) or None)
    return seen
