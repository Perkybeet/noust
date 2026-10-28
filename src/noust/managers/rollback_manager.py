# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Going back to a backup: the rollback points of an application, the safety backup taken
first, and the rebuild of a release application that was deployed in place.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from noust.central import require_server_role
from noust.core.appdir.layout import INPLACE, RELEASES, env_file_in, layout_on_disk
from noust.core.appdir.permissions import hand_over_tree
from noust.core.applock import app_lock
from noust.core.config import Config
from noust.core.exceptions import BackupError, DeploymentError, NoustError, ServiceError
from noust.core.fs import FileSystem
from noust.core.logger import Logger
from noust.core.runner import CommandRunner
from noust.core.store import AppType, DeploymentStatus, DeploymentTrigger, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.recorder import CapturingLogger, DeploymentRecorder
from noust.managers.app_identity import ensure_service_account
from noust.managers.backup_manager import BackupManager, BackupMetadata, _deployed_at, _row_of
from noust.managers.service_manager import ServiceManager


@runtime_checkable
class _InPlaceRebuilder(Protocol):
    """
    The part of a deployer a rollback needs: rebuild what is already on disk.

    A rollback must not re-fetch the source; the restored tree *is* the source.
    Deployers that cannot rebuild in place (a static site has nothing to build)
    simply do not satisfy this protocol.
    """

    def install_dependencies(self) -> bool:
        """
        Install the application's dependencies.

        Returns:
            True when the install succeeded.
        """

    def build(self) -> bool:
        """
        Build the application in place.

        Returns:
            True when the build succeeded.
        """


class RollbackManager:
    """
    Manager for application rollbacks.

    Provides automatic pre-deploy backups and a one-step return to a previous
    state.
    """

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        """
        Initialize the rollback manager.

        Args:
            verbose: Enable verbose logging.
            runner: Command runner passed to the managers it drives.
            fs: Filesystem passed to the backup manager it drives.
        """
        self.verbose = verbose
        # Capturable so the rollback recording can mirror what this manager
        # reports into the captured log.
        self.logger = CapturingLogger(verbose=verbose)
        self.backup_manager = BackupManager(verbose=verbose, runner=runner, fs=fs)
        self.service_manager = ServiceManager(verbose=verbose, runner=runner)
        self.config = Config()
        #: The history row the last :meth:`rollback` wrote, when it wrote one.
        self.last_deployment_id: int | None = None

    def create_pre_deploy_backup(
        self,
        domain: str,
        description: str = "Pre-deploy backup",
        *,
        protect: Collection[str] = (),
        stack_databases: bool = True,
        include_databases: bool = False,
    ) -> BackupMetadata | None:
        """
        Create a backup before a deployment.

        For a Docker Compose application the backup carries a dump of the
        databases its stack runs, unless that was switched off
        (``noust app backup-before-update``, or ``backup.databases: off`` in
        the project file): going back to the previous containers does not undo
        what a migration did to the data, and this is the copy that does.

        Args:
            domain: Domain name.
            description: Backup description.
            protect: Backup ids the rotation after this backup must not
                delete: the one a rollback is about to restore.
            stack_databases: Take the stack's database dumps when the
                application is a Compose stack that has them switched on. A
                rollback's safety backup passes False: the rollback does not
                touch the databases, so there is nothing of theirs to protect.
            include_databases: Dump the databases linked to the application
                into the archive too. An update whose backup is required
                (:mod:`noust.core.update_policy_store`) asks for it: a
                migration that runs after it cannot be undone without it.

        Returns:
            The backup metadata, or None when there is nothing deployed yet.

        Raises:
            StackBackupError: A database of the stack could not be dumped, and
                the copy is on. The update that asked for this backup should
                stop: the error says how to switch the copy off.
        """
        require_server_role("Rollbacks")
        app_name = domain_to_app_name(domain)
        app_path = _deployed_at(domain, app_name, self.config)

        if not app_path.exists():
            self.logger.debug(f"No existing app to backup: {domain}")
            return None

        backup = self.backup_manager.create(
            domain=domain,
            description=description,
            include_env=True,
            tags=["pre-deploy", "auto"],
            protect=protect,
            include_databases=include_databases,
            include_stack_databases=stack_databases and self._copies_the_stack(domain),
        )
        # On releases the previous build stays on disk as a release, which is
        # what going back to a deployment activates; only in place is this
        # backup the one copy of what the previous deployment produced.
        if (
            backup is not None
            and not self.backup_manager._rehearsing
            and layout_on_disk(app_path) != RELEASES
        ):
            self._link_snapshot(domain, backup)
        return backup

    def _copies_the_stack(self, domain: str) -> bool:
        """
        Tell whether an update of this application copies its stack's databases.

        Args:
            domain: Domain name.

        Returns:
            True for a Docker Compose application that has not switched the
            copy off.
        """
        app = _row_of(domain)
        if app is None or getattr(app, "app_type", None) != AppType.DOCKER_COMPOSE.value:
            return False
        return bool(getattr(app, "backup_before_update", True))

    def _link_snapshot(self, domain: str, backup: BackupMetadata) -> None:
        """
        Record a pre-deploy backup as the snapshot of the deployment it holds.

        The tree being backed up is what the most recent finished deployment
        left, provided it succeeded: after a failed one the tree is whatever
        the failure left behind, which is nobody's snapshot. Both sides must
        know the commit and it must be the deployment's: a tree changed by
        hand since, or one an update that never finished (its row still
        ``running``, skipped below) left half-changed, is not passed off as
        the deployment's. A tree without history therefore gets no snapshot;
        going back to it is a backup restored by hand.

        Args:
            domain: The application's domain.
            backup: The backup just taken.
        """
        store = get_store()
        unfinished = {DeploymentStatus.QUEUED.value, DeploymentStatus.RUNNING.value}
        try:
            for record in store.list_deployments(domain, limit=20):
                # The operation taking this backup may already be recording.
                if record.status in unfinished:
                    continue
                if record.status != DeploymentStatus.SUCCESS.value or record.id is None:
                    return
                if not backup.git_commit or not record.git_commit:
                    return
                short = min(len(backup.git_commit), len(record.git_commit))
                if backup.git_commit[:short].lower() != record.git_commit[:short].lower():
                    return
                store.set_deployment_snapshot(record.id, backup.id)
                self.logger.debug(f"Backup {backup.id} holds deployment {record.id}")
                return
        except (NoustError, sqlite3.Error) as exc:
            # The backup exists and serves its purpose; only the link to the
            # deployment it holds is missing, which costs that deployment its
            # one-step rollback.
            self.logger.warning(f"Could not link backup {backup.id} to its deployment: {exc}")

    def rollback_target(self, domain: str, backup_id: str | None = None) -> BackupMetadata:
        """
        Name the backup a rollback of an application restores.

        Args:
            domain: Domain name.
            backup_id: The backup asked for; None for the default, the newest
                one taken by hand, or the oldest automatic one when there is
                none.

        Returns:
            The backup's metadata.

        Raises:
            BackupError: The backup asked for does not exist, or there is none.
        """
        if backup_id:
            metadata = self.backup_manager.get_backup(backup_id)
            if not metadata:
                raise BackupError(f"Backup not found: {backup_id}")
            return metadata
        all_backups = self.backup_manager.list_backups(domain=domain)
        for backup in all_backups:
            # Skip auto-generated safety backups.
            if "auto" not in backup.tags and "pre-deploy" not in backup.tags:
                return backup
        if all_backups:
            # All of them are automatic: the oldest is the most stable.
            return all_backups[-1]
        raise BackupError(f"No backups found for: {domain}")

    def rollback(
        self,
        domain: str,
        backup_id: str | None = None,
        rebuild: bool = True,
        trigger: str = DeploymentTrigger.CLI.value,
        *,
        restore_env: bool = True,
        keep: Sequence[str] = (),
        app_type: str | None = None,
        gate: Callable[[], tuple[bool, str]] | None = None,
        schema_changed_ok: bool = False,
    ) -> bool:
        """
        Roll an application back to a previous state.

        The rollback is recorded in the deployment history as **its own row**,
        with its outcome and its captured log, never by rewriting the history
        of the deploy it reverts: an operator triggered an operation and the
        history must say so. On success the domain's most recent ``success``
        row - the build this rollback discards - is additionally flipped to
        ``rolled_back`` when one exists, so the timeline shows which
        deployment stopped serving. Refusals before anything is attempted (no
        backup to roll back to) are not recorded: nothing ran.

        With a ``gate`` the rollback is held to what a deploy is held to: the
        restored tree must rebuild (a deployer that cannot rebuild in place,
        or a failed install or build, fails the rollback instead of warning),
        is handed back to the service account, and is restarted and probed by
        the gate; one that does not answer fails the rollback, and the error
        names the safety backup that holds what served before it.

        Args:
            domain: Domain name.
            backup_id: Specific backup (defaults to the latest manual one).
            rebuild: Rebuild the application after the restore.
            trigger: What initiated the rollback, recorded in the history:
                ``cli`` (the default), ``panel`` or ``webhook``.
            restore_env: Restore the ``.env`` files from the archive. False
                keeps the ``.env`` that is deployed.
            keep: Top-level names carried from the tree being replaced, such
                as ``.git`` (see :meth:`BackupManager.restore`).
            app_type: The type to rebuild as, as the store records it. None
                detects it from the restored files.
            gate: Restarts the application and judges whether it answers,
                returning that and the evidence when it does not. None starts
                the unit without judging it, as 1.x did.
            schema_changed_ok: Go back even past deployments, made after the
                backup, that changed the database's schema.

        Returns:
            True if the rollback succeeded.

        Raises:
            BackupError: If there is no backup to roll back to, or the restore
                fails.
            SchemaChangedError: Deployments made after the backup changed the
                schema, and that was not confirmed. Nothing was touched.
            DeploymentError: With a gate, the restored tree did not rebuild or
                did not pass the health check.
            AppBusyError: Another deploy, update, rollback, migration or
                restore is already running on the application.
        """
        require_server_role("Rollbacks")
        self.last_deployment_id = None
        app_type, gate = self._held_like_a_deploy(domain, app_type, gate)
        with app_lock(domain, "rollback"):
            metadata = self.rollback_target(domain, backup_id)
            # The files go back to before the deployments made since; the
            # database does not, and that is asked first. Here, where every
            # rollback to a backup passes, so no caller can forget it.
            from noust.deployers.lifecycle import require_schema_change_confirmed

            require_schema_change_confirmed(
                domain,
                target=f"backup {metadata.id}",
                schema_changed_ok=schema_changed_ok,
                since=metadata.created_at,
            )

            recorder = DeploymentRecorder(
                get_store(), domain, trigger, logger=self.logger, operation="rollback"
            )
            recorder.start()
            recorder.annotate(git_commit=metadata.git_commit, git_branch=metadata.git_branch)
            self.last_deployment_id = recorder.deployment_id

            try:
                # A safety backup of the current state, taken here so that both
                # the CLI and the panel get one: this used to be the CLI's own
                # extra step before calling rollback(), which meant a rollback
                # triggered from the panel had no way back if the restore itself
                # went wrong. A missing safety net is worth a warning, not an
                # abort - the operator already has a reason to go back.
                self.logger.info("Creating a safety backup of the current state")
                safety: BackupMetadata | None = None
                try:
                    # The target is protected from the rotation this backup
                    # triggers: when every backup is automatic the target is
                    # the oldest, exactly the one rotation would delete.
                    safety = self.create_pre_deploy_backup(
                        domain,
                        description="Pre-rollback safety backup",
                        protect=[metadata.id],
                        stack_databases=False,
                    )
                except NoustError as exc:
                    self.logger.warning(f"Could not create safety backup: {exc}")

                self.logger.info(f"Rolling back to: {metadata.id}")
                self.logger.info(f"  Created: {metadata.age}")
                if metadata.git_commit:
                    self.logger.info(f"  Commit: {metadata.git_commit}")

                self.backup_manager.restore(
                    backup_id=metadata.id,
                    restore_env=restore_env,
                    stop_service=True,
                    keep=keep,
                    stack_databases=False,
                    # Asked above, for the rollback as a whole.
                    schema_changed_ok=True,
                )
                if any(isinstance(entry.get("stack"), dict) for entry in metadata.database_backups):
                    self.logger.warning(
                        f"Backup {metadata.id} also holds a copy of the stack's databases, "
                        "taken before the update it precedes. The rollback did not restore it: "
                        "the databases keep what was written since. To put it back (the "
                        f"application is stopped meanwhile): noust backup restore {metadata.id} "
                        "--databases-only"
                    )

                app_name = domain_to_app_name(domain)

                app_path = _deployed_at(domain, app_name, self.config)
                if layout_on_disk(app_path) == RELEASES:
                    # The processes the restored release declares run beside it, as they do
                    # when a deployment or an activation makes it the one that serves.
                    self._follow_restored_release(domain)
                if rebuild and layout_on_disk(app_path) == RELEASES:
                    # A rebuild in place would install and build in the
                    # application directory, which on releases is not where the
                    # code is. A release is built by an update, behind the health
                    # gate, and going back to one is `noust releases rollback`.
                    self.logger.warning(
                        "The restored application is on the release layout and was not "
                        f"rebuilt. Run 'noust update {domain}' to build a release from it."
                    )
                elif rebuild:
                    self._rebuild(
                        domain, app_path, recorder, app_type=app_type, strict=gate is not None
                    )

                if gate is not None:
                    self.logger.info("Restarting behind the health check")
                    healthy, evidence = gate()
                    if not healthy:
                        way_back = (
                            f"What served before the rollback is in backup {safety.id}: "
                            f"noust rollback {domain} {safety.id}"
                            if safety is not None
                            else "No safety backup could be taken before the rollback."
                        )
                        raise DeploymentError(
                            f"{domain} did not pass its health check after the rollback",
                            details=f"{evidence}\n\n{way_back}",
                        )
                else:
                    try:
                        status = self.service_manager.get_status(app_name)
                        if status.get("exists"):
                            self.service_manager.start(app_name)
                    except ServiceError as exc:
                        self.logger.debug(f"Could not start service: {exc}")
            except Exception as exc:
                # Not handling: the failure is recorded and re-raised unchanged.
                recorder.finish_failure(exc)
                raise

            # The restore succeeded, so the newest successful deployment is the
            # build that just stopped serving.
            recorder.mark_previous_success_rolled_back()
            recorder.finish_success()
            return True

    def _follow_restored_release(self, domain: str) -> None:
        """
        Declare the processes of the release a restore brought back.

        Args:
            domain: The application's domain.
        """
        from noust.deployers.helpers.process_declaration import declare_restored_tree

        app = _row_of(domain)
        if app is None:
            return
        declare_restored_tree(
            app,
            _deployed_at(domain, domain_to_app_name(domain), self.config),
            store=get_store(),
            log=self.logger,
            services=self.service_manager,
        )

    def _held_like_a_deploy(
        self,
        domain: str,
        app_type: str | None,
        gate: Callable[[], tuple[bool, str]] | None,
    ) -> tuple[str | None, Callable[[], tuple[bool, str]] | None]:
        """
        Give every rollback the type and the health gate the store implies.

        Going back to a deployment passed both; going back to a backup, from
        the console or the CLI, passed neither. On the owner's central such a
        rollback of an Astro site restored its source without ``dist/`` (a
        backup never holds the build), found no type in the tree (Astro has no
        vite.config) and returned without rebuilding or saying so; with no gate
        to ask, it reported success while every page answered 404.

        Args:
            domain: The application.
            app_type: What the caller passed.
            gate: What the caller passed.

        Returns:
            The type (the store's when the caller had none) and the gate (the
            one a deploy uses when the caller had none).
        """
        try:
            store = get_store()
            app = store.get_app(domain)
        except NoustError as exc:
            self.logger.warning(f"Could not read {domain} from the store: {exc}")
            return app_type, gate
        if app is None:
            return app_type, gate
        if gate is None:
            from noust.deployers.lifecycle import health_gate_for

            def gate() -> tuple[bool, str]:
                return health_gate_for(app, store, self.logger).restart_and_probe()

        return app_type or app.app_type, gate

    def _rebuild(
        self,
        domain: str,
        app_path: Path,
        recorder: DeploymentRecorder,
        *,
        app_type: str | None,
        strict: bool,
    ) -> None:
        """
        Install and build a restored in-place tree, where it is.

        Args:
            domain: The application's domain.
            app_path: The restored application directory.
            recorder: The rollback's recording, which captures the build.
            app_type: The type to rebuild as, or None to detect it.
            strict: Fail instead of warning when the tree cannot be rebuilt,
                and hand the rebuilt tree back to the service account.

        Raises:
            DeploymentError: Strict, the deployer cannot rebuild in place, or
                the install or the build failed.
        """
        self.logger.info("Rebuilding application...")

        from noust.deployers import detect_app_type, get_deployer

        app_type = app_type or detect_app_type(app_path, verbose=self.verbose)
        if not app_type:
            message = f"Cannot tell what kind of application the restored tree of {domain} is"
            if strict:
                raise DeploymentError(
                    message,
                    details="Nothing was rebuilt. Redeploy it with: noust update " + domain,
                )
            self.logger.warning(f"{message}; nothing was rebuilt (noust update {domain})")
            return
        deployer = get_deployer(app_type, verbose=self.verbose)
        # The rebuild happens through the deployer's own logger; capturing it
        # puts the build output in this rollback's log. The interface does not
        # promise a logger, so one that is missing or not capturable is simply
        # not mirrored.
        delegate_logger = getattr(deployer, "logger", None)
        if isinstance(delegate_logger, Logger):
            recorder.also_capture(delegate_logger)
        # The source is already on disk: this is a rebuild in place, not a
        # deployment, so the restored directory is its own source.
        deployer.configure(domain, source=str(app_path), app_path=app_path)

        if not isinstance(deployer, _InPlaceRebuilder):
            message = f"The {app_type} deployer cannot rebuild in place"
            if strict:
                raise DeploymentError(
                    message, details="The files are restored but not rebuilt; nothing restarted."
                )
            self.logger.warning(f"{message}; the files are restored but not rebuilt")
            return

        try:
            built = deployer.install_dependencies() and deployer.build()
        except NoustError as exc:
            if strict:
                raise
            self.logger.warning(f"Rebuild failed: {exc}")
            self.logger.info("Application restored but may need manual rebuild")
            return
        if not built:
            if strict:
                raise DeploymentError(
                    f"The restored tree of {domain} did not build",
                    details="The install or the build reported a failure; nothing restarted.",
                )
            self.logger.warning("Rebuild failed; the application may need a manual rebuild")
            return

        if strict:
            # The install and the build ran as root; the service writes into
            # what they produced.
            account, group = ensure_service_account(domain, self.backup_manager.runner, self.config)
            hand_over_tree(
                app_path,
                user=account,
                group=group,
                runner=self.backup_manager.runner,
                fs=self.backup_manager.fs,
                logger=self.logger,
                env_files=(env_file_in(app_path, INPLACE),),
            )

    def list_rollback_points(self, domain: str) -> list[BackupMetadata]:
        """
        List the backups an application can be rolled back to.

        Args:
            domain: Domain name.

        Returns:
            The available backups, newest first.
        """
        return self.backup_manager.list_backups(domain=domain)
