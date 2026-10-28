# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_server_api.py`` that other tests build on."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.web.api import server as server_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from noust.web.api.server import common
from noust.web.api.server.common import set_server_context
from tests.support.server_support import RecordingAudit, make_machine


class FakeJob:
    """A queued job, as far as the endpoints look at one."""

    def __init__(self, job_id: str, kwargs: dict[str, Any]) -> None:
        self.id = job_id
        self.type = kwargs["job_type"]
        self.name = kwargs["name"]
        self.status = types.SimpleNamespace(value="pending")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": getattr(self.type, "value", self.type), "name": self.name}


class FakeJobs:
    """Stands in for the job manager: records what is queued, runs nothing."""

    def __init__(self) -> None:
        self.queued: list[dict[str, Any]] = []
        self.active: list[Any] = []

    def create_job(self, **kwargs: Any) -> FakeJob:
        self.queued.append(kwargs)
        return FakeJob(f"job{len(self.queued)}", kwargs)

    def get_active_jobs(self) -> list[Any]:
        return self.active


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "api.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store):
    """The router over a server made of fixtures, with auth stubbed and jobs recorded."""
    machine = make_machine(tmp_path, monkeypatch, store)
    jobs = FakeJobs()
    audit = RecordingAudit()
    for module in (
        "noust.web.api.server.common",
        "noust.web.api.server.updates",
        "noust.web.api.server.storage",
    ):
        monkeypatch.setattr(f"{module}.get_job_manager", lambda: jobs)
    monkeypatch.setattr("noust.core.audit.record", audit.record)
    # As the console builds it: what its job queue is running is what an update
    # or a reboot must not overlap.
    machine.ctx.blockers = common.running_job_names
    set_server_context(machine.ctx)

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(server_api.router, prefix="/api/server")
    session = {"sid": "operator", "type": "master"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield types.SimpleNamespace(
            client=client, jobs=jobs, audit=audit, machine=machine, app=app, session=session
        )
    finally:
        set_server_context(None)
