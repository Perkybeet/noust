# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A job that fails keeps the sentence that ended it, with its code, for whoever reads it (item 151).

A job's error was ``str(exc)``: the English of the message, the word "Details:" and the fix, glued
into one line, and every code the exception carried was lost on the way. The job page then drew
English in a Spanish console for any failure of a deployment, a firewall change or a backup, which is
nearly everything that takes long. The failure now travels as its code and values (``error_coded``)
with the failing program's own words apart (``error_output``), is kept with the job, and comes back
from the store the same way.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.job_errors import JobErrors
from noust.core.server_text import say
from noust.core.store import JobRecord, NoustStore
from noust.web.jobs import Job, JobContext, JobManager, JobType


@pytest.fixture
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    try:
        yield instance
    finally:
        instance.close()


def _run(func: Any) -> Job:
    manager = JobManager()
    job_id = str(uuid.uuid4())[:8]
    job = Job(id=job_id, type=JobType.DEPLOY, name="deploy", description="deploy")
    manager._jobs[job_id] = job
    manager._open_log(job_id)
    manager._notify_subscribers(job)
    manager._execute_job(job_id, func, (), {})
    return job


def test_a_failure_with_a_code_keeps_it_with_its_values(store: NoustStore) -> None:
    def work(job_context: JobContext | None = None) -> None:
        raise DeploymentError.from_text(say("apps.not_found", domain="shop.example.com"))

    job = _run(work)

    assert job.status.value == "failed"
    assert job.error_coded == say("apps.not_found", domain="shop.example.com")
    assert job.error is not None and "Application not found: shop.example.com" in job.error
    assert job.to_dict()["error_coded"] == {
        "code": "apps.not_found",
        "params": {"domain": "shop.example.com"},
    }


def test_the_failing_programs_own_words_travel_apart_from_the_sentence(store: NoustStore) -> None:
    def work(job_context: JobContext | None = None) -> None:
        raise ValidationError.from_text(
            say("apps.not_found", domain="a.example.com"), output="nginx: [emerg] unexpected end\n"
        )

    job = _run(work)

    assert job.error_output == "nginx: [emerg] unexpected end"
    assert job.to_dict()["error_output"] == "nginx: [emerg] unexpected end"


def test_a_failure_with_no_code_has_none_and_says_so(store: NoustStore) -> None:
    def work(job_context: JobContext | None = None) -> None:
        raise DeploymentError("Something nobody gave a code")

    def crash(job_context: JobContext | None = None) -> None:
        raise RuntimeError("a defect")

    for func in (work, crash):
        job = _run(func)
        assert job.error_coded is None and job.to_dict()["error_coded"] is None
        assert job.to_dict()["error_output"] is None


def test_a_secret_of_the_job_is_taken_out_of_the_values_of_the_sentence(store: NoustStore) -> None:
    def work(job_context: JobContext | None = None) -> None:
        assert job_context is not None
        job_context._job.scrubber.add(["s3cr3t-value"])
        raise DeploymentError.from_text(say("apps.not_found", domain="a-s3cr3t-value.example.com"))

    job = _run(work)

    assert job.error_coded is not None
    assert "s3cr3t-value" not in str(job.error_coded.params)
    assert job.error is not None and "s3cr3t-value" not in job.error


def test_the_failure_is_kept_with_the_job_and_read_back_from_the_store(store: NoustStore) -> None:
    def work(job_context: JobContext | None = None) -> None:
        raise DeploymentError.from_text(
            say("apps.not_found", domain="shop.example.com"), output="the output\n"
        )

    job = _run(work)

    record = store.get_job(job.id)
    assert isinstance(record, JobRecord)
    assert JobErrors(store).get(job.id) == (
        say("apps.not_found", domain="shop.example.com"),
        "the output",
    )
    from noust.web.api.jobs import _from_record

    shown = _from_record(record).model_dump()
    assert shown["error_coded"] == {
        "code": "apps.not_found",
        "params": {"domain": "shop.example.com"},
    }
    assert shown["error_output"] == "the output"


def test_a_row_from_before_the_code_was_kept_reads_back_without_one(store: NoustStore) -> None:
    store.save_job(
        JobRecord(
            id="old1",
            type="deploy",
            name="deploy",
            description="deploy",
            status="failed",
            error="Application not found: shop.example.com\n  Details: Run it.",
        )
    )
    record = store.get_job("old1")
    assert record is not None

    from noust.web.api.jobs import _from_record

    shown = _from_record(record).model_dump()
    assert shown["error_coded"] is None and shown["error_output"] is None
    assert shown["error"].startswith("Application not found")


def test_the_row_goes_with_the_job_and_a_store_the_older_version_reads_keeps_the_jobs_table_as_it_was(
    store: NoustStore,
) -> None:
    def work(job_context: JobContext | None = None) -> None:
        raise DeploymentError.from_text(say("apps.not_found", domain="shop.example.com"))

    job = _run(work)
    assert JobErrors(store).get(job.id)[0] is not None

    with store._transaction() as cursor:
        cursor.execute("DELETE FROM jobs WHERE id = ?", (job.id,))

    assert JobErrors(store).get(job.id) == (None, None)
    # The jobs table has the columns a JobRecord has and no more: the version this store may be taken
    # back to builds a record from every column of the row.
    with store._transaction() as cursor:
        columns = {row["name"] for row in cursor.execute("PRAGMA table_info(jobs)").fetchall()}
    assert columns == {
        "id",
        "type",
        "name",
        "description",
        "status",
        "progress",
        "total_steps",
        "domain",
        "error",
        "result_json",
        "created_at",
        "started_at",
        "finished_at",
        "log_path",
        "actor",
        "unit",
    }


def test_a_build_stopped_by_the_time_limit_says_so_with_its_code(store: NoustStore) -> None:
    manager = JobManager()
    job = Job(
        id="t1",
        type=JobType.UPDATE,
        name="update",
        description="",
        metadata={"domain": "shop.example.com"},
    )
    manager._over_time["t1"] = 15

    manager._time_limit_failure(job)

    assert job.error_coded == say(
        "delivery.time_limit_update", domain="shop.example.com", limit="15 minutes"
    )
    # The words every client always read are the same ones, from the same place as the deployment's history.
    assert job.error is not None and "maximum deployment time (15 minutes)" in job.error
    shown = job.to_dict()["error_coded"]
    assert shown is not None and shown["code"] == "delivery.time_limit_update"
