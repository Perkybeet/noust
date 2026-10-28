# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_previews.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, NoustStore, PreviewRecord
from noust.managers import previews
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

PARENT = "shop.example.com"
BASE = "previews.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of the test's own with the application previewed, tracking no branch."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    instance.create_app(
        App(
            domain=PARENT,
            app_type="nodejs",
            source="https://github.com/acme/shop.git",
            branch=None,
            port=3000,
            app_path="/var/www/apps/shop-example-com",
        )
    )
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the sweep timer off the machine."""
    monkeypatch.setattr(previews, "sync_sweep_timer", lambda **_: True)


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def master_token(app: FastAPI) -> str:
    return get_token_manager().generate_master_token()


@pytest.fixture
def session(app: FastAPI, master_token: str) -> TestClient:
    """A cookie session, signed in but not in sudo mode."""
    client = TestClient(app, client=("testclient", 50000))
    response = client.post("/api/auth/login", json={"token": master_token})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


@pytest.fixture
def elevated(session: TestClient, master_token: str) -> TestClient:
    """The same session after confirming it is the operator."""
    response = session.post("/api/auth/elevate", json={"token": master_token})
    assert response.status_code == 200, response.text
    return session


class FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.status = type("Status", (), {"value": "pending"})()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id}


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every job the API, the hook or the manager queues."""
    captured: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> FakeJob:
            captured.append(kwargs)
            return FakeJob(f"job-{len(captured)}")

    monkeypatch.setattr(previews, "_jobs", Manager)
    monkeypatch.setattr("noust.web.api.previews.get_job_manager", Manager)
    monkeypatch.setattr("noust.web.api.hooks.get_job_manager", Manager)
    return captured


def preview(store: NoustStore, number: int = 7) -> PreviewRecord:
    return store.save_preview(
        PreviewRecord(
            parent_domain=PARENT,
            domain=f"pr-{number}-shop-example-com.{BASE}",
            number=number,
            branch="feature/x",
            provider="github",
            expires_at="2026-10-05T12:00:00+00:00",
            head_sha="a" * 40,
            repository="acme/shop",
            status="ready",
        )
    )
