# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Updating an application that already runs, and the hooks around a deployment.
"""

from __future__ import annotations

from dataclasses import replace

from noust.central import require_server_role
from noust.core import cancellation
from noust.core.appdir.layout import RELEASES
from noust.core.deploy_failures import app_unhealthy
from noust.core.exceptions import DeploymentError
from noust.deployers.base.release import ReleaseMixin
from noust.deployers.helpers.hooks import CommandHookExecutor, DeploymentHooks, HookSet
from noust.deployers.helpers.release_build import StagedRelease
from noust.deployers.helpers.sandbox import BuildPhase
from noust.deployers.interface import StepReporter, UpdateResult
from noust.deployers.pipeline import DeployStep
from noust.deployers.recorder import recording


class UpdateMixin(ReleaseMixin):
    """
    Updating an application that already runs, and the hooks around a deployment.
    """

    def update(self, on_step: StepReporter | None = None) -> UpdateResult:
        """
        Rebuild this application without a full redeploy.

        The sequence used to live in ``noust.cli.commands.webapp``, which drove
        the deployer step by step and reached into ``_package_manager`` to do
        it. Keeping it here means the update path is the deployer's own, gets
        the same detection and error handling as a deploy, and can be tested.

        In place, the tree is rebuilt where it is and the caller restarts the
        unit. On releases, the source is fetched into a new release, built
        there and activated behind the health gate; a release that does not
        answer is rolled back before this raises, so the caller has nothing
        left to restart.

        Like a deploy, an update is recorded in the deployment history with
        its build log captured; recording failures are reported and never
        fail the update itself.

        Args:
            on_step: Called as each step begins.

        Returns:
            What was done, for the caller to present.

        Raises:
            NoustError: When a step fails.
        """
        require_server_role("Applications")
        report = on_step or (lambda _message: None)
        releases = self.resolve_layout() == RELEASES
        self._forget_hooks()

        recorder = self._recorder()
        try:
            with recording(
                recorder,
                git_branch=self.branch,
                on_failure=(lambda _exc: self._abandon_release()) if releases else None,
            ):
                try:
                    result = (
                        self._update_release(report) if releases else self._update_in_place(report)
                    )
                except DeploymentError as exc:
                    self.deploy_hooks.say_schema_changed(exc)
                    raise
                if self._leftover_unit():
                    report("Removing the unit a previous deployment left")
                    self.retire_leftover_unit()
                self._run_post_deploy_hooks(report)
        finally:
            self.deploy_hooks.record(
                self.store, recorder.deployment_id, self.logger, domain=self.domain
            )
            self._record_certificate(recorder.deployment_id)
        self.last_deployment_id = recorder.deployment_id
        return self._with_hook_result(result)

    def stage(self, on_step: StepReporter | None = None) -> StagedRelease:
        """
        Build the latest source as a new release and leave it there, not serving.

        What a staged deployment is (checklist B-18): the release is fetched, linked to
        ``shared/``, installed and built exactly as an update would, and then nothing
        else happens. ``current`` stays where it is, no hook runs (a ``pre_deploy`` hook
        migrates a database, which is for the moment the release serves), Prisma's client
        is generated and its migrations are not applied, and nothing restarts. The release
        is recorded as ``built``; :meth:`deploy_prebuilt` activates it later, running the
        hooks and the migrations then, behind the health gate, without building it again.

        Args:
            on_step: Called as each step begins.

        Returns:
            The release, built.

        Raises:
            DeploymentError: The application is not on the release layout.
            NoustError: A step failed; the half-built release is removed.
        """
        require_server_role("Applications")
        report = on_step or (lambda _message: None)
        if self.resolve_layout() != RELEASES:
            raise DeploymentError(
                f"{self.domain} is not on the release layout",
                details="Only an application that builds releases can build one without "
                f"serving it: noust app migrate {self.domain}",
            )
        self._forget_hooks()
        built = False
        try:
            report("Fetching into a new release")
            self._step_fetch_release()
            self._sandbox_by_default(report)
            self._ensure_system_packages(report)
            cancellation.checkpoint()
            report("Installing dependencies")
            with self._in_phase(BuildPhase.INSTALL):
                self._install_release_dependencies()
            cancellation.checkpoint()
            if self.has_prisma:
                report("Generating the Prisma client")
                with self._in_phase(BuildPhase.INSTALL):
                    self.generate_prisma()
            cancellation.checkpoint()
            report("Building")
            with self._in_phase(BuildPhase.BUILD):
                self.build()
            cancellation.checkpoint()
            self._set_permissions()
            built = True
        finally:
            if not built:
                self._abandon_release()
        return self._require_staged()

    def deploy_prebuilt(
        self,
        staged: StagedRelease,
        on_step: StepReporter | None = None,
        *,
        apply_migrations: bool = False,
    ) -> UpdateResult:
        """
        Activate a release that someone else built, in this application.

        What promoting a release from one application to another does on the
        receiving side: the release is already in this application's
        ``releases/``, copied there; nothing is fetched, installed or built. It
        is linked to this application's own ``shared/`` (its ``.env``, its
        persistent paths), its references are resolved for it, its
        ``pre_deploy`` hooks run (this application's migrations, against this
        application's database), and it is activated behind the same health
        gate as any release. The deployment is recorded like any other.

        Args:
            staged: The release, in place.
            on_step: Called as each step begins.
            apply_migrations: The release was staged by this application (:meth:`stage`)
                and has not met its database: apply Prisma's migrations before it serves,
                as an update does.

        Returns:
            What was done.

        Raises:
            DeploymentError: The application is not on the release layout, or the
                release did not pass its health check (the one that served is
                back by then).
            NoustError: A hook failed, or a reference does not resolve.
        """
        require_server_role("Applications")
        report = on_step or (lambda _message: None)
        if self.resolve_layout() != RELEASES:
            raise DeploymentError(
                f"{self.domain} is not on the release layout",
                details="A release can only be promoted into an application that builds releases.",
            )
        self.adopt_release(staged)
        self._forget_hooks()
        recorder = self._recorder()
        try:
            with recording(
                recorder, git_branch=self.branch, on_failure=lambda _e: self._abandon_release()
            ):
                try:
                    result = self._activate_prebuilt(report, apply_migrations=apply_migrations)
                except DeploymentError as exc:
                    self.deploy_hooks.say_schema_changed(exc)
                    raise
                self._run_post_deploy_hooks(report)
        finally:
            self.deploy_hooks.record(
                self.store, recorder.deployment_id, self.logger, domain=self.domain
            )
            self._record_certificate(recorder.deployment_id)
        self.last_deployment_id = recorder.deployment_id
        return self._with_hook_result(result)

    def _activate_prebuilt(
        self, report: StepReporter, *, apply_migrations: bool = False
    ) -> UpdateResult:
        """
        Bring a copied release into line with this application and switch to it.

        Args:
            report: Called as each step begins.
            apply_migrations: Apply Prisma's migrations before the release serves, as an
                update does.

        Returns:
            What was done.
        """
        staged = self._require_staged()
        report(f"Receiving release {staged.id}")
        self._record_release_row(staged)
        # What this application's own list declares: the release runs here, not where it was built.
        self._ensure_system_packages(report)
        self._prime_build()
        self._refresh_variables()
        links = staged.manager.link_shared(staged.path, self._persistent_paths())
        for path in links.linked:
            self.logger.substep(f"Linked {path} to shared/{path}")
        cancellation.checkpoint()
        self._set_permissions()
        cancellation.checkpoint()
        prisma_updated = False
        if apply_migrations:
            # A release staged earlier has not met this application's database yet: Prisma's
            # migrations, which an update applies as it activates, are applied now (unless
            # hooks are declared, which win).
            self.pre_install()
            prisma_updated = self._update_prisma(report)
        self._run_pre_deploy_hooks(report)
        report(f"Activating release {staged.id}")
        put_unit_back = self._unit_edits()
        activated = False
        try:
            self._activate_release()
            activated = True
        finally:
            if put_unit_back is not None and not activated:
                put_unit_back()
                self._restart_and_probe()
        return self._update_result(prisma_updated)

    def _update_in_place(self, report: StepReporter) -> UpdateResult:
        """
        Rebuild the application in the tree it runs from.

        Args:
            report: Called as each step begins.

        Returns:
            What was done.
        """
        self._sandbox_by_default(report)
        self._ensure_system_packages(report)
        self._prime_build()

        report("Inspecting the project")
        with self._in_phase(BuildPhase.INSTALL):
            self.pre_install()

        report("Installing dependencies")
        with self._in_phase(BuildPhase.INSTALL):
            self.install_dependencies()

        with self._in_phase(BuildPhase.INSTALL):
            prisma_updated = self._update_prisma(report)

        report("Building")
        with self._in_phase(BuildPhase.BUILD):
            self.build()

        # The pull, the install and the build all ran as root, and the
        # service writes into what they produced: Next.js creates
        # .next/cache/images on the first optimised image, and uploads land
        # in directories a pull may have just added.
        self._set_permissions()
        self._run_pre_deploy_hooks(report)

        result = self._update_result(prisma_updated)
        # post_deploy hooks run once the new build passed its gate, so an
        # application that has them restarts behind it here, not later.
        gated = self._gate_in_place or bool(self._hook_set().post_deploy)
        put_unit_back = self._unit_edits()
        if not (gated and result.start_command):
            return result
        # Inside the recording: a build that does not answer is a failed row
        # whose log holds the probes and the journal.
        report("Restarting behind the health check")
        healthy, evidence = self._restart_and_probe()
        if not healthy:
            if put_unit_back is not None:
                put_unit_back()
                self._restart_and_probe()
            raise app_unhealthy(self.domain, evidence=evidence)
        return replace(result, restarted=(self.app_name,) if self.app_name else ())

    def _update_release(self, report: StepReporter) -> UpdateResult:
        """
        Build the latest source as a new release and activate it behind the health gate.

        Args:
            report: Called as each step begins.

        Returns:
            What was done.

        Raises:
            DeploymentError: When the new release did not pass its health
                check; the previous one is active and restarted by then.
        """
        report("Fetching into a new release")
        self._step_fetch_release()
        self._sandbox_by_default(report)
        self._ensure_system_packages(report)

        # A cancellation is honoured between the steps as well as inside the
        # commands of the install and the build: a step that cannot be
        # interrupted finishes, and the next one never starts.
        cancellation.checkpoint()
        report("Installing dependencies")
        with self._in_phase(BuildPhase.INSTALL):
            self._install_release_dependencies()

        cancellation.checkpoint()
        with self._in_phase(BuildPhase.INSTALL):
            prisma_updated = self._update_prisma(report)

        cancellation.checkpoint()
        report("Building")
        with self._in_phase(BuildPhase.BUILD):
            self.build()

        cancellation.checkpoint()
        self._set_permissions()
        # Last before anything serves it: a failing hook leaves current where
        # it is, and the release is abandoned like a failed build.
        cancellation.checkpoint()
        self._run_pre_deploy_hooks(report)

        report(f"Activating release {self._require_staged().id}")
        put_unit_back = self._unit_edits()
        activated = False
        try:
            self._activate_release()
            activated = True
        finally:
            if put_unit_back is not None and not activated:
                # What served before is put back, the unit with it.
                put_unit_back()
                self._restart_and_probe()

        return self._update_result(prisma_updated)

    def _ensure_system_packages(self, report: StepReporter) -> None:
        """
        Install the system packages the operator declared for this application, if any is missing.

        Done before anything is installed or built, as root and outside the build sandbox: the
        names are the operator's (a repository cannot ask for one), and what the build then links
        against is on the host. A failure stops the update here, and what served before keeps
        serving.

        Args:
            report: Called when something is installed.

        Raises:
            DeploymentError: The package manager failed, or does not install on this host.
        """
        from noust.managers.app_packages import AppPackages

        installed = AppPackages(store=self.store, runner=self._runner, fs=self._fs).ensure(
            self.domain or "", logger=self.logger
        )
        if installed:
            report(f"Installed system packages: {', '.join(installed)}")

    def _update_prisma(self, report: StepReporter) -> bool:
        """
        Regenerate the Prisma client and apply migrations, when the project uses Prisma.

        Declared deploy hooks replace the automatic migration: what the
        project says explicitly wins, and the log says so.

        Args:
            report: Called when the step begins.

        Returns:
            Whether Prisma was updated.

        Raises:
            DeploymentError: The migration failed (see :meth:`run_prisma_migrate`).
        """
        if not self.has_prisma:
            return False
        report("Updating Prisma")
        self.generate_prisma()
        if self._hook_set().declared:
            self.logger.substep(
                "Deploy hooks are declared, so Prisma's automatic migration does not run"
            )
        else:
            self.run_prisma_migrate(deploy=True)
        return True

    def _forget_hooks(self) -> None:
        """Start a deployment with nothing read or run yet."""
        self._deploy_hooks_state = DeploymentHooks()

    def _hook_set(self) -> HookSet:
        """
        Read the hooks this deployment runs, once, from the tree being built.

        Returns:
            The operator's, the repository's ``noust.yaml``, or none.

        Raises:
            ValidationError: The document that applies is not valid, or the
                application's type has no hooks (a static site).
        """
        return self.deploy_hooks.resolve(
            self._app_row(),
            self.tree_root,
            self.store,
            app_type=self.APP_TYPE,
            serves=bool(self.start_command()),
            release=self._release_hook(),
        )

    def _hook_executor(self) -> CommandHookExecutor:
        """
        Run hooks where a migration runs: the release phase, in the tree being built.

        Returns:
            The executor: as the application, with its ``.env``, in the
            sandbox when it builds there; as this process otherwise, which is
            what its builds run as too.
        """
        return CommandHookExecutor(
            run=lambda argv, *, cwd, timeout: self._run(
                argv, cwd=cwd, timeout=timeout, phase=BuildPhase.RELEASE
            ),
            root=self.build_path,
            app_type=self.APP_TYPE,
        )

    def _run_pre_deploy_hooks(self, report: StepReporter | None = None) -> None:
        """
        Run the ``pre_deploy`` hooks, before anything serves the new version.

        Args:
            report: Called when there are hooks to run.

        Raises:
            HookFailedError: A hook failed; nothing was switched over.
            ValidationError: The hooks are not valid, or not for this type.
        """
        if not self._hook_set().pre_deploy:
            return
        if report is not None:
            report("Running pre-deploy hooks")
        self.deploy_hooks.run_pre(self._hook_executor(), self.logger)

    def _run_post_deploy_hooks(self, report: StepReporter | None = None) -> None:
        """
        Run the ``post_deploy`` hooks, once the new version passed its gate.

        A failure does not fail the deployment: what serves now stays, and
        the deployment is recorded as deployed with warnings.

        Args:
            report: Called when there are hooks to run.
        """
        if not self._hook_set().post_deploy:
            return
        if report is not None:
            report("Running post-deploy hooks")
        self.deploy_hooks.run_post(self._hook_executor(), self.logger)

    def _with_hooks(self, steps: list[DeployStep]) -> list[DeployStep]:
        """
        Run the hooks inside a deploy pipeline, without steps of their own.

        ``pre_deploy`` runs at the end of the step that hands the tree to the
        service (the last before anything is exposed), ``post_deploy`` at the
        end of the pipeline's last step. Neither adds a step: an application
        without hooks deploys exactly as before, line for line.

        Args:
            steps: The deployer's pipeline.

        Returns:
            The same pipeline, with the hooks attached.
        """
        if not steps:
            return steps
        at = next((i for i, step in enumerate(steps) if step.run == self._set_permissions), 0)
        end = len(steps) - 1

        def attach(index: int) -> DeployStep:
            step = steps[index]

            def run() -> None:
                step.run()
                if index == at:
                    self._run_pre_deploy_hooks()
                if index == end:
                    self._run_post_deploy_hooks()

            return replace(step, run=run)

        for index in {at, end}:
            steps[index] = attach(index)
        return steps

    def _with_hook_result(self, result: UpdateResult) -> UpdateResult:
        """
        Add what the hooks did to an update's result.

        Args:
            result: The update's result.

        Returns:
            The result with the hooks that ran, whether the schema changed and
            the warnings.
        """
        state = self.deploy_hooks
        return replace(
            result,
            hooks=tuple(state.runs),
            schema_changed=state.schema_changed,
            warnings=tuple(state.warnings),
        )

    def _update_result(self, prisma_updated: bool) -> UpdateResult:
        """
        Describe what an update did.

        Args:
            prisma_updated: Whether Prisma was updated.

        Returns:
            The result for the caller.
        """
        start_command = self.start_command()
        return UpdateResult(
            package_manager=self.package_manager,
            prisma_updated=prisma_updated,
            is_static=not bool(start_command),
            start_command=start_command,
        )
