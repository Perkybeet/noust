# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Running a command of the build: as whom, in what environment and under which sandbox,
and the variables and the .env file it is given.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from noust.core import cancellation
from noust.core.appdir.layout import env_file_in
from noust.core.appdir.permissions import hand_over_tree
from noust.core.exceptions import DeploymentError
from noust.core.fs import SECRET_MODE
from noust.core.runner import CommandResult, SandboxSpec
from noust.core.variables.predefined import Deployment
from noust.deployers.base.project import ProjectMixin
from noust.deployers.base.state import COMMAND_TIMEOUT, _dotenv_files
from noust.deployers.helpers import PrismaHelper
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.deployment_variables import DeploymentVariables, describe_deployment
from noust.deployers.helpers.hooks import DeploymentHooks, prisma_applied_any
from noust.deployers.helpers.release_build import REPO_CACHE_DIR
from noust.deployers.helpers.sandbox import BuildPhase, SandboxState
from noust.deployers.interface import StepReporter
from noust.deployers.recorder import checkout_git_info
from noust.managers import app_identity


class ExecutionMixin(ProjectMixin):
    """
    Running a command of the build: as whom, in what environment and under which sandbox,
    """

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
        Execute a command in the application directory, where its phase says it runs.

        The one chokepoint for the code a deployment runs from the repository
        (rule 4): an install or a build of an application in the sandbox runs
        in it, as ``noust-build`` (see :mod:`noust.deployers.helpers.sandbox`);
        a migration runs as the application; anything else as this process,
        with its environment, as before 3.1.

        Args:
            command: Program and arguments.
            cwd: Working directory. Defaults to the application directory.
            env: Extra environment, merged over the configured env_vars.
            timeout: Deadline in seconds.
            stream: Print output line by line while the command runs. Used for
                installs and builds, which otherwise look frozen for minutes.
            phase: What the command does. Defaults to the phase the caller is
                in (:meth:`_in_phase`), and to ``build`` outside one.
            tools: Put the project's own tool directories on ``PATH``: for the
                build command a build block wrote, which has no ``npm run`` to
                do it. The commands of a type keep the ``PATH`` they always had.

        Returns:
            The command outcome.

        Raises:
            BuildError: The application builds in the sandbox and the sandbox
                does not hold on this server; it never falls back to root.
        """
        effective = phase or (self._phases[-1] if self._phases else BuildPhase.BUILD)
        sandbox = self._execution(effective)

        # A runtime the project is pinned to comes first on the command's PATH
        # and is named by its absolute path: the sandbox finds programs through
        # its own fixed PATH before this one applies. The tool directories of
        # the project (node_modules/.bin, the virtual environment) follow it
        # for a build command of the build block, once an install has put
        # something in them.
        pinned = self._runtime_environment(
            sandboxed=sandbox is not None, tools=tools and effective is BuildPhase.BUILD
        )
        if pinned:
            env = {**pinned, **(env or {})}
        if command and self._pinned_runtime() is not None:
            command = [self._pinned_program(command[0]), *command[1:]]
        self.logger.debug(f"Running: {' '.join(command)}")

        # Beyond the .env: what only the build is given and what Noust says
        # about the deployment, less what only the running application is given.
        extra, hidden = self._build_environment(effective)
        run_env = {**extra, **self.env_vars}
        for name in hidden:
            run_env.pop(name, None)
        if env:
            run_env.update(env)

        if sandbox is not None and effective is BuildPhase.RELEASE and cwd is not None:
            # A deploy hook's workdir: the release spec names the tree's root,
            # and a spec's working directory wins over the call's.
            sandbox = replace(sandbox, working_dir=cwd)
        options: dict[str, Any] = {}
        if sandbox is not None:
            options["sandbox"] = sandbox
            if effective is not BuildPhase.RELEASE and isinstance(
                self._sandbox_regime, SandboxState
            ):
                run_env = build_sandbox.command_environment(
                    self._sandbox_regime,
                    effective,
                    configured=self._build_variables(run_env),
                    given=env,
                    cache=self._sandbox_cache or build_sandbox.cache_dir_for(self.app_name),
                )
            options["sandbox"] = build_sandbox.install_environment(sandbox, effective, run_env)
            if hidden:
                # The .env systemd hands in also holds them; take them out of
                # what the command sees.
                options["sandbox"] = replace(
                    options["sandbox"], unset_env=(*options["sandbox"].unset_env, *hidden)
                )
        # Outside the sandbox (an application from before 3.1, an explicit
        # root build) a command inherits this process's environment exactly as
        # before 3.1: a build relying on a variable of it must not break on
        # upgrade. The sandbox starts clean by construction (sandbox_environment).

        # What a repository's code runs (install, build) is what an operator
        # cancels a deployment to stop; a migration (RELEASE) is never killed
        # halfway, and root's own commands are not the build.
        interrupt: contextlib.AbstractContextManager[None] = (
            cancellation.interruptible(f"running the {effective.value}")
            if effective in (BuildPhase.INSTALL, BuildPhase.BUILD)
            else contextlib.nullcontext()
        )
        with interrupt, self._env_file_for_build(effective, sandboxed=sandbox is not None):
            if stream:
                result = self.runner.stream(
                    command,
                    on_line=self.logger.debug,
                    cwd=cwd or self.build_path,
                    env=run_env or None,
                    timeout=timeout,
                    **options,
                )
            else:
                result = self.runner.run(
                    command,
                    cwd=cwd or self.build_path,
                    env=run_env or None,
                    timeout=timeout,
                    **options,
                )
                self.logger.command_output(result.stdout, result.stderr)
        return result

    @contextlib.contextmanager
    def _env_file_for_build(self, phase: BuildPhase, *, sandboxed: bool) -> Iterator[None]:
        """
        Show an install or a build the ``.env`` as a file, not as the link a release keeps.

        A release's ``.env`` is a relative link into ``shared/``. A tool that copies the tree
        keeps the link's text and so leaves it pointing at nothing: Next.js 15.5 writes its
        standalone server that way and then copies the ``.env`` onto its own dangling copy,
        which fails with ``ENOENT`` (found deploying Umami from its recipe). So for the length of
        the command the link is a plain file, and the link comes back, whatever the command did.

        The file holds what a loader of the ``.env`` can read: nothing in the sandbox, which
        hands the variables over through the environment and masks the file (its content is
        the application's secrets, which the build account cannot open), and the file's own
        content in a build as root. A project in a subdirectory keeps its own ``.env``, and its
        build reads that one.

        Args:
            phase: The command's phase.
            sandboxed: Whether the command runs in the build sandbox.

        Yields:
            Nothing.
        """
        link = self.build_path / ".env"
        if (
            not self.uses_releases
            or phase not in (BuildPhase.INSTALL, BuildPhase.BUILD)
            or not link.is_symlink()
        ):
            yield
            return
        target = Path(os.readlink(link))
        content = ""
        if not sandboxed:
            try:
                content = link.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                content = ""
        self.fs.remove(link)
        self.fs.write_text(link, content, mode=0o644 if sandboxed else SECRET_MODE)
        try:
            yield
        finally:
            self.fs.remove(link)
            self.fs.symlink(target, link)

    @contextlib.contextmanager
    def _in_phase(self, phase: BuildPhase) -> Iterator[None]:
        """
        Run the commands of a block as the given phase, whoever issues them.

        A deployer's hooks (the Python one creates its virtualenv in
        ``pre_install``, PHP overrides ``install_dependencies``) call
        :meth:`_run` without a phase; the step around them decides it.

        Args:
            phase: The phase.

        Yields:
            Nothing.
        """
        self._phases = (*self._phases, phase)
        try:
            yield
        finally:
            self._phases = self._phases[:-1]

    def _phased(self, phase: BuildPhase, step: Callable[[], object]) -> Callable[[], None]:
        """
        Wrap a pipeline step so its commands run as a phase.

        Args:
            phase: The phase.
            step: The step.

        Returns:
            The wrapped step.
        """

        def run() -> None:
            with self._in_phase(phase):
                step()

        return run

    def _build_regime(self) -> SandboxState | None:
        """
        Decide, once per deployment, whether this build runs in the sandbox.

        Returns:
            The application's regime when its builds are sandboxed; None when
            they run as this process: its regime says root (with a warning in
            the log), or this process is not root and has no privilege to
            take away.
        """
        if self._sandbox_regime is not False:
            return self._sandbox_regime if isinstance(self._sandbox_regime, SandboxState) else None
        regime = build_sandbox.decide_regime(
            self.domain, store=self.store, logger=self.logger, forced=self._sandbox_forced
        )
        self._sandbox_regime = regime
        return regime

    def _build_variables(self, configured: dict[str, str]) -> dict[str, str]:
        """
        Name the application's variables a sandboxed install or build is given.

        Args:
            configured: This deployment's variables, the command's merged in.

        Returns:
            ``configured`` as is, except for a preview building with the
            network: it gets its ``.env`` (which the sandbox then does not
            load) without the production secrets it was copied.
        """
        state = self._sandbox_regime
        app = self._app_row()
        if (
            not isinstance(state, SandboxState)
            or app is None
            or app.preview_parent is None
            or state.network == build_sandbox.NetworkProfile.STRICT.value
        ):
            return configured
        env_file = self._existing_env_file()
        from_file = self._env_manager.read_env_file(env_file) if env_file is not None else {}
        return build_sandbox.preview_build_variables(
            {**from_file, **configured}, app.env_secret_marks
        )

    def _build_account(self) -> tuple[str, str]:
        """
        Name the account a sandboxed build of this application runs as.

        Returns:
            ``noust-build`` on releases, whose release the service does not
            use while it is built; the service account in place, whose tree
            the service runs from.
        """
        if self.uses_releases:
            return build_sandbox.BUILD_USER, build_sandbox.BUILD_GROUP
        return self._service_account()

    def _service_account(self) -> tuple[str, str]:
        """
        Name the account this application runs as, which its files are handed to.

        Returns:
            Its own account when it has one (3.2), the configured service
            account otherwise: :func:`noust.managers.app_identity.service_account`.
        """
        return app_identity.service_account(self._app_row(), self.config)

    def _existing_env_file(self) -> Path | None:
        """
        Return this application's ``.env`` when there is one.

        Returns:
            The file, or None.
        """
        env_file = self._env_file()
        # In place this is the repository's own tree, and systemd reads the
        # file as root outside the sandbox: a committed link is not a .env.
        return env_file if env_file.is_file() and not env_file.is_symlink() else None

    def _execution(self, phase: BuildPhase) -> SandboxSpec | None:
        """
        Return the sandbox a command of this phase runs in.

        The first sandboxed command of a deployment creates the build account
        and proves the sandbox holds (failing closed when it does not), makes
        the application's cache, and hands the tree being built to the build
        account.

        Args:
            phase: The command's phase.

        Returns:
            The spec, or None to run as this process.

        Raises:
            BuildError: The sandbox does not hold here.
        """
        if phase is BuildPhase.PRIVILEGED:
            return None
        state = self._build_regime()
        if state is None:
            return None
        user, group = self._build_account()
        if self._sandbox_cache is None:
            build_sandbox.require_working_sandbox(self.runner, self.domain, self.fs)
            self._sandbox_cache = build_sandbox.ensure_cache_dir(
                self.app_name, user=user, group=group, runner=self.runner, fs=self.fs
            )
        if self._sandbox_tree != self.tree_root:
            # The whole tree, not the project inside it: a workspace installs
            # and builds at its root.
            hand_over_tree(
                self.tree_root,
                user=user,
                group=group,
                runner=self.runner,
                fs=self.fs,
                logger=self.logger,
                env_files=_dotenv_files(self.tree_root)
                if self.uses_releases
                else self._env_files(),
            )
            self._sandbox_tree = self.tree_root
        if phase is BuildPhase.RELEASE:
            user, group = self._service_account()
            return build_sandbox.release_spec(
                app_name=self.app_name,
                user=user,
                group=group,
                build_path=self.build_path,
                env_file=self._existing_env_file(),
            )
        app = self._app_row()
        preview = app is not None and app.preview_parent is not None
        return build_sandbox.build_spec(
            app=app,
            app_name=self.app_name,
            phase=phase,
            state=state,
            user=user,
            group=group,
            build_path=self.tree_root,
            work_dir=self.build_path,
            apps_dir=self.config.apps_directory,
            cache=self._sandbox_cache,
            env_file=self._existing_env_file(),
            shared=self.releases.shared_dir if self.uses_releases else None,
            memory_max_mb=build_sandbox.PREVIEW_BUILD_MEMORY_MB if preview else None,
            cpu_quota_percent=build_sandbox.PREVIEW_BUILD_CPU_PERCENT if preview else None,
            tasks_max=build_sandbox.BUILD_TASKS_MAX,
        )

    def _sandbox_by_default(self, report: StepReporter) -> None:
        """
        Try an application still building as root in the sandbox before this update builds.

        The sandbox by default (3.2), once the source the update builds is in
        place: see :func:`~noust.deployers.helpers.sandbox_trial.trial_before_update`.
        The trial builds in a scratch copy with a deployer like this one, so
        nothing of this deployment's state changes but its regime, decided
        again afterwards.

        Args:
            report: Called when the trial begins.
        """
        from noust.deployers.helpers.sandbox_trial import trial_before_update

        def sibling(_app_type: str) -> ExecutionMixin:
            deployer = type(self)(verbose=self.verbose, runner=self._runner, fs=self._fs)
            deployer.source_manager = self.source_manager
            return deployer

        if trial_before_update(
            self.domain,
            store=self.store,
            logger=self.logger,
            runner=self.runner,
            commit=self._staged.commit if self._staged is not None else None,
            make_deployer=sibling,
            fs=self.fs,
        ):
            report("Building in the sandbox from now on")
        self._sandbox_regime = False

    def _detect_package_manager(self) -> str:
        """
        Detect the package manager used in the project.

        Returns:
            Detected package manager name.
        """
        return self._pm_helper.detect_up(self.build_path, self.tree_root, self._package_manager)

    def _verify_package_manager(self) -> None:
        """
        Verify the package manager is installed and available.
        Falls back to an available package manager if the requested one is not installed.

        Raises:
            DeploymentError: If no package manager is available at all.
        """
        self.package_manager = self._pm_helper.verify(self.package_manager)

    def _detect_prisma(self) -> bool:
        """
        Detect if project uses Prisma ORM.

        Returns:
            True if Prisma is detected.
        """
        return self._ensure_prisma_helper().detect(self.build_path)

    def _get_pm_install_command(self) -> list[str]:
        """
        Get the package manager install command.

        Returns:
            Install command as list.
        """
        lock_dir = self._pm_helper.lockfile_dir(self.build_path, self.tree_root)
        return self._pm_helper.get_install_command(self.package_manager, lock_dir)

    def _get_pm_run_command(self, script: str) -> list[str]:
        """
        Get the package manager run command.

        Args:
            script: Script name to run.

        Returns:
            Run command as list.
        """
        return self._pm_helper.get_run_command(self.package_manager, script)

    def _get_pm_exec_command(self, command: str) -> list[str]:
        """
        Get the package manager exec/dlx command.

        Args:
            command: Command to execute.

        Returns:
            Exec command as list.
        """
        return self._pm_helper.get_exec_command(self.package_manager, command)

    def _resolve_absolute_path(self, command: str) -> str:
        """
        Resolve command to use absolute paths for executables.

        This is required for systemd services as ExecStart
        must use absolute paths. Prefers global system paths
        over user-specific installations (like nvm) to avoid
        permission issues with systemd services.

        Args:
            command: Command string (e.g., "npm run start")

        Returns:
            Command with absolute path (e.g., "/usr/bin/npm run start")
        """
        runtime = self._pinned_runtime()
        prefer = [runtime.bin_dir] if runtime is not None else []
        return self._path_resolver.resolve_command(command, prefer=prefer)

    def _ensure_prisma_helper(self) -> PrismaHelper:
        """
        Ensure PrismaHelper is initialized and return it.

        Returns:
            Initialized PrismaHelper instance.
        """
        if self._prisma_helper is None:
            self._prisma_helper = PrismaHelper(
                logger=self.logger,
                run_command=self._run,
                get_exec_command=self._get_pm_exec_command,
            )
        return self._prisma_helper

    def generate_prisma(self) -> bool:
        """
        Generate Prisma client if Prisma is detected.

        Returns:
            True if successful or not needed.
        """
        if not self.has_prisma:
            return True

        return self._ensure_prisma_helper().generate(self.build_path)

    def run_prisma_migrate(self, deploy: bool = True) -> bool:
        """
        Run Prisma migrations, recording what they applied.

        A migration that fails aborts the deployment (3.2): new code must not
        start serving against the schema it was not written for. One that
        applies anything marks the deployment ``schema_changed``.

        Args:
            deploy: If True, run deploy (production), else run dev.

        Returns:
            True once the migrations ran, or when there are none.

        Raises:
            DeploymentError: The migration failed, with Prisma's own output.
        """
        if not self.has_prisma:
            return True

        # Check if there's a migrations folder
        migrations_dir = self.build_path / "prisma" / "migrations"
        if not migrations_dir.exists():
            self.logger.debug("No Prisma migrations found")
            return True

        # A migration is not a build: it runs with the application's own
        # identity and secrets (the release phase), never as noust-build.
        migrator = PrismaHelper(
            logger=self.logger,
            run_command=lambda *args, **kwargs: self._run(
                *args, phase=BuildPhase.RELEASE, **kwargs
            ),
            get_exec_command=self._get_pm_exec_command,
        )
        command = "prisma migrate " + ("deploy" if deploy else "dev")
        try:
            migration = migrator.migrate(self.build_path, deploy=deploy)
        except DeploymentError as exc:
            output = exc.output or ""
            # A failed migration may have applied the ones before it.
            self.deploy_hooks.note_prisma(
                output, applied=prisma_applied_any(output), ok=False, command=command
            )
            raise
        if migration is not None:
            self.deploy_hooks.note_prisma(
                migration.output, applied=migration.applied, ok=True, command=migration.command
            )
        return True

    @property
    def _variables(self) -> DeploymentVariables:
        """
        Returns:
            The variables of this deployment, the one implementation every type of application
            shares (:mod:`noust.deployers.helpers.deployment_variables`).
        """
        if self._deployment_variables is None:
            self._deployment_variables = DeploymentVariables(
                store=self.store,
                domain=self.domain,
                logger=self.logger,
                fs=lambda: self.fs,
                manager=lambda: self._env_manager,
                row=self._app_row,
                deployment=self._deployment,
                audience=self._env_audience,
            )
        return self._deployment_variables

    def _deployment(self) -> Deployment:
        """
        Describe this deployment for the references and the variables Noust sets.

        Returns:
            Where the application answers and what is being built.
        """
        commit, branch = self._git_info()
        # A preview's row is linked to its application from the start, so a
        # first build already knows which pull request it is for.
        return describe_deployment(
            self.store,
            self.domain,
            row=self._app_row(),
            ssl=self.ssl,
            branch=self.branch or branch,
            commit=commit,
            release=self._staged.id if self._staged is not None else None,
            port=self.port,
        )

    def _build_environment(self, phase: BuildPhase) -> tuple[dict[str, str], tuple[str, ...]]:
        """
        Say what an install or a build is given beyond the ``.env``, and what it is not.

        Args:
            phase: The command's phase.

        Returns:
            The variables to add (the ones only the build is given, and what
            Noust says about the deployment bar what changes on every deploy)
            and the names to take away (the ones only the running application
            is given). Nothing for a command that is not an install or a build.
        """
        if phase not in (BuildPhase.INSTALL, BuildPhase.BUILD) or not self.domain:
            return {}, ()
        built = self._variables.build_environment()
        return dict(built.extra), built.hidden

    def _env_file(self) -> Path:
        """
        Return where this application's ``.env`` lives.

        Returns:
            ``shared/.env`` on releases, the application directory's in place.
        """
        return env_file_in(self.app_path, self._layout)

    def _env_files(self) -> list[Path]:
        """
        List the environment files whose modes must survive ``_set_permissions``.

        Returns:
            The dotenv files at the application root. Subclasses that write
            environment files elsewhere override this.
        """
        if not self.app_path.is_dir():
            return []
        # is_file() follows a link, and a repository can commit one as .env.
        return sorted(
            path for path in self.app_path.glob(".env*") if path.is_file() and not path.is_symlink()
        )

    def _git_info(self) -> tuple[str | None, str | None]:
        """
        Read the short commit and branch of the deployed tree, when it is one.

        Returns:
            ``(commit, branch)``, each None when the tree is not a git
            checkout or the information is unavailable. Test doubles stand in
            for the source manager without implementing all of it, so a
            manager that cannot answer is treated as "unknown", not an error.
        """
        reader = getattr(self.source_manager, "get_repo_info", None)
        if self._staged is not None:
            # A release has no .git; its commit is part of what was staged,
            # and the branch is whatever the repository cache follows.
            branch = self.branch
            cache = self.app_path / REPO_CACHE_DIR
            if branch is None and reader is not None and (cache / ".git").is_dir():
                branch = reader(cache).get("branch")
            return self._staged.short_commit, branch
        return checkout_git_info(self.source_manager, self.app_path)()

    # Deploy hooks ----------------------------------------------------------

    @property
    def deploy_hooks(self) -> DeploymentHooks:
        """What this deployment's hooks are and did (one per deployment)."""
        if self._deploy_hooks_state is None:
            self._deploy_hooks_state = DeploymentHooks()
        return self._deploy_hooks_state
