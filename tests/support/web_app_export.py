# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_app_export.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.appdir.layout import env_file_for
from noust.core.store import App, NoustStore
from noust.deployers import app_export
from noust.web.api import app_export as api_module
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

DOMAIN = "shop.example.com"
STRIPE = "sk_live_" + "51Habcdefghijklmn" + "opqrstuvwxyz"


class NoCron:
    def list_jobs(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    app = instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/acme/shop.git",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
        )
    )
    env = env_file_for(app)
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(f"APP_NAME=Shop\nSTRIPE_KEY={STRIPE}\n")
    monkeypatch.setattr(app_export, "CronManager", NoCron)
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def master_token(app: FastAPI) -> str:
    return get_token_manager().generate_master_token()


@pytest.fixture
def session(app: FastAPI, master_token: str) -> TestClient:
    client = TestClient(app, client=("testclient", 50000))
    response = client.post("/api/auth/login", json={"token": master_token})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


@pytest.fixture
def elevated(session: TestClient, master_token: str) -> TestClient:
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
    captured: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> FakeJob:
            captured.append(kwargs)
            return FakeJob(f"job-{len(captured)}")

    monkeypatch.setattr(api_module, "get_job_manager", Manager)
    return captured


def exported(session: TestClient) -> dict[str, Any]:
    response = session.get(f"/api/apps/{DOMAIN}/export")
    assert response.status_code == 200, response.text
    return dict(response.json())
