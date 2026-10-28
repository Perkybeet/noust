# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_apps_env_secrets.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        A store of this test's own.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore, runner: object) -> FastAPI:
    """
    Args:
        tmp_path: Per-test temporary directory.
        store: The store fixture.
        runner: The fake command runner, so no manager reaches a real process.

    Returns:
        The application.
    """
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """
    Args:
        app: The application.

    Returns:
        A signed-in client carrying the CSRF header.
    """
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


def elevate(client: TestClient) -> None:
    """
    Confirm sudo mode for the signed-in client.

    Unmasking an environment and writing one both require elevation; see
    tests/test_web_sudo.py for the gate itself. This module is about the
    endpoint's own behaviour, so it confirms once per test that needs it.

    Args:
        client: A signed-in client.
    """
    response = client.post(
        "/api/auth/elevate", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text


def deployed_env(
    store: NoustStore, tmp_path: Path, domain: str = "example.com", env_text: str = ""
) -> Path:
    """
    Deploy an application whose ``.env`` file lives inside the sandbox.

    On the default (in-place) layout the environment endpoint reads and
    writes ``<app_path>/.env`` directly, so the application directory has to
    exist on disk for the round trip to mean anything.

    Args:
        store: The store to write the application record to.
        tmp_path: Per-test temporary directory.
        domain: The application's domain.
        env_text: Content to write to its ``.env`` file, when not empty.

    Returns:
        The application's directory.
    """
    app_dir = tmp_path / "apps" / domain
    app_dir.mkdir(parents=True, exist_ok=True)
    if env_text:
        (app_dir / ".env").write_text(env_text, encoding="utf-8")
    store.create_app(
        App(
            domain=domain,
            app_type="nextjs",
            source="https://github.com/you/app",
            port=3000,
            app_path=str(app_dir),
            status="running",
        )
    )
    return app_dir


def read_audit(tmp_path: Path) -> list[dict[str, Any]]:
    """
    Read the audit log written during a test.

    Args:
        tmp_path: Per-test temporary directory, where the panel's state lives.

    Returns:
        One dict per audit line, oldest first. Empty when nothing was audited.
    """
    import json

    path = tmp_path / "state" / "web-audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
