# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a background job says it is doing is a sentence with a code, for the console to translate.

A job reported its step as text (``Creating backup``), and the app's header drew it in English in
a Spanish console for every kind of job the console did not know by name. A step that is one of
the server's own sentences now carries its code and values beside the text (``JobContext.update``
takes either), the log line it leaves and the job's ``current_step_coded`` say so, and a secret
of the job is taken out of the sentence's values as it is out of its text. The jobs' own rules are
elsewhere; here, that every step a job says in the modules that were given codes is a sentence of
the catalog and never a text of its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from noust.core.redact import Scrubber
from noust.core.server_text import CATALOG, say
from noust.web import jobs as jobs_module
from noust.web.job_log import JobLogEntry, scrub_sentence
from noust.web.jobs import Job, JobContext, JobType

SRC = Path(jobs_module.__file__).resolve().parents[1]


def _context() -> tuple[Job, JobContext]:
    job = Job(id="j1", type=JobType.BACKUP, name="b", description="")
    return job, JobContext(job, lambda _job: None)


def test_a_step_that_is_a_sentence_carries_its_code_beside_its_text() -> None:
    job, context = _context()
    step = say("jobs.step.backup.creating")

    context.update(step, 20)

    assert job.current_step == "Creating backup"
    assert job.current_step_coded == step
    assert job.progress == 20
    last = job.logs[-1]
    assert last.message == "Creating backup" and last.coded == step and last.step == 20
    shown = job.to_dict()
    assert shown["current_step_coded"] == {"code": "jobs.step.backup.creating", "params": {}}
    assert shown["logs"][-1]["coded"] == {"code": "jobs.step.backup.creating", "params": {}}


def test_a_step_that_is_only_text_has_no_code() -> None:
    job, context = _context()

    context.update("Pulling the images", 30)
    context.log("npm warn deprecated left-pad", "warning")

    assert job.current_step == "Pulling the images" and job.current_step_coded is None
    assert [entry.coded for entry in job.logs] == [None, None]
    assert job.to_dict()["current_step_coded"] is None


def test_a_line_may_be_a_sentence_too_and_keeps_its_level() -> None:
    job, context = _context()

    context.log(say("jobs.step.renewal.none_due"), "success")

    entry = job.logs[-1]
    assert entry.level == "success" and entry.coded is not None
    assert entry.message == "No certificate was due for renewal"
    # A line does not move the step the job is on.
    assert job.current_step == "" and job.current_step_coded is None


def test_a_later_text_step_takes_the_code_of_the_one_before_away() -> None:
    job, context = _context()

    context.update(say("jobs.step.backup.creating"), 20)
    context.update("Something the catalog does not know", 40)

    assert job.current_step_coded is None


def test_a_secret_of_the_job_is_taken_out_of_the_text_and_out_of_the_values() -> None:
    job, context = _context()
    job.scrubber = Scrubber()
    job.scrubber.add(["s3cr3t-value"])

    context.update(say("jobs.step.backup.uploading", destination="bucket-s3cr3t-value"), 20)

    assert "s3cr3t-value" not in job.current_step
    assert job.current_step_coded is not None
    assert "s3cr3t-value" not in str(job.current_step_coded.params)
    assert job.current_step == job.current_step_coded.text


def test_scrubbing_a_sentence_keeps_its_numbers_and_flags() -> None:
    sentence = say("jobs.step.rollback.to_deployment", deployment=42)

    assert scrub_sentence(sentence, Scrubber()) == sentence


def test_the_log_entry_still_builds_the_way_it_always_did() -> None:
    from datetime import datetime

    entry = JobLogEntry(timestamp=datetime(2026, 1, 1), level="info", message="x", step=1)

    assert entry.coded is None and entry.to_dict()["coded"] is None


def test_the_api_says_the_step_and_every_line_with_their_codes() -> None:
    from noust.web.api.jobs import _to_response

    job, context = _context()
    context.update(say("jobs.step.backup.creating"), 20)
    context.log("pg_dump: dumping contents of table users")

    shown = _to_response(job).model_dump()

    assert shown["current_step"] == "Creating backup"
    assert shown["current_step_coded"] == {"code": "jobs.step.backup.creating", "params": {}}
    assert [entry["coded"] for entry in shown["logs"]] == [
        {"code": "jobs.step.backup.creating", "params": {}},
        None,
    ]


# -- nothing a job says in the modules that were given codes is text of its own ----------------

#: What was given codes: the standard jobs and the app-scoped ones the app's header follows.
CODED_MODULES = (
    "web/jobs.py",
    "web/api/sandbox.py",
    "web/api/app_packages.py",
    "web/api/hardening.py",
    "web/api/tuning.py",
    "web/api/identity.py",
    "web/api/replicas.py",
    "web/api/zero_downtime.py",
    "web/api/app_staged.py",
    "web/api/app_release_bundles.py",
    "web/api/app_disk.py",
    "web/api/app_promote.py",
    "web/api/app_export.py",
    "web/api/domains.py",
)


def _steps(relative: str) -> list[ast.Call]:
    tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "update"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"context", "job_context"}
    ]


@pytest.mark.parametrize("relative", CODED_MODULES)
def test_no_step_of_a_coded_module_is_a_text_of_its_own(relative: str) -> None:
    for call in _steps(relative):
        step = call.args[0]
        # A phase reported by a deployer (``on_phase``) arrives as a name, never as a literal here.
        assert not isinstance(step, ast.Constant | ast.JoinedStr), (
            f"{relative}:{call.lineno} says a step in words of its own: give it a sentence "
            "in noust.core.server_text"
        )


def test_every_step_sentence_is_in_the_catalog() -> None:
    asked: set[str] = set()
    for relative in CODED_MODULES:
        for call in _steps(relative):
            step = call.args[0]
            for node in ast.walk(step):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "say"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    asked.add(str(node.args[0].value))

    assert asked
    assert asked <= set(CATALOG), sorted(asked - set(CATALOG))
