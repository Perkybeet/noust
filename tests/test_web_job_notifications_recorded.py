# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A console job's deployment is announced exactly once, whichever path fails.

The deployment recorder announces every deployment it opens
(:mod:`wasm.core.deploy_notifications`). Two things went wrong around it:

- A rollback queued from the console was a ``restore`` job, the type a
  backup restore still announces from :mod:`wasm.web.server` - so every
  console rollback was announced twice. Rollbacks are ``rollback`` jobs now.
- A job that fails before the recorder opens (the application is busy, the
  in-place pull fails, the pre-flight check refuses) was announced by nobody.
  The job subscriber now sends ``deploy_failed`` for a failed deploy, update
  or rollback job in whose run no deployment event was published.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from wasm.core.notifier import NotificationEvent
from wasm.deployers.deploy_events import DeployEvent, DeployEventKind
from wasm.web.jobs import Job, JobStatus, JobType
from wasm.web.server import DeploymentWitness, JobNotificationSubscriber


def make_job(
    status: JobStatus,
    job_type: JobType,
    *,
    job_id: str = "job-1",
    domain: str = "example.com",
    error: str | None = None,
) -> Job:
    """
    Build a job the way the job manager reports one.

    Args:
        status: Its status.
        job_type: Its type.
        job_id: Its id.
        domain: The application it is about.
        error: The failure, verbatim.

    Returns:
        The job.
    """
    return Job(
        id=job_id,
        type=job_type,
        name=f"{job_type.value.capitalize()} {domain}",
        description="",
        status=status,
        completed_at=datetime.now() if status in (JobStatus.COMPLETED, JobStatus.FAILED) else None,
        error=error,
        metadata={"domain": domain},
    )


@pytest.fixture
def sent() -> list[NotificationEvent]:
    """
    Returns:
        What the subscriber delivered.
    """
    return []


@pytest.fixture
def witness() -> DeploymentWitness:
    """
    Returns:
        A witness not subscribed to the real publisher; tests feed it events.
    """
    return DeploymentWitness()


@pytest.fixture
def subscriber(sent: list[NotificationEvent], witness: DeploymentWitness) -> Any:
    """
    Args:
        sent: The capture.
        witness: The witness.

    Returns:
        The subscriber, delivering into the capture.
    """
    return JobNotificationSubscriber(deliver=sent.append, witness=witness)


def run(
    subscriber: Any,
    job_type: JobType,
    *,
    events: list[DeployEvent],
    witness: DeploymentWitness,
    job_id: str = "job-1",
    error: str = "Application example.com is busy: deploy (pid 42)",
) -> None:
    """
    Drive one job through running to failed, publishing events meanwhile.

    Args:
        subscriber: The job subscriber.
        job_type: The job's type.
        events: Deployment events published while it runs.
        witness: Where the events go.
        job_id: The job's id.
        error: The failure.
    """
    subscriber(make_job(JobStatus.RUNNING, job_type, job_id=job_id))
    for event in events:
        witness.on_deploy_event(event)
    subscriber(make_job(JobStatus.FAILED, job_type, job_id=job_id, error=error))


@pytest.mark.parametrize("job_type", [JobType.DEPLOY, JobType.UPDATE, JobType.ROLLBACK])
def test_a_job_that_fails_before_the_recorder_opens_is_announced_once(
    subscriber: Any,
    witness: DeploymentWitness,
    sent: list[NotificationEvent],
    job_type: JobType,
) -> None:
    """Nobody else will: no deployment row, no deployment event."""
    run(subscriber, job_type, events=[], witness=witness)
    subscriber(make_job(JobStatus.FAILED, job_type, error="again"))

    assert len(sent) == 1
    assert sent[0].kind == "deploy_failed"
    assert sent[0].domain == "example.com"
    assert "is busy" in sent[0].body


def test_a_job_whose_deployment_was_recorded_is_left_to_the_recorder(
    subscriber: Any, witness: DeploymentWitness, sent: list[NotificationEvent]
) -> None:
    """The recorder announced it, by job id."""
    failed = DeployEvent(kind=DeployEventKind.FAILED, domain="example.com", job_id="job-1")
    run(subscriber, JobType.UPDATE, events=[failed], witness=witness)

    assert sent == []


def test_a_rollback_is_recognised_by_its_domain(
    subscriber: Any, witness: DeploymentWitness, sent: list[NotificationEvent]
) -> None:
    """A rollback's history row carries no job id; the job's domain is enough."""
    started = DeployEvent(kind=DeployEventKind.STARTED, domain="example.com", trigger="rollback")
    run(subscriber, JobType.ROLLBACK, events=[started], witness=witness)

    assert sent == []


def test_another_applications_deployment_does_not_count(
    subscriber: Any, witness: DeploymentWitness, sent: list[NotificationEvent]
) -> None:
    """Only this job's own deployment silences it."""
    other = DeployEvent(kind=DeployEventKind.FAILED, domain="other.example.com")
    elsewhere = DeployEvent(kind=DeployEventKind.FAILED, domain="example.com", job_id="job-9")
    run(subscriber, JobType.DEPLOY, events=[other, elsewhere], witness=witness)

    assert len(sent) == 1


@pytest.mark.parametrize("job_type", [JobType.DEPLOY, JobType.UPDATE, JobType.ROLLBACK])
def test_a_completed_deployment_job_is_never_announced_here(
    subscriber: Any, sent: list[NotificationEvent], job_type: JobType
) -> None:
    """Success always went through the recorder."""
    subscriber(make_job(JobStatus.RUNNING, job_type))
    subscriber(make_job(JobStatus.COMPLETED, job_type))

    assert sent == []


def test_a_backup_restore_is_still_announced(
    subscriber: Any, sent: list[NotificationEvent]
) -> None:
    """``restore`` is a backup restore now, and nothing else announces it."""
    subscriber(make_job(JobStatus.RUNNING, JobType.RESTORE))
    subscriber(make_job(JobStatus.COMPLETED, JobType.RESTORE))

    assert [event.kind for event in sent] == ["deploy_success"]


def test_the_witness_forgets_finished_jobs(subscriber: Any, witness: DeploymentWitness) -> None:
    """A long-running console must not grow a set per job it ever ran."""
    for n in range(50):
        subscriber(make_job(JobStatus.RUNNING, JobType.UPDATE, job_id=f"j{n}"))
        witness.on_deploy_event(
            DeployEvent(kind=DeployEventKind.STARTED, domain="example.com", job_id=f"j{n}")
        )
        final = JobStatus.COMPLETED if n % 2 else JobStatus.FAILED
        subscriber(make_job(final, JobType.UPDATE, job_id=f"j{n}"))

    assert witness.tracked() == 0


# -- The endpoints queue rollback jobs ----------------------------------------


def test_both_rollback_endpoints_queue_a_rollback_job(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither the deployments page nor POST /api/jobs/rollback queues a restore."""
    from wasm.core.store import DeploymentStatus, get_store
    from wasm.web.api import deployments as deployments_api
    from wasm.web.auth import CSRF_HEADER_NAME, SecurityConfig
    from wasm.web.server import create_app, get_token_manager

    created: list[JobType] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> Job:
            created.append(kwargs["job_type"])
            return make_job(JobStatus.PENDING, kwargs["job_type"], job_id="ab12cd34")

    monkeypatch.setattr("wasm.web.api.jobs.get_job_manager", lambda: Jobs())
    monkeypatch.setattr(deployments_api, "get_job_manager", lambda: Jobs())
    monkeypatch.setattr(deployments_api, "rollback_availability", lambda records: {})

    app = create_app(SecurityConfig(state_dir=sandbox / "state", rate_limit_requests=5000))
    client = TestClient(app, client=("testclient", 50000))
    token = get_token_manager().generate_master_token()
    login = client.post("/api/auth/login", json={"token": token})
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    client.post("/api/auth/elevate", json={"token": token})

    from wasm.core.store import App

    store = get_store()
    store.create_app(App(domain="example.com", app_type="static"))
    deployment = store.record_deployment_start("example.com", "cli")
    store.finish_deployment(deployment, DeploymentStatus.SUCCESS.value)

    by_backup = client.post("/api/jobs/rollback", json={"domain": "example.com"})
    by_deployment = client.post(f"/api/apps/example.com/deployments/{deployment}/rollback")

    assert by_backup.status_code == 202, by_backup.text
    assert by_deployment.status_code == 202, by_deployment.text
    assert created == [JobType.ROLLBACK, JobType.ROLLBACK]
