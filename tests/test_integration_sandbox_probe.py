# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The harness scenario of the runner's sandbox does not depend on the scenarios that ran before it.

``build_sandbox_says_how_a_command_ended`` (``tests/integration/run.py``) runs commands as ``noust-build``
inside a transient unit. That account does not exist on a new server: the first deployment of a sandboxed
build makes it, so the scenario passed after another one had deployed and failed as the first of a run.
The probe it sends into the container now makes the account the way a build does
(:func:`noust.deployers.helpers.sandbox.ensure_build_account`), before it uses it. The harness needs Docker
and is not part of pytest, so this reads the probe it would send.
"""

from __future__ import annotations

import ast
from pathlib import Path

RUN = Path(__file__).resolve().parent / "integration" / "run.py"


def _probe() -> str:
    tree = ast.parse(RUN.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", "") == "SANDBOX_RUNNER_PROBE" for target in node.targets
        ):
            assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            return node.value.value
    raise AssertionError("tests/integration/run.py has no SANDBOX_RUNNER_PROBE")


def _calls(source: str) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            function = node.func
            name = (
                function.attr
                if isinstance(function, ast.Attribute)
                else getattr(function, "id", "")
            )
            found.append((node.lineno, name))
    return sorted(found)


def test_the_probe_is_python_the_container_can_run() -> None:
    ast.parse(_probe())


def test_the_account_is_made_before_the_first_command_runs_as_it() -> None:
    calls = _calls(_probe())
    made = [line for line, name in calls if name == "ensure_build_account"]
    ran = [line for line, name in calls if name in {"run", "stream"}]

    assert made, "the probe never makes the account it runs as"
    assert ran and min(made) < min(ran)


def test_the_account_is_made_by_the_function_a_build_makes_it_with() -> None:
    from noust.deployers.helpers import sandbox

    assert callable(sandbox.ensure_build_account)
    assert "from noust.deployers.helpers.sandbox import" in _probe()
