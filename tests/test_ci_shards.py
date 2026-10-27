# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every machine that runs the suite in CI runs a slice, and the slices add up to all of it.

The suite is cut by ``--shard K/N`` (``tests/sharding.py``), one slice to a machine of the matrix.
Two ways that goes quietly wrong: a matrix with fewer slices than the count says (a fifth of the
suite that nobody runs, with a green run to show for it), and a job that runs the whole suite on a
machine that was only meant to run a slice (the runner is lost before it ends, which is why the
slices exist). The workflow files are read here, so neither can be committed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

#: ``${{ matrix.shard }}/4``
SHARD_ENV = re.compile(r"\$\{\{\s*matrix\.shard\s*\}\}/([0-9]+)")


def jobs_that_run_the_suite() -> list[tuple[str, str, dict[str, Any]]]:
    """The jobs of the workflows with a step that starts ``pytest`` on the whole ``tests`` tree."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in document.get("jobs", {}).items():
            for step in job.get("steps", []):
                command = str(step.get("run", ""))
                if re.search(r"^\s*pytest\b[^\n]*-n\b", command, re.MULTILINE):
                    found.append((path.name, name, job))
                    break
    return found


def test_the_jobs_that_run_the_suite_are_found() -> None:
    names = {(file, name) for file, name, _job in jobs_that_run_the_suite()}
    assert ("ci.yml", "test") in names
    assert ("release.yml", "verify") in names


@pytest.mark.parametrize(
    ("file", "name", "job"),
    [
        pytest.param(file, name, job, id=f"{file}:{name}")
        for file, name, job in jobs_that_run_the_suite()
    ],
)
def test_a_machine_runs_a_slice_and_the_matrix_has_every_slice(
    file: str, name: str, job: dict[str, Any]
) -> None:
    declared = str(job.get("env", {}).get("NOUST_TEST_SHARD", ""))
    for step in job["steps"]:
        declared = declared or str(step.get("env", {}).get("NOUST_TEST_SHARD", ""))
    found = SHARD_ENV.fullmatch(declared)
    assert found, (
        f"{file}: job {name} runs the whole suite on one machine; set "
        "NOUST_TEST_SHARD: ${{ matrix.shard }}/N so it runs one slice of N"
    )
    count = int(found.group(1))
    shards = job.get("strategy", {}).get("matrix", {}).get("shard")
    assert shards == list(range(1, count + 1)), (
        f"{file}: job {name} cuts the suite into {count} slices but its matrix runs {shards}: "
        f"the slices it leaves out are never tested"
    )
