# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every job a workflow waits for exists in that workflow.

GitHub refuses a workflow whose ``needs`` names a job it does not have, and refuses it whole: the
release workflow once waited for ``upgrade-from-2x`` after that job was retired with the 2.x line,
and a tag would have published nothing. YAML that parses is not enough, so the jobs are read here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_every_job_a_workflow_waits_for_is_one_of_its_jobs(path: Path) -> None:
    jobs = yaml.safe_load(path.read_text(encoding="utf-8")).get("jobs", {})
    missing = {
        name: wanted for name, job in jobs.items() for wanted in _needs(job) if wanted not in jobs
    }
    assert not missing, f"{path.name}: these jobs wait for one that does not exist: {missing}"


def test_the_workflows_are_found() -> None:
    assert any(path.name == "release.yml" for path in WORKFLOWS)


def _release_jobs() -> dict[str, dict]:
    path = next(path for path in WORKFLOWS if path.name == "release.yml")
    jobs: dict[str, dict] = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _waited_for(jobs: dict[str, dict], name: str) -> set[str]:
    """Every job ``name`` waits for, directly or through another."""
    found: set[str] = set()
    pending = list(_needs(jobs[name]))
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(_needs(jobs[current]))
    return found


def test_the_release_is_announced_only_after_every_job_that_publishes_or_signs_it() -> None:
    """
    ``announce`` makes the release the latest one, which is what every server's update check reads.

    A job nobody waits for can fail with the release already announced: ``attest-assets`` signs the
    checksums and attests every file, and it was the one job that was. Whatever runs after
    ``release`` is either waited for by another job or is ``announce`` itself.
    """
    jobs = _release_jobs()
    waited_for = {wanted for job in jobs.values() for wanted in _needs(job)}
    loose = sorted(name for name in jobs if name not in waited_for and name != "announce")
    assert not loose, f"release.yml: announce does not wait for {loose}"
    assert _waited_for(jobs, "announce") == set(jobs) - {"announce"}


def test_what_the_announcement_waits_for_is_never_skipped() -> None:
    """
    A skipped job is not a successful one: with an ``if`` on a job it needs, ``announce`` would be
    skipped too on the run where everything passed, which is how the 4.0.0 release had to be
    announced by hand.
    """
    jobs = _release_jobs()
    waits = _waited_for(jobs, "announce")
    conditional = sorted(
        name
        for name in waits | {"announce"}
        if "if" in jobs[name]
        or jobs[name].get("continue-on-error")
        or any("if" in step for step in jobs[name].get("steps", []))
    )
    assert not conditional, f"release.yml: these jobs can be skipped or ignored: {conditional}"


def test_the_announcement_takes_the_tag_from_the_environment() -> None:
    """A tag is text somebody typed: it reaches the shell as a variable, never as a substitution."""
    for step in _release_jobs()["announce"]["steps"]:
        assert "${{" not in step.get("run", ""), step
