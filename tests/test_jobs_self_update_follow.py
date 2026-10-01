# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A self-update's job ends as the update did, not as the restart it caused.

Every fleet update left "Update Noust - failed (Interrupted by a panel
restart)" on every node, although each update succeeded: the package restarts
the console that follows the update, and the new console failed every job it
found unfinished. The update keeps its own record; the job now follows it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.store import JobRecord, NoustStore
from noust.web import jobs
from noust.web.jobs import INTERRUPTED_REASON, JobManager


@pytest.fixture
def store(tmp_path: Path) -> NoustStore:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


class FakeSelfUpdate:
    """Stands in for SelfUpdate: the record, and what settling it says."""

    record: SimpleNamespace | None = None
    endings: list[str] = []

    def read(self) -> SimpleNamespace | None:
        return FakeSelfUpdate.record

    def settle(self, record: SimpleNamespace) -> SimpleNamespace:
        if FakeSelfUpdate.endings:
            record.status = FakeSelfUpdate.endings.pop(0)
        return record


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> type[FakeSelfUpdate]:
    monkeypatch.setattr("noust.managers.self_update.SelfUpdate", FakeSelfUpdate)
    FakeSelfUpdate.record = SimpleNamespace(job_id="job1", status="running", error=None)
    FakeSelfUpdate.endings = []
    return FakeSelfUpdate


def _job(store: NoustStore, job_id: str = "job1") -> None:
    store.create_job(
        JobRecord(id=job_id, type="self_update", name="Update Noust", status="running")
    )


def test_an_update_that_succeeded_completes_its_job(store, fake) -> None:
    _job(store)
    store.fail_interrupted_jobs(INTERRUPTED_REASON)
    fake.endings = ["running", "installed", "succeeded"]

    jobs._settle_update_job(store, "job1", sleep=lambda _s: None)

    job = store.get_job("job1")
    assert job.status == "completed" and not job.error


def test_an_update_that_failed_keeps_its_own_reason(store, fake) -> None:
    _job(store)
    fake.record.error = "apt-get failed; its words are below"
    fake.endings = ["failed"]

    jobs._settle_update_job(store, "job1", sleep=lambda _s: None)

    job = store.get_job("job1")
    assert job.status == "failed" and job.error == "apt-get failed; its words are below"


def test_an_update_that_never_says_fails_with_that_said(store, fake, monkeypatch) -> None:
    _job(store)
    monkeypatch.setattr(jobs, "UPDATE_FOLLOW_SECONDS", 10)

    jobs._settle_update_job(store, "job1", sleep=lambda _s: None)

    job = store.get_job("job1")
    assert job.status == "failed" and "did not say how it ended" in job.error


def test_startup_sets_the_update_job_running_again_and_follows_it(store, fake, monkeypatch) -> None:
    _job(store)
    _job(store, "other")
    started: list[tuple] = []
    monkeypatch.setattr(
        jobs.threading,
        "Thread",
        lambda target, args, daemon: SimpleNamespace(start=lambda: started.append(args)),
    )

    store.fail_interrupted_jobs(INTERRUPTED_REASON)
    JobManager._follow_interrupted_updates(store, [store.get_job("job1"), store.get_job("other")])

    assert store.get_job("job1").status == "running"
    # A job the record is not about stays as the restart left it.
    assert store.get_job("other").status == "failed"
    assert started == [(store, "job1")]
