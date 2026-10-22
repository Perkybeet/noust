# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The queue's words and the cancellation of a job from outside the console.

The CLI cannot import the console, so the words live in
:mod:`noust.core.delivery_queue`; the console's job manager takes them from it,
and these tests are what keep the two from drifting.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import delivery_queue
from noust.core.delivery_queue import cancel_recorded_job
from noust.core.exceptions import CancelRefused
from noust.core.store import JobRecord, NoustStore
from noust.web import jobs as console_jobs


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    A store of the test's own.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


def test_the_cli_s_words_are_the_console_s() -> None:
    """One answer to "what is a build" and "what is a state": the console's enums say the same."""
    assert {status.value for status in console_jobs.JobStatus} == {
        delivery_queue.PENDING,
        delivery_queue.RUNNING,
        delivery_queue.COMPLETED,
        delivery_queue.FAILED,
        delivery_queue.CANCELLED,
    }
    assert {
        status.value for status in console_jobs.FINISHED_STATUSES
    } == delivery_queue.FINISHED_STATUSES
    assert {kind.value for kind in console_jobs.BUILD_JOB_TYPES} == delivery_queue.BUILD_JOB_TYPES
    for status in console_jobs.JobStatus:
        assert console_jobs.visible_state(status) == delivery_queue.visible_state(status.value)


def test_a_job_that_waits_is_marked_cancelled_in_the_store(store: NoustStore) -> None:
    store.create_job(JobRecord(id="aa11", type="update", name="Update", status="pending"))

    outcome = cancel_recorded_job("aa11", actor="cli:root", store=store)

    assert (outcome.state, outcome.request) == ("cancelled", None)
    record = store.get_job("aa11")
    assert record is not None and record.status == "cancelled" and record.finished_at


@pytest.mark.parametrize(
    ("status", "reason"),
    [("completed", "finished"), ("failed", "finished"), ("cancelled", "finished")],
)
def test_a_job_that_ended_is_refused(store: NoustStore, status: str, reason: str) -> None:
    store.create_job(JobRecord(id="bb22", type="update", name="Update", status=status))

    with pytest.raises(CancelRefused) as caught:
        cancel_recorded_job("bb22", store=store)

    assert caught.value.reason == reason


def test_an_unknown_job_is_refused(store: NoustStore) -> None:
    with pytest.raises(CancelRefused) as caught:
        cancel_recorded_job("nope", store=store)

    assert caught.value.reason == "unknown"
    assert "noust deploy queue" in caught.value.details


@pytest.mark.parametrize("kind", ["backup", "restore", "migrate", "rollback", "os_update"])
def test_a_running_job_that_is_not_a_build_is_refused(store: NoustStore, kind: str) -> None:
    store.create_job(
        JobRecord(id="cc33", type=kind, name=kind, status="running", domain="shop.example.com")
    )

    with pytest.raises(CancelRefused) as caught:
        cancel_recorded_job("cc33", store=store)

    assert caught.value.reason == "unsupported"


def test_a_running_build_with_nothing_holding_its_application_says_so(store: NoustStore) -> None:
    """A job the store thinks runs while no process holds its lock: nothing to stop."""
    store.create_job(
        JobRecord(
            id="dd44", type="update", name="Update", status="running", domain="shop.example.com"
        )
    )

    with pytest.raises(CancelRefused) as caught:
        cancel_recorded_job("dd44", store=store)

    assert caught.value.reason == "idle"
