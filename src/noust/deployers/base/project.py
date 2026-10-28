# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the project asks for: its processes, its runtime and the command that starts it.
"""

from __future__ import annotations

import os
from abc import abstractmethod
from pathlib import Path

from noust.core.appdir.releases import CURRENT_LINK
from noust.core.fs import DryRunFileSystem
from noust.core.runner import SANDBOX_PATH
from noust.core.versions import RuntimeRequest
from noust.deployers.base.state import DeployerState
from noust.deployers.helpers.build_config import project_dir, set_operator_build
from noust.deployers.helpers.hooks import COMPOSE_TYPE, Hook, hooks_unsupported_reason
from noust.deployers.helpers.processes import RELEASE, ProcessSet
from noust.deployers.helpers.release_build import StagedRelease
from noust.deployers.helpers.runtime_request import detect_request
from noust.deployers.helpers.unit_launch import systemd_command
from noust.managers.runtimes import InstalledRuntime, RuntimeChoice, RuntimeManager


class ProjectMixin(DeployerState):
    """
    What the project asks for: its processes, its runtime and the command that starts it.
    """

    def processes(self) -> ProcessSet:
        """
        Name the process types the project declares.

        Returns:
            What its ``noust.yaml`` or ``Procfile`` declares; none for a tree not on disk yet.
        """
        self.build_config()
        return self._process_set if self._process_set is not None else ProcessSet()

    def _release_hook(self) -> Hook | None:
        """
        Build the ``pre_deploy`` hook the project's ``release`` process stands for.

        Returns:
            The hook; None when the project has no ``release`` process, its ``Procfile`` is not
            one this application honours, or this type has no hooks.
        """
        found = self.processes()
        release = found.named(RELEASE)
        if release is None or self.APP_TYPE == COMPOSE_TYPE:
            return None
        if found.source == "Procfile" and not self._procfile_applies():
            return None
        if hooks_unsupported_reason(self.APP_TYPE, serves=bool(self.start_command())) is not None:
            return None
        self.logger.substep(
            f"Running the release process of {found.source} before the new version serves: "
            f"{release.command}. Declare it under hooks.pre_deploy with migrates: true if it "
            "changes the database schema."
        )
        return Hook(run=release.argv)

    def _persist_initial_build(self) -> None:
        """
        Store the build block the application was created with as the operator's.

        Written once the application's row exists, so every later update builds
        with it; audited like any change of the block.
        """
        if self._initial_build is None or isinstance(self.fs, DryRunFileSystem):
            return
        set_operator_build(
            self.domain,
            self._initial_build.to_document(),
            actor=f"create ({self.trigger})",
            store=self.store,
        )

    def _prime_build(self) -> None:
        """
        Read the build block of the code that was just put in the tree.

        Forgets what was read before the fetch, checks that the base
        directory is a directory of the tree and not a link, and says what
        the block changes, so the log of a deployment shows it.

        Raises:
            ValidationError: The block is not valid, or its base directory is
                a link or missing.
        """
        self._build_block = None
        self._build_block_tree = None
        self._runtime_state = None
        self._runtime_tree = None
        self._process_set = None
        block = self.build_config()
        if block.base_directory:
            project_dir(self.tree_root, block.base_directory)
        if block.declared:
            where = "the operator's settings" if block.source == "operator" else "noust.yaml"
            self.logger.substep(f"Build block from {where}: {', '.join(block.declared_keys)}")

    def _reconcile_registration(self) -> None:
        """
        Correct what the application's row says about serving, once the code that decides it is there.

        The row is written before anything is fetched, from what the type can
        say without its source: a framework that is a server or static files
        depending on its configuration is registered as the server it most
        likely is, and learns here which it is (and so whether it has a port).
        A type whose answer never depends on its source finds nothing to correct.
        """
        record = self._app_record
        if record is None or isinstance(self.fs, DryRunFileSystem):
            return
        static = not bool(self.start_command())
        if bool(record.is_static) == static:
            return
        record.is_static = static
        record.port = None if static else self.port
        self.store.update_app(record)

    @property
    def runtimes(self) -> RuntimeManager:
        """The manager of the language runtimes applications are pinned to."""
        if self._runtime_manager is None:
            manager = RuntimeManager(verbose=self.verbose, runner=self._runner, fs=self._fs)
            # The deployment's own log, so "Installing Node 22.11.0" is in it.
            manager.logger = self.logger
            self._runtime_manager = manager
        return self._runtime_manager

    def runtime_choice(self) -> RuntimeChoice | None:
        """
        Decide which runtime this deployment builds and runs on, once per tree.

        What the build block pins (``runtime: node@22``) is an order; what the
        project's version files say (``.nvmrc``, ``engines.node``,
        ``.python-version``, ``composer.json``) is met by the server's own
        runtime when that satisfies it. See
        :meth:`noust.managers.runtimes.RuntimeManager.choose`.

        Returns:
            The choice; None for a type that runs on no pinned runtime, or a
            project that asks for none.

        Raises:
            DeploymentError: A runtime the build block pins cannot be had.
        """
        tree = self.tree_root
        if self._runtime_tree == tree:
            return self._runtime_state
        if not tree.is_dir():
            return None
        build = self.build_config()
        request: RuntimeRequest | None = build.runtime
        if request is None and self.PRIMARY_RUNTIME is not None:
            request, notes = detect_request(self.PRIMARY_RUNTIME, self.build_path, tree)
            for note in notes:
                self.logger.warning(note)
        choice: RuntimeChoice | None = None
        if request is not None:
            choice = self.runtimes.choose(request)
            self.logger.substep(choice.reason)
            if choice.warning:
                self.logger.warning(choice.warning)
        self._runtime_state = choice
        self._runtime_tree = tree
        return choice

    def _pinned_runtime(self) -> InstalledRuntime | None:
        """
        Return the runtime this deployment puts first on ``PATH``, if any.

        Returns:
            None when it runs on the server's own, as every deployment did
            before 4.0.
        """
        choice = self.runtime_choice()
        runtime = choice.runtime if choice is not None else None
        if runtime is None or not (runtime.managed or runtime.language == "php"):
            return None
        return runtime

    def _tool_dirs(self) -> list[Path]:
        """
        List the directories of the project's own tools, which a custom command may name.

        The Node deployers return ``node_modules/.bin`` of the project and of
        the workspace root above it, the Python one the virtual environment's
        ``bin``: where ``next``, ``tsc`` or ``gunicorn`` are once installed.

        Returns:
            Directories in the build tree; none for a type without any.
        """
        return []

    def _runtime_environment(self, *, sandboxed: bool, tools: bool = False) -> dict[str, str]:
        """
        Compose the ``PATH`` a command of this deployment runs with.

        Args:
            sandboxed: The command runs in the sandbox, whose ``PATH`` is the
                system's fixed one, not this process's.
            tools: Put the project's own tool directories after the runtime
                (the build's custom commands, which have no ``npm run`` to do
                it for them).

        Returns:
            ``{"PATH": ...}``, or nothing when neither a runtime nor a tool
            directory changes it.
        """
        runtime = self._pinned_runtime()
        front = [str(runtime.bin_dir)] if runtime is not None else []
        if tools:
            front += [str(path) for path in self._tool_dirs() if path.is_dir()]
        if not front:
            return {}
        rest = SANDBOX_PATH if sandboxed else os.environ.get("PATH") or SANDBOX_PATH
        return {"PATH": ":".join([*front, rest])}

    def _pinned_program(self, program: str) -> str:
        """
        Name a program in the runtime this deployment is pinned to, when it has it.

        A sandboxed command is found through the system's ``PATH`` before its
        own is applied, and would be the distribution's ``npm``: the pinned
        runtime's is named by its absolute path instead.

        Args:
            program: The first word of a command.

        Returns:
            The runtime's own program as an absolute path; the word unchanged
            when it is a path already or the runtime has none by that name.
        """
        runtime = self._pinned_runtime()
        if runtime is None or "/" in program:
            return program
        found = runtime.program(program)
        return str(found) if found is not None else program

    def _runtime_unit_environment(self) -> dict[str, str]:
        """
        Say what a unit sets for the runtime it runs on.

        Returns:
            ``PATH`` with the pinned runtime first, since a start command such
            as ``npm run start`` runs ``node`` through it; nothing without one.
        """
        return self._runtime_environment(sandboxed=True)

    def _at_runtime(self, path: Path) -> Path:
        """
        Translate a path inside the build tree to the path the service will see.

        Args:
            path: A path at or below :attr:`build_path`.

        Returns:
            The same path below :attr:`runtime_path`. In place the two trees
            are one, and the path comes back untouched.
        """
        if self.runtime_path == self.build_path:
            return path
        try:
            return self.runtime_path / path.relative_to(self.build_path)
        except ValueError:
            return path

    def adopt_release(self, staged: StagedRelease) -> None:
        """
        Build a release someone else already staged, instead of fetching one.

        :class:`~noust.deployers.auto.AutoDeployer` has to fetch the source to
        know which deployer builds it; fetching again would clone twice and
        leave an empty release behind.

        Args:
            staged: The release, with its source in place.
        """
        self._staged = staged
        self._releases = staged.manager
        self.source_already_fetched = True

    @abstractmethod
    def get_start_command(self) -> str:
        """
        Get the command to start the application.

        Returns:
            Start command string.
        """
        pass

    def start_command(self) -> str:
        """
        Name the command the unit runs.

        Returns:
            The build block's ``start`` when it sets one, as the text of an
            ``ExecStart=`` whose program is an absolute path; the type's
            :meth:`get_start_command` otherwise (an empty string for a site
            the web server serves).
        """
        custom = self.build_config().start
        if not custom:
            return self.get_start_command()
        program = self._resolve_program(custom[0])
        return systemd_command([*self._interpret(program), *custom[1:]])

    def _interpret(self, path: str) -> list[str]:
        """
        Say how a program of a start command is launched.

        Args:
            path: The program, absolute.

        Returns:
            The words that start it; the program itself unless the type has to
            run it through an interpreter (see the Python deployer).
        """
        return [path]

    def _resolve_program(self, program: str) -> str:
        """
        Make the program of a start command an absolute path, as a unit needs.

        A path with a slash (``./bin/server``) is inside the project, as the
        application will see it through ``current``. A name without one is
        looked for in the project's own tools (``venv/bin``,
        ``node_modules/.bin``), then in the runtime the application is pinned
        to, then in the system's directories: never nvm's.

        Args:
            program: The first word of the command.

        Returns:
            The absolute path; the name itself, with a warning in the log,
            when nothing has it (systemd then fails with a clear message).
        """
        if program.startswith("/"):
            return program
        if "/" in program:
            return os.path.normpath(self.runtime_path / program)
        for directory in self._tool_dirs():
            if (directory / program).is_file():
                return str(self._runtime_equivalent(directory / program))
        runtime = self._pinned_runtime()
        if runtime is not None and runtime.program(program) is not None:
            return str(runtime.bin_dir / program)
        return self._path_resolver.resolve_command(program)

    def _runtime_root(self) -> Path:
        """
        Return where the tree is seen once it is running: ``current``, or the application.

        Returns:
            The directory the unit's paths are relative to.
        """
        return self.app_path / CURRENT_LINK if self.uses_releases else self.app_path

    def _runtime_equivalent(self, path: Path) -> Path:
        """
        Translate a path in the tree being deployed to the path the service sees.

        Args:
            path: A path at or below :attr:`tree_root`.

        Returns:
            The same path below :meth:`_runtime_root`; a path outside the tree
            comes back unchanged.
        """
        try:
            return self._runtime_root() / path.relative_to(self.tree_root)
        except ValueError:
            return path
