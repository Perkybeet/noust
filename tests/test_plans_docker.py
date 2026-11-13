# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What applying a cleanup of Docker would run, in the common shape (item 183).

The cleanup already says what would go and what stays; this is the commands of removing what was chosen,
from the code that removes it, rehearsed. The machine is the simulated Docker of the cleanup's own tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest

from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import Registry
from noust.core.runner import is_read_only, set_runner
from noust.core.teams import Scope
from noust.deployers.plans.docker_cleanup import plan_docker_cleanup
from tests.support.docker_machine import DockerMachine

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def machine() -> Iterator[DockerMachine]:
    """A machine with two images nobody names and nobody uses, as the process's runner."""
    found = DockerMachine()
    found.image("1", "<none>", "<none>", "380MB")
    found.image("2", "<none>", "<none>", "260MB")
    found.image("3", "debian", "11", "120MB")
    set_runner(found)
    yield found
    set_runner(None)


def gate(runner: DockerMachine | None = None) -> DockerGate:
    """The gate of a person who sees everything, over a machine nobody registered anything on."""
    return DockerGate(
        runner,
        registry=lambda: Registry(apps=(), engines=()),
        scope=Scope.everything(),
        clock=lambda: NOW.timestamp(),
    )


def test_the_commands_of_removing_what_was_chosen_are_the_ones_applying_it_runs(
    machine: DockerMachine,
) -> None:
    shown = gate(machine).cleanup_plan()
    chosen = [item.id for item in shown.items if item.tick]
    assert len(chosen) == 2
    machine.calls.clear()

    plan = plan_docker_cleanup(shown.token, chosen, gate=gate)

    assert [call for call in machine.calls if not is_read_only(call)] == []
    removals = [command.argv for command in plan.commands if not command.probe]
    assert len(removals) == 2 and all(argv[:3] == ("docker", "image", "rm") for argv in removals)
    assert plan.effect.code == "plans.docker.cleanup" and plan.effect.params["count"] == 2
    assert "plans.unknown.docker_owner" in [item.code for item in plan.unknown]

    machine.calls.clear()
    gate(machine).apply_cleanup(shown.token, chosen)

    assert [call for call in machine.calls if not is_read_only(call)] == removals


def test_a_plan_that_is_not_the_one_that_was_shown_is_a_cleanup_that_would_be_refused(
    machine: DockerMachine,
) -> None:
    shown = gate(machine).cleanup_plan()
    chosen = [item.id for item in shown.items if item.tick]
    machine.image("4", "<none>", "<none>", "99MB")

    plan = plan_docker_cleanup(shown.token, chosen, gate=gate)

    assert plan.blocked is not None and plan.blocked.sentence.code.startswith(
        "plans.blocked.refused"
    )
    assert [command for command in plan.commands if not command.probe] == []
