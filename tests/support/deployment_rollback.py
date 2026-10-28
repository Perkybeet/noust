# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_deployment_rollback.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, DeploymentStatus, NoustStore
from noust.core.teams import ScopedStore
from noust.web.api import deployments as deployments_api
from noust.web.api import jobs as jobs_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from noust.web.jobs import Job, JobStatus

INPLACE = "shop.example.com"


def deployment(
    store: NoustStore,
    status: str,
    *,
    domain: str = INPLACE,
    commit: str | None = "abc1234",
    release_id: str | None = None,
) -> int:
    """Write a finished (or running) deployment row."""
    deployment_id = store.record_deployment_start(domain, "cli", git_commit=commit)
    if status != DeploymentStatus.RUNNING.value:
        store.finish_deployment(deployment_id, status)
    if release_id is not None:
        store.annotate_deployment(deployment_id, release_id=release_id)
    return deployment_id


def inplace_app(store: NoustStore, tmp_path: Path) -> App:
    """An in-place application with its directory."""
    app_path = tmp_path / "apps" / "shop-example-com"
    app_path.mkdir(parents=True)
    return store.create_app(
        App(
            domain=INPLACE,
            app_type="nodejs",
            source="https://github.com/example/shop.git",
            branch="main",
            port=3000,
            app_path=str(app_path),
        )
    )


class Jobs:
    """Stands in for the job manager, keeping what was queued."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create_job(self, **kwargs: Any) -> Job:
        self.created.append(kwargs)
        return Job(
            id="ab12cd34",
            type=kwargs["job_type"],
            name=kwargs["name"],
            description=kwargs["description"],
            status=JobStatus.PENDING,
            metadata=kwargs.get("metadata") or {},
        )


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> Jobs:
    """The job manager every endpoint queues through."""
    fake = Jobs()
    monkeypatch.setattr(deployments_api, "get_job_manager", lambda: fake)
    monkeypatch.setattr(jobs_api, "get_job_manager", lambda: fake)
    return fake


@pytest.fixture
def client(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The deployments, deployment actions and jobs routers, authenticated."""
    monkeypatch.setattr(deployments_api, "scoped_store", lambda: ScopedStore(store=store))
    monkeypatch.setattr(jobs_api, "scoped_store", lambda: ScopedStore(store=store))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(deployments_api.router, prefix="/api/deployments")
    app.include_router(deployments_api.app_router, prefix="/api/apps")
    app.include_router(jobs_api.router, prefix="/api")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}
    return TestClient(app, raise_server_exceptions=False)
