# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/github/test_github_hooks.py`` that other tests build on."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from noust.web.api import github_hooks
from noust.web.auth import SecurityConfig
from noust.web.server import create_app as build_app
from tests.github.fakes import INSTALLATION_ID

SECRET = "hook-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    """
    Sign a body the way GitHub does.

    Args:
        body: The raw body.
        secret: The webhook secret.

    Returns:
        The ``X-Hub-Signature-256`` value.
    """
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(
    client: TestClient,
    event: str,
    payload: dict[str, Any],
    *,
    secret: str | None = SECRET,
    delivery: str | None = None,
) -> Any:
    """
    Post a delivery.

    Args:
        client: The anonymous client.
        event: ``X-GitHub-Event``.
        payload: The body.
        secret: Sign with this; None sends no signature.
        delivery: ``X-GitHub-Delivery``; a fresh one by default.

    Returns:
        The response.
    """
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    if secret is not None:
        headers["X-Hub-Signature-256"] = sign(body, secret)
    return client.post("/hooks/github", content=body, headers=headers)


@pytest.fixture(autouse=True)
def fresh_deliveries() -> None:
    """The replay cache is process-wide."""
    github_hooks._deliveries.clear()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    """
    Args:
        tmp_path: Per-test directory.
        store: The store fixture.

    Returns:
        The console application.
    """
    return build_app(
        SecurityConfig(
            state_dir=tmp_path / "state", rate_limit_requests=5000, webhook_max_failures=3
        )
    )


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        An anonymous client: GitHub holds no session.
    """
    return TestClient(app, client=("testclient", 50000))


def push_payload(
    repo: str = "you/app", branch: str = "main", default: str = "main"
) -> dict[str, Any]:
    """
    Build a push payload.

    Args:
        repo: ``owner/repo``.
        branch: The branch pushed to.
        default: The repository's default branch.

    Returns:
        The payload.
    """
    return {
        "ref": f"refs/heads/{branch}",
        "after": "c0ffee",
        "repository": {
            "full_name": repo,
            "clone_url": f"https://github.com/{repo}.git",
            "default_branch": default,
        },
        "installation": {"id": INSTALLATION_ID},
    }
