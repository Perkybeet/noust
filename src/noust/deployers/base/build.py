# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Fetching the source and the steps of building it: install, build, and the removal of what a failed deploy left.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable

from noust.core import delivery
from noust.core.exceptions import (
    BuildError,
    DeploymentError,
    NoustError,
    OutOfMemoryError,
    ServiceError,
)
from noust.deployers.base.commands import CommandsMixin
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.health import failure_output
from noust.deployers.helpers.release_build import StagedRelease
from noust.deployers.helpers.sandbox import BuildPhase, SandboxState
from noust.deployers.helpers.site import remove_app_site
from noust.deployers.helpers.target import fetch_into_target


class BuildMixin(CommandsMixin):
    """
    Fetching the source and the steps of building it: install, build, and the removal of what a failed deploy left.
    """

    def sandbox_trial(self, staged: StagedRelease) -> None:
        """
        Install and build a tree in the sandbox, activating and recording nothing.

        What ``noust app sandbox test`` runs: the current commit, exported to a
        scratch directory, built the way an enabled sandbox would build it.

        Args:
            staged: The exported tree.

        Raises:
            NoustError: The install or the build failed, with its output.
        """
        self.adopt_release(staged)
        self.resolve_layout()
        self._sandbox_forced = True
        self._sandbox_regime = False
        with self._in_phase(BuildPhase.INSTALL):
            self.install_dependencies()
        with self._in_phase(BuildPhase.BUILD):
            self.build()

    # Undo actions ---------------------------------------------------------
    #
    # Each of these reverses exactly one pipeline step. They are idempotent and
    # tolerate the resource never having been created, because a step can fail
    # halfway through its own work.

    def remove_source(self) -> None:
        """
        Take back the application directory a failed deploy fetched into.

        During a deploy, what was there before decides: a directory this
        deploy created is deleted, one it found empty is emptied, one that
        held files is never touched. Outside a deploy nothing records that,
        and the directory is deleted, as this always did.
        """
        if self.deploy_target is not None:
            self.deploy_target.undo_fetch(self.fs, self.logger)
            return
        if self.app_path and self.app_path.exists():
            self.logger.debug(f"Removing app files: {self.app_path}")
            try:
                self.fs.remove_tree(self.app_path)
            except OSError as e:
                # An undo runs while something has already gone wrong; one
                # directory that will not go away must not hide that failure.
                self.logger.debug(f"Could not remove {self.app_path}: {e}")

    def remove_site(self) -> None:
        """Remove the web server site configuration for this domain."""
        if not self.domain:
            return
        remove_app_site(self.domain, logger=self.logger, manager=self._webserver_manager())

    def remove_service(self) -> None:
        """Stop and delete the systemd service for this application."""
        if not self.app_name:
            return
        status = self.service_manager.get_status(self.app_name)
        if not status.get("exists"):
            return
        self.logger.debug(f"Removing service: {self.app_name}")
        try:
            self.service_manager.stop(self.app_name)
        except NoustError as e:
            self.logger.debug(f"Service was not running: {e}")
        self.service_manager.delete_service(self.app_name)

    def _leftover_unit(self) -> bool:
        """
        Report whether a unit is on disk for an application that runs no process.

        Asked again when the unit is retired rather than trusted from the
        pipeline's construction: a Vite build decides whether it serves files
        or runs a server only once it has looked at the source.

        Returns:
            True when this type serves files (no start command) and a unit
            file for the application is in the managed directory.
        """
        if not self.app_name or self.start_command():
            return False
        return self.service_manager.service_exists(self.app_name)

    def retire_leftover_unit(self) -> None:
        """
        Stop, disable and delete the unit a process type left behind.

        An application first deployed as a type that runs a process and later
        redeployed as one that serves files keeps its old unit otherwise, and
        with ``Restart=always`` that unit restarts a command the new tree does
        not have every ten seconds, forever: one production server was found
        at 7750 consecutive "Missing script: start" failures. The deletion is
        :meth:`ServiceManager.delete_service`, which refuses a unit Noust does
        not own; that refusal, like any other failure here, is reported and
        does not fail a deployment whose site already serves the new files.
        """
        if not self._leftover_unit():
            return
        name = self.app_name or ""
        try:
            self.service_manager.delete_service(name)
        except ServiceError as exc:
            self.logger.warning(f"The unit of {name} was left in place: {exc.message}")
            if exc.details:
                self.logger.substep(exc.details)
            return
        self.logger.substep(
            f"Removed the unit of {name}: as {self.APP_TYPE} it is served by the web server "
            "and runs no process"
        )

    def forget_records(self) -> None:
        """Delete the app, site and service rows this deployment created."""
        if self.domain:
            self.registrar.forget(domain=self.domain, service_name=self.app_name)

    def rollback(self, keep_files: bool = False) -> bool:
        """
        Undo everything a deployment may have created.

        The pipeline undoes only the steps that ran, which is what a failed
        deploy needs. This method is the blunt version, kept for callers that
        want to clean up after the fact.

        Args:
            keep_files: If True, preserve the application files.

        Returns:
            True if every cleanup action completed.
        """
        actions: list[tuple[str, Callable[[], None]]] = [
            ("service", self.remove_service),
            ("site", self.remove_site),
            ("store records", self.forget_records),
        ]
        if not keep_files:
            actions.append(("files", self.remove_source))

        errors = 0
        for what, action in actions:
            try:
                action()
            # Cleanup is an error boundary: one failure must not abort the rest.
            except Exception as e:
                errors += 1
                self.logger.debug(f"Rollback of {what} failed: {e}")

        return errors == 0

    def pre_install(self) -> bool:
        """
        Pre-installation hook.

        Override to perform actions before dependency installation.
        Detects package manager and Prisma by default.

        Returns:
            True if successful.
        """
        # Detect package manager
        self.package_manager = self._detect_package_manager()
        self.logger.debug(f"Using package manager: {self.package_manager}")

        # Verify the package manager is available
        self._verify_package_manager()

        # Detect Prisma
        self.has_prisma = self._detect_prisma()
        if self.has_prisma:
            self.logger.debug("Prisma detected")

        return True

    def post_install(self) -> bool:
        """
        Post-installation hook.

        Override to perform actions after dependency installation.
        Generates Prisma client by default if needed.

        Returns:
            True if successful.
        """
        # Generate Prisma client if detected
        if self.has_prisma:
            self.generate_prisma()

        return True

    def pre_build(self) -> bool:
        """
        Pre-build hook.

        Override to perform actions before building.

        Returns:
            True if successful.
        """
        return True

    def post_build(self) -> bool:
        """
        Post-build hook.

        Override to perform actions after building.

        Returns:
            True if successful.
        """
        return True

    def fetch_source(self) -> bool:
        """
        Fetch the source code.

        Returns:
            True if successful.
        """
        if self.source_already_fetched:
            # AutoDeployer put the code here to detect the type; fetching again
            # with clean=True would delete it and clone it a second time.
            self.logger.substep(f"Source already present at {self.app_path}")
            return True

        self.logger.substep(f"Source: {self.source}")
        self.logger.substep(f"Target: {self.app_path}")

        if self.deploy_target is None:
            return self.source_manager.fetch(self.source, self.app_path, branch=self.branch)
        # Never empties a directory that held files (noust create --force):
        # a checkout is updated in place, anything else is refused.
        fetch_into_target(
            self.deploy_target,
            self.source_manager,
            self.source,
            branch=self.branch,
            domain=self.domain,
            logger=self.logger,
        )
        return True

    def skip_build_cache(self) -> bool:
        """
        Whether this build ignores every cache.

        Returns:
            True for a build asked to start from scratch (``--no-cache``, the
            console's "Rebuild without cache"), or for an application whose
            build block says ``cache: false``.
        """
        return self.no_cache or self.build_config().cache is False

    def _empty_build_cache(self) -> None:
        """Empty the application's package manager cache when this build skips it."""
        if not self.skip_build_cache():
            return
        freed = build_sandbox.clear_cache_dir(self.app_name, fs=self.fs)
        if freed:
            self.logger.substep(f"Build cache emptied ({freed // 1024} KiB)")

    def install_timeout(self) -> int:
        """
        Say how long the install of the dependencies may take.

        Returns:
            Seconds: the build block's ``timeout`` when it sets one, else the server's
            ``deploy.install_timeout``.
        """
        declared = self.build_config().timeout
        return declared if declared is not None else delivery.current().install_timeout

    def build_timeout(self) -> int:
        """
        Say how long the build may take.

        Returns:
            Seconds: the build block's ``timeout`` when it sets one, else the server's
            ``deploy.build_timeout``.
        """
        declared = self.build_config().timeout
        return declared if declared is not None else delivery.current().build_timeout

    def _timeout_hint(self, step: str, limit: int) -> str:
        """
        Say how to give a step that ran out of time more of it.

        Args:
            step: ``install`` or ``build``.
            limit: The seconds it had.

        Returns:
            One sentence naming the application's own setting and the server's.
        """
        longer = min(limit * 2, 86400)
        return (
            f"Give this application's {step} more time: noust app build set {self.domain} "
            f"--timeout {longer}; or every application's: noust config set "
            f"deploy.{step}_timeout {longer}. If the {step} is not slow but stuck (a private "
            "registry that does not answer, a prompt), more time will not help."
        )

    def install_dependencies(self) -> bool:
        """
        Install application dependencies.

        Returns:
            True if successful.
        """
        self.pre_install()
        self._empty_build_cache()

        command = self.install_command()
        if not command:
            return True

        self.logger.substep(f"Running: {shlex.join(command)}")

        limit = self.install_timeout()
        result = self._run(command, timeout=limit, stream=True)
        if not result.success:
            # Try fallback install methods, for the type's own command only:
            # what the project wrote is what it wants run, and a retry of
            # something else would hide that it failed.
            fallback_command = None
            custom = self.build_config().install is not None

            # Check if it's a frozen lockfile issue (pnpm/yarn/bun)
            if custom:
                pass
            elif "--frozen-lockfile" in command:
                self.logger.warning("Strict lockfile install failed, trying regular install...")
                fallback_command = [c for c in command if c != "--frozen-lockfile"]

            # Check if it's npm ci failing (no package-lock.json)
            elif command == ["npm", "ci"]:
                if "package-lock.json" in str(result.stderr) or "EUSAGE" in str(result.stderr):
                    self.logger.warning("npm ci failed (no lockfile), using npm install...")
                    fallback_command = ["npm", "install"]

            if fallback_command:
                self.logger.substep(f"Running: {' '.join(fallback_command)}")
                result = self._run(fallback_command, timeout=limit, stream=True)

            if not result.success:
                error_output = failure_output(result)
                if result.out_of_memory:
                    raise OutOfMemoryError(
                        "Dependency installation ran out of memory and was killed",
                        details=error_output,
                    )
                if result.timed_out:
                    raise DeploymentError(
                        f"Dependency installation did not finish within {limit}s and was stopped",
                        details="\n\n".join(
                            part
                            for part in (self._timeout_hint("install", limit), error_output)
                            if part
                        ),
                    )
                raise DeploymentError(
                    "Dependency installation failed",
                    details=error_output
                    or "No error output captured. Check if the package manager is properly installed.",
                )

        self.post_install()
        return True

    def build(self) -> bool:
        """
        Build the application.

        Returns:
            True if successful.

        Raises:
            OutOfMemoryError: If build is killed due to OOM (exit code 137).
            BuildError: If build fails for other reasons.
        """
        self.pre_build()

        command = self.build_command()
        if not command:
            return True

        self.logger.substep(f"Running: {shlex.join(command)}")

        limit = self.build_timeout()
        result = self._run(
            command,
            timeout=limit,
            stream=True,
            tools=self.build_config().build is not None,
        )
        if not result.success:
            error_output = failure_output(result)

            # Check for OOM killer (exit code 137 = 128 + SIGKILL). In the
            # sandbox the unit's own Result says so, since systemd-run
            # itself exits 1 for it.
            if result.exit_code == 137 or result.out_of_memory:
                raise OutOfMemoryError(
                    "Build killed due to insufficient memory (exit code 137)",
                    details=error_output or "Process was killed by the OOM killer.",
                )
            if result.timed_out:
                raise BuildError(
                    f"Build did not finish within {limit}s and was stopped",
                    details="\n\n".join(
                        part
                        for part in (
                            self._timeout_hint("build", limit),
                            error_output or "No error output captured.",
                        )
                        if part
                    ),
                )

            hint = build_sandbox.network_hint(
                self._sandbox_regime if isinstance(self._sandbox_regime, SandboxState) else None,
                self.domain,
                error_output,
            )
            raise BuildError(
                "Build failed",
                details="\n\n".join(
                    part for part in (hint, error_output or "No error output captured.") if part
                ),
            )

        self.post_build()
        return True
