# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which modules still ask Docker by themselves, and the list that can only get shorter.

Noust had no client of Docker: modules wrote ``["docker", ...]`` argument lists by hand, each with its
own idea of the format, the timeout and which containers are whose, and four different fakes of Docker
in the tests. ``noust.core.docker`` is the one door now (the engine's doctor, the objects with their
owners, the actions judged as (owner, action, object)), and new code uses it. This holds the rest to
the rule of the other ratchets of the project (``tests/test_layering.py``, ``tests/test_architecture.py``):
the test fails when a module outside the gate starts asking Docker by itself, and when a module that
is listed stops, so the list can only be shortened.

What is counted is a *list* whose first element is the word ``"docker"``: an argument list. A tuple
starting with it is a declaration of a read-only probe (``READ_ONLY_PROBES``), which is not a command,
and a string that a person runs on another server (an importer's export command) is not one of this
machine's. Two things are not seen and are not claimed: an argument list built from a program found
with ``shutil.which("docker")``, and the commands Docker's own ``compose`` is given by the deployer
through ``self._compose(...)``, which is the deployer's own door to a stack.
"""

from __future__ import annotations

import ast
from pathlib import Path

import noust
from tests.support.ratchet_support import check_ratchet

ROOT = Path(noust.__file__).resolve().parent
WHERE = "tests/test_docker_ratchet.py"

#: The door itself.
THE_GATE = "core/docker/"

#: Modules that still write a ``["docker", ...]`` argument list. Taking one off means giving what it
#: asks to the gate (``noust.core.docker.reading`` for a look, ``DockerGate`` for an action) or to the
#: deployer's door to its stack (``DockerComposeDeployer._compose``). 27 before 4.3's gate; the
#: storage manager, the installation check, the installer of Docker and the application backup use it now.
STILL_ASK_DOCKER = {
    "deployers/compose_adopt.py",  # list_stack_containers: its own tab format, three fakes of it
    "deployers/compose_reclaim.py",
    "deployers/compose_relay.py",
    "deployers/docker_compose.py",
    "deployers/helpers/compose_retention.py",
    "deployers/helpers/container_image.py",
    "managers/database/base.py",
    "managers/database/exposure.py",
    "managers/database/instances.py",
    "managers/database/managed.py",
    "managers/docker_volumes.py",
    "managers/log_search.py",
    "managers/log_shipping.py",
    "managers/name_migration.py",
    "managers/registry_logins.py",
    "managers/server/security_firewall.py",
    "managers/site_topology.py",
    "managers/stack_drill.py",
    "managers/stack_networks.py",
    "managers/volume_backups.py",
    "monitor/plan.py",
    "monitor/watch_containers.py",
    "terminal/targets.py",
}


def docker_argument_lists(path: Path) -> list[int]:
    """
    Find the argument lists in a module that start with ``"docker"``.

    Args:
        path: A Python file.

    Returns:
        The line of each.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.List)
        and node.elts
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "docker"
    ]


def modules_that_ask_docker() -> set[str]:
    """
    Returns:
        The modules of the package, outside the gate, that write a Docker argument list.
    """
    found = set()
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith(THE_GATE):
            continue
        if docker_argument_lists(path):
            found.add(relative)
    return found


def test_no_module_asks_docker_by_itself_and_the_ones_that_still_do_are_listed() -> None:
    check_ratchet(
        modules_that_ask_docker(),
        STILL_ASK_DOCKER,
        "a module outside noust.core.docker writes an argument list for docker",
        WHERE,
    )


def test_the_gate_is_where_an_action_on_a_docker_object_is_run() -> None:
    """start, stop, restart and rm of an object are spelled in the gate, and nowhere else is a verb of one."""
    verbs = {"start", "stop", "restart", "rm", "kill", "pause", "unpause"}
    outside: dict[str, list[str]] = {}
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith(THE_GATE):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.List)
                and len(node.elts) >= 2
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "docker"
                and isinstance(node.elts[1], ast.Constant)
                and node.elts[1].value in verbs
            ):
                outside.setdefault(relative, []).append(str(node.elts[1].value))
    # Known, and not the gate's to judge: each is code that is the owner of what it acts on, an
    # application pausing its own containers for the length of a copy, the relay's twin of a service
    # and the scratch containers of a restore drill. What a future change to the gate has to look at.
    assert set(outside) <= {
        "deployers/compose_relay.py",
        "managers/stack_drill.py",
        "managers/volume_backups.py",
    }, outside
