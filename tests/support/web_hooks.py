# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_hooks.py`` that other tests build on."""

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

from noust.core.store import App, NoustStore
from noust.web.api import hooks as hooks_module
from noust.web.api.hooks import mint_webhook_secret
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager

DOMAIN = "app.example.com"

#: A push to the branch the seeded application tracks.
PUSH_MAIN = json.dumps({"ref": "refs/heads/main"}).encode()


def github_signature(secret: str, body: bytes) -> str:
    """
    Sign a body the way GitHub does.

    Args:
        secret: The webhook secret in clear.
        body: The raw request body.

    Returns:
        The ``X-Hub-Signature-256`` header value.
    """
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def gitea_signature(secret: str, body: bytes) -> str:
    """
    Sign a body the way Gitea does.

    Args:
        secret: The webhook secret in clear.
        body: The raw request body.

    Returns:
        The ``X-Gitea-Signature`` header value.
    """
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def github_headers(secret: str, body: bytes, delivery: str | None = None) -> dict[str, str]:
    """
    Build the headers of a GitHub push delivery.

    Args:
        secret: The webhook secret in clear.
        body: The raw request body.
        delivery: Delivery id, generated when omitted.

    Returns:
        The request headers.
    """
    return {
        "X-Hub-Signature-256": github_signature(secret, body),
        "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


@pytest.fixture(autouse=True)
def fresh_delivery_cache() -> None:
    """The replay cache is process-wide; no test may inherit another's ids."""
    hooks_module._deliveries.clear()


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """
    Give the panel a store of its own.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The store the hook reads.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def seeded(store: NoustStore) -> App:
    """
    Deploy one application on paper.

    Args:
        store: The store fixture.

    Returns:
        The stored application, tracking the ``main`` branch.
    """
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/you/app",
            branch="main",
            port=3000,
            app_path=f"/var/www/apps/{DOMAIN.replace('.', '-')}",
        )
    )


@pytest.fixture
def secret(store: NoustStore, seeded: App) -> str:
    """
    Enable webhooks for the seeded application.

    Args:
        store: The store fixture.
        seeded: The seeded application.

    Returns:
        The webhook secret in clear.
    """
    return mint_webhook_secret(DOMAIN)


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    """
    Args:
        tmp_path: Per-test temporary directory.
        store: The store fixture, so the panel never opens a real database.

    Returns:
        The application.
    """
    return build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        An anonymous client: a git forge holds no session.
    """
    return TestClient(app, client=("testclient", 50000))


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        A signed-in client carrying the CSRF header, in sudo mode: minting
        or discarding a secret needs it.
    """
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    elevated = signed_in.post("/api/auth/elevate", json={"token": token})
    assert elevated.status_code == 200, elevated.text
    return signed_in


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """
    Capture the update instead of queueing a real job.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The job descriptions that reached the job manager.
    """
    captured: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> Any:
        """
        Args:
            **kwargs: The job description.

        Returns:
            An object shaped like a queued job.
        """
        captured.append(kwargs)

        class Queued:
            """A job that was accepted but never run."""

            id = "job-1"
            status = type("Status", (), {"value": "pending"})()

            def to_dict(self) -> dict[str, Any]:
                """
                Returns:
                    The job as JSON-serialisable data.
                """
                return {"id": self.id}

        return Queued()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr("noust.web.api.hooks.get_job_manager", lambda: manager)
    return captured


def read_audit(tmp_path: Path) -> str:
    """
    Read the audit log written during a test.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The whole log, one JSON object per line.
    """
    path = tmp_path / "state" / "web-audit.log"
    return path.read_text() if path.exists() else ""


def audit_entries(tmp_path: Path) -> list[dict[str, Any]]:
    """
    Parse the audit log written during a test.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        One dict per audit line.
    """
    return [json.loads(line) for line in read_audit(tmp_path).splitlines() if line.strip()]
