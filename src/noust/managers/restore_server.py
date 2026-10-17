# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Restoring a server: ``noust restore-server``, one guided flow from a bundle to answering applications.

The disaster-recovery path of H-27 (a bundle of Noust's own state,
:mod:`noust.managers.self_backup`) joined to what Noust already keeps of every
application. On a new machine with Noust installed, one command:

1. **Puts Noust's state back** from the bundle (:class:`~noust.managers.self_restore.SelfRestore`):
   the store with its applications, the secrets and the keys that open them, the
   configuration, the certificates and the units. The store now says which
   applications there were, how each is backed up and where the copies are.
2. **Says what the machine still lacks** that the restore cannot bring: a
   database engine an application's database lives on, with the command that
   installs it.
3. **Brings each application back, one after the other**, from the newest
   backup there is of it, on this server or on the destinations its own
   schedule sends to: the application backup (its release and ``shared/``,
   ``.env``, database dumps and volumes when it was taken with them) and then
   the data backups of its persistent paths and volumes that are newer than it.
4. **Renders its web server site again** from the store, with the certificate
   the bundle brought, and
5. **starts it behind the same health gate as every deploy**, and records what
   the gate said: the probes and the journal, verbatim, when it does not pass.

An application that fails any step is reported and the next one goes on; there
is a final report with each application's outcome and nothing is rolled back,
because there is nothing to roll back to on a machine that was empty. The flow
is resumable: with the state already restored (``--skip-state``) it only runs
steps 2 to 5, so a destination that was unreachable can be tried again.

What it does not do, said here and in the report: install the web server, Docker
or the database engines (they are the operating system's, and the report names
what is missing), and redeploy an application nothing backed up from its
source (the report says so, and the application's definition is in the store).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.managers.backup_destinations import BackupDestinationManager
from noust.managers.backup_manager import BackupManager, BackupMetadata
from noust.managers.self_backup import SelfBackupManager
from noust.managers.self_restore import RestoreReport, SelfRestore
from noust.managers.volume_backups import VolumeBackupManager, VolumeBackupMetadata

__all__ = ["AppOutcome", "RestoreServer", "ServerRestoreReport"]

_log = logging.getLogger(__name__)

#: What an application's outcome says.
RESTORED = "restored"
NO_BACKUP = "no_backup"
FAILED = "failed"
SKIPPED = "skipped"


@dataclass
class AppOutcome:
    """
    What happened to one application.

    Attributes:
        domain: The application.
        status: ``restored`` (its data is back and it was started),
            ``no_backup`` (nothing was found to restore it from), ``failed`` or
            ``skipped`` (the operator left it out).
        source: Where its backup came from: ``local`` or a destination's name.
        backup_id: The application backup restored.
        volumes: The data backups restored on top of it.
        gate: ``passed`` or ``failed`` for the health gate; None when it did not run.
        site: ``refreshed``, or why not.
        healthy: Whether it answers (the gate passed, or there was none to ask).
        error: What went wrong, verbatim.
        evidence: The gate's probes and the journal, verbatim, when it failed.
        notes: Anything else the operator should know.
    """

    domain: str
    status: str
    source: str | None = None
    backup_id: str | None = None
    volumes: list[str] = field(default_factory=list)
    gate: str | None = None
    site: str | None = None
    healthy: bool = False
    error: str | None = None
    evidence: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The outcome as plain data, JSON-ready.
        """
        return asdict(self)


@dataclass
class ServerRestoreReport:
    """
    The final report of a server restore.

    Attributes:
        state: What putting Noust's state back did; None when it was skipped.
        apps: One outcome per application.
        engines_missing: Database engines the applications use that are not
            installed here, each with the command that installs it.
        notes: Anything else.
    """

    state: RestoreReport | None = None
    apps: list[AppOutcome] = field(default_factory=list)
    engines_missing: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every application that was asked for is back and answering."""
        return all(
            outcome.status in (RESTORED, SKIPPED) and (outcome.healthy or outcome.status == SKIPPED)
            for outcome in self.apps
        )

    def counts(self) -> dict[str, int]:
        """
        Returns:
            How many applications ended in each status.
        """
        found: dict[str, int] = {}
        for outcome in self.apps:
            found[outcome.status] = found.get(outcome.status, 0) + 1
        return found

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The report as plain data, JSON-ready.
        """
        return {
            "ok": self.ok,
            "counts": self.counts(),
            "state": self.state.to_dict() if self.state else None,
            "apps": [outcome.to_dict() for outcome in self.apps],
            "engines_missing": self.engines_missing,
            "notes": self.notes,
        }


def _moment(value: str | None) -> datetime:
    """
    Read a timestamp as an aware moment; the epoch when it cannot be read.

    Args:
        value: ISO 8601 (a backup's sidecar) or RFC 3339 (rclone), naive or not.

    Returns:
        The moment. A naive one is the server's local time.
    """
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    text = value.strip().replace("Z", "+00:00")
    head, plus, tail = text.partition("+")
    if "." in head and len(head.split(".")[1]) > 6:
        head = head.split(".")[0] + "." + head.split(".")[1][:6]
    try:
        moment = datetime.fromisoformat(head + plus + tail)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment.astimezone() if moment.tzinfo is None else moment


def _names(raw: str | None) -> list[str]:
    """
    Name the destinations a stored schedule sends to.

    Args:
        raw: The ``destinations`` column of a schedule: a JSON list of objects.

    Returns:
        Their names; empty when the column holds anything else.
    """
    try:
        entries = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(entries, list):
        return []
    return [
        str(entry["name"]) for entry in entries if isinstance(entry, dict) and entry.get("name")
    ]


class RestoreServer:
    """
    Bring a server back from a bundle of Noust's own state and what it kept of each application.

    Construct one per command; every collaborator is replaceable, which is how
    the flow is tested without a machine.
    """

    def __init__(
        self,
        *,
        logger: Logger | None = None,
        manager: SelfBackupManager | None = None,
        backups: BackupManager | None = None,
        destinations: BackupDestinationManager | None = None,
        volumes: VolumeBackupManager | None = None,
        gate: Callable[[App], tuple[bool, str]] | None = None,
        refresh_site: Callable[[App], None] | None = None,
        engine_installed: Callable[[str], bool] | None = None,
        actor: audit.Actor | None = None,
    ) -> None:
        """
        Args:
            logger: Where progress goes.
            manager: The bundle manager. A default one when None.
            backups: Restores an application from an application backup.
            destinations: Lists and fetches what a destination holds.
            volumes: Restores an application's data backups.
            gate: Starts an application and asks whether it is up. The deploy's
                own health gate by default.
            refresh_site: Renders an application's site again from the store.
            engine_installed: Says whether a database engine is installed here.
            actor: Who acts, for the audit trail.
        """
        self.logger = logger or Logger(verbose=False)
        self._manager = manager
        self._backups = backups
        self._destinations = destinations
        self._volumes = volumes
        self._gate = gate
        self._refresh_site = refresh_site
        self._engine_installed = engine_installed
        self.actor = actor

    # ------------------------------------------------------------ collaborators

    @property
    def manager(self) -> SelfBackupManager:
        """The bundle manager."""
        if self._manager is None:
            self._manager = SelfBackupManager(logger=self.logger, actor=self.actor)
        return self._manager

    @property
    def backups(self) -> BackupManager:
        """Restores application backups."""
        if self._backups is None:
            self._backups = BackupManager(verbose=False)
        return self._backups

    @property
    def destinations(self) -> BackupDestinationManager:
        """Lists and fetches what a destination holds."""
        if self._destinations is None:
            self._destinations = BackupDestinationManager()
        return self._destinations

    @property
    def volumes(self) -> VolumeBackupManager:
        """Restores data backups."""
        if self._volumes is None:
            self._volumes = VolumeBackupManager(logger=self.logger, actor=self.actor)
        return self._volumes

    def _gate_for(self, app: App) -> tuple[bool, str]:
        """
        Start an application and ask the health gate whether it is up.

        Args:
            app: The application.

        Returns:
            Whether it is healthy, and the evidence when it is not.
        """
        if self._gate is not None:
            return self._gate(app)
        from noust.deployers.lifecycle import health_gate_for

        return health_gate_for(app, get_store(), self.logger).restart_and_probe()

    def _site_for(self, app: App) -> None:
        """
        Render an application's site again from the store.

        Args:
            app: The application.
        """
        if self._refresh_site is not None:
            self._refresh_site(app)
            return
        from noust.deployers.bluegreen import ensure_upstream_file
        from noust.deployers.domains import refresh_site

        # The upstream file is the web server's, and a bundle of Noust's state does not hold it.
        ensure_upstream_file(app)
        refresh_site(app)

    def _installed(self, engine: str) -> bool:
        """
        Args:
            engine: A database engine's name.

        Returns:
            Whether it is installed on this server.
        """
        if self._engine_installed is not None:
            return self._engine_installed(engine)
        from noust.managers.database.registry import get_db_manager

        manager = get_db_manager(engine)
        return bool(manager is not None and manager.is_installed())

    # ---------------------------------------------------------------- the plan

    def plan(self, name_or_path: str, passphrase: str) -> dict[str, Any]:
        """
        Say what restoring a bundle would bring back, changing nothing.

        Reads the store inside the bundle: which applications there were, where
        each sends its backups, which engines their databases use.

        Args:
            name_or_path: The bundle.
            passphrase: Its passphrase.

        Returns:
            ``bundle`` (what it is), ``apps`` (each with its type, destinations
            and whether it has a data policy) and ``engines``.

        Raises:
            EnvelopeError: Wrong passphrase, or the bundle was altered.
            BackupError: The bundle does not hold a readable store.
        """
        import tempfile

        path = self.manager.resolve(name_or_path)
        with tempfile.TemporaryDirectory(
            prefix=".noust-restore-plan-", dir=self.manager._scratch_base()
        ) as scratch:
            root, manifest = self.manager.open_bundle(path, passphrase, Path(scratch))
            snapshot = self.manager._snapshot_in(root, manifest)
            apps: list[dict[str, Any]] = []
            engines: set[str] = set()
            if snapshot is not None:
                connection = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True, timeout=30)
                connection.row_factory = sqlite3.Row
                try:
                    destinations = {
                        row["app_domain"]: row["destinations"]
                        for row in connection.execute(
                            "SELECT app_domain, destinations FROM backup_schedules"
                        )
                    }
                    volume_policies = {
                        row["app_domain"]
                        for row in connection.execute(
                            "SELECT app_domain FROM volume_backup_policies"
                        )
                    }
                    for row in connection.execute(
                        "SELECT domain, app_type, layout FROM apps ORDER BY domain"
                    ):
                        apps.append(
                            {
                                "domain": row["domain"],
                                "type": row["app_type"],
                                "layout": row["layout"],
                                "destinations": _names(destinations.get(row["domain"])),
                                "data_policy": row["domain"] in volume_policies,
                            }
                        )
                    for row in connection.execute("SELECT DISTINCT engine FROM databases"):
                        engines.add(str(row["engine"]))
                except sqlite3.Error as exc:
                    raise ValidationError(
                        "The bundle's store could not be read", details=str(exc)
                    ) from exc
                finally:
                    connection.close()
        return {
            "bundle": path.name,
            "created_at": manifest.get("created_at"),
            "host": manifest.get("host"),
            "noust_version": manifest.get("noust_version"),
            "apps": apps,
            "engines": sorted(engines),
        }

    # --------------------------------------------------------------------- run

    def run(
        self,
        name_or_path: str,
        passphrase: str,
        *,
        force: bool = False,
        skip_state: bool = False,
        only: Sequence[str] = (),
        skip: Sequence[str] = (),
        accept_schema_changes: bool = False,
        on_step: Callable[[str], None] | None = None,
    ) -> ServerRestoreReport:
        """
        Restore Noust's state and then every application, and report.

        Args:
            name_or_path: The bundle, by its name here or its path.
            passphrase: Its passphrase.
            force: Restore over a server that already holds state.
            skip_state: The state is already back (a restore that stopped
                half-way): only bring the applications up.
            only: Restore only these applications.
            skip: Leave these applications out.
            accept_schema_changes: Restore an application's files even past
                deployments that changed its database's schema, when nothing
                restores the database with them (see
                :meth:`~noust.managers.backup_manager.BackupManager.require_restore_confirmed`).
            on_step: Told what is happening, for a job's progress.

        Returns:
            The report. ``ok`` says whether every application is back and answering.

        Raises:
            RoleError: This server is a hub.
            EnvelopeError: Wrong passphrase, or the bundle was altered.
            BackupError: The state could not be restored; nothing about the
                applications was attempted.
        """
        require_server_role("Restoring a server")

        def step(text: str) -> None:
            self.logger.info(text)
            if on_step:
                on_step(text)

        report = ServerRestoreReport()
        if skip_state:
            step("Keeping the state that is already here")
        else:
            step("Putting Noust's own state back")
            report.state = SelfRestore(self.manager, restart=True).restore(
                name_or_path, passphrase, force=force
            )
        # The store the bundle brought: restoring it left this process holding it.
        store = get_store()
        applications = [
            app
            for app in store.list_apps()
            if (not only or app.domain in only) and app.domain not in skip
        ]
        for app in store.list_apps():
            if app.domain in skip or (only and app.domain not in only):
                report.apps.append(AppOutcome(domain=app.domain, status=SKIPPED, healthy=True))
        report.engines_missing = self._missing_engines(store, applications)
        for engine in report.engines_missing:
            step(f"{engine['engine']} is not installed here: {engine['command']}")
        for app in applications:
            step(f"Bringing {app.domain} back")
            report.apps.append(self._restore_app(store, app, accept_schema_changes))
        report.apps.sort(key=lambda outcome: outcome.domain)
        if any(outcome.status == NO_BACKUP for outcome in report.apps):
            report.notes.append(
                "An application with no backup anywhere has its definition in the store but "
                "nothing to restore it from: deploy it again from its source."
            )
        audit.record(
            "restore_server.run",
            actor=self.actor,
            target="noust:self",
            outcome="ok" if report.ok else "failure",
            details={"counts": report.counts(), "state": report.state is not None},
        )
        return report

    def _missing_engines(self, store: NoustStore, apps: list[App]) -> list[dict[str, str]]:
        """
        Name the database engines the applications use that this server lacks.

        Args:
            store: The restored store.
            apps: The applications being restored.

        Returns:
            ``engine`` and the ``command`` that installs it, each once.
        """
        wanted = {app.id for app in apps}
        engines: set[str] = set()
        for database in store.list_databases():
            if database.app_id in wanted or database.app_id is None:
                engines.add(str(database.engine))
        missing = []
        for engine in sorted(engines):
            try:
                installed = self._installed(engine)
            except NoustError as exc:
                _log.warning("Could not tell whether %s is installed: %s", engine, exc)
                continue
            if not installed:
                missing.append({"engine": engine, "command": f"noust db install {engine}"})
        return missing

    # -------------------------------------------------------------- one application

    def _restore_app(self, store: NoustStore, app: App, accept: bool) -> AppOutcome:
        """
        Bring one application back: its backup, its newer data, its site, its gate.

        Args:
            store: The restored store.
            app: The application.
            accept: Restore past a schema change when nothing restores the database.

        Returns:
            What happened. Never raises: the next application goes on.
        """
        outcome = AppOutcome(domain=app.domain, status=FAILED)
        try:
            found = self._newest_backup(store, app)
            if found is None:
                outcome.status = NO_BACKUP
                outcome.notes.append(
                    "No application backup was found here or on its destinations: deploy it again "
                    "from its source."
                )
                return outcome
            where, metadata = found
            outcome.source, outcome.backup_id = where, metadata.id
            self._restore_files(app, where, metadata, accept)
            outcome.volumes = self._restore_data(app, metadata, outcome)
            outcome.site = self._render_site(app, outcome)
            ok, evidence = self._gate_for(app)
            outcome.gate = "passed" if ok else "failed"
            outcome.healthy = ok
            if not ok:
                outcome.evidence = evidence
                outcome.error = f"{app.domain} did not pass its health gate after the restore"
            outcome.status = RESTORED
        except NoustError as exc:
            outcome.status = FAILED
            outcome.error = exc.message
            outcome.evidence = exc.details or outcome.evidence
        return outcome

    def _newest_backup(self, store: NoustStore, app: App) -> tuple[str, BackupMetadata] | None:
        """
        Find the newest application backup, here or on the destinations its schedule uses.

        Args:
            store: The restored store.
            app: The application.

        Returns:
            Where it is (``local`` or a destination's name) and its metadata,
            or None when there is none anywhere.
        """
        app_name = domain_to_app_name(app.domain)
        best: tuple[datetime, str, BackupMetadata] | None = None
        for backup in self.backups.list_backups(domain=app.domain):
            moment = _moment(backup.created_at)
            if best is None or moment > best[0]:
                best = (moment, "local", backup)
        schedule = store.get_backup_schedule(app.domain)
        for entry in schedule.destinations if schedule else []:
            name = str(entry.get("name") or "")
            if not name:
                continue
            try:
                listed = self.destinations.remote_list(name, app_name)["backups"]
            except NoustError as exc:
                self.logger.warning(f"Could not list the backups of {app.domain} on {name}: {exc}")
                continue
            if not listed:
                continue
            newest = listed[0]
            moment = _moment(newest.get("modified"))
            if best is None or moment > best[0]:
                metadata = BackupMetadata(
                    id=str(newest["backup_id"]),
                    domain=app.domain,
                    app_name=app_name,
                    created_at=str(newest.get("modified") or ""),
                    size_bytes=int(newest.get("size") or 0),
                    app_type=app.app_type,
                    version="",
                    description="",
                    includes_env=True,
                    includes_node_modules=False,
                )
                best = (moment, name, metadata)
        return (best[1], best[2]) if best else None

    def _restore_files(self, app: App, where: str, metadata: BackupMetadata, accept: bool) -> None:
        """
        Restore an application from a backup, local or on a destination.

        Args:
            app: The application.
            where: ``local`` or a destination's name.
            metadata: The backup.
            accept: Restore past a schema change when nothing restores the database.
        """
        if where == "local":
            self.backups.restore(
                metadata.id,
                target_domain=app.domain,
                restore_env=True,
                stop_service=True,
                schema_changed_ok=accept,
            )
            return
        self.destinations.restore_remote(
            where,
            metadata.id,
            domain_to_app_name(app.domain),
            target_domain=app.domain,
            backup_manager=self.backups,
            schema_changed_ok=accept,
        )

    def _restore_data(self, app: App, metadata: BackupMetadata, outcome: AppOutcome) -> list[str]:
        """
        Put back the data backups newer than the application backup.

        An application backup holds the data as it was when it was taken; the
        data backups of its persistent paths and volumes run more often, so
        where one is newer it is what the data should be.

        Args:
            app: The application.
            metadata: The application backup that was restored.
            outcome: Where a failure is noted: the application is still back.

        Returns:
            The ids of the data backups restored.
        """
        policy = self.volumes.get_policy(app.domain).policy
        if policy is None:
            return []
        floor = _moment(metadata.created_at)
        restored: list[str] = []
        for key in self._data_targets(app, policy.targets):
            try:
                found = self._newest_data(app, key, policy.destinations)
                if found is None or found[0] <= floor:
                    continue
                self._restore_one_data(app, found)
                restored.append(found[2])
            except NoustError as exc:
                outcome.notes.append(f"The data {key} could not be restored: {exc.message}")
        return restored

    def _data_targets(self, app: App, chosen: list[str]) -> list[str]:
        """
        Name the data targets of an application.

        Args:
            app: The application.
            chosen: What its policy names; empty is every declared one.

        Returns:
            The targets' keys.
        """
        if chosen:
            return list(chosen)
        return [target.key for target in self.volumes.targets(app.domain)]

    def _newest_data(
        self, app: App, key: str, destinations: list[dict[str, Any]]
    ) -> tuple[datetime, str | None, str, str, str | None] | None:
        """
        Find the newest data backup of one target, here or on a destination.

        Args:
            app: The application.
            key: The target's key.
            destinations: The destinations its policy sends to.

        Returns:
            ``(moment, destination or None, id or name, slug, ...)``, or None
            when there is none.
        """
        best: tuple[datetime, str | None, str, str, str | None] | None = None
        local: list[VolumeBackupMetadata] = self.volumes.list_backups(app.domain, key)
        if local:
            newest = local[0]
            best = (_moment(newest.created_at), None, newest.id, newest.slug, None)
        for entry in destinations:
            name = str(entry.get("name") or "")
            try:
                listed = self.volumes.remote_backups(name, app.domain)
            except NoustError as exc:
                self.logger.warning(f"Could not list the data of {app.domain} on {name}: {exc}")
                continue
            for slug, files in listed.items():
                for item in files:
                    if item.get("target") != key:
                        continue
                    moment = _moment(item.get("modified"))
                    if best is None or moment > best[0]:
                        best = (moment, name, str(item["name"]), slug, str(item["name"]))
                    break
        return best

    def _restore_one_data(
        self, app: App, found: tuple[datetime, str | None, str, str, str | None]
    ) -> None:
        """
        Restore one data backup, from this server or a destination.

        Args:
            app: The application.
            found: What :meth:`_newest_data` found.
        """
        _, destination, identifier, slug, file_name = found
        if destination is None:
            self.volumes.restore(identifier, safety_backup=False)
        else:
            self.volumes.restore_remote(
                destination, app.domain, slug, file_name or identifier, safety_backup=False
            )

    def _render_site(self, app: App, outcome: AppOutcome) -> str:
        """
        Render an application's site again, noting why when it cannot be.

        Args:
            app: The application.
            outcome: Where the note goes.

        Returns:
            ``refreshed``, or what went wrong.
        """
        try:
            self._site_for(app)
        except NoustError as exc:
            outcome.notes.append(
                f"Its web server site was not rendered: {exc.message}. Write it again by updating the application: "
                f"noust update {app.domain}"
            )
            return exc.message
        return "refreshed"
