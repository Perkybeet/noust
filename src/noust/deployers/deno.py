# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deno applications: cache the dependencies in the release, run the program as the unit (A-40).

A repository with a ``deno.json`` used to be only ever a container. Deno needs no build step of
its own and keeps no ``node_modules`` unless asked, so the whole of a native deployment is small:
``deno install`` fetches the dependencies the configuration and the entry point name, a ``build``
task runs when there is one (Fresh and the frameworks on top of Vite have it), and the unit runs
the ``start`` task or the entry point.

Decisions worth knowing:

- **The module cache lives in the release** (``.deno``, ``DENO_DIR``). The build account's own
  cache is hidden from the service, and a service account with no home has nowhere else to write,
  so a cache anywhere else would mean the first start downloads everything again, with the
  network the unit may not have. A rollback is then the release's own dependencies.
- **A library is not deployed.** A package published to JSR (a ``name`` and ``exports`` and no
  ``start`` task) has no program to run; the verdict says so instead of "no deployer".
- **An entry point runs with the permissions a server needs** (network, reading its files,
  environment variables) and no more; a program that writes, runs subprocesses or reads the
  system asks for them in a ``start`` task of its own or in the build block. A permission it
  lacks is Deno's own error in the journal, not a prompt: there is no terminal.
- **A Dockerfile means the container**, for the reason :mod:`noust.deployers.compiled` gives.

The Deno is the server's own (``deno`` on the system ``PATH``, not under ``/root``): Deno 2 or
later, because ``deno install`` with no arguments does not exist before.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from noust.core.exceptions import DeploymentError
from noust.core.fs import FileSystem
from noust.core.runner import CommandResult, CommandRunner
from noust.deployers.base import COMMAND_TIMEOUT, BaseDeployer
from noust.deployers.compiled import has_container_file
from noust.deployers.helpers import jsonc
from noust.deployers.helpers.sandbox import BuildPhase
from noust.deployers.helpers.unit_launch import systemd_command
from noust.deployers.registry import DeployerRegistry

#: The configuration files Deno reads, in the order it prefers them.
CONFIG_FILES: tuple[str, ...] = ("deno.json", "deno.jsonc")

#: Where the module cache is, relative to the project: the release's own.
CACHE_DIR = ".deno"

#: Entry points tried when the configuration names no ``start`` task, in order.
ENTRY_POINTS: tuple[str, ...] = (
    "main.ts",
    "main.tsx",
    "main.js",
    "main.mjs",
    "server.ts",
    "server.js",
    "index.ts",
    "index.js",
    "src/main.ts",
    "src/main.js",
)

#: Tasks that start a server, in the order they are preferred.
START_TASKS: tuple[str, ...] = ("start", "serve")

#: What an entry point is allowed to do unless the project says otherwise.
DEFAULT_PERMISSIONS: tuple[str, ...] = ("--allow-net", "--allow-read", "--allow-env")

#: What Deno is told for a build and a run, whatever ``.env`` says.
QUIET: dict[str, str] = {"DENO_NO_UPDATE_CHECK": "1"}

_DENO_VERSION = re.compile(r"^deno\s+(\d+)\.(\d+)", re.MULTILINE)
_SCRIPT_FILE = re.compile(r"^[\w./@-]+\.(?:[cm]?[jt]sx?)$")


def read_config(project: Path) -> dict[str, Any] | None:
    """
    Read a project's Deno configuration.

    Args:
        project: The project.

    Returns:
        The document of ``deno.json`` (or ``deno.jsonc``); None when there is none, it is a
        link, or it is not a JSON object. A repository is untrusted: the file is parsed, never
        evaluated.
    """
    for name in CONFIG_FILES:
        path = project / name
        if path.is_symlink() or not path.is_file():
            continue
        try:
            document = jsonc.loads(path.read_text(encoding="utf-8", errors="strict"))
        except (OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None
    return None


def tasks_of(config: Mapping[str, Any]) -> dict[str, str]:
    """
    Read the tasks of a configuration.

    Args:
        config: The document.

    Returns:
        Task name to command; a task written as an object (``{"command": ...}``) is read by its
        command, and anything that is not text is left out.
    """
    tasks = config.get("tasks")
    if not isinstance(tasks, dict):
        return {}
    found: dict[str, str] = {}
    for name, value in tasks.items():
        if isinstance(value, dict):
            value = value.get("command")
        if isinstance(name, str) and isinstance(value, str):
            found[name] = value
    return found


def start_task(config: Mapping[str, Any]) -> str | None:
    """
    Name the task that starts the server.

    Args:
        config: The document.

    Returns:
        ``start`` or ``serve``, whichever the configuration has; None for neither.
    """
    tasks = tasks_of(config)
    return next((name for name in START_TASKS if name in tasks), None)


def entry_point(project: Path) -> str | None:
    """
    Find the file a program without a ``start`` task is run from.

    Args:
        project: The project.

    Returns:
        The first of :data:`ENTRY_POINTS` that is a file of the project (not a link), else None.
    """
    for name in ENTRY_POINTS:
        path = project / name
        if path.is_file() and not path.is_symlink():
            return name
    return None


def is_library(config: Mapping[str, Any]) -> bool:
    """
    Tell whether a configuration describes a package published for others to import.

    Args:
        config: The document.

    Returns:
        True for a ``name`` with ``exports`` and no ``start`` task: JSR's shape of a library.
    """
    return bool(config.get("name")) and "exports" in config and start_task(config) is None


def task_entry_points(command: str) -> list[str]:
    """
    Read the files a task runs, so their dependencies are fetched before it does.

    Args:
        command: The task's command line.

    Returns:
        The script files named in a ``deno run`` command, in order; nothing for a task that
        runs something else. The words are read, never executed.
    """
    try:
        words = shlex.split(command)
    except ValueError:
        return []
    if words[:2] != ["deno", "run"]:
        return []
    return [word for word in words[2:] if _SCRIPT_FILE.fullmatch(word) and ".." not in word]


def installed_deno(output: str) -> tuple[int, int] | None:
    """
    Read the release of the Deno this server has.

    Args:
        output: What ``deno --version`` printed.

    Returns:
        ``(major, minor)``; None when the output is not recognisable.
    """
    found = _DENO_VERSION.search(output)
    return (int(found.group(1)), int(found.group(2))) if found else None


class DenoDeployer(BaseDeployer):
    """
    Deployer for Deno programs: the configuration's ``start`` task, or the entry point.

    Dependencies are cached in the release (``.deno``), a ``build`` task runs when there is
    one, and the unit starts the program on the loopback address and the application's port.
    """

    APP_TYPE = "deno"
    DISPLAY_NAME = "Deno"
    DETECTION_FILES: ClassVar[list[str]] = list(CONFIG_FILES)
    #: Read by :meth:`detect`, so a remote repository's inspection fetches them too.
    FRAMEWORK_CONFIG_FILES: ClassVar[list[str]] = list(ENTRY_POINTS)
    DEFAULT_PORT = 8000

    #: With Go, Rust, Ruby and the other native stacks: below Python, above Node, because a
    #: ``deno.json`` is a decision a ``package.json`` next to it does not overrule.
    DETECTION_PRIORITY = 45

    SYSTEM_DEPS: ClassVar[list[str]] = ["deno"]

    #: ``runtime`` is for the languages Noust installs per application; Deno is the server's.
    BUILD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"base_directory", "install", "build", "start"}
    )

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for installs and builds. Defaults to the
                process-wide runner.
            fs: Filesystem every change goes through. Defaults to the process-wide one.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)

    # Detection -------------------------------------------------------------------------

    def detect(self, path: Path) -> bool:
        """
        Detect a Deno program.

        Args:
            path: Path to check.

        Returns:
            True for a ``deno.json`` or ``deno.jsonc`` with a ``start`` (or ``serve``) task or
            an entry point, and no Dockerfile: a published library has nothing to run, and a
            Dockerfile is how the repository says it runs.
        """
        if has_container_file(path):
            return False
        config = read_config(path)
        if config is None or is_library(config):
            return False
        return start_task(config) is not None or entry_point(path) is not None

    def _config(self) -> dict[str, Any]:
        """
        Returns:
            The project's configuration; empty when it has none or cannot be read.
        """
        return read_config(self.build_path) or {}

    # Commands --------------------------------------------------------------------------

    def _entry_files(self) -> list[str]:
        """
        Returns:
            The files whose imports ``deno install`` should fetch: those the start task runs,
            else the entry point.
        """
        config = self._config()
        task = start_task(config)
        if task is not None:
            return task_entry_points(tasks_of(config)[task])
        entry = entry_point(self.build_path)
        return [entry] if entry else []

    def get_install_command(self) -> list[str]:
        """
        Get the command that fetches the dependencies into the release's cache.

        Returns:
            ``deno install``, with the entry files named so the imports written in the program
            (a ``jsr:`` or ``https:`` specifier no configuration lists) are fetched too.
        """
        entries = self._entry_files()
        return ["deno", "install", *(["--entrypoint", *entries] if entries else [])]

    def get_build_command(self) -> list[str]:
        """
        Get the command that builds the program.

        Returns:
            ``deno task build`` when the configuration has the task; nothing otherwise.
        """
        return ["deno", "task", "build"] if "build" in tasks_of(self._config()) else []

    def get_start_command(self) -> str:
        """
        Get the command the unit runs.

        Returns:
            The ``start`` (or ``serve``) task, else the entry point with the permissions a
            server needs. The program is Deno by its path on this server.
        """
        config = self._config()
        task = start_task(config)
        if task is not None:
            argv = ["deno", "task", task]
        else:
            entry = entry_point(self.build_path) or ENTRY_POINTS[0]
            argv = ["deno", "run", *DEFAULT_PERMISSIONS, entry]
        return systemd_command(argv)

    def get_health_check(self) -> str:
        """
        Get the health check endpoint.

        Returns:
            ``/``: any answer but a server error means the program is up.
        """
        return "/"

    def _unit_environment(self) -> dict[str, str]:
        """
        Say what the unit sets: the port, the loopback address and where the cache is.

        Returns:
            ``PORT`` and ``HOST`` (a program that reads ``HOST`` stays off the network), the
            release's ``DENO_DIR`` through ``current`` and no update check.
        """
        return {
            "PORT": str(self.port),
            "HOST": "127.0.0.1",
            "DENO_DIR": str(self._at_runtime(self.build_path / CACHE_DIR)),
            **QUIET,
        }

    def _run(
        self,
        command: Sequence[str],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = COMMAND_TIMEOUT,
        *,
        stream: bool = False,
        phase: BuildPhase | None = None,
        tools: bool = False,
    ) -> CommandResult:
        """
        Run a command with Deno's cache in the release.

        Args:
            command: Program and arguments.
            cwd: Working directory.
            env: Extra environment, which wins over what this adds.
            timeout: Deadline in seconds.
            stream: Print output while the command runs.
            phase: What the command does.
            tools: Put the project's tool directories on ``PATH``.

        Returns:
            The command outcome.
        """
        cache = {"DENO_DIR": str(self.build_path / CACHE_DIR), **QUIET}
        return super()._run(
            command, cwd, {**cache, **(env or {})}, timeout, stream=stream, phase=phase, tools=tools
        )

    # Steps -----------------------------------------------------------------------------

    def pre_install(self) -> bool:
        """
        Check that this server's Deno can install what a Deno 2 project asks for.

        Returns:
            True.

        Raises:
            DeploymentError: The Deno is older than 2.
        """
        probe = self._run(["deno", "--version"], timeout=60)
        have = installed_deno(probe.stdout or "")
        if have is not None and have[0] < 2:
            raise DeploymentError(
                f"This server has Deno {have[0]}.{have[1]} and Noust needs Deno 2 or later",
                details="Upgrade it system-wide (the build account cannot use one under /root): "
                "deno upgrade, or npm install -g deno. See "
                "https://docs.deno.com/runtime/getting_started/installation/",
            )
        if start_task(self._config()) is None and entry_point(self.build_path) is None:
            raise DeploymentError(
                "This Deno project has no program to run",
                details="Add a start task to deno.json, or a main.ts at the root, or give the "
                "start command in the build block: build.start: [deno, run, -A, app.ts].",
            )
        return True

    # Reporting -------------------------------------------------------------------------

    @classmethod
    def install_suggestion(cls, runner: CommandRunner) -> str | None:
        """
        Say how to get Deno, for ``noust inspect``.

        Args:
            runner: Unused: no distribution ships Deno.

        Returns:
            The next step, which is the same on every distribution.
        """
        return (
            "Install Deno 2 system-wide, where the build account can run it (npm install -g "
            "deno, or the installer of https://docs.deno.com/runtime/getting_started/"
            "installation/ with DENO_INSTALL=/usr/local). Then deploy it."
        )

    def _missing_dependencies_issue(self, missing: list[str]) -> str:
        """
        Say which programs are missing and how to install them.

        Args:
            missing: The programs of :attr:`SYSTEM_DEPS` this server lacks.

        Returns:
            The pre-flight line, with where Deno comes from.
        """
        line = super()._missing_dependencies_issue(missing)
        return f"{line}. Install Deno 2 system-wide: npm install -g deno (or see deno.com)"

    def get_template_context(self) -> dict[str, Any]:
        """
        Get the template context.

        Returns:
            The generic one, marked as a Deno application.
        """
        context = super().get_template_context()
        context["is_deno"] = True
        return context


DeployerRegistry.register(DenoDeployer)
