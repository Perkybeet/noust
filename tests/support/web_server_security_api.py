# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_server_security_api.py`` that other tests build on."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import NoustError
from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.managers.server import host as host_module
from noust.managers.server import security_checks
from noust.managers.server.security import ServerSecurity
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from noust.web.api.server import security as security_api
from noust.web.jobs import Job, JobContext, JobStatus
from tests.support.server_security_support import NOW, FakeHost, FakeSshd

PREFIX = "/api/server/security"


class InlineJobs:
    """Runs each job at once, on the request's thread, keeping its log."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def create_job(self, **kwargs: Any) -> Job:
        job = Job(
            id=f"job{len(self.jobs) + 1}",
            type=kwargs["job_type"],
            name=kwargs["name"],
            description=kwargs["description"],
            metadata=kwargs.get("metadata") or {},
            actor=kwargs.get("actor"),
        )
        context = JobContext(job, lambda _job: None)
        try:
            job.result = kwargs["func"](**kwargs["kwargs"], job_context=context)
            job.status = JobStatus.COMPLETED
        except NoustError as exc:
            job.error = str(exc)
            job.status = JobStatus.FAILED
        self.jobs.append(job)
        return job


@pytest.fixture(autouse=True)
def fresh() -> None:
    host_module.reset_platform_cache()
    security_checks.forget_report()
    yield
    security_checks.wait_for_refresh(timeout=30)
    host_module.reset_platform_cache()
    security_checks.forget_report()


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
def machine(tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch):
    host = FakeHost(tmp_path / "root")
    host.write("/etc/os-release", 'ID=debian\nVERSION_ID="12"\n')
    runner = FakeSshd(host)
    runner.only_knows("apt-get", "ufw", "systemctl", "sshd", "fail2ban-client")
    runner.script(["ufw", "status", "verbose"], stdout="Status: inactive\n")
    runner.script(["ufw", "show", "added"], stdout="")
    runner.script(["fail2ban-client", "ping"], stdout="Server replied: pong\n")
    runner.script(["fail2ban-client", "status"], stdout="Status\n`- Jail list:\tsshd\n")

    # The console reads a pending change again every few seconds and the proof is remembered
    # for a few: a test that expects a new login to show moves this on.
    moment = types.SimpleNamespace(now=NOW)

    def build(*, actor: str, on_output: Any = None) -> ServerSecurity:
        return ServerSecurity(
            actor=actor,
            runner=runner,
            host=host.paths,
            changes=tmp_path / "changes",
            on_output=on_output,
            console_port=8080,
            clock=lambda: moment.now,
            python="/usr/bin/python3",
        )

    monkeypatch.setattr(security_api, "ServerSecurity", build)
    jobs = InlineJobs()
    monkeypatch.setattr(security_api, "get_job_manager", lambda: jobs)
    return types.SimpleNamespace(host=host, runner=runner, jobs=jobs, moment=moment)


@pytest.fixture
def client(machine) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(security_api.router, prefix="/api/server")
    session = {"sid": "master", "type": "master"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    return TestClient(app, raise_server_exceptions=False)
