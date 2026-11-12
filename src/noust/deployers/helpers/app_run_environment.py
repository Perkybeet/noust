# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
With what environment does something of an application run, from outside its deployment.

The application's own unit is the answer: it loads the application's ``.env`` and what Noust says
of the release, and sets inline what is Noust's to decide (the port, the mode, the ``PATH`` of the
runtime it is pinned to). A scheduled task of the application ran in its folder and nothing else
of that, so ``manage.py migrate`` failed for want of ``DATABASE_URL`` and ``python`` was the
system's (item 150). :func:`run_environment` gives a task the same answer, and puts the
application's own tools (the virtual environment, ``node_modules/.bin``) first on its ``PATH``,
which a unit does not need because its start command names its program by path.

It reads the unit's text instead of asking a deployer, because outside a deployment there is no
deployer to ask: a deployer knows its tool directories from a tree it is building.
``tests/test_cron_app_environment.py`` holds :func:`tool_directories` to what they say.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from noust.core.appdir.layout import code_path_for, env_file_for
from noust.core.runner import SANDBOX_PATH
from noust.core.store import App
from noust.core.variables.predefined import RELEASE_ENV_NAME
from noust.deployers.unit_environment import kept_inline

__all__ = ["RunEnvironment", "run_environment", "tool_directories"]


@dataclass(frozen=True)
class RunEnvironment:
    """
    What a command of an application runs with.

    Attributes:
        environment_files: The files its unit loads, in the order it loads them (a later one wins):
            the ``.env``, then what Noust says of the release. Each is optional, as in the unit.
        variables: What the unit sets inline, and the ``PATH`` with the application's own tools in
            front of it.
    """

    environment_files: tuple[Path, ...]
    variables: dict[str, str]

    @property
    def path(self) -> str | None:
        """The ``PATH`` a command is found on: the tools first; None when nothing changes it."""
        return self.variables.get("PATH")


def tool_directories(code: Path) -> list[Path]:
    """
    List the directories of the programs an application installed in its own tree.

    Args:
        code: The running code (``current`` on releases, the application directory in place).

    Returns:
        The virtual environment's ``bin`` and ``node_modules/.bin``, when they are there. These are
        the directories the Python and the Node deployers name as their tools.
    """
    found = [code / "venv" / "bin", code / "node_modules" / ".bin"]
    return [directory for directory in found if directory.is_dir()]


def run_environment(app: App, unit_text: str | None) -> RunEnvironment:
    """
    Say what a command of an application runs with.

    Args:
        app: The application's store row.
        unit_text: The text of its own unit; None when it has none.

    Returns:
        The files and variables. Only what a unit keeps inline is taken from it
        (:data:`~noust.deployers.unit_environment.INLINE`): a task's unit is world-readable like
        the application's, and a secret that a unit from before 2.0 still held inline is not copied
        into another one.
    """
    code = code_path_for(app)
    variables = kept_inline(unit_text) if unit_text else {}
    tools = [str(directory) for directory in tool_directories(code)]
    if tools:
        # Before the pinned runtime: a virtual environment's python is the one it was made with.
        variables["PATH"] = ":".join([*tools, variables.get("PATH") or SANDBOX_PATH])
    return RunEnvironment(
        environment_files=(env_file_for(app), code / RELEASE_ENV_NAME),
        variables=variables,
    )
