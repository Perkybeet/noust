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
2. **Says what the machine still lacks** that the restore cannot bring, and with
   ``--install`` installs it: the web server the configuration names, Docker when an
   application or an instance needs it, and the database engines the databases live on.
   Without ``--install`` it installs nothing and names the command.
3. **Puts the PostgreSQL roles back** the bundle carries (``pg_dumpall --globals-only``), once
   their engine is there and only the ones the server lacks, and **brings back the databases no
   application owns** from their newest dump, here or on a destination.
4. **Brings each application back, one after the other**, from the newest
   backup there is of it, on this server or on the destinations its own
   schedule sends to: the application backup (its release and ``shared/``,
   ``.env``, database dumps and volumes when it was taken with them) and then
   the data backups of its persistent paths and volumes that are newer than it.
5. **Renders its web server site again** from the store, with the certificate
   the bundle brought, and
6. **starts it behind the same health gate as every deploy**, and records what
   the gate said: the probes and the journal, verbatim, when it does not pass.

An application that fails any step is reported and the next one goes on; there
is a final report with each application's outcome and nothing is rolled back,
because there is nothing to roll back to on a machine that was empty. The flow
is resumable: with the state already restored (``--skip-state``) it only runs
steps 2 to 6, so a destination that was unreachable can be tried again. That is also
the part the console can run: restoring the state replaces the store the console
runs on, so it is a command at a terminal.

What it does not do, said here and in the report: add a repository for Docker on a
distribution that does not carry it, make a database *instance* again (a container Noust ran:
``noust db instance create``), put back the accounts of MySQL, MariaDB and MongoDB (only
PostgreSQL has roles in a file of its own), and redeploy an application nothing backed up from
its source (the report says so, and the application's definition is in the store).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.exceptions import DependencyError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore, get_store, is_compose_backed
from noust.core.utils import domain_to_app_name
from noust.managers.backup_destinations import BackupDestinationManager
from noust.managers.backup_manager import BackupManager, BackupMetadata
from noust.managers.restore_databases import DatabaseOutcome, OrphanDatabases, parse_moment
from noust.managers.self_backup import SelfBackupManager
from noust.managers.self_restore import RestoreReport, SelfRestore
from noust.managers.server_provision import ServerProvisioner
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
        requirements: The software the server runs on (web server, Docker, engines): each
            ``name``, ``state`` (``present``, ``installed``, ``missing`` or ``failed``),
            ``detail`` and the ``command`` that installs it by hand.
        roles: The PostgreSQL roles put back: ``engine``, ``state`` (``created``,
            ``waiting`` for its engine, ``failed``), ``created``, ``present``, ``detail``.
        databases: The databases no application owns, and what became of each.
        notes: Anything else.
    """

    state: RestoreReport | None = None
    apps: list[AppOutcome] = field(default_factory=list)
    engines_missing: list[dict[str, str]] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    roles: list[dict[str, Any]] = field(default_factory=list)
    databases: list[DatabaseOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every application is back and answering, the software is there and no database is lost."""
        return (
            all(
                outcome.status in (RESTORED, SKIPPED)
                and (outcome.healthy or outcome.status == SKIPPED)
                for outcome in self.apps
            )
            and all(step["state"] in ("present", "installed") for step in self.requirements)
            and all(database.status not in ("failed", "no_dump") for database in self.databases)
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
            "requirements": self.requirements,
            "roles": self.roles,
            "databases": [database.to_dict() for database in self.databases],
            "notes": self.notes,
        }


_moment = parse_moment


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
        provisioner: ServerProvisioner | None = None,
        orphans: OrphanDatabases | None = None,
        apply_roles: Callable[[str, Path], Any] | None = None,
        install_engine: Callable[[str], str] | None = None,
        webserver_name: Callable[[], str] | None = None,
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
            provisioner: Installs the web server and Docker.
            orphans: Brings back the databases no application owns.
            apply_roles: Creates the roles of a PostgreSQL server from a file: given the engine
                and the file, returns the result, or None when the engine is not installed.
            install_engine: Installs a database engine and says what it did.
            webserver_name: Names the web server the restored configuration chooses.
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
        self._provisioner = provisioner
        self._orphans = orphans
        self._apply_roles = apply_roles
        self._install_engine = install_engine
        self._webserver_name = webserver_name

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

    @property
    def provisioner(self) -> ServerProvisioner:
        """Installs the web server and Docker."""
        if self._provisioner is None:
            self._provisioner = ServerProvisioner(logger=self.logger)
        return self._provisioner

    @property
    def orphans(self) -> OrphanDatabases:
        """Brings back the databases no application owns."""
        if self._orphans is None:
            self._orphans = OrphanDatabases(logger=self.logger)
        return self._orphans

    def _webserver(self) -> str:
        """
        Returns:
            The web server the restored configuration names: ``nginx`` or ``apache``.
        """
        if self._webserver_name is not None:
            return self._webserver_name()
        from noust.core.config import Config

        name = Config().webserver
        return "apache" if name.startswith(("apache", "httpd")) else "nginx"

    def _install(self, engine: str) -> str:
        """
        Install a database engine.

        Args:
            engine: The engine's name.

        Returns:
            A sentence saying what was done.

        Raises:
            NoustError: The engine could not be installed.
        """
        if self._install_engine is not None:
            return self._install_engine(engine)
        from noust.managers.database.service import DatabaseService

        outcome = DatabaseService(logger=self.logger).install_engine(engine)
        return str(getattr(outcome, "message", "") or f"{engine} was installed")

    def _roles_for(self, engine: str, source: Path) -> Any:
        """
        Create the roles of a PostgreSQL server from a file.

        Args:
            engine: The engine's name.
            source: The file ``pg_dumpall --globals-only`` wrote.

        Returns:
            What the engine did, or None when it is not installed here.
        """
        if self._apply_roles is not None:
            return self._apply_roles(engine, source)
        from noust.managers.database.service import DatabaseService

        manager = DatabaseService(logger=self.logger).manager(engine)
        if not manager.is_installed() or not getattr(manager, "HAS_GLOBALS", False):
            return None
        return manager.restore_globals(source)

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
            and whether it has a data policy), ``engines``, ``databases`` (the
            ones no application owns, as ``engine/name``) and ``pg_globals`` (the
            servers whose roles it carries).

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
            orphans: list[str] = []
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
                    for row in connection.execute(
                        "SELECT engine, name FROM databases WHERE app_id IS NULL "
                        "ORDER BY engine, name"
                    ):
                        orphans.append(f"{row['engine']}/{row['name']}")
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
            "databases": orphans,
            "pg_globals": [
                str(entry.get("engine")) for entry in manifest.get("pg_globals", []) or []
            ],
        }

    # --------------------------------------------------------------------- run

    def run(
        self,
        name_or_path: str = "",
        passphrase: str = "",
        *,
        force: bool = False,
        skip_state: bool = False,
        only: Sequence[str] = (),
        skip: Sequence[str] = (),
        accept_schema_changes: bool = False,
        install: bool = False,
        databases: bool = True,
        from_destination: str | None = None,
        on_step: Callable[[str], None] | None = None,
    ) -> ServerRestoreReport:
        """
        Restore Noust's state and then every application, and report.

        Args:
            name_or_path: The bundle, by its name here or its path; not needed with
                ``skip_state``.
            passphrase: Its passphrase; not needed with ``skip_state``.
            force: Restore over a server that already holds state.
            skip_state: The state is already back (a restore that stopped
                half-way): only bring the applications up.
            only: Restore only these applications.
            skip: Leave these applications out.
            accept_schema_changes: Restore an application's files even past
                deployments that changed its database's schema, when nothing
                restores the database with them (see
                :meth:`~noust.managers.backup_manager.BackupManager.require_restore_confirmed`).
            install: Install the web server, Docker (when something needs it) and the
                database engines the databases live on. Without it nothing is installed and
                the report names what is missing.
            databases: Also bring back the databases no application owns.
            from_destination: The destination a move was prepared on: the databases no
                application owns are looked for there too.
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
        if not skip_state and not (name_or_path and passphrase):
            raise ValidationError(
                "Restoring the state needs the bundle and its passphrase",
                details="Give both, or pass skip_state when the state is already here.",
                field="name_or_path",
            )

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
        report.requirements = self._requirements(store, applications, install, step)
        report.engines_missing = self._missing_engines(store, applications)
        self._install_engines(report, install, step)
        for engine in report.engines_missing:
            step(f"{engine['engine']} is not installed here: {engine['command']}")
        report.roles = self._put_back_roles(step)
        if databases:
            step("Bringing back the databases no application owns")
            report.databases = self.orphans.restore(store, from_destination=from_destination)
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

    def preview(self, *, from_destination: str | None = None) -> dict[str, Any]:
        """
        Say what bringing this server's applications and databases back would do. Changes nothing.

        The state is already here (restoring it replaces the store the console runs on, which
        only a terminal can do), so this reads the store: what is missing on the machine, the
        roles waiting for their engine, the databases no application owns with the dump each
        would be loaded from, and every application with its newest backup.

        Args:
            from_destination: The destination a move was prepared on.

        Returns:
            ``requirements`` (what the machine has and lacks, with the command that installs
            each), ``engines_missing``, ``roles``, ``databases`` and ``apps``.
        """
        require_server_role("Restoring a server")
        store = get_store()
        applications = [app for app in store.list_apps() if not app.preview_parent]
        requirements = self._requirements(store, applications, False, lambda text: None)
        engines_missing = self._missing_engines(store, applications)
        directory = self.manager.restore_targets().get("pg-globals")
        waiting = (
            sorted(path.stem for path in directory.glob("*.sql"))
            if directory is not None and directory.is_dir()
            else []
        )
        apps: list[dict[str, Any]] = []
        for app in applications:
            try:
                found = self._newest_backup(store, app)
            except NoustError as exc:
                apps.append({"domain": app.domain, "type": app.app_type, "error": exc.message})
                continue
            apps.append(
                {
                    "domain": app.domain,
                    "type": app.app_type,
                    "source": found[0] if found else None,
                    "backup_id": found[1].id if found else None,
                    "created_at": found[1].created_at if found else None,
                }
            )
        return {
            "requirements": requirements,
            "engines_missing": engines_missing,
            "roles": waiting,
            "databases": [
                database.to_dict()
                for database in self.orphans.plan(store, from_destination=from_destination)
            ],
            "apps": apps,
        }

    def _requirements(
        self, store: NoustStore, apps: list[App], install: bool, step: Callable[[str], None]
    ) -> list[dict[str, Any]]:
        """
        Check, and with ``install`` install, the web server and Docker.

        Docker is a requirement only when an application runs in containers or the server ran a
        database instance: installing it on a server that has neither would be software nobody
        asked for.

        Args:
            store: The restored store.
            apps: The applications being restored.
            install: Whether to install what is missing.
            step: Reports progress.

        Returns:
            One entry per requirement: ``name``, ``state``, ``detail`` and ``command``.
        """
        provisioner = self.provisioner
        wanted: list[tuple[str, Callable[[], Any], Callable[[], Any]]] = []
        webserver = self._webserver()
        wanted.append(
            (
                webserver,
                lambda: provisioner.check_webserver(webserver),
                lambda: provisioner.ensure_webserver(webserver),
            )
        )
        if any(is_compose_backed(app.app_type) for app in apps) or self._has_instances(store):
            wanted.append(("docker", provisioner.check_docker, provisioner.ensure_docker))
        found: list[dict[str, Any]] = []
        for name, check, ensure in wanted:
            try:
                if install:
                    step(f"Making sure {name} is installed")
                done = ensure() if install else check()
            except DependencyError as exc:
                found.append(
                    {
                        "name": name,
                        "state": "failed",
                        "detail": f"{exc.message}\n{exc.details}".strip(),
                        "command": "",
                    }
                )
                continue
            found.append(
                {
                    "name": name,
                    "state": done.state,
                    "detail": done.detail,
                    "command": done.command,
                }
            )
        return found

    @staticmethod
    def _has_instances(store: NoustStore) -> bool:
        """
        Args:
            store: The restored store.

        Returns:
            Whether the old server ran database instances (containers Noust made).
        """
        from noust.core.db_instance_store import DbInstanceStore

        try:
            return bool(DbInstanceStore(store).all())
        except sqlite3.Error:
            return False

    def _install_engines(
        self, report: ServerRestoreReport, install: bool, step: Callable[[str], None]
    ) -> None:
        """
        Install the engines the report found missing, when asked.

        An engine that installs leaves ``engines_missing``; one that does not stays there and
        has a line in ``requirements`` saying why.

        Args:
            report: The report being made.
            install: Whether to install.
            step: Reports progress.
        """
        if not install:
            return
        still: list[dict[str, str]] = []
        for missing in report.engines_missing:
            engine = missing["engine"]
            step(f"Installing {engine}")
            try:
                detail = self._install(engine)
            except NoustError as exc:
                report.requirements.append(
                    {
                        "name": engine,
                        "state": "failed",
                        "detail": f"{exc.message}\n{exc.details or ''}".strip(),
                        "command": missing["command"],
                    }
                )
                still.append(missing)
                continue
            report.requirements.append(
                {"name": engine, "state": "installed", "detail": detail, "command": ""}
            )
        report.engines_missing = still

    def _put_back_roles(self, step: Callable[[str], None]) -> list[dict[str, Any]]:
        """
        Create the roles of the PostgreSQL servers the bundle carried.

        The files were put in the state directory by the restore of the state. A file is
        applied once its engine is installed and removed once it was (it holds password
        hashes); one whose engine is not there yet, or that the server refused, stays for the
        next run. A file of a database instance's server is left for the instance.

        Args:
            step: Reports progress.

        Returns:
            One entry per file.
        """
        directory = self.manager.restore_targets().get("pg-globals")
        if directory is None or not directory.is_dir():
            return []
        found: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.sql")):
            engine = path.stem
            entry: dict[str, Any] = {
                "engine": engine,
                "state": "waiting",
                "created": [],
                "present": [],
                "detail": "",
            }
            found.append(entry)
            if "." in engine or "@" in engine or "-" in engine:
                entry["detail"] = (
                    "It belongs to a database instance Noust ran: make the instance again, "
                    f"then apply it with 'noust db globals restore {path}'."
                )
                continue
            step(f"Creating the roles of {engine}")
            try:
                result = self._roles_for(engine, path)
            except NoustError as exc:
                entry["state"] = "failed"
                entry["detail"] = f"{exc.message}\n{exc.details or ''}".strip()
                continue
            if result is None:
                entry["detail"] = f"{engine} is not installed yet; the roles wait for it."
                continue
            entry.update(
                state="created",
                created=list(result.created),
                present=list(result.present),
                detail="; ".join([*result.left_out, *result.errors]),
            )
            self.manager.fs.remove(path)
        return found

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
