# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A job that only queues other jobs and waits for them runs outside the line (J-15).

Applying a manifest creates applications and waits for their deployments. If that
job stood in the deploy queue like a restore does (alone, after everything running,
holding up everything behind it) it would wait for the builds it had just queued.
These tests run the real job manager on real threads, as ``test_delivery_queue`` does.
"""

# Fixtures are imported from test_delivery_queue and used as arguments by name.
# ruff: noqa: F811

from __future__ import annotations

import pytest

from noust.core.delivery_queue import ORCHESTRATION_JOB_TYPES
from noust.web.jobs import Job, JobContext, JobManager, JobStatus, JobType
from tests.test_delivery_queue import (  # noqa: F401 - the fixtures register themselves
    A,
    B,
    Work,
    finish,
    limits,
    log,
    manager,
    queue_work,
    store,
    wait_until,
)


def test_the_kinds_that_only_orchestrate_are_declared_once() -> None:
    assert ORCHESTRATION_JOB_TYPES == {"manifest"}
    assert JobType.MANIFEST.value in ORCHESTRATION_JOB_TYPES


def test_it_starts_at_once_even_while_a_build_runs(manager: JobManager, log: list[str]) -> None:
    build, apply = Work("build", log), Work("apply", log)
    queue_work(manager, build, A)
    assert build.started.wait(20)

    job = queue_work(manager, apply, None, JobType.MANIFEST)

    # The build is still held open: starting now is not waiting for it. A gate holds for ten
    # seconds at most, so a job held up by it would fail here instead of passing late.
    assert apply.started.wait(3)
    assert job.status is JobStatus.RUNNING
    finish(build, apply)
    assert wait_until(lambda: job.status is JobStatus.COMPLETED)


def test_builds_are_not_held_up_by_it_and_do_not_wait_for_it(
    manager: JobManager, log: list[str]
) -> None:
    apply, build = Work("apply", log), Work("build", log)
    queue_work(manager, apply, None, JobType.MANIFEST)
    assert apply.started.wait(3)

    queue_work(manager, build, A)

    assert build.started.wait(3)
    finish(apply, build)


def test_it_takes_no_build_slot(manager: JobManager, log: list[str], limits) -> None:
    limits[0] = limits[0].__class__(zero_downtime=False, max_concurrent_builds=1)
    apply, first, second = Work("apply", log), Work("first", log), Work("second", log)
    queue_work(manager, apply, None, JobType.MANIFEST)
    assert apply.started.wait(20)
    queue_work(manager, first, A)
    assert first.started.wait(3)

    waiting = queue_work(manager, second, B)

    # One slot, taken by the first build: the orchestrator did not take it, and the second waits
    # for the first only.
    assert wait_until(lambda: waiting.queue_position == 1)
    assert waiting.waiting_for is not None and "build slot" in waiting.waiting_for
    assert not second.started.is_set()
    finish(apply, first, second)
    assert wait_until(lambda: waiting.status is JobStatus.COMPLETED)


def test_what_runs_alone_does_not_wait_for_it_either(manager: JobManager, log: list[str]) -> None:
    apply, backup = Work("apply", log), Work("backup", log)
    queue_work(manager, apply, None, JobType.MANIFEST)
    assert apply.started.wait(20)

    queue_work(manager, backup, None, JobType.BACKUP)

    assert backup.started.wait(3)
    finish(apply, backup)


def test_an_orchestrator_that_waits_for_the_build_it_queued_does_not_deadlock(
    manager: JobManager,
    log: list[str],
    limits,
) -> None:
    """The regression this exists for: one slot, and the job that fills it is queued by the one waiting."""
    limits[0] = limits[0].__class__(zero_downtime=False, max_concurrent_builds=1)
    queued: list[Job] = []
    gate = Work("update of A", log)

    def apply(*, job_context: JobContext | None = None, **_kwargs: object) -> dict[str, str]:
        update = queue_work(manager, gate, A)
        queued.append(update)
        assert wait_until(lambda: update.status is JobStatus.COMPLETED, seconds=15)
        return {"waited_for": update.id}

    job = manager.create_job(
        job_type=JobType.MANIFEST,
        name="Apply a manifest",
        description="d",
        func=apply,
        kwargs={},
        metadata={},
    )

    assert gate.started.wait(20)
    finish(gate)
    assert wait_until(lambda: job.status is JobStatus.COMPLETED)
    assert job.result == {"waited_for": queued[0].id}


def test_a_job_that_waits_to_start_can_still_be_cancelled_and_one_that_runs_cannot(
    manager: JobManager, log: list[str]
) -> None:
    from noust.core.exceptions import CancelRefused

    apply = Work("apply", log)
    job = queue_work(manager, apply, None, JobType.MANIFEST)
    assert apply.started.wait(20)

    try:
        manager.cancel(job.id)
    except CancelRefused as refused:
        assert refused.reason == "unsupported"
    else:  # pragma: no cover - the assertion is the point
        raise AssertionError("a manifest being applied was cancelled mid-way")
    finish(apply)


def test_the_queue_command_does_not_say_a_job_waits_for_a_manifest_being_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.cli.commands.deploy import _waiting_for
    from noust.core import delivery
    from noust.core.store import JobRecord

    monkeypatch.setattr(
        delivery,
        "current",
        lambda **_kwargs: delivery.DeliverySettings(zero_downtime=False, max_concurrent_builds=2),
    )
    applying = JobRecord(id="m1", type="manifest", status="running")
    restore = JobRecord(id="r1", type="restore", status="pending")
    building = JobRecord(id="b1", type="update", status="running", domain=A)

    assert _waiting_for(restore, [applying]) == ""
    assert _waiting_for(restore, [applying, building]) == "the jobs that are running now"
