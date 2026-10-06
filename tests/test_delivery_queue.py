# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The deploy queue: per-application order, builds that overlap up to a limit,
a bounded queue, requests that merge, and a build that is cancelled.

These tests run the real job manager on real threads. A job's function is a
small gate the test opens, so what is asserted is order and overlap as they
happen, not as a fake clock says they would.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import cancellation, delivery
from noust.core.applock import AppBusyError, app_lock, holder_of
from noust.core.exceptions import CancelRefused, DeploymentCancelled, QueueFullError
from noust.core.store import App, AppLayout, NoustStore
from noust.web import jobs as jobs_module
from noust.web.jobs import (
    BUILD_JOB_TYPES,
    Job,
    JobContext,
    JobManager,
    JobStatus,
    JobType,
    is_cancellable,
    visible_state,
)

A = "alpha.example.com"
B = "bravo.example.com"
C = "charlie.example.com"
D = "delta.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    A store in the test directory, with an application on releases for each domain.

    Yields:
        The store; the application locks live beside it.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    for domain in (A, B, C, D):
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
    """
    The server's limits, as a one-element list a test replaces.

    Returns:
        The settings handed out.
    """
    current = [delivery.DeliverySettings(zero_downtime=False, max_concurrent_builds=2)]
    monkeypatch.setattr(delivery, "current", lambda **_kwargs: current[0])
    return current


@pytest.fixture
def manager(store: NoustStore, limits: list[delivery.DeliverySettings]) -> Iterator[JobManager]:
    """
    A job manager of its own for the test, looking at the queue ten times as often.

    Yields:
        The manager.
    """
    JobManager.reset_instance()
    mp = pytest.MonkeyPatch()
    mp.setattr(jobs_module, "DISPATCH_POLL_SECONDS", 0.05)
    mp.setattr(jobs_module, "LOCK_RETRY_SECONDS", 0.05)
    mp.setattr(jobs_module, "EXTERNAL_SYNC_SECONDS", 0.05)
    mp.setattr(cancellation, "POLL_INTERVAL", 0.05)
    instance = JobManager()
    try:
        yield instance
    finally:
        JobManager.reset_instance()
        mp.undo()


def wait_until(condition: Callable[[], object], seconds: float = 5.0) -> bool:
    """
    Poll a condition.

    Args:
        condition: Returns something true when it holds.
        seconds: How long to wait.

    Returns:
        Whether it held in time.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class Work:
    """
    A job's function that the test holds open.

    Attributes:
        name: What the test calls it.
        started: Set when the function begins.
        release: The function returns when this is set.
        log: Where every function of the test writes ``start``/``end`` lines.
    """

    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self.started = threading.Event()
        self.release = threading.Event()
        self.log = log

    def __call__(self, *, job_context: JobContext | None = None, **_kwargs: Any) -> dict[str, str]:
        self.log.append(f"start {self.name}")
        self.started.set()
        self.release.wait(10)
        self.log.append(f"end {self.name}")
        return {"name": self.name}


def queue_work(
    manager: JobManager,
    work: Work,
    domain: str | None,
    job_type: JobType = JobType.UPDATE,
    **options: Any,
) -> Job:
    """
    Queue a gate as a job of a type on an application.

    Args:
        manager: The manager.
        work: The gate.
        domain: The application, or None for a job about the server.
        job_type: The type.
        **options: More arguments of ``create_job``.

    Returns:
        The queued job.
    """
    return manager.create_job(
        job_type=job_type,
        name=f"{job_type.value} {domain}",
        description=work.name,
        func=work,
        kwargs=options.pop("kwargs", {}),
        metadata={"domain": domain} if domain else {},
        **options,
    )


def finish(*works: Work) -> None:
    """
    Let gates return.

    Args:
        *works: The gates.
    """
    for work in works:
        work.release.set()


@pytest.fixture
def log() -> list[str]:
    """What the jobs wrote, in order."""
    return []


# ---------------------------------------------------------------------------
# One application at a time
# ---------------------------------------------------------------------------


def test_a_second_update_of_an_application_waits_for_the_first_instead_of_failing(
    manager: JobManager, log: list[str]
) -> None:
    """The push that arrives during a deploy is queued, and runs when the deploy is done."""
    first, second = Work("first", log), Work("second", log)
    one = queue_work(manager, first, A)
    assert first.started.wait(5)

    two = queue_work(manager, second, A)

    assert wait_until(lambda: two.queue_position == 1)
    assert two.status is JobStatus.PENDING
    assert visible_state(two.status) == "queued"
    assert two.waiting_for is not None and f"update of {A}" in two.waiting_for
    assert not second.started.is_set()
    finish(first)
    assert second.started.wait(5)
    finish(second)
    assert wait_until(lambda: two.status is JobStatus.COMPLETED)
    assert (one.status, two.status) == (JobStatus.COMPLETED, JobStatus.COMPLETED)
    assert log == ["start first", "end first", "start second", "end second"]
    assert two.queue_position is None and two.waiting_for is None


def test_a_job_held_up_by_its_application_does_not_hold_up_the_others(
    manager: JobManager, log: list[str]
) -> None:
    """The second update of A waits; the update of B, queued after it, runs beside the first."""
    a1, a2, b1 = Work("a1", log), Work("a2", log), Work("b1", log)
    queue_work(manager, a1, A)
    assert a1.started.wait(5)
    queue_work(manager, a2, A)
    queue_work(manager, b1, B)

    assert b1.started.wait(5)
    assert not a2.started.is_set()
    finish(a1, b1, a2)
    assert wait_until(lambda: log.count("end a2") == 1)


def test_requests_of_one_application_run_in_the_order_they_arrived(
    manager: JobManager, log: list[str]
) -> None:
    """First in, first out, however many are waiting."""
    works = [Work(f"w{i}", log) for i in range(4)]
    for work in works:
        queue_work(manager, work, A)

    for work in works:
        assert work.started.wait(5)
        finish(work)
    assert wait_until(lambda: len(log) == 8)
    assert [line for line in log if line.startswith("start")] == [f"start w{i}" for i in range(4)]


# ---------------------------------------------------------------------------
# Builds that overlap, up to the server's limit
# ---------------------------------------------------------------------------


def test_builds_of_different_applications_overlap_up_to_the_limit(
    manager: JobManager, log: list[str]
) -> None:
    """Two at a time by default; the third waits for a free slot and says so."""
    a, b, c = Work("a", log), Work("b", log), Work("c", log)
    queue_work(manager, a, A)
    queue_work(manager, b, B)
    third = queue_work(manager, c, C)

    assert a.started.wait(5) and b.started.wait(5)
    assert wait_until(lambda: third.waiting_for is not None)
    assert third.waiting_for is not None and "2 of 2" in third.waiting_for
    assert third.queue_position == 1
    assert not c.started.is_set()
    snapshot = manager.queue()
    assert (snapshot.builds_running, snapshot.max_concurrent_builds) == (2, 2)
    assert [job.id for job in snapshot.queued] == [third.id]
    finish(a)
    assert c.started.wait(5)
    finish(b, c)


def test_the_limit_is_read_from_the_configuration_as_it_changes(
    manager: JobManager, limits: list[delivery.DeliverySettings], log: list[str]
) -> None:
    """``noust config set deploy.max_concurrent_builds 1`` applies to the next build."""
    limits[0] = delivery.DeliverySettings(zero_downtime=False, max_concurrent_builds=1)
    a, b = Work("a", log), Work("b", log)
    queue_work(manager, a, A)
    queue_work(manager, b, B)

    assert a.started.wait(5)
    time.sleep(0.2)
    assert not b.started.is_set()
    finish(a)
    assert b.started.wait(5)
    finish(b)


def test_a_job_that_is_not_a_build_runs_alone(manager: JobManager, log: list[str]) -> None:
    """A backup waits for the builds before it, and the builds after it wait for the backup."""
    build, backup, after = Work("build", log), Work("backup", log), Work("after", log)
    queue_work(manager, build, A)
    assert build.started.wait(5)
    queue_work(manager, backup, B, JobType.BACKUP)
    queue_work(manager, after, C)

    time.sleep(0.2)
    assert not backup.started.is_set() and not after.started.is_set()
    finish(build)
    assert backup.started.wait(5)
    time.sleep(0.2)
    assert not after.started.is_set()
    finish(backup)
    assert after.started.wait(5)
    finish(after)
    assert wait_until(lambda: len(log) == 6)
    assert log == [
        "start build",
        "end build",
        "start backup",
        "end backup",
        "start after",
        "end after",
    ]


def test_only_deploys_and_updates_are_builds() -> None:
    assert BUILD_JOB_TYPES == {JobType.DEPLOY, JobType.UPDATE}


# ---------------------------------------------------------------------------
# A bounded queue
# ---------------------------------------------------------------------------


def test_a_full_queue_refuses_the_next_build_and_says_how_to_go_on(
    manager: JobManager, limits: list[delivery.DeliverySettings], log: list[str]
) -> None:
    """Refused (429 in the API), never dropped silently."""
    limits[0] = delivery.DeliverySettings(
        zero_downtime=False, max_concurrent_builds=1, queue_limit=2
    )
    running = Work("running", log)
    queue_work(manager, running, A)
    assert running.started.wait(5)
    queue_work(manager, Work("w1", log), B)
    queue_work(manager, Work("w2", log), C)

    with pytest.raises(QueueFullError) as caught:
        queue_work(manager, Work("w3", log), D)

    assert caught.value.limit == 2
    assert "deploy.queue_limit" in caught.value.details
    assert "noust deploy queue" in caught.value.details
    # Refused means nothing was registered for it.
    assert len(manager.get_active_jobs()) == 3
    finish(running)


def test_the_limit_counts_builds_that_wait_not_jobs_that_are_not_builds(
    manager: JobManager, limits: list[delivery.DeliverySettings], log: list[str]
) -> None:
    """A backup waiting behind a build does not use up the deploy queue."""
    limits[0] = delivery.DeliverySettings(
        zero_downtime=False, max_concurrent_builds=1, queue_limit=1
    )
    running = Work("running", log)
    queue_work(manager, running, A)
    assert running.started.wait(5)

    queue_work(manager, Work("backup", log), B, JobType.BACKUP)
    queue_work(manager, Work("build", log), C)

    finish(running)


def test_a_request_that_merges_does_not_need_room(
    manager: JobManager, limits: list[delivery.DeliverySettings], log: list[str]
) -> None:
    """The full queue still takes a push for an update that already waits in it."""
    limits[0] = delivery.DeliverySettings(
        zero_downtime=False, max_concurrent_builds=1, queue_limit=1
    )
    running = Work("running", log)
    queue_work(manager, running, A)
    assert running.started.wait(5)
    waiting = queue_work(manager, Work("b", log), B, coalesce_key=f"update:{B}")

    again = queue_work(manager, Work("b-again", log), B, coalesce_key=f"update:{B}")

    assert again.id == waiting.id
    with pytest.raises(QueueFullError):
        queue_work(manager, Work("c", log), C)
    finish(running)


# ---------------------------------------------------------------------------
# Requests that are the same request
# ---------------------------------------------------------------------------


def test_a_push_during_a_deploy_is_queued_once_however_many_pushes_arrive(
    manager: JobManager, log: list[str]
) -> None:
    """Five pushes while the first builds: the update that waits builds the last of them."""
    running = Work("running", log)
    queue_work(manager, running, A, coalesce_key=f"update:{A}")
    assert running.started.wait(5)
    ran: list[str] = []

    def push(*, commit: str, job_context: JobContext | None = None) -> None:
        ran.append(commit)

    first = manager.create_job(
        job_type=JobType.UPDATE,
        name="Update",
        description="push",
        func=push,
        kwargs={"commit": "c1"},
        metadata={"domain": A, "commit": "c1"},
        coalesce_key=f"update:{A}",
    )
    assert first.status is JobStatus.PENDING  # the running one's key is not waiting
    others = [
        manager.create_job(
            job_type=JobType.UPDATE,
            name="Update",
            description="push",
            func=push,
            kwargs={"commit": f"c{n}"},
            metadata={"domain": A, "commit": f"c{n}"},
            coalesce_key=f"update:{A}",
            merge={"commit": f"c{n}"},
            note=f"Another push arrived (c{n}): merged, the last commit wins",
        )
        for n in range(2, 6)
    ]

    assert {job.id for job in others} == {first.id}
    assert first.metadata["commit"] == "c5"
    assert first.metadata["merged_requests"] == 4
    assert any("merged" in entry.message for entry in first.logs)
    assert len(manager.queue().queued) == 1
    finish(running)
    assert wait_until(lambda: first.status is JobStatus.COMPLETED)
    assert ran == ["c5"], "one update, for the last push"


def test_a_request_of_another_kind_is_not_merged(manager: JobManager, log: list[str]) -> None:
    """The key says what is the same: another application, or another key, queues apart."""
    running = Work("running", log)
    queue_work(manager, running, A)
    assert running.started.wait(5)
    one = queue_work(manager, Work("one", log), B, coalesce_key=f"update:{B}")

    other_app = queue_work(manager, Work("two", log), C, coalesce_key=f"update:{C}")
    other_kind = queue_work(manager, Work("three", log), B, coalesce_key=f"rebuild:{B}")

    assert len({one.id, other_app.id, other_kind.id}) == 3
    finish(running)


def test_a_request_is_not_merged_into_a_job_that_already_runs(
    manager: JobManager, log: list[str]
) -> None:
    """The running one built what the branch was when it started: the new push waits for its own."""
    running = Work("running", log)
    first = queue_work(manager, running, A, coalesce_key=f"update:{A}")
    assert running.started.wait(5)

    second = queue_work(manager, Work("next", log), A, coalesce_key=f"update:{A}")

    assert second.id != first.id
    assert second.status is JobStatus.PENDING
    finish(running)


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------


def test_a_job_that_waits_is_cancelled_at_once_and_never_runs(
    manager: JobManager, log: list[str]
) -> None:
    """The queue moves on without it."""
    running, doomed, next_one = Work("running", log), Work("doomed", log), Work("next", log)
    queue_work(manager, running, A)
    assert running.started.wait(5)
    job = queue_work(manager, doomed, A)
    after = queue_work(manager, next_one, A)
    assert after.queue_position == 2

    outcome = manager.cancel(job.id, actor="token:ci")

    assert outcome.state == "cancelled"
    assert job.status is JobStatus.CANCELLED
    assert visible_state(job.status) == "cancelled"
    assert after.queue_position == 1
    finish(running)
    assert next_one.started.wait(5)
    assert not doomed.started.is_set()
    finish(next_one)


def test_the_job_manager_still_answers_yes_or_no_to_the_old_question(
    manager: JobManager, log: list[str]
) -> None:
    running = Work("running", log)
    queue_work(manager, running, A)
    assert running.started.wait(5)
    waiting = queue_work(manager, Work("waiting", log), A)

    assert manager.cancel_job(waiting.id) is True
    assert manager.cancel_job(waiting.id) is False
    assert manager.cancel_job("nonexistent") is False
    finish(running)


def test_a_build_that_is_cancelled_while_it_builds_ends_cancelled_and_the_queue_advances(
    manager: JobManager, store: NoustStore, log: list[str]
) -> None:
    """The lock is released, the next request for the application starts, nothing is failed."""
    reached = threading.Event()

    def build(*, job_context: JobContext | None = None) -> None:
        with cancellation.operation_window(A, "update"):
            reached.set()
            while True:
                cancellation.checkpoint()
                time.sleep(0.01)

    building = manager.create_job(
        job_type=JobType.UPDATE,
        name="Update",
        description="build",
        func=build,
        metadata={"domain": A},
    )
    assert reached.wait(5)
    follow_up = Work("follow-up", log)
    queued = queue_work(manager, follow_up, A)
    assert is_cancellable(building) is True

    outcome = manager.cancel(building.id, actor="token:ci")

    assert outcome.state == "cancelling"
    assert wait_until(lambda: building.status is JobStatus.CANCELLED)
    assert building.error is None
    assert building.result == {"domain": A, "status": "cancelled", "cancelled_by": "token:ci"}
    assert any("cancelled" in entry.message.lower() for entry in building.logs)
    assert follow_up.started.wait(5), "the queue advanced"
    assert queued.status is JobStatus.RUNNING
    record = store.get_job(building.id)
    assert record is not None and record.status == "cancelled" and record.error is None
    finish(follow_up)
    assert wait_until(lambda: holder_of(A) is None)


def test_cancelling_a_build_that_is_not_started_to_activate_is_accepted_then_refused_after(
    manager: JobManager, log: list[str]
) -> None:
    """Past the point of no return the request is refused with what to do."""
    committed = threading.Event()
    done = threading.Event()

    def build(*, job_context: JobContext | None = None) -> None:
        with cancellation.operation_window(A, "update"):
            cancellation.commit("activating release 1")
            committed.set()
            done.wait(5)

    building = manager.create_job(
        job_type=JobType.UPDATE,
        name="Update",
        description="build",
        func=build,
        metadata={"domain": A},
    )
    assert committed.wait(5)
    assert is_cancellable(building) is False

    with pytest.raises(CancelRefused) as caught:
        manager.cancel(building.id, actor="op")

    assert caught.value.reason == "too_late"
    done.set()
    assert wait_until(lambda: building.status is JobStatus.COMPLETED)


def test_a_running_job_that_is_not_a_build_cannot_be_cancelled(
    manager: JobManager, log: list[str]
) -> None:
    running = Work("backup", log)
    job = queue_work(manager, running, A, JobType.BACKUP)
    assert running.started.wait(5)

    with pytest.raises(CancelRefused) as caught:
        manager.cancel(job.id)

    assert caught.value.reason == "unsupported"
    assert is_cancellable(job) is False
    finish(running)


def test_a_job_that_ended_or_never_was_cannot_be_cancelled(
    manager: JobManager, log: list[str]
) -> None:
    work = Work("quick", log)
    job = queue_work(manager, work, A)
    finish(work)
    assert wait_until(lambda: job.status is JobStatus.COMPLETED)

    with pytest.raises(CancelRefused) as ended:
        manager.cancel(job.id)
    with pytest.raises(CancelRefused) as unknown:
        manager.cancel("deadbeef")

    assert ended.value.reason == "finished"
    assert unknown.value.reason == "unknown"


def test_a_cancellation_another_process_recorded_is_applied_to_the_waiting_job(
    manager: JobManager, store: NoustStore, log: list[str]
) -> None:
    """``noust deploy cancel <job>`` in a terminal writes it in the store; the queue reads it."""
    running, waiting = Work("running", log), Work("waiting", log)
    queue_work(manager, running, A)
    assert running.started.wait(5)
    job = queue_work(manager, waiting, A)
    assert wait_until(lambda: store.get_job(job.id) is not None)

    store.update_job(job.id, status="cancelled")

    assert wait_until(lambda: job.status is JobStatus.CANCELLED)
    finish(running)
    time.sleep(0.2)
    assert not waiting.started.is_set()


# ---------------------------------------------------------------------------
# An operation of another process holds the application
# ---------------------------------------------------------------------------


def hold(domain: str, operation: str) -> tuple[threading.Event, threading.Event, threading.Thread]:
    """
    Hold an application's lock on a thread of its own, as a terminal's update would.

    Args:
        domain: The application.
        operation: What the lock records.

    Returns:
        The event set once held, the event that releases it, and the thread.
    """
    held, release = threading.Event(), threading.Event()

    def run() -> None:
        with app_lock(domain, operation):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return held, release, thread


def test_a_push_waits_for_an_update_somebody_runs_by_hand_and_then_deploys(
    manager: JobManager, log: list[str]
) -> None:
    """Not AppBusyError, not a lost push: queued, saying what it waits for, then run."""
    held, release, thread = hold(A, "update")
    assert held.wait(5)
    work = Work("push", log)

    job = queue_work(manager, work, A)

    assert wait_until(lambda: job.waiting_for is not None)
    assert job.status is JobStatus.PENDING
    assert job.waiting_for is not None and "update running on" in job.waiting_for
    assert any("Waiting for" in entry.message for entry in job.logs)
    time.sleep(0.2)
    assert not work.started.is_set()
    release.set()
    thread.join(5)
    assert work.started.wait(5)
    assert job.status is JobStatus.RUNNING
    assert holder_of(A) is not None, "the queue holds the application while it runs"
    finish(work)
    assert wait_until(lambda: job.status is JobStatus.COMPLETED)
    # Said once, not at every look.
    assert sum("Waiting for" in entry.message for entry in job.logs) == 1


def test_a_build_that_waits_too_long_for_the_application_fails_naming_the_holder(
    manager: JobManager, monkeypatch: pytest.MonkeyPatch, log: list[str]
) -> None:
    """An operation that never ends is not hidden by a queue that waits for ever."""
    monkeypatch.setattr(jobs_module, "LOCK_WAIT_SECONDS", 0.3)
    held, release, thread = hold(A, "update")
    assert held.wait(5)

    job = queue_work(manager, Work("push", log), A)

    assert wait_until(lambda: job.status is JobStatus.FAILED)
    assert job.error is not None and "update started at" in job.error
    release.set()
    thread.join(5)


def test_a_job_waiting_for_another_process_does_not_hold_up_other_applications(
    manager: JobManager, log: list[str]
) -> None:
    held, release, thread = hold(A, "update")
    assert held.wait(5)
    queue_work(manager, Work("a", log), A)
    other = Work("b", log)

    queue_work(manager, other, B)

    assert other.started.wait(5)
    finish(other)
    release.set()
    thread.join(5)


def test_the_queue_holds_the_application_s_lock_so_a_request_in_this_process_is_refused(
    manager: JobManager, log: list[str]
) -> None:
    """Whoever else wants the application meanwhile gets AppBusyError naming the job's operation."""
    running = Work("running", log)
    queue_work(manager, running, A)
    assert running.started.wait(5)
    refused: list[AppBusyError] = []

    def try_lock() -> None:
        try:
            with app_lock(A, "rollback"):
                pass
        except AppBusyError as busy:
            refused.append(busy)

    thread = threading.Thread(target=try_lock)
    thread.start()
    thread.join(5)

    assert len(refused) == 1
    assert refused[0].holder is not None and refused[0].holder.operation == "update"
    finish(running)


# ---------------------------------------------------------------------------
# What the queue says
# ---------------------------------------------------------------------------


def test_the_queue_snapshot_lists_running_and_waiting_in_order(
    manager: JobManager, log: list[str]
) -> None:
    a, b, c, d = (Work(name, log) for name in "abcd")
    first = queue_work(manager, a, A)
    second = queue_work(manager, b, B)
    third = queue_work(manager, c, C)
    fourth = queue_work(manager, d, D)
    assert a.started.wait(5) and b.started.wait(5)

    snapshot = manager.queue()

    assert [job.id for job in snapshot.running] == [first.id, second.id]
    assert [job.id for job in snapshot.queued] == [third.id, fourth.id]
    assert (snapshot.builds_queued, snapshot.queue_limit) == (2, 25)
    assert [job.queue_position for job in snapshot.queued] == [1, 2]
    finish(a, b, c, d)


def test_a_job_of_the_job_to_dict_carries_the_queue_s_words(
    manager: JobManager, log: list[str]
) -> None:
    running = Work("running", log)
    queue_work(manager, running, A)
    assert running.started.wait(5)
    waiting = queue_work(manager, Work("waiting", log), A)

    data = waiting.to_dict()

    assert data["state"] == "queued"
    assert data["queue_position"] == 1
    assert data["waiting_for"]
    assert data["cancel_requested"] is False
    finish(running)


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (JobStatus.PENDING, "queued"),
        (JobStatus.RUNNING, "running"),
        (JobStatus.COMPLETED, "succeeded"),
        (JobStatus.FAILED, "failed"),
        (JobStatus.CANCELLED, "cancelled"),
    ],
)
def test_the_five_visible_states(status: JobStatus, state: str) -> None:
    assert visible_state(status) == state
    assert visible_state(status.value) == state


def test_a_job_that_raised_the_cancellation_is_cancelled_not_failed(
    manager: JobManager, store: NoustStore
) -> None:
    """Whatever raises it (a deployer on its own, a cancelled clone): an operator's decision."""

    def stops(*, job_context: JobContext | None = None) -> None:
        raise DeploymentCancelled("The update of x was cancelled", cancelled_by="op")

    job = manager.create_job(
        job_type=JobType.BACKUP, name="x", description="x", func=stops, metadata={"domain": A}
    )

    assert wait_until(lambda: job.status is JobStatus.CANCELLED)
    assert job.error is None
    assert wait_until(
        lambda: (record := store.get_job(job.id)) is not None and record.status == "cancelled"
    )
