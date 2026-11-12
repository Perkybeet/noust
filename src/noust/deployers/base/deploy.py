# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The deployment itself: the entry point that runs the pipeline, reports and registers.
"""

from __future__ import annotations

from noust.central import require_server_role
from noust.core import cancellation
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError
from noust.core.logger import Icons
from noust.core.store import App, AppStatus
from noust.deployers.base.update import UpdateMixin
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.summary import print_deployment_summary
from noust.deployers.helpers.target import claim_deploy_target
from noust.deployers.pipeline import DeployStep, run_pipeline
from noust.deployers.recorder import recording
from noust.managers import app_identity, unit_hardening


class DeployMixin(UpdateMixin):
    """
    The deployment itself: the entry point that runs the pipeline, reports and registers.
    """

    def deploy(self, total_steps: int = 7) -> bool:
        """
        Run the full deployment workflow.

        The workflow itself is :meth:`build_pipeline`. This method only handles
        what surrounds it: validation, the store row that tracks progress, and
        reporting. A step that fails undoes every step that ran before it,
        including the app row, so a failed first deployment leaves nothing.

        Every run is also recorded in the deployment history with its build
        log captured to a file; recording failures are reported as warnings
        and never fail the deployment itself.

        Args:
            total_steps: Ignored. The pipeline knows how many steps it has;
                the parameter is kept so existing callers still work.

        Returns:
            True if the application ended up deployed.

        Raises:
            NoustError: Whatever the failing step raised, after the rollback.
            AppBusyError: Another operation is running on the application.
        """
        require_server_role("native_deploy")
        if not self.domain:
            raise DeploymentError(
                "Deployer was not configured",
                details="Call configure(domain=..., source=...) before deploy().",
            )

        # Held from the first look at the directory to the last step: an
        # update, a migration or a second deploy of the same application
        # must not interleave with this one. The same span is what an
        # operator can cancel, until the release starts to be activated.
        with app_lock(self.domain, "deploy"), cancellation.operation_window(self.domain, "deploy"):
            return self._deploy()

    def _deploy(self) -> bool:
        """
        Run the deployment, holding the application's lock.

        Returns:
            True if the application ended up deployed.

        Raises:
            NoustError: Whatever the failing step raised, after the rollback.
        """
        self._ssl_obtained = False
        self._certificate_outcome = None

        # A redeployment must not lose its app row just because this attempt
        # failed, so only a genuinely new app registers an undo for it.
        existing = self.store.get_app(self.domain)
        is_new_deployment = existing is None
        self._is_new_deployment = is_new_deployment
        if is_new_deployment and self._procfile_decision is None:
            # Decided now, while the application has no row: once it is registered below, "has no
            # row" no longer says it is new, and its Procfile would never be read.
            self._procfile_decision = True
        self._status_before_deploy = existing.status if existing is not None else None
        self.resolve_layout(existing)
        if self.deploy_target is None:
            # Before anything is fetched: a directory that holds an
            # application must not be emptied by the fetch, nor deleted by
            # the undo of a deploy that fails.
            self.deploy_target = claim_deploy_target(
                self.app_path,
                domain=self.domain,
                existing=existing,
                replace=self._replace_existing,
            )

        self.logger.debug("Running pre-flight validation...")
        self.pre_flight_check()

        self._app_record = self._register_app_in_store(AppStatus.DEPLOYING.value)
        self._persist_initial_build()
        if is_new_deployment:
            # Created from 3.1: it builds in the sandbox from its first build;
            # a preview with the network but without production's secrets,
            # or strict when its parent is. The row goes with the
            # application's if this deployment is undone.
            build_sandbox.adopt_new_app(
                self.domain, preview=self._preview_parent is not None, store=self.store
            )
        # Created from 3.2: it runs as its own account, made before anything
        # is handed to it; one that has an account gets it back if missing.
        self._app_record.identity = app_identity.adopt_new_app(
            self.domain,
            app_name=self.app_name,
            app_type=self.APP_TYPE,
            is_static=not bool(self.start_command()),
            runner=self.runner,
            store=self.store,
            new=is_new_deployment,
        )

        if is_new_deployment:
            # Created from 4.0: its unit is written hardened from the first
            # deployment (a read-only filesystem but for its own directories,
            # no capability); the unit asks for the directives when it is made.
            unit_hardening.adopt_new_app(self.domain, store=self.store)

        self._forget_hooks()
        steps = self._with_hooks(self.build_pipeline())
        if self._leftover_unit():
            # Last, once the site serves the new files: until then the old
            # unit may still be what answers for the domain.
            steps.append(
                DeployStep(
                    title="Removing the unit a previous deployment left",
                    icon=Icons.GEAR,
                    run=self.retire_leftover_unit,
                )
            )
        if is_new_deployment:
            steps.insert(
                0,
                DeployStep(
                    title="Registering application",
                    icon=Icons.PACKAGE,
                    run=lambda: None,
                    undo=self.forget_records,
                ),
            )

        recorder = self._recorder()
        try:
            with recording(recorder, git_branch=self.branch, on_failure=self._deploy_failed):
                try:
                    run_pipeline(steps, self.logger)
                except DeploymentError as exc:
                    self.deploy_hooks.say_schema_changed(exc)
                    raise
        finally:
            self.deploy_hooks.record(
                self.store, recorder.deployment_id, self.logger, domain=self.domain
            )
            self._record_certificate(recorder.deployment_id)
        self.last_deployment_id = recorder.deployment_id

        self._report_result()
        return True

    def _deploy_failed(self, error: BaseException) -> None:
        """
        Mark a failed deployment in the application's row and say so.

        Args:
            error: What the failing step raised.
        """
        if cancellation.is_cancellation(error):
            if not self._is_new_deployment and self._app_record is not None:
                # Nothing that served was touched: the row says what it said
                # before the attempt marked it "deploying".
                self._app_record.status = self._status_before_deploy or AppStatus.RUNNING.value
                self.store.update_app(self._app_record)
            self.logger.warning(f"Deployment cancelled: {getattr(error, 'message', error)}")
            return
        if not self._is_new_deployment and self._app_record is not None:
            # The rows survive a failed redeployment; mark them honestly.
            self._app_record.status = AppStatus.FAILED.value
            self.store.update_app(self._app_record)
        self.logger.error(f"Deployment failed: {error}")

    def _report_result(self) -> None:
        """Print the summary, plus troubleshooting hints when unhealthy."""
        # A release only becomes active after passing the health gate, so a
        # second, stricter probe could only contradict what was just proven.
        healthy = True if self.uses_releases else self.health_check()
        print_deployment_summary(
            self.logger,
            domain=self.domain or "",
            app_name=self.app_name or "",
            port=self.port,
            app_path=self.app_path,
            ssl_requested=self.ssl,
            ssl_obtained=self._ssl_obtained,
        )
        if healthy:
            return

        self.logger.warning("Application started but health check failed")
        self.logger.blank()
        self.logger.info("Troubleshooting commands:")
        self.logger.info(f"  noust logs {self.domain}        # View application logs")
        self.logger.info(f"  noust status {self.domain}      # Check service status")

    def _register_app_in_store(self, status: str) -> App:
        """
        Register or update application in persistent store.

        Args:
            status: Initial app status.

        Returns:
            The created or updated App object.
        """
        return self.registrar.register_app(
            domain=self.domain or "",
            app_type=self.APP_TYPE,
            source=self.source or "",
            branch=self.branch,
            port=self.port,
            app_path=self.app_path,
            webserver=self.webserver,
            ssl_enabled=self.ssl,
            status=status,
            is_static=not bool(self.start_command()),
            env_vars=self.env_vars,
            layout=self._layout,
            persistent_paths=self._persistent_request,
            memory_max_mb=self.memory_max_mb,
            cpu_quota_percent=self.cpu_quota_percent,
            tasks_max=self.tasks_max,
            limits_given=self._resource_limits_given,
            preview_parent=self._preview_parent,
            env_secret_marks=self._initial_secret_marks,
            initial_health=self._initial_health,
        )
