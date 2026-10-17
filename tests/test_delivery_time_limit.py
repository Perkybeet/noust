# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The longest a deployment may take on this server (checklist F-28).

Coolify's equivalent setting is ignored once a build is running (its issues 5747 and
6708): a hung build holds its slot for ever and the queue behind it never moves. These
tests run the real job manager on real threads and prove the limit is enforced on the
build that runs, not only read at queueing: the build is cancelled through the same
window an operator's cancellation uses, the job says it failed because of the time limit
(not that somebody cancelled it), the queue advances, and what cannot be interrupted is
reported instead of pretending.

What the configuration holds for the limit, and what refuses it, is in
``tests/test_delivery_config.py`` with the other two.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from noust.core import cancellation, delivery
from noust.core.store import App, AppLayout, NoustStore
from noust.web import jobs as jobs_module
from noust.web.jobs import JobContext, JobManager, JobStatus, JobType

A = "alpha.example.com"
B = "bravo.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store with two applications on releases."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    for domain in (A, B):
        instance.create_app(
            App(
                domain=domain,
                app_type="nodejs",
                app_path=f"/var/www/apps/{domain}",
                port=3000,
                layout=AppLayout.RELEASES.value,
            )
        )
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> list[delivery.DeliverySettings]:
    """The server's limits as a one-element list a test replaces."""
    current = [
        delivery.DeliverySettings(zero_downtime=False, max_concurrent_builds=2, max_minutes=1)
    ]
    monkeypatch.setattr(delivery, "current", lambda **_kwargs: current[0])
    return current


@pytest.fixture
def manager(
    store: NoustStore, limits: list[delivery.DeliverySettings], monkeypatch: pytest.MonkeyPatch
) -> Iterator[JobManager]:
    """
    A job manager that looks at the queue twenty times a second, where a minute is a tenth of one.

    Yields:
        The manager.
    """
    JobManager.reset_instance()
    mp = pytest.MonkeyPatch()
    mp.setattr(jobs_module, "DISPATCH_POLL_SECONDS", 0.05)
    mp.setattr(jobs_module, "LOCK_RETRY_SECONDS", 0.05)
    mp.setattr(jobs_module, "EXTERNAL_SYNC_SECONDS", 0.05)
    mp.setattr(cancellation, "POLL_INTERVAL", 0.05)
    mp.setattr(delivery, "SECONDS_PER_MINUTE", 0.1)
    instance = JobManager()
    try:
        yield instance
    finally:
        JobManager.reset_instance()
        mp.undo()


def wait_until(condition: Callable[[], object], seconds: float = 20.0) -> bool:
    """Poll a condition until it holds or the time is up."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def build_until_cancelled(domain: str, reached: threading.Event):
    """A build that loops at checkpoints, like the fetch/install/build of a release."""

    def build(*, job_context: JobContext | None = None) -> None:
        with cancellation.operation_window(domain, "update"):
            reached.set()
            while True:
                cancellation.checkpoint()
                time.sleep(0.01)

    return build


# ---------------------------------------------------------------------------
# A build that runs past it
# ---------------------------------------------------------------------------


def test_a_build_that_outlives_the_limit_is_stopped_and_fails_with_the_limit_as_the_reason(
    manager: JobManager, store: NoustStore
) -> None:
    reached = threading.Event()
    job = manager.create_job(
        job_type=JobType.UPDATE,
        name="Update",
        description="build",
        func=build_until_cancelled(A, reached),
        metadata={"domain": A},
    )
    assert reached.wait(20)

    assert wait_until(lambda: job.status is JobStatus.FAILED)

    assert job.error is not None
    assert "maximum deployment time" in job.error
    assert "1 minute" in job.error
    assert "deploy.max_minutes" in job.error
    assert any("maximum deployment time" in entry.message for entry in job.logs)
    # The record is written just after the job object changes state: wait for it, do not race it.
    assert wait_until(
        lambda: (record := store.get_job(job.id)) is not None and record.status == "failed"
    )


def test_the_queue_moves_on_when_a_build_is_stopped_by_the_limit(manager: JobManager) -> None:
    reached = threading.Event()
    first = manager.create_job(
        job_type=JobType.UPDATE,
        name="First",
        description="hung",
        func=build_until_cancelled(A, reached),
        metadata={"domain": A},
    )
    assert reached.wait(20)
    second_ran = threading.Event()

    def second(*, job_context: JobContext | None = None) -> str:
        second_ran.set()
        return "done"

    follow_up = manager.create_job(
        job_type=JobType.UPDATE,
        name="Second",
        description="behind it",
        func=second,
        metadata={"domain": A},
    )

    assert wait_until(lambda: first.status is JobStatus.FAILED)
    assert second_ran.wait(20), "the application's lock was released and its next job started"
    assert wait_until(lambda: follow_up.status is JobStatus.COMPLETED)


def test_the_time_is_counted_from_the_start_of_the_build_not_from_the_queueing(
    manager: JobManager, limits: list[delivery.DeliverySettings]
) -> None:
    """A job that waited three limits for a slot still gets its whole time to build."""
    limits[0] = delivery.DeliverySettings(
        zero_downtime=False, max_concurrent_builds=1, max_minutes=2
    )
    gate = threading.Event()
    started = threading.Event()

    def holds_the_slot(*, job_context: JobContext | None = None) -> None:
        gate.wait(20)

    def quick(*, job_context: JobContext | None = None) -> str:
        started.set()
        time.sleep(0.05)
        return "ok"

    manager.create_job(
        job_type=JobType.UPDATE,
        name="A",
        description="a",
        func=holds_the_slot,
        metadata={"domain": A},
    )
    waiting = manager.create_job(
        job_type=JobType.UPDATE, name="B", description="b", func=quick, metadata={"domain": B}
    )
    time.sleep(0.5)  # well past two "minutes" of 0.1 s, all of it spent waiting
    assert waiting.status is JobStatus.PENDING
    gate.set()

    assert started.wait(20)
    assert wait_until(lambda: waiting.status is JobStatus.COMPLETED)


def test_zero_is_no_limit(manager: JobManager, limits: list[delivery.DeliverySettings]) -> None:
    limits[0] = delivery.DeliverySettings(
        zero_downtime=False, max_concurrent_builds=2, max_minutes=0
    )
    reached = threading.Event()
    stop = threading.Event()

    def long_build(*, job_context: JobContext | None = None) -> str:
        reached.set()
        stop.wait(20)
        return "ok"

    job = manager.create_job(
        job_type=JobType.UPDATE, name="U", description="u", func=long_build, metadata={"domain": A}
    )
    assert reached.wait(20)

    time.sleep(0.6)

    assert job.status is JobStatus.RUNNING
    stop.set()
    assert wait_until(lambda: job.status is JobStatus.COMPLETED)


def test_a_job_that_is_not_a_build_is_not_held_to_it(manager: JobManager) -> None:
    """A backup of a big database may take longer than a deployment is allowed to."""
    reached = threading.Event()
    stop = threading.Event()

    def backup(*, job_context: JobContext | None = None) -> str:
        reached.set()
        stop.wait(20)
        return "ok"

    job = manager.create_job(
        job_type=JobType.BACKUP, name="B", description="b", func=backup, metadata={"domain": A}
    )
    assert reached.wait(20)

    time.sleep(0.6)

    assert job.status is JobStatus.RUNNING
    stop.set()
    assert wait_until(lambda: job.status is JobStatus.COMPLETED)


def test_a_build_that_cannot_be_interrupted_is_left_running_and_the_job_says_so_once(
    manager: JobManager,
) -> None:
    """A Compose build runs inside the Docker daemon: the limit reports it, it does not kill the daemon."""
    reached = threading.Event()
    stop = threading.Event()

    def compose_build(*, job_context: JobContext | None = None) -> str:
        reached.set()
        stop.wait(20)
        return "ok"

    job = manager.create_job(
        job_type=JobType.DEPLOY,
        name="D",
        description="compose",
        func=compose_build,
        metadata={"domain": "stack.example.com", "app_type": "docker-compose"},
    )
    assert reached.wait(20)

    assert wait_until(lambda: any("cannot be stopped" in entry.message for entry in job.logs))
    time.sleep(0.4)

    notes = [entry for entry in job.logs if "maximum deployment time" in entry.message]
    assert len(notes) == 1, "said once, not at every look of the dispatcher"
    assert job.status is JobStatus.RUNNING
    stop.set()
    assert wait_until(lambda: job.status is JobStatus.COMPLETED)


def test_an_operators_cancellation_before_the_limit_is_still_a_cancellation(
    manager: JobManager, limits: list[delivery.DeliverySettings]
) -> None:
    limits[0] = delivery.DeliverySettings(
        zero_downtime=False, max_concurrent_builds=2, max_minutes=600
    )
    reached = threading.Event()
    job = manager.create_job(
        job_type=JobType.UPDATE,
        name="U",
        description="u",
        func=build_until_cancelled(A, reached),
        metadata={"domain": A},
    )
    assert reached.wait(20)

    manager.cancel(job.id, actor="token:ci")

    assert wait_until(lambda: job.status is JobStatus.CANCELLED)
    assert job.error is None


# ---------------------------------------------------------------------------
# What the deployment's history says
# ---------------------------------------------------------------------------


def test_the_history_row_of_a_build_the_limit_stopped_is_a_failure_not_a_cancellation(
    store: NoustStore, limits: list[delivery.DeliverySettings], tmp_path: Path
) -> None:
    """Nobody decided to stop it: the row carries the sentence and no cancellation mark."""
    from noust.core.exceptions import DeploymentCancelled
    from noust.deployers.recorder import CapturingLogger, DeploymentRecorder

    recorder = DeploymentRecorder(
        store, A, "panel", logger=CapturingLogger(), log_root=tmp_path / "logs", operation="update"
    )
    recorder.start()
    assert recorder.deployment_id is not None

    recorder.finish_failure(
        DeploymentCancelled("cancelled", cancelled_by=delivery.TIME_LIMIT_ACTOR)
    )

    record = store.get_deployment(recorder.deployment_id)
    assert record is not None and record.status == "failed"
    assert record.error is not None and "maximum deployment time (1 minute)" in record.error
    assert record.cancelled_at is None and record.cancelled_by is None
