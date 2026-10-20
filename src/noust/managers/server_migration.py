# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving to another server: ``noust server migrate``, the three steps around ``noust restore-server`` (3.4, H-30).

``noust restore-server`` brings a whole server back from a bundle of Noust's own state and the
newest backup of every application. Moving *to another server* is that, with two things around
it that an operator would otherwise do by hand and get wrong at midnight:

1. **Prepare** (on the old server, :meth:`ServerMigration.prepare`): take a fresh backup of
   everything, in an order that makes the bundle consistent with it, and send each piece to a
   destination both servers can reach: every application (its release, ``.env``, the dumps of its
   databases and its Docker volumes), the data of every application (persistent paths and
   volumes), the databases no application owns, and finally the bundle of Noust's own state.
   Each piece is its own step: one that fails is reported and the rest go on, so a single bad
   volume does not leave the other twenty applications without a copy. Nothing on the old
   server is stopped, changed or removed: until the domains are pointed at the new server, the old
   one is still the one answering.
2. **Restore** (on the new server): ``noust restore-server --from-destination``, which this
   module prints the exact command for.
3. **Check** (on the new server, :meth:`ServerMigration.check`): ask every application the
   questions a person would before moving DNS: does its unit run, does it answer the way the
   deploy's health gate asks, is its certificate there, are its databases up, and where does each of
   its names resolve now (here, still on the old server, or nowhere). The report is dated, and
   says for each name still pointing elsewhere that the record is the operator's to change: Noust
   does not edit DNS.

**What it does not do**, said in the plan and in the report: install the web server, Docker or the
engines on the new server (``restore-server`` says which are missing), move the containers of a
database *instance* (their databases come in the application backups; the instance itself is made
again with ``noust db instance create``), and copy anything that is not under Noust's care. A backup
taken while an application runs is as consistent as that application's own dump and volume
protocol makes it: for a final copy, stop writing to the applications first and run ``prepare``
again, which is safe to repeat.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.central import require_server_role
from noust.core import audit
from noust.core.app_state import AppState, resolve_states
from noust.core.audit import Actor
from noust.core.db_instance_store import DbInstanceStore
from noust.core.exceptions import (
    BackupError,
    DependencyError,
    NoustError,
    ValidationError,
)
from noust.core.logger import Logger
from noust.core.store import App, NoustStore, get_store
from noust.deployers.domains import DnsCheck, check_dns
from noust.managers.backup_destinations import BackupDestinationManager
from noust.managers.backup_manager import BackupManager
from noust.managers.cert_manager import CertManager
from noust.managers.database.records import DatabaseRecords
from noust.managers.self_backup import SelfBackupManager
from noust.managers.service_manager import ServiceManager
from noust.managers.volume_backups import VolumeBackupManager
from noust.monitor.reachability import AppProbe, probe_application

__all__ = [
    "AppCheck",
    "MigrationCheck",
    "MigrationPlan",
    "MigrationStep",
    "PreparedMigration",
    "ServerMigration",
]

#: What a step of the preparation ended as.
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"

#: What an application's check ended as.
OK = "ok"
DOWN = "down"
ATTENTION = "attention"


@dataclass
class MigrationStep:
    """
    One piece of the preparation.

    Attributes:
        kind: ``application``, ``data``, ``database`` or ``noust``.
        name: What it is a copy of: a domain, ``engine/database``, or this server.
        state: ``done``, ``failed`` or ``skipped``.
        detail: Why it failed or was skipped, in the manager's own words.
        backup: The id or file name of the copy that was made and sent.
    """

    kind: str
    name: str
    state: str
    detail: str = ""
    backup: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "kind": self.kind,
            "name": self.name,
            "state": self.state,
            "detail": self.detail,
            "backup": self.backup,
        }


@dataclass
class MigrationPlan:
    """
    What moving this server would take, and what stops it. Nothing was changed.

    Attributes:
        host: This server's name as a folder on the destination.
        destination: The destination the copies would go to.
        applications: Each application: its domain, type, every name it answers on, the
            databases it uses, how many pieces of data it has and when it was last backed up.
        databases: The databases no application owns, as ``engine/database``.
        engines: The database engines the new server needs, for ``noust db install``.
        instances: The database instances Noust runs here, which the new server makes again.
        passphrase_stored: Whether the bundle can be sealed without asking.
        blockers: What stops the preparation.
        warnings: What to know first.
    """

    host: str
    destination: str
    applications: list[dict[str, Any]] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    instances: list[str] = field(default_factory=list)
    passphrase_stored: bool = False
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "host": self.host,
            "destination": self.destination,
            "applications": [dict(item) for item in self.applications],
            "databases": list(self.databases),
            "engines": list(self.engines),
            "instances": list(self.instances),
            "passphrase_stored": self.passphrase_stored,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass
class PreparedMigration:
    """
    What the preparation did.

    Attributes:
        host: This server's name as a folder on the destination.
        destination: Where the copies went.
        started_at: When it began, ISO 8601 in UTC.
        finished_at: When it ended.
        steps: One per piece, in the order they ran.
        bundle: The name of the bundle of Noust's own state, or None when it could not be made.
        next_steps: The commands to run on the new server, in order.
        warnings: What to know before switching.
    """

    host: str
    destination: str
    started_at: str
    finished_at: str = ""
    steps: list[MigrationStep] = field(default_factory=list)
    bundle: str | None = None
    next_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every piece was copied and sent, the bundle included."""
        return self.bundle is not None and all(step.state != FAILED for step in self.steps)

    def counts(self) -> dict[str, int]:
        """
        Returns:
            How many steps ended in each state.
        """
        counted = {DONE: 0, FAILED: 0, SKIPPED: 0}
        for step in self.steps:
            counted[step.state] = counted.get(step.state, 0) + 1
        return counted

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "host": self.host,
            "destination": self.destination,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": self.ok,
            "counts": self.counts(),
            "steps": [step.to_dict() for step in self.steps],
            "bundle": self.bundle,
            "next_steps": list(self.next_steps),
            "warnings": list(self.warnings),
        }


@dataclass
class AppCheck:
    """
    What the check found of one application on the new server.

    Attributes:
        domain: The application.
        status: ``ok``, ``down`` (it does not run or does not answer) or ``attention``
            (it runs; something around it is not as it should be before DNS moves).
        state: What the unit or the containers say, in a word.
        answers: Whether it answered the deploy's probe; None for what has nothing to ask
            (a static site).
        probe: The probe's words when it did not answer, verbatim.
        names: Each name it answers on: where it resolves, whether that is this server, and
            when its certificate ends (None for none).
        databases: Each database it uses: whether the engine runs and the database is there.
        problems: What is wrong, as sentences.
    """

    domain: str
    status: str = OK
    state: str = ""
    answers: bool | None = None
    probe: str = ""
    names: list[dict[str, Any]] = field(default_factory=list)
    databases: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "domain": self.domain,
            "status": self.status,
            "state": self.state,
            "answers": self.answers,
            "probe": self.probe,
            "names": [dict(item) for item in self.names],
            "databases": [dict(item) for item in self.databases],
            "problems": list(self.problems),
        }


@dataclass
class MigrationCheck:
    """
    The report of the check on the new server.

    Attributes:
        generated_at: When it was made, ISO 8601 in UTC.
        host: This server's name.
        applications: One per application.
        addresses: This server's addresses, which a name should resolve to.
        pending: The names that still resolve elsewhere, with where: the operator's to change.
    """

    generated_at: str
    host: str
    applications: list[AppCheck] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every application runs and answers (names that point elsewhere are pending, not wrong)."""
        return all(item.status != DOWN for item in self.applications)

    def counts(self) -> dict[str, int]:
        """
        Returns:
            How many applications ended in each status.
        """
        counted = {OK: 0, ATTENTION: 0, DOWN: 0}
        for item in self.applications:
            counted[item.status] = counted.get(item.status, 0) + 1
        return counted

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "generated_at": self.generated_at,
            "host": self.host,
            "ok": self.ok,
            "counts": self.counts(),
            "addresses": list(self.addresses),
            "applications": [item.to_dict() for item in self.applications],
            "pending": [dict(item) for item in self.pending],
        }


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 in UTC.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ServerMigration:
    """
    Prepare a move to another server, and check it once it has happened.

    Args:
        logger: Where progress goes.
        store: The store; the process-wide one when None.
        backups: Takes the applications' backups; a default one when None.
        volumes: Takes the applications' data backups; a default one when None.
        destinations: Sends files to the destination; a default one when None.
        self_backups: Seals the bundle of Noust's own state; a default one when None.
        databases: Takes a standalone database's dump and sends it:
            ``(engine, database, destination) -> file name``; the database
            backups' own when None.
        dns: Resolves a name against this machine's addresses.
        probe: Asks an application the deploy's question.
        states: Says whether each application's unit or containers run.
        certificates: Lists the certificates this server holds.
        engine_up: Says whether a database engine runs and holds a database:
            ``(engine, database) -> (ok, detail)``.
        actor: Who the audit trail names for what this does.
    """

    def __init__(
        self,
        *,
        logger: Logger | None = None,
        store: NoustStore | None = None,
        backups: BackupManager | None = None,
        volumes: VolumeBackupManager | None = None,
        destinations: BackupDestinationManager | None = None,
        self_backups: SelfBackupManager | None = None,
        databases: Callable[[str, str, str], str] | None = None,
        dns: Callable[[str], DnsCheck] = check_dns,
        probe: Callable[[App], AppProbe | None] = probe_application,
        states: Callable[[list[App]], dict[str, AppState]] | None = None,
        certificates: Callable[[], list[dict[str, Any]]] | None = None,
        engine_up: Callable[[str, str], tuple[bool, str]] | None = None,
        actor: Actor | None = None,
    ) -> None:
        self.logger = logger or Logger()
        self.actor = actor
        self._store = store
        self._backups = backups
        self._volumes = volumes
        self._destinations = destinations
        self._self_backups = self_backups
        self._databases = databases
        self._dns = dns
        self._probe = probe
        self._states = states
        self._certificates = certificates
        self._engine_up = engine_up

    # ------------------------------------------------------------- collaborators

    @property
    def store(self) -> NoustStore:
        """The store."""
        if self._store is None:
            self._store = get_store()
        return self._store

    @property
    def backups(self) -> BackupManager:
        """Takes an application's backup."""
        if self._backups is None:
            self._backups = BackupManager(verbose=self.logger.verbose)
        return self._backups

    @property
    def volumes(self) -> VolumeBackupManager:
        """Takes an application's data backups."""
        if self._volumes is None:
            self._volumes = VolumeBackupManager()
        return self._volumes

    @property
    def destinations(self) -> BackupDestinationManager:
        """Sends files to a destination."""
        if self._destinations is None:
            self._destinations = BackupDestinationManager()
        return self._destinations

    @property
    def self_backups(self) -> SelfBackupManager:
        """Seals the bundle of Noust's own state."""
        if self._self_backups is None:
            self._self_backups = SelfBackupManager()
        return self._self_backups

    # --------------------------------------------------------------------- plan

    def plan(self, destination: str) -> MigrationPlan:
        """
        Say what moving this server would take, changing nothing.

        Args:
            destination: A destination created with ``noust backup destination add``.

        Returns:
            The plan; ``blockers`` says whether the preparation can go ahead.

        Raises:
            NoustError: This server is a hub: it has no applications to move.
        """
        require_server_role("Moving a server")
        plan = MigrationPlan(host=self.self_backups.host_slug(), destination=destination)
        plan.passphrase_stored = self.self_backups.stored_passphrase() is not None
        self._check_destination(plan)

        engines: set[str] = set()
        records = DatabaseRecords(self.store)
        for app in self._applications():
            links = records.links(app_id=app.id)
            engines.update(link.engine.partition("@")[0] for link in links)
            try:
                targets = len(self.volumes.targets(app.domain))
            except NoustError:
                targets = 0
            newest = self._newest_backup(app.domain)
            plan.applications.append(
                {
                    "domain": app.domain,
                    "type": app.app_type,
                    "names": [record.domain for record in self.store.list_domains(app.domain)]
                    or [app.domain],
                    "databases": sorted(f"{link.engine}/{link.db_name}" for link in links),
                    "data": targets,
                    "last_backup": newest,
                }
            )
        for row in self.store.list_databases():
            if row.app_id is None:
                plan.databases.append(f"{row.engine}/{row.name}")
                engines.add(row.engine.partition("@")[0])
        plan.databases.sort()
        plan.engines = sorted(engines)
        plan.instances = sorted(record.name for record in DbInstanceStore(self.store).all())

        if not plan.applications:
            plan.warnings.append("This server runs no application: only its own state is copied")
        if plan.instances:
            plan.warnings.append(
                f"{', '.join(plan.instances)} run as containers Noust made: their databases "
                "travel in the applications' backups, but the instances themselves are made "
                "again on the new server with 'noust db instance create'"
            )
        plan.warnings.append(
            "A backup of a running application is as consistent as its own dump allows: for a "
            "final copy, stop what writes and run the preparation again"
        )
        return plan

    def _check_destination(self, plan: MigrationPlan) -> None:
        """
        Add to the plan what is wrong with the destination, if anything.

        Args:
            plan: The plan, whose blockers are added to.
        """
        if self.destinations.get(plan.destination) is None:
            plan.blockers.append(
                f"The backup destination {plan.destination!r} does not exist: create it with "
                "'noust backup destination add'"
            )
            return
        try:
            self.destinations.test(plan.destination)
        except (BackupError, DependencyError) as exc:
            plan.blockers.append(f"{plan.destination} cannot be reached: {exc.message}")

    def _applications(self) -> list[App]:
        """
        Returns:
            The applications that move, by domain: every one except the previews of a pull request.
        """
        return sorted(
            (app for app in self.store.list_apps() if not app.preview_parent),
            key=lambda app: app.domain,
        )

    def _newest_backup(self, domain: str) -> str | None:
        """
        Args:
            domain: An application.

        Returns:
            When its newest backup was made, or None when there is none.
        """
        newest = self.backups.get_latest_backup(domain)
        return newest.created_at if newest is not None else None

    # ------------------------------------------------------------------ prepare

    def prepare(
        self,
        destination: str,
        passphrase: str,
        *,
        only: Iterable[str] = (),
        data: bool = True,
    ) -> PreparedMigration:
        """
        Copy everything now and send it to a destination.

        Args:
            destination: A destination created with ``noust backup destination add``.
            passphrase: The one the bundle of Noust's own state is sealed under. It is never sent.
            only: Move only these applications' domains (the bundle and the standalone
                databases are still copied); every application when empty.
            data: Also copy the applications' data (persistent paths and volumes).

        Returns:
            What was done, step by step, and the commands for the new server.

        Raises:
            ValidationError: The destination cannot be used or an application named is unknown;
                nothing was copied.
        """
        plan = self.plan(destination)
        if plan.blockers:
            raise ValidationError(
                "This server cannot be prepared for a move yet",
                details="\n".join(f"- {blocker}" for blocker in plan.blockers),
                field="destination",
            )
        chosen = set(only)
        known = {entry["domain"] for entry in plan.applications}
        unknown = sorted(chosen - known)
        if unknown:
            raise ValidationError(
                f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not applications of this server",
                details=f"Applications: {', '.join(sorted(known)) or 'none'}.",
                field="only",
            )
        report = PreparedMigration(
            host=plan.host,
            destination=destination,
            started_at=_now(),
            warnings=list(plan.warnings),
        )
        for entry in plan.applications:
            domain = str(entry["domain"])
            if chosen and domain not in chosen:
                report.steps.append(MigrationStep("application", domain, SKIPPED, "not asked for"))
                continue
            report.steps.append(self._back_up_app(domain, destination))
            if data and entry["data"]:
                report.steps.extend(self._back_up_data(domain, destination))
        for name in plan.databases:
            report.steps.append(self._back_up_database(name, destination))
        report.steps.append(self._seal_state(destination, passphrase, report))
        report.finished_at = _now()
        report.next_steps = self._next_steps(report, plan)
        audit.record(
            "server.migrate.prepare",
            actor=self.actor,
            target=f"server:{plan.host}",
            outcome="ok" if report.ok else "failure",
            details={"destination": destination, **report.counts()},
        )
        return report

    def _back_up_app(self, domain: str, destination: str) -> MigrationStep:
        """
        Take an application's backup and send it.

        Args:
            domain: The application.
            destination: Where it goes.

        Returns:
            The step.
        """
        self.logger.info(f"Backing up {domain}")
        try:
            metadata = self.backups.create(
                domain,
                description="Server migration",
                include_env=True,
                include_databases=True,
                include_docker_volumes=True,
                tags=["migration"],
            )
            self.destinations.push(metadata, destination, backup_manager=self.backups)
        except (NoustError, OSError) as exc:
            return MigrationStep("application", domain, FAILED, _words(exc))
        return MigrationStep("application", domain, DONE, backup=metadata.id)

    def _back_up_data(self, domain: str, destination: str) -> list[MigrationStep]:
        """
        Take an application's data backups and send them, one step per archive.

        Args:
            domain: The application.
            destination: Where they go.

        Returns:
            The steps.
        """
        self.logger.info(f"Backing up the data of {domain}")
        try:
            made = self.volumes.create(domain, description="Server migration", tags=["migration"])
        except (NoustError, OSError) as exc:
            return [MigrationStep("data", domain, FAILED, _words(exc))]
        steps = []
        for metadata in made:
            label = f"{domain}/{metadata.target}"
            try:
                self.volumes.push(metadata.id, destination)
            except (NoustError, OSError) as exc:
                steps.append(MigrationStep("data", label, FAILED, _words(exc)))
            else:
                steps.append(MigrationStep("data", label, DONE, backup=metadata.id))
        return steps

    def _back_up_database(self, name: str, destination: str) -> MigrationStep:
        """
        Dump a database no application owns and send it.

        Args:
            name: ``engine/database``.
            destination: Where it goes.

        Returns:
            The step.
        """
        engine, _, database = name.partition("/")
        self.logger.info(f"Dumping {name}")
        dumper = self._databases or self._dump_and_send
        try:
            file_name = dumper(engine, database, destination)
        except (NoustError, OSError) as exc:
            return MigrationStep("database", name, FAILED, _words(exc))
        return MigrationStep("database", name, DONE, backup=file_name)

    def _dump_and_send(self, engine: str, database: str, destination: str) -> str:
        """
        Dump a database and send the dump, the way ``noust db backup-push`` does.

        Args:
            engine: The engine key.
            database: The database.
            destination: Where it goes.

        Returns:
            The dump's file name.
        """
        from noust.managers.database.backups import DatabaseBackups
        from noust.managers.database.service import DatabaseService

        backups = DatabaseBackups(DatabaseService(logger=self.logger))
        dump = backups.dump(engine, database, origin="manual")
        backups.push(engine, dump.name, destination)
        return dump.name

    def _seal_state(
        self, destination: str, passphrase: str, report: PreparedMigration
    ) -> MigrationStep:
        """
        Seal Noust's own state and send it.

        Args:
            destination: Where it goes.
            passphrase: What it is sealed under.
            report: The preparation, whose bundle is set.

        Returns:
            The step.
        """
        self.logger.info("Sealing Noust's own state")
        try:
            bundle = self.self_backups.create(passphrase, kind="manual")
            self.self_backups.push(bundle.path.name, destination)
        except (NoustError, OSError) as exc:
            return MigrationStep("noust", report.host, FAILED, _words(exc))
        report.bundle = bundle.path.name
        return MigrationStep("noust", report.host, DONE, backup=bundle.path.name)

    @staticmethod
    def _next_steps(report: PreparedMigration, plan: MigrationPlan) -> list[str]:
        """
        Say what to run on the new server.

        Args:
            report: What the preparation did.
            plan: The plan it followed.

        Returns:
            The commands, in order.
        """
        steps = ["Install Noust on the new server (its installer)."]
        if report.bundle is not None:
            engines = f" and {', '.join(plan.engines)}" if plan.engines else ""
            steps.append(
                f"noust restore-server --install --from-destination {report.destination} "
                f"--name {report.bundle} --host {report.host}"
            )
            steps.append(
                f"That installs the web server, Docker{engines} where they are missing, creates "
                "the PostgreSQL roles, brings back the databases no application owns and then "
                "each application."
            )
        else:
            steps.append(
                "Seal the state again ('noust self-backup create' then 'noust self-backup push') "
                "before restoring: the bundle was not made"
            )
        steps.append("noust server migrate check")
        steps.append(
            "Point each name at the new server (the check lists them and where they resolve now), "
            "then 'noust cert create' for the ones without a certificate."
        )
        return steps

    # -------------------------------------------------------------------- check

    def check(self) -> MigrationCheck:
        """
        Ask every application the questions to answer before DNS moves.

        Returns:
            The dated report; ``ok`` is False when an application is down.

        Raises:
            NoustError: This server is a hub.
        """
        require_server_role("Checking a moved server")
        apps = self._applications()
        report = MigrationCheck(generated_at=_now(), host=self.self_backups.host_slug())
        states = self._states(apps) if self._states else resolve_states(apps, ServiceManager())
        certificates = self._certificate_days()
        records = DatabaseRecords(self.store)
        for app in apps:
            report.applications.append(self._check_app(app, states, certificates, records))
        for item in report.applications:
            for name in item.names:
                if name["resolves_here"] is False:
                    report.pending.append(
                        {
                            "name": name["name"],
                            "application": item.domain,
                            "resolves_to": name["resolves_to"],
                        }
                    )
        if report.applications:
            report.addresses = _first_addresses(report.applications)
        return report

    def _check_app(
        self,
        app: App,
        states: dict[str, AppState],
        certificates: dict[str, int | None],
        records: DatabaseRecords,
    ) -> AppCheck:
        """
        Check one application.

        Args:
            app: The application.
            states: What every unit says.
            certificates: Days left of the certificate of each name that has one.
            records: Which databases each application uses.

        Returns:
            What was found.
        """
        item = AppCheck(domain=app.domain)
        state = states.get(app.domain)
        if state is not None:
            item.state = state.label
            if not state.healthy:
                item.status = DOWN
                item.problems.append(state.detail or f"It is {state.label.lower()}")
        try:
            probed = self._probe(app)
        except (NoustError, OSError) as exc:
            probed = AppProbe(ok=False, detail=_words(exc))
        if probed is not None:
            item.answers = probed.ok
            if not probed.ok:
                item.status = DOWN
                item.probe = probed.detail
                item.problems.append("It does not answer the deploy's health probe")
        names = [record.domain for record in self.store.list_domains(app.domain)] or [app.domain]
        for name in names:
            item.names.append(self._check_name(name, certificates, item))
        for link in records.links(app_id=app.id):
            item.databases.append(self._check_database(link.engine, link.db_name, item))
        if item.status == OK and item.problems:
            item.status = ATTENTION
        return item

    def _check_name(
        self, name: str, certificates: dict[str, int | None], item: AppCheck
    ) -> dict[str, Any]:
        """
        Say where a name resolves and what certificate covers it.

        Args:
            name: A name the application answers on.
            certificates: Days left of the certificate of each name that has one.
            item: The application's check, whose problems are added to.

        Returns:
            The name's entry.
        """
        try:
            found = self._dns(name)
            resolves = list(found.resolved_addresses)
            here: bool | None = found.points_here
            local = list(found.expected_addresses)
        except NoustError as exc:
            resolves, here, local = [], None, []
            item.problems.append(f"{name}: could not check DNS ({exc.message})")
        days = certificates.get(name)
        if name not in certificates:
            item.problems.append(f"{name} has no certificate on this server yet")
        elif days is not None and days < 0:
            item.problems.append(f"The certificate of {name} expired {abs(days)} days ago")
        return {
            "name": name,
            "resolves_to": resolves,
            "resolves_here": here,
            "local_addresses": local,
            "certificate_days": days,
            "has_certificate": name in certificates,
        }

    def _check_database(self, engine: str, database: str, item: AppCheck) -> dict[str, Any]:
        """
        Say whether a database an application uses is there.

        Args:
            engine: The engine key.
            database: The database.
            item: The application's check, whose problems are added to.

        Returns:
            The database's entry.
        """
        ok, detail = (
            self._engine_up(engine, database)
            if self._engine_up
            else _engine_holds(engine, database)
        )
        if not ok:
            item.status = DOWN
            item.problems.append(f"{engine}/{database}: {detail}")
        return {"engine": engine, "database": database, "ok": ok, "detail": detail}

    def _certificate_days(self) -> dict[str, int | None]:
        """
        Returns:
            For each name a certificate of this server covers, the days it has left (None
            when its expiry cannot be read).
        """
        try:
            listed = (
                self._certificates()
                if self._certificates
                else CertManager(verbose=self.logger.verbose).list_certificates()
            )
        except NoustError:
            return {}
        covered: dict[str, int | None] = {}
        for certificate in listed:
            days = _days_left(certificate.get("expiry"))
            for name in certificate.get("domains") or []:
                covered[str(name)] = days
        return covered


# --------------------------------------------------------------------- helpers


def _words(exc: Exception) -> str:
    """
    Args:
        exc: What failed.

    Returns:
        Its own message, with the fix when the error carries one.
    """
    message = getattr(exc, "message", None) or str(exc)
    details = getattr(exc, "details", None)
    return f"{message} ({details})" if details else str(message)


def _days_left(expiry: object) -> int | None:
    """
    Args:
        expiry: A certificate's expiry as certbot reports it.

    Returns:
        Whole days left (negative once expired), or None when it cannot be read.
    """
    if not isinstance(expiry, str):
        return None
    try:
        when = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when - datetime.now(timezone.utc)).days


def _first_addresses(checks: list[AppCheck]) -> list[str]:
    """
    Args:
        checks: The applications checked.

    Returns:
        This server's addresses, as the first name that reported them said.
    """
    for item in checks:
        for name in item.names:
            if name["local_addresses"]:
                return list(name["local_addresses"])
    return []


def _engine_holds(engine: str, database: str) -> tuple[bool, str]:
    """
    Ask a database engine whether it runs and holds a database.

    Args:
        engine: The engine key.
        database: The database.

    Returns:
        Whether it does, and if not, why.
    """
    from noust.managers.database.registry import get_db_manager

    try:
        manager = get_db_manager(engine)
        if manager is None or not manager.is_installed():
            return False, f"{engine} is not installed here: noust db install {engine}"
        if not manager.is_running():
            return False, f"{engine} is not running"
        if not manager.database_exists(database):
            return False, f"the database {database} is not on {engine}"
    except NoustError as exc:
        return False, exc.message
    return True, ""
