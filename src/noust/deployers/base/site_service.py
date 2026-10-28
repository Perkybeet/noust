# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The site, the unit and the certificate, and starting, stopping and probing what runs.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from noust.core import app_build_store
from noust.core.exceptions import DeploymentError, ServiceError, ValidationError
from noust.core.fs import DryRunFileSystem
from noust.core.store import App, DomainKind
from noust.core.variables.predefined import RELEASE_ENV_NAME
from noust.deployers.base.build import BuildMixin
from noust.deployers.base.state import _RECORDING_ERRORS
from noust.deployers.bluegreen import BlueGreen
from noust.deployers.helpers.health import wait_until_healthy
from noust.deployers.helpers.health_gate import HealthCheck
from noust.deployers.helpers.release_build import StagedRelease
from noust.deployers.helpers.site import (
    WriteOutcome,
    has_certificate,
    load_app_site,
    refresh_app_site,
    write_template_site,
)
from noust.deployers.helpers.site import obtain_certificate as obtain_app_certificate
from noust.deployers.helpers.site import reduce_certificate as reduce_app_certificate
from noust.deployers.helpers.unit_launch import (
    attach_release_env,
    release_env_line,
    with_launch,
    with_release_env,
)
from noust.managers.apache_manager import ApacheManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ResourceLimits
from noust.validators.environment import validate_environment, validate_unit_value


class SiteServiceMixin(BuildMixin):
    """
    The site, the unit and the certificate, and starting, stopping and probing what runs.
    """

    def create_site(self, with_ssl: bool = False) -> bool:
        """
        Create web server site configuration.

        Args:
            with_ssl: If True, create config with SSL enabled.
                      If False, create config without SSL (for initial setup).

        Returns:
            True if successful.
        """
        if not self.domain:
            raise DeploymentError(
                "Deployer was not configured",
                details="Call configure(domain=..., source=...) before deploy().",
            )

        self._record_requested_domains()
        manager = self._webserver_manager()
        self._write_site(manager, with_ssl=with_ssl)
        load_app_site(manager, self.domain, self.logger)
        return True

    def _write_site(self, manager: NginxManager | ApacheManager, *, with_ssl: bool) -> WriteOutcome:
        """
        Render the site and put it on disk, without reloading anything.

        Args:
            manager: The web server manager to write through.
            with_ssl: Render the TLS server blocks.

        Returns:
            Whether it was written or the operator's own was kept.
        """
        context = self.get_template_context()
        # Override SSL setting based on parameter
        context["ssl"] = with_ssl

        template = (
            self.get_nginx_template() if self.webserver == "nginx" else self.get_apache_template()
        )
        if template != "proxy" and self._zero_downtime_app() is not None:
            raise DeploymentError(
                f"{self.domain} runs blue/green, which needs the proxy site; this release "
                f"renders the {template} template",
                details="A noust.nginx.yaml gives the site routes of its own, which the two "
                f"instances cannot share. Remove it, or turn the mode off first: "
                f"noust app zero-downtime {self.domain} off",
            )

        self.logger.substep(f"Web server: {self.webserver}")
        self.logger.substep(f"Template: {template}")
        if self.ssl:
            self.logger.substep(f"SSL: {'enabled' if with_ssl else 'pending certificate'}")

        # The operator's own site (no marker) is kept: a release update once
        # rewrote it with the template, which put back the access_log a
        # whistleblowing channel had removed on purpose.
        outcome = write_template_site(
            manager, self.domain, template=template, context=context, logger=self.logger
        )

        # A static site's row records the directory the server serves (``current/dist``, ``current/_site``),
        # not the application's: a rollback asks the gate whether that directory has a page, and
        # ``current`` alone has none when the page is a build's output.
        served = context.get("static_dir") if template == "static" else None
        self.registrar.register_site(
            domain=self.domain,
            webserver=self.webserver,
            template=template,
            app_path=Path(str(served)) if served else self.runtime_path,
            port=self.port,
            with_ssl=with_ssl,
        )
        return outcome

    def _record_requested_domains(self) -> None:
        """
        Record the ``www`` name ``--www`` asked for, as a redirect to the primary.

        A redirect rather than an alias: one canonical address is what a site
        wants, and a redirect costs the visitor nothing. An application that
        already has the name keeps it in whatever role it has - an operator
        who made it an alias with ``noust domain`` is not overruled by a flag
        on a redeploy.

        Nothing is recorded when there is no application row to attach it to,
        which is the case under ``--dry-run``: the store rolled the row back.
        """
        if not self.include_www or self.store.get_app(self.domain) is None:
            return
        www = f"www.{self.domain}"
        if any(record.domain == www for record in self.store.list_domains(self.domain)):
            return
        self.store.add_domain(self.domain, www, DomainKind.REDIRECT.value)
        self.logger.substep(f"{www} redirects to {self.domain}")

    def inspect_site(self) -> None:
        """
        Read what the site's configuration depends on from the tree being served.

        A deploy learns these as a side effect of building; re-rendering the
        site of an application that is already deployed has to learn them
        without building anything, so each deployer that renders from more
        than its settings says what it reads here. Reads only.
        """
        self._detect_nginx_config()

    def webserver_manager(self) -> NginxManager | ApacheManager:
        """
        Return the manager of the web server that serves this application.

        Returns:
            An NginxManager or an ApacheManager.
        """
        return self._webserver_manager()

    def has_certificate(self) -> bool:
        """
        Tell whether a certificate lineage for this application is on disk.

        Returns:
            True when certbot's live directory holds one for the primary.
        """
        return has_certificate(self.cert_manager, self.domain)

    def refresh_site(self, *, with_ssl: bool) -> None:
        """
        Render the site of a deployed application again, and load it.

        The same rendering a deploy does, from what is deployed: the layout the
        store records, the active release on releases, and whatever the
        deployer reads off the served tree (:meth:`inspect_site`). Nothing is
        fetched or built, and the unit is not touched. This is how a change to
        an application's domains reaches its web server.

        The whole configuration is tested before the web server is asked to
        reload. A failing test puts the previous file back, so a change that
        nginx refuses never lingers on disk to fail the next reload of some
        other site.

        Args:
            with_ssl: Render the TLS server blocks.

        Raises:
            DeploymentError: When the application is on releases and none is
                active, or the web server does not reload.
            ValidationError: When the web server rejects the configuration;
                ``details`` is its own output, and the previous configuration
                is back in place.
        """
        self.resolve_layout(self._app_row())
        if self.uses_releases and self._staged is None:
            active = self.releases.current()
            if active is None:
                raise DeploymentError(
                    f"{self.domain} has no active release to serve",
                    details=f"Build one first: noust update {self.domain}",
                )
            self.adopt_release(
                StagedRelease(path=active.path, commit=active.commit, manager=self.releases)
            )
        self.inspect_site()

        manager = self._webserver_manager()
        refresh_app_site(manager, self.domain, lambda: self._write_site(manager, with_ssl=with_ssl))

    def create_service(self) -> bool:
        """
        Create systemd service.

        Returns:
            True if successful.

        Raises:
            EnvironmentValidationError: If an environment variable or a unit
                directive value cannot be written safely into the unit file.
        """
        if not self.domain or not self.app_name:
            raise DeploymentError(
                "Deployer was not configured",
                details="Call configure(domain=..., source=...) before deploy().",
            )

        start_command = self._unit_command()

        self.logger.substep(f"Service: {self.app_name}")
        self.logger.substep(f"Command: {start_command}")

        # Only what the unit decides itself is written into it; the variables
        # given at create time are in the env file _prepare_env wrote, which the
        # unit loads. Everything interpolated into a unit is still validated:
        # a newline there starts a new directive.
        env = self._unit_environment_inline()
        env_file = self._settle_env_file()
        start_command = validate_unit_value(start_command, field="ExecStart")
        working_directory = validate_unit_value(str(self.runtime_path), field="WorkingDirectory")
        description = validate_unit_value(
            f"Noust: {self.domain} ({self.APP_TYPE})", field="Description"
        )

        zero_downtime = self._zero_downtime_app()
        if zero_downtime is not None:
            self._refuse_blue_green_in_subdirectory()
            # Two instances run from a template; the application's own unit
            # is retired while the mode is on. The row still describes how it
            # runs, which is what turning the mode off writes the unit from.
            self.logger.substep(f"Template: {self.app_name}@.service (blue/green)")
            self._blue_green(zero_downtime).write_template(command=start_command, environment=env)
            self.registrar.register_service(
                domain=self.domain,
                name=self.app_name,
                command=start_command,
                working_directory=self.runtime_path,
                environment=env,
                port=self.port,
                user=self._service_account()[0],
                group=self._service_account()[1],
            )
            self._record_applied()
            return True

        self.service_manager.create_service(
            name=self.app_name,
            command=start_command,
            working_directory=working_directory,
            environment=env,
            environment_file=str(env_file),
            description=description,
            # The application's settings, not this deployment's: a redeploy
            # writes a new unit and must not drop the limits set on the old.
            limits=ResourceLimits.of(self._app_row()),
            runtime_environment_file=str(self.runtime_path / RELEASE_ENV_NAME),
        )

        # Enable service
        self.service_manager.enable(self.app_name)

        self.registrar.register_service(
            domain=self.domain,
            name=self.app_name,
            command=start_command,
            working_directory=self.runtime_path,
            environment=env,
            port=self.port,
            user=self._service_account()[0],
            group=self._service_account()[1],
        )
        self._record_applied()

        return True

    def _unit_command(self) -> str:
        """
        Name the ``ExecStart=`` of this application's unit.

        Returns:
            The command with an absolute program: the build block's ``start``
            already has one (:meth:`_resolve_program`); the type's is resolved
            to the system's directories, or to the pinned runtime's, which
            come first.
        """
        command = self.start_command()
        if not command:
            return command
        if not self.build_config().start:
            return self._resolve_absolute_path(command)
        program = command.split(" ", 1)[0].strip('"')
        if not program.startswith("/"):
            raise DeploymentError(
                f"build.start names {program}, which is in none of the project's tools, "
                "the pinned runtime or the system's directories",
                details="Install it where the service can run it, or name the program by its "
                "path in the project (./bin/server).",
            )
        return command

    def _unit_environment_inline(self) -> dict[str, str]:
        """
        Name the variables the unit sets itself, validated.

        Returns:
            What :meth:`_unit_environment` says, and the ``PATH`` of the
            runtime the application is pinned to.

        Raises:
            EnvironmentValidationError: A value that cannot be written into a unit.
        """
        return validate_environment(
            {**self._unit_environment(), **self._runtime_unit_environment()}
        )

    def _refuse_blue_green_in_subdirectory(self) -> None:
        """
        Refuse blue/green for a project that lives in a subdirectory.

        Both instances run from a template whose working directory is the
        instance's release; there is no room in it for a base directory.

        Raises:
            DeploymentError: The build block sets a base directory.
        """
        base = self.build_config().base_directory
        if base:
            raise DeploymentError(
                f"{self.domain} runs blue/green, which cannot serve a project from the "
                f"subdirectory {base}",
                details="Both instances run from the root of their release. Turn the mode off "
                f"(noust app zero-downtime {self.domain} off), or move the project to the root "
                "of the repository.",
            )

    def _launch_record(self) -> dict[str, Any] | None:
        """
        Describe what the unit is written from, when the build block or a pinned runtime shaped it.

        Returns:
            The command, the working directory, the ``PATH`` and the runtime;
            None for an application the build block does not touch, which is
            every application from before 4.0 until its operator says
            otherwise.
        """
        runtime = self._pinned_runtime()
        build = self.build_config()
        procfile = self.processes().source == "Procfile" and self._procfile_applies()
        if not (build.start or build.base_directory or runtime or procfile):
            return None
        record: dict[str, Any] = {
            "start": self._unit_command(),
            "working_directory": str(self.runtime_path),
            "path": self._runtime_unit_environment().get("PATH"),
            "runtime": (
                {"language": runtime.language, "version": runtime.version}
                if runtime is not None
                else None
            ),
        }
        if procfile:
            # What makes an update read the Procfile again (see _procfile_applies).
            record["procfile"] = True
        return record

    def _record_applied(self) -> None:
        """Remember what the unit was just written from, so an update can tell when it is stale."""
        if not self.domain or isinstance(self.fs, DryRunFileSystem):
            return
        try:
            app_build_store.set_applied(self.domain, self._launch_record(), store=self.store)
        except _RECORDING_ERRORS as exc:
            self.logger.warning(f"Could not record what the unit runs: {exc}")

    def _unit_edits(self) -> Callable[[], None] | None:
        """
        Make the edits an update brings to the unit before the release is activated.

        The unit gets the name it has since 0.14.1 when it still carries the old prefix, then
        the launch it should have, then the line that loads what Noust says about each release.

        Returns:
            What puts the unit as it was, undoing the last edit first; None when it was
            already as it should be.
        """
        return self._undo_both(
            self._rename_legacy_unit(),
            self._undo_both(self._refresh_unit(), self._attach_release_env()),
        )

    def _rename_legacy_unit(self) -> Callable[[], None] | None:
        """
        Rename the application's unit when it still has the prefix units had before 0.14.1.

        Done here, and only here, because the activation that follows restarts the unit: the
        old one is stopped, the new one is written and enabled, and the restart starts it
        behind the same health gate as the release. A release that does not pass puts the old
        unit back.

        Returns:
            What puts the old unit back, or None when there was nothing to rename or it was not
            safe to (an application as instances, a unit that is not Noust's).
        """
        if not self.domain or isinstance(self.fs, DryRunFileSystem) or not self.start_command():
            return None
        rename = getattr(self.service_manager, "rename_legacy_unit", None)
        if rename is None or self._zero_downtime_app() is not None:
            return None
        try:
            renamed = rename(self.app_name)
        except (ServiceError, ValidationError) as exc:
            self.logger.warning(f"The unit keeps its old name: {exc}")
            return None
        if renamed is None:
            return None
        self.logger.substep(f"The unit {renamed.old} is now called {renamed.new}")

        def put_back() -> None:
            self.service_manager.restore_legacy_unit(renamed)

        return put_back

    def _refresh_unit(self) -> Callable[[], None] | None:
        """
        Rewrite the unit when the build block or the runtime changed what it launches.

        An update does not write the unit again, so a start command, a base
        directory or a runtime the operator (or the repository) changed would
        never reach the process. What the unit was last written from
        (:meth:`_record_applied`) is compared with what this deployment would
        write; when they differ the unit's ``ExecStart``, ``WorkingDirectory``
        and ``PATH`` are edited, and nothing else of it.

        Returns:
            What puts the previous unit back, for the caller to run when the
            release does not pass its health check; None when the unit was
            already as it should be (nearly always), or this application has
            no unit Noust may rewrite.
        """
        if not self.domain or isinstance(self.fs, DryRunFileSystem) or not self.start_command():
            return None
        record = self._launch_record()
        previous_record = app_build_store.get_applied(self.domain, store=self.store)
        if record == previous_record:
            return None
        env = self._unit_environment_inline()
        command = self._unit_command()
        zero_downtime = self._zero_downtime_app()
        self.logger.substep("The unit is written again: its command, directory or runtime changed")
        if zero_downtime is not None:
            self._refuse_blue_green_in_subdirectory()
            blue_green = self._blue_green(zero_downtime)
            previous_template = blue_green.write_template(command=command, environment=env)

            def put_template_back() -> None:
                self.service_manager.restore_template(blue_green.base, previous_template)
                app_build_store.set_applied(self.domain, previous_record, store=self.store)

            self._register_launch(command, env)
            app_build_store.set_applied(self.domain, record, store=self.store)
            return put_template_back

        try:
            text = self.service_manager.get_service_config(self.app_name)
        except ServiceError as exc:
            self.logger.warning(
                f"The unit is not Noust's to rewrite, so it is left as it is: {exc}"
            )
            return None
        if text is None:
            return None
        edited = with_launch(
            text,
            command=command,
            working_directory=validate_unit_value(str(self.runtime_path), field="WorkingDirectory"),
            path=env.get("PATH"),
        )
        previous_text = self.service_manager.update_config(self.app_name, edited)

        def put_unit_back() -> None:
            self.service_manager.update_config(self.app_name, previous_text)
            app_build_store.set_applied(self.domain, previous_record, store=self.store)

        self._register_launch(command, env)
        app_build_store.set_applied(self.domain, record, store=self.store)
        return put_unit_back

    def _attach_release_env(self) -> Callable[[], None] | None:
        """
        Make a unit written before 4.0 load the variables Noust says about a deployment.

        An update does not write the unit again, so an application created
        earlier gets the line the first time it is updated, as a text edit that
        changes nothing else. The same health gate that judges the release judges
        the unit, and the caller puts the old one back when it does not pass.

        Returns:
            What puts the previous unit back; None when the unit has the line
            already (nearly always), or is not Noust's to edit.
        """
        if not self.domain or isinstance(self.fs, DryRunFileSystem) or not self.start_command():
            return None
        # getattr: a duck-typed service manager of a test implements what its
        # test needs; a real one always reads its units.
        if getattr(self.service_manager, "read_unit", None) is None:
            return None
        zero_downtime = self._zero_downtime_app()
        try:
            if zero_downtime is not None:
                template = self.service_manager.get_template_config(self.app_name)
                if template is None:
                    return None
                directory = str(self._blue_green(zero_downtime).releases().colors_dir)
                edited = with_release_env(template, release_env_line(directory, instance=True))
                if edited == template:
                    return None
                self.service_manager.restore_template(self.app_name, edited)
                return lambda: self.service_manager.restore_template(self.app_name, template)
            previous = attach_release_env(
                self.service_manager, self.app_name, str(self.runtime_path)
            )
            if previous is None:
                return None
        except (ServiceError, ValidationError) as exc:
            self.logger.warning(f"The unit is left as it is: {exc}")
            return None
        self.logger.substep("The unit now loads what Noust says about each release")

        def put_back() -> None:
            self.service_manager.update_config(self.app_name, previous)

        return put_back

    @staticmethod
    def _undo_both(
        first: Callable[[], None] | None, second: Callable[[], None] | None
    ) -> Callable[[], None] | None:
        """
        Join what puts two unit edits back.

        Args:
            first: The earlier edit's undo.
            second: The later edit's undo.

        Returns:
            One that undoes the later and then the earlier; None when neither edited.
        """
        if first is None or second is None:
            return first or second

        def undo() -> None:
            second()
            first()

        return undo

    def _register_launch(self, command: str, env: dict[str, str]) -> None:
        """
        Record the unit's command and directory in the store, as a deploy does.

        Args:
            command: The ``ExecStart=``.
            env: The variables the unit sets inline.
        """
        self.registrar.register_service(
            domain=self.domain,
            name=self.app_name,
            command=command,
            working_directory=self.runtime_path,
            environment=env,
            port=self.port,
            user=self._service_account()[0],
            group=self._service_account()[1],
        )

    def obtain_certificate(self, *, force: bool = False) -> bool:
        """
        Obtain SSL certificate.

        Args:
            force: Order again although the certificate covers every name already.

        Returns:
            True if successful.
        """
        if not self.ssl:
            return True

        # Under --dry-run the store keeps no rows; include_www still shows
        # the rehearsal what the certificate would cover.
        obtain_app_certificate(
            self.cert_manager,
            self.store,
            self.domain,
            webserver=self.webserver,
            logger=self.logger,
            include_www=self.include_www,
            force=force,
        )
        return True

    def reduce_certificate(self) -> bool:
        """
        Order the certificate again with only the names the application answers on.

        Returns:
            True when it was ordered again.

        Raises:
            CertificateError: When certbot fails, with its output.
        """
        return reduce_app_certificate(
            self.cert_manager,
            self.store,
            self.domain,
            webserver=self.webserver,
            logger=self.logger,
        )

    def start(self) -> bool:
        """
        Start the application service.

        Returns:
            True if successful.
        """
        self.service_manager.start(self.app_name)
        return True

    def stop(self) -> bool:
        """
        Stop the application service.

        Returns:
            True if successful.
        """
        self.service_manager.stop(self.app_name)
        return True

    def restart(self) -> bool:
        """
        Restart the application service.

        Returns:
            True if successful.
        """
        self.service_manager.restart(self.app_name)
        return True

    def health_check(self, retries: int = 5, delay: float = 2.0) -> bool:
        """
        Check if the application is healthy.

        Args:
            retries: Number of retries.
            delay: Delay between retries in seconds.

        Returns:
            True if application is healthy.
        """
        check = self._health_check_settings()
        url = check.url(self.port)
        self.logger.substep(f"Checking: {url}")

        return wait_until_healthy(
            url,
            retries=retries,
            delay=delay,
            on_attempt=self.logger.debug,
            # Without an expectation of its own, the strict check this
            # report always made: a 200, redirects followed.
            accept=check.accepts if check.expect is not None else None,
        )

    def _health_check_settings(self) -> HealthCheck:
        """
        Read what the application's health check asks.

        Returns:
            The application's own path, expectation and timeout, over this
            deployer's path.
        """
        return HealthCheck.for_app(self._app_row(), default_path=self.get_health_check())

    def _zero_downtime_app(self) -> App | None:
        """
        Return the application's row when it activates blue/green.

        Read from the store each time, not from the row this deployment
        registered: the mode is written only by its own setter, and the
        registered row does not carry it.

        Returns:
            The row, when the application is on releases and in
            zero-downtime mode; None otherwise, and for a new application.
        """
        if not self.domain or not self.uses_releases:
            return None
        app = self.store.get_app(self.domain)
        return app if app is not None and app.zero_downtime else None

    def _blue_green(self, app: App, *, drain: bool = True) -> BlueGreen:
        """
        Build the blue/green engine over this deployer's managers.

        Args:
            app: The application's row.
            drain: Whether the instance that stops waits for its drain time.
                False for an application nobody has used yet.

        Returns:
            The engine, probing with the probe this module holds.
        """
        return BlueGreen(
            app,
            logger=self.logger,
            store=self.store,
            services=self.service_manager,
            web=self._webserver_manager(),
            # Looked up here, at call time, so it is the one this module holds.
            probe=wait_until_healthy,
            sleep=None if drain else (lambda _seconds: None),
        )
