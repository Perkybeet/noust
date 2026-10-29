# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The pipeline of a deploy and the activation of a release: steps, health gate, records and pruning.
"""

from __future__ import annotations

import shlex
import sqlite3
from dataclasses import replace
from datetime import datetime

from noust.core import cancellation, delivery
from noust.core.appdir.permissions import hand_over_tree
from noust.core.exceptions import (
    CertificateError,
    DeploymentError,
    NoustError,
    RolledBackError,
    SourceError,
)
from noust.core.fs import DryRunFileSystem
from noust.core.logger import Icons
from noust.core.store import DEFAULT_KEEP_RELEASES, AppStatus, ReleaseStatus
from noust.core.variables.predefined import RELEASE_ENV_NAME
from noust.deployers.base.site_service import SiteServiceMixin
from noust.deployers.base.state import GIT_LOG_TIMEOUT, _dotenv_files
from noust.deployers.bluegreen import adopt_for_new_app
from noust.deployers.helpers.build_config import project_dir
from noust.deployers.helpers.health import wait_until_healthy
from noust.deployers.helpers.health_gate import HealthGate
from noust.deployers.helpers.process_declaration import declare_for_release
from noust.deployers.helpers.release_book import ReleaseBook
from noust.deployers.helpers.release_build import (
    REPO_CACHE_DIR,
    StagedRelease,
    discard_release,
    reuse_dependencies,
    stage_release,
    stamp_installed_dependencies,
)
from noust.deployers.helpers.sandbox import BuildPhase
from noust.deployers.helpers.seed_files import seed_files
from noust.deployers.pipeline import DeployStep
from noust.deployers.recorder import DeploymentRecorder, note_source, recorder_for
from noust.managers.app_processes import Declaration, ProcessManager
from noust.managers.source_manager import git_tree_owner


class ReleaseMixin(SiteServiceMixin):
    """
    The pipeline of a deploy and the activation of a release: steps, health gate, records and pruning.
    """

    def build_pipeline(self) -> list[DeployStep]:
        """
        Describe the deployment as an ordered list of steps.

        Subclasses override this to add, drop or reorder steps instead of
        rewriting the whole ``deploy`` method, which is how static and vite
        deployments used to end up with their own copies of the workflow.

        Returns:
            The steps to execute, each with the undo that reverses it.
        """
        if self.uses_releases:
            return self._release_pipeline()
        return [
            DeployStep(
                title="Fetching source code",
                icon=Icons.DOWNLOAD,
                run=self._step_fetch,
                undo=self.remove_source,
            ),
            DeployStep(
                title="Installing dependencies",
                icon=Icons.PACKAGE,
                run=self._phased(BuildPhase.INSTALL, self.install_dependencies),
            ),
            DeployStep(
                title="Building application",
                icon=Icons.BUILD,
                run=self._phased(BuildPhase.BUILD, self.build),
            ),
            DeployStep(
                title="Setting permissions",
                icon=Icons.LOCK,
                run=self._set_permissions,
            ),
            DeployStep(
                title="Creating site configuration",
                icon=Icons.GLOBE,
                run=lambda: self.create_site(with_ssl=False),
                undo=self.remove_site,
            ),
            DeployStep(
                title="Obtaining SSL certificate",
                icon=Icons.LOCK,
                run=self._step_certificate,
                skip_if=lambda: not self.ssl,
            ),
            DeployStep(
                title="Creating systemd service",
                icon=Icons.GEAR,
                run=self.create_service,
                undo=self.remove_service,
            ),
            DeployStep(
                title="Starting application",
                icon=Icons.ROCKET,
                run=self._step_start,
            ),
        ]

    def _release_pipeline(
        self,
        build_steps: list[DeployStep] | None = None,
        *,
        with_service: bool = True,
    ) -> list[DeployStep]:
        """
        Describe a deployment that builds a release and activates it behind a health gate.

        The release is fetched, linked to ``shared/``, installed and built in
        its own directory; the unit and the site are written against
        ``current``; activation swaps ``current`` and restarts, and a release
        that does not answer is rolled back to the one before it.

        For a new application every step keeps its undo, so a failed first
        deploy leaves nothing behind. For one that exists, the site and the
        unit are left alone on failure: they point at ``current``, which the
        rollback has already pointed back at a release that works.

        Args:
            build_steps: What turns the fetched source into something that
                runs. Defaults to installing and building.
            with_service: Whether the application runs as a unit.

        Returns:
            The steps to execute, each with the undo that reverses it.
        """
        new_app = self._is_new_deployment
        steps = [
            DeployStep(
                title="Fetching source into a new release",
                icon=Icons.DOWNLOAD,
                run=self._step_fetch_release,
                undo=self._undo_release_fetch,
            ),
        ]
        steps += (
            build_steps
            if build_steps is not None
            else [
                DeployStep(
                    title="Installing dependencies",
                    icon=Icons.PACKAGE,
                    run=self._phased(BuildPhase.INSTALL, self._install_release_dependencies),
                ),
                DeployStep(
                    title="Building application",
                    icon=Icons.BUILD,
                    run=self._phased(BuildPhase.BUILD, self.build),
                ),
            ]
        )
        steps += [
            DeployStep(
                title="Setting permissions",
                icon=Icons.LOCK,
                run=self._set_permissions,
            ),
            DeployStep(
                title="Creating site configuration",
                icon=Icons.GLOBE,
                run=lambda: self.create_site(with_ssl=False),
                undo=self.remove_site if new_app else None,
            ),
            DeployStep(
                title="Obtaining SSL certificate",
                icon=Icons.LOCK,
                run=self._step_certificate,
                skip_if=lambda: not self.ssl,
            ),
        ]
        if with_service:
            steps.append(
                DeployStep(
                    title="Creating systemd service",
                    icon=Icons.GEAR,
                    run=self.create_service,
                    undo=self.remove_service if new_app else None,
                )
            )
        steps.append(
            DeployStep(
                title="Activating release",
                icon=Icons.ROCKET,
                run=self._step_activate,
            )
        )
        if with_service and new_app and self._starts_without_downtime():
            # Last, once the first release answers: the application is the
            # single unit it was created as until this converts it, and
            # converting is undone whole when a step fails.
            steps.append(
                DeployStep(
                    title="Enabling updates without downtime",
                    icon=Icons.ROCKET,
                    run=self._step_zero_downtime,
                )
            )
        return steps

    def _starts_without_downtime(self) -> bool:
        """
        Tell whether a new application of this deployer starts in zero-downtime mode.

        Returns:
            True when the server's configuration asks for it
            (``deploy.zero_downtime``), nginx serves the application and it is
            not a pull request's preview, which is thrown away.
        """
        return (
            delivery.current().zero_downtime
            and self.webserver == "nginx"
            and not self._preview_parent
        )

    def _step_zero_downtime(self) -> None:
        """
        Start the application as two instances, so its next update never cuts it.

        Never fails the deployment: an application that cannot run twice (its
        site has routes of its own, the next port is taken) keeps running as
        the one unit it has, and the log says why.
        """
        # The old unit is not left running to drain: nothing but the health
        # gate's own probes has used it yet.
        adopt_for_new_app(
            self.domain,
            logger=self.logger,
            store=self.store,
            engine=lambda app, _logger: self._blue_green(app, drain=False),
        )

    def _step_fetch_release(self) -> None:
        """
        Put the source in a new release and link it to what releases share.

        The ``.env`` and the persistent paths are linked before anything is
        installed or built, because both may need them: a build that reads
        ``DATABASE_URL`` or a postinstall that writes into ``storage/``.
        """
        if self._staged is None:
            self.logger.substep(f"Source: {self.source}")
            # A clone of a large repository is the first thing an operator
            # cancels: the release it was filling is discarded by the staging.
            with cancellation.interruptible("fetching the source"):
                self._staged = stage_release(
                    self.source,
                    self.branch,
                    releases=self.releases,
                    source_manager=self.source_manager,
                    logger=self.logger,
                )
        staged = self._staged
        self._record_release_row(staged)

        # All three describe the code that was just fetched.
        self._prime_build()
        self._reconcile_registration()
        self._detect_nginx_config()
        self._prepare_env()
        self._refresh_variables()
        self._announce_detection()
        note_source()

        self._seed_shared_files()
        links = staged.manager.link_shared(staged.path, self._persistent_paths())
        for path in links.linked:
            self.logger.substep(f"Linked {path} to shared/{path}")

    def _install_release_dependencies(self) -> None:
        """
        Install the release's dependencies, or take them from the active release.

        The copy happens before :meth:`pre_install`, because the Python
        deployer creates its virtual environment there and a copy into an
        existing directory would nest inside it.
        """
        staged = self._require_staged()
        active = staged.manager.current()
        if self.skip_build_cache():
            # A build from scratch installs what the lockfile says, not what the
            # serving release happens to hold.
            active = None
        base = self.build_config().base_directory
        if active is not None and base:
            # The project's own dependencies: the same subdirectory of the
            # release that serves. A workspace's, at the root, are installed.
            active = replace(active, path=active.path / base)
        # The versions the stamps carry are those of the runtime the install
        # runs with, which a pinned runtime changes.
        runtime_env = self._runtime_environment(sandboxed=False)
        # A different install command is a different install.
        install = self.build_config().install
        extra = {"install": shlex.join(install)} if install else None
        self.dependencies_reused_from = reuse_dependencies(
            self.build_path,
            active,
            runner=self.runner,
            fs=self.fs,
            logger=self.logger,
            env=runtime_env or None,
            extra=extra,
        )
        if self.dependencies_reused_from is None:
            self.install_dependencies()
            # So the next deploy can tell whether reusing this install would
            # still match the runtime it was built with.
            stamp_installed_dependencies(
                self.build_path,
                runner=self.runner,
                fs=self.fs,
                logger=self.logger,
                env=runtime_env or None,
                extra=extra,
            )
            return

        self.pre_install()
        self.logger.substep(f"Dependencies reused from {self.dependencies_reused_from}")
        self.post_install()

    def _step_activate(self) -> None:
        """Activate the release behind the health gate, then record that it runs."""
        self._activate_release()
        self._mark_running()

    def _require_staged(self) -> StagedRelease:
        """
        Return the release being built.

        Returns:
            The staged release.

        Raises:
            DeploymentError: When no release was staged, which means a step
                ran out of order.
        """
        if self._staged is None:
            raise DeploymentError(
                "No release is being built",
                details="The fetch step stages the release; it has to run first.",
            )
        return self._staged

    def _activate_release(self) -> None:
        """
        Point ``current`` at the new release, restart, and keep it only if it answers.

        A release that does not pass the health check is recorded as failed
        and the previous one is activated and restarted again, so the
        application goes back to what served a moment ago. The deployment
        then fails with the probe's and the journal's own output.

        An application in zero-downtime mode is switched by
        :class:`~noust.deployers.bluegreen.BlueGreen` instead: the idle
        instance starts on the release and takes the traffic once it
        answers, and ``current`` moves last.

        Raises:
            DeploymentError: When the release did not pass the health check,
                whether or not there was a release to go back to.
        """
        staged = self._require_staged()
        # The point of no return of a cancellation: from here the release is
        # switched in behind the health gate, which finishes or puts back what
        # served, and is never interrupted halfway.
        cancellation.commit(f"activating release {staged.id}")
        zero_downtime = self._zero_downtime_app()
        if zero_downtime is not None:
            self._refuse_blue_green_in_subdirectory()
            # Recorded and written before the idle instance starts: the switch restarts the
            # processes on the release and proves they stay up before the traffic moves.
            declared = self._declare_processes()
            try:
                self._blue_green(zero_downtime).activate(staged.path, staged.manager)
            except NoustError:
                self._record_release_status(staged, ReleaseStatus.FAILED)
                if declared is not None:
                    declared.rollback()
                raise
            if declared is not None:
                declared.commit()
            self._record_release_active(staged)
            self._prune_releases(staged)
            return

        previous = staged.manager.activate(staged.path)
        if previous is None:
            self.logger.substep(f"Activated release {staged.id}")
        else:
            self.logger.substep(f"Activated release {staged.id} (was {previous.id})")

        # After current points at the release (a first deploy has none before) and before the
        # restart, which restarts the declared processes with the unit and judges them with it.
        declared = self._declare_processes()
        try:
            healthy, evidence = self._restart_and_probe()
        except NoustError:
            if declared is not None:
                declared.rollback()
            raise
        if healthy:
            if declared is not None:
                declared.commit()
            self._record_release_active(staged)
            self._prune_releases(staged)
            return

        self._record_release_status(staged, ReleaseStatus.FAILED)
        # Before going back: the release that served knew nothing of these processes, and the
        # restart that follows must not ask for them.
        if declared is not None:
            declared.rollback()
        if previous is None:
            raise DeploymentError(
                f"Release {staged.id} did not pass its health check",
                details=evidence,
            )

        self.logger.warning(f"Release {staged.id} did not pass its health check")
        self.logger.substep(f"Going back to release {previous.id}")
        staged.manager.activate(previous.path)
        restored, _ = self._restart_and_probe()
        state = "is active again" if restored else "is active again but is not answering either"
        # Only a previous release that answers makes this a rollback: one that
        # does not leaves the application down, which is a plain failure.
        error_class = RolledBackError if restored else DeploymentError
        raise error_class(
            f"Release {staged.id} did not pass its health check; release {previous.id} {state}",
            details=evidence,
        )

    def _declare_processes(self) -> Declaration | None:
        """
        Make the processes a repository declares besides ``web`` and ``release`` run beside the application.

        They are recorded and their units written, and nothing is started: the health gate that
        activates the release restarts every copy with the web unit and judges them with it, and the
        caller commits the declaration when the release holds or rolls it back when it does not. A
        process the operator defined under the same name is theirs and stays so.

        Never fails the deployment: a Procfile that cannot be used, an application deployed in place
        (processes run from the releases layout), a declared process that cannot be a unit, or a
        unit that cannot be written is said in the log and the application is deployed without it.

        Returns:
            What to commit or roll back; None when there is nothing to judge.
        """
        if not self.domain or isinstance(self.fs, DryRunFileSystem) or not self.start_command():
            return None
        manager = ProcessManager(
            store=self.store, services=self.service_manager, runner=self.runner, log=self.logger
        )
        return declare_for_release(
            manager,
            self.domain,
            self.processes(),
            procfile_applies=self._procfile_applies(),
            releases_layout=self.uses_releases,
            log=self.logger,
        )

    def _restart_and_probe(self) -> tuple[bool, str]:
        """
        Restart the application on whatever ``current`` points at, and ask if it is up.

        Returns:
            Whether it is healthy, and when it is not, the evidence: the
            failed probes and the unit's journal, verbatim.
        """
        return self._health_gate().restart_and_probe()

    def _health_gate(self) -> HealthGate:
        """
        Build the gate a release must pass to stay active.

        A service answers over HTTP, on the application's health path, with a
        status its expectation accepts: without settings of its own, any
        response below 500 means the process started and routes requests,
        redirects included. A site without a service is checked by the
        deployer's own :meth:`health_check`, which looks for the files it
        serves.

        Returns:
            The gate, the same one an operator's rollback goes through.
        """
        serves = bool(self.start_command())
        check = self._health_check_settings()
        return HealthGate(
            unit=self.app_name if serves and self.app_name else None,
            url=check.url(self.port) if serves else None,
            check=check,
            services=self.service_manager,
            logger=self.logger,
            # Looked up here, at call time, so it is the one this module holds.
            probe=wait_until_healthy,
            restart=self.restart,
            files_check=None if serves else self.health_check,
        )

    def _undo_release_fetch(self) -> None:
        """
        Undo the fetch of a release deploy that failed.

        A new application whose directory this deploy created (or found
        empty) leaves nothing behind. Anything else loses only the release
        this deploy staged: the releases, ``shared/`` and whatever else the
        directory held before are not this deploy's.
        """
        target = self.deploy_target
        if self._is_new_deployment and (target is None or not target.had_files):
            self.remove_source()
            return
        staged = self._staged
        if self._is_new_deployment and staged is not None:
            active = staged.manager.current()
            if active is not None and active.id == staged.id:
                # A first release that failed its gate had nothing to go back
                # to, so current was made by this deploy and points at it.
                self.fs.remove(staged.manager.current_link)
        self._abandon_release()

    def _abandon_release(self) -> None:
        """
        Throw away a release that failed before it could serve.

        Recorded as failed and removed from disk, unless ``current`` points
        at it: whatever went wrong, the release that is serving is never the
        one deleted.
        """
        staged = self._staged
        if staged is None:
            return
        self._record_release_status(staged, ReleaseStatus.FAILED)
        discard_release(staged.path, releases=staged.manager, logger=self.logger)

    def _persistent_paths(self) -> list[str]:
        """
        Return the paths every release shares through ``shared/``.

        Returns:
            What this deployment was configured with, or else what the
            application has recorded.
        """
        if self._persistent_request is not None:
            paths = list(self._persistent_request)
        else:
            app = self._app_row()
            paths = list(app.persistent_paths) if app is not None else []
        # A seeded file is shared like any persistent path: written once into
        # shared/ and linked into every release.
        paths.extend(path for path in self._seed_files if path not in paths)
        # The files Noust keeps for the application are linked into every
        # release like a persistent path: a new release has them before it starts.
        from noust.managers.app_mounts import MountManager

        mounted = MountManager(store=self.store).host_targets(self.domain)
        return [*paths, *[target for target in mounted if target not in paths]]

    def _keep_releases(self) -> int:
        """
        Return how many releases the application keeps on disk.

        Returns:
            The recorded retention, never less than 1.
        """
        app = self._app_row()
        keep = app.keep_releases if app is not None else DEFAULT_KEEP_RELEASES
        return max(1, keep or DEFAULT_KEEP_RELEASES)

    def _release_book(self) -> ReleaseBook:
        """
        Returns:
            The writer of this application's release rows, over this deployer's store, logger
            and filesystem.
        """
        return ReleaseBook(self.store, self.logger, self.fs, self.domain)

    def _record_release_row(self, staged: StagedRelease) -> None:
        """
        Remember a release that was just staged.

        Args:
            staged: The release.
        """
        self._release_book().record_row(staged)

    def _record_release_status(self, staged: StagedRelease, status: ReleaseStatus) -> None:
        """
        Record how a release ended up.

        Args:
            staged: The release.
            status: Its new status.
        """
        self._release_book().record_status(staged, status)

    def _record_release_active(self, staged: StagedRelease) -> None:
        """
        Record that a release is now the active one.

        Args:
            staged: The release.
        """
        self._release_book().record_active(staged)

    def _prune_releases(self, staged: StagedRelease) -> None:
        """
        Delete the releases beyond the application's retention, and forget them.

        Args:
            staged: The release just activated.
        """
        self._release_book().prune(staged, self._keep_releases())

    def _seed_shared_files(self) -> None:
        """
        Write the files this deployment is seeded with into ``shared/``, once.

        A file that is already there is the operator's by now and is left alone.

        Raises:
            DeploymentError: ``shared/`` or a parent of a file is a link or a file.
        """
        if self._seed_files:
            seed_files(
                self.releases.shared_dir,
                self._seed_files,
                self._seed_modes,
                fs=self.fs,
                logger=self.logger,
                label="shared/",
            )

    def _step_fetch(self) -> None:
        """Fetch the source, then read the configuration that ships with it."""
        self.fetch_source()
        if self._seed_files:
            # In place nothing links a shared directory in: the file lives in the tree.
            seed_files(
                self.tree_root,
                self._seed_files,
                self._seed_modes,
                fs=self.fs,
                logger=self.logger,
                label="",
            )

        # All three of these describe the code that was just fetched, so they
        # cannot run any earlier.
        self._prime_build()
        self._reconcile_registration()
        self._detect_nginx_config()
        self._prepare_env()
        self._refresh_variables()
        self._announce_detection()
        note_source()

    def _announce_detection(self) -> None:
        """
        Say in one line what the deployment understood, before any of it runs.

        A build log that opens with the steps leaves a person whose first deployment fails
        unable to tell whether their project was read as they meant it. Once the source is
        fetched the type knows the commands and the port, so they are said here: which type,
        from which file, and what it will install, build and start.
        """
        found = self._detection_evidence()
        said = [f"Detected {self.DISPLAY_NAME}{f' ({found})' if found else ''}."]
        install = self.install_command()
        build = self.build_command()
        start = self.start_command()
        if install:
            said.append(f"Install: {shlex.join(install)}.")
        if build:
            said.append(f"Build: {shlex.join(build)}.")
        if start:
            said.append(f"Start: {start}.")
            if self.port:
                said.append(f"Port {self.port}.")
        elif not install and not build:
            said.append("It is served as static files.")
        self.logger.info(" ".join(said))

    def _detection_evidence(self) -> str:
        """
        Name the file of the source that made the type apply.

        Returns:
            The first of the type's ``DETECTION_FILES`` the source holds, or an empty string
            when none is there (a type chosen by hand, a build block's directory).
        """
        roots = [self.tree_root]
        base = self.build_config().base_directory
        if base:
            roots.insert(0, project_dir(self.tree_root, base))
        for name in self.DETECTION_FILES:
            for directory in roots:
                if next(directory.glob(name), None) is not None:
                    return name
        return ""

    def _step_certificate(self) -> None:
        """
        Obtain a certificate and re-render the site with TLS enabled.

        A certificate failure is not a deployment failure: the application is
        still reachable over HTTP, and forcing a rollback here would throw away
        a working build because DNS had not propagated yet. When a certificate
        is already on disk - the order failed because one new alias does not
        resolve yet - the site keeps TLS with it.
        """
        try:
            self.obtain_certificate()
        except (CertificateError, NoustError) as e:
            # What the authority said, word for word, is kept on the deployment: the console
            # shows it under "What certbot said" when the application ends up on HTTP.
            self._certificate_outcome = ("failed", str(e.output or e.details or e.message))
            if self.has_certificate():
                # Extending the lineage to a name whose DNS is not ready must
                # not take TLS off the names the certificate already covers.
                self.logger.warning(f"The certificate could not be extended: {e}")
                self.logger.substep("The existing certificate keeps serving what it covers")
                self._ssl_obtained = True
                self.create_site(with_ssl=True)
                return
            self.logger.warning(f"SSL certificate failed: {e}")
            self.logger.warning("Continuing deployment without SSL...")
            self.logger.substep("Application will be available via HTTP only")
            return

        self._certificate_outcome = ("issued", "")
        self._ssl_obtained = True
        self.logger.substep("Updating site configuration with SSL")
        self.create_site(with_ssl=True)

    def _record_certificate(self, deployment_id: int | None) -> None:
        """
        Write how the certificate of this run ended on its deployment row.

        Never fails the deployment it describes: the row is history, and a failure to write it
        is reported and nothing more.

        Args:
            deployment_id: The row; nothing is written without one (a rehearsal, or recording
                failed) or when no certificate was ordered.
        """
        outcome, self._certificate_outcome = self._certificate_outcome, None
        if deployment_id is None or outcome is None:
            return
        try:
            self.store.record_deployment_certificate(deployment_id, *outcome)
        except (NoustError, sqlite3.Error) as exc:
            self.logger.warning(f"Could not record how the certificate ended: {exc}")

    def _step_start(self) -> None:
        """Start the service and record that the deployment succeeded."""
        self.start()
        self._mark_running()

    def _mark_running(self) -> None:
        """Record that the deployment succeeded and the application runs."""
        if self._app_record is not None:
            self._app_record.status = AppStatus.RUNNING.value
            self._app_record.ssl_enabled = self._ssl_obtained
            self._app_record.deployed_at = datetime.now().isoformat()
            self.store.update_app(self._app_record)

        if self.app_name:
            self.store.update_service_status(self.app_name, active=True, enabled=True)

    def _set_permissions(self) -> None:
        """
        Hand the deployed tree over to the account the service runs as.

        The deployment ran as root, so without this step the service user
        cannot write into its own app directory and the unit fails at start
        with EACCES.

        On releases that is the new release and ``shared/``, where uploads and
        the ``.env`` live; the repository cache and the application directory
        itself stay root's, because the service never writes there.
        """
        # Written first, so that the hand-over below covers it: a file created after it is
        # root's, the one thing in the tree the service account does not own.
        self._write_release_env()
        if not self.uses_releases:
            hand_over_tree(
                self.app_path,
                user=self._service_account()[0],
                group=self._service_account()[1],
                runner=self.runner,
                fs=self.fs,
                logger=self.logger,
                env_files=self._env_files(),
            )
            return

        shared = self.releases.shared_dir
        trees = [self.tree_root] + ([shared] if shared.is_dir() else [])
        for tree in trees:
            hand_over_tree(
                tree,
                user=self._service_account()[0],
                group=self._service_account()[1],
                runner=self.runner,
                fs=self.fs,
                logger=self.logger,
                env_files=_dotenv_files(tree),
            )

    def _write_release_env(self) -> None:
        """
        Write what Noust says about this deployment next to the code it describes.

        ``NOUST_URL``, ``NOUST_DOMAIN``, ``NOUST_BRANCH``, ``NOUST_COMMIT`` and
        ``NOUST_RELEASE`` go in a file of the release itself, which the unit
        loads: the release is what ``current`` points at, so going back to an
        older one gives the process that release's commit with no further step.
        Only an application with a process of its own gets one: a static site
        is served from this very directory, and the file would be downloadable.
        """
        if not self.domain or not self.start_command():
            return
        values = self._deployment().environment()
        if not values:
            return
        self._env_manager.write_env_file(self.build_path / RELEASE_ENV_NAME, values)

    def _recorder(self) -> DeploymentRecorder:
        """
        Build the recorder :meth:`deploy` and :meth:`update` write history with.

        Returns:
            A recorder for this deployment attempt, wired to this deployer's
            store, logger and filesystem. One shared construction site, so the
            deploy and update paths cannot record differently.
        """
        return recorder_for(
            self,
            git_info=self._git_info,
            commit_message=self._commit_message_for_recording,
            release_id=self._release_id_for_recording,
        )

    def _commit_message_for_recording(self) -> str | None:
        """
        Read the subject line of the deployed commit, for a git source only.

        In place, that is :attr:`app_path` itself. On releases the exported
        release has no ``.git`` - it is exported without one - so this reads
        the repository cache instead, which persists across deploys.

        Returns:
            The subject (``git log -1 --format=%s``), or None when the
            checkout is not a git repository or the command fails. A missing
            subject must not cost the deployment its history row.
        """
        checkout = (self.app_path / REPO_CACHE_DIR) if self._staged is not None else self.app_path
        if not (checkout / ".git").is_dir():
            return None
        # The staged commit, not the cache's HEAD: a rebuild of an older
        # commit exports it while the cache stays on the head of the branch.
        staged = self._staged.commit if self._staged is not None else None
        try:
            # git log runs gpg.program when the tree's config asks for
            # signatures; whoever owns the tree can write that config.
            owner = git_tree_owner(checkout)
        except SourceError as exc:
            self.logger.debug(f"Not reading the commit subject: {exc.message}")
            return None
        result = self.runner.run(
            ["git", "log", "-1", "--format=%s", *([staged] if staged else [])],
            cwd=checkout,
            timeout=GIT_LOG_TIMEOUT,
            user=owner,
        )
        subject = result.stdout.strip()
        return subject if result.success and subject else None

    def _release_id_for_recording(self) -> str | None:
        """
        Read the release this deployment built, once the fetch step has staged it.

        Returns:
            The release id, or None for an in-place deployment or one where
            the fetch step has not run yet (a failure before it).
        """
        return self._staged.id if self._staged is not None else None
