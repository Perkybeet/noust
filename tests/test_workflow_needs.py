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
