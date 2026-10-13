# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Move an engine to a newer major version: a new instance, its databases dumped and loaded, a way back.

A major version cannot be installed over the data of the one before it, and
Noust does not run an in-place ``pg_upgrade`` it cannot take back. What it does
is the sequence a careful operator would run by hand, in one place, with the
same safety the rest of Noust has:

1. **A new instance** of the newer version is made beside the engine
   (:class:`~noust.managers.database.managed.ManagedInstances`), from the
   official image pinned by digest. The engine being left is the host's or
   another instance; it keeps running.
2. **Each database is moved** (:class:`~noust.managers.database.moves.DatabaseMover`):
   dumped, its accounts made on the new instance with the passwords Noust keeps,
   loaded, and every application that uses it pointed at the new instance
   behind its health gate. The source is not touched.
3. **A failure at any point puts everything back**: the applications point at
   the engine they came from again, and the instance that was made is removed.
4. **A finished move can be rolled back.** The old engine still holds every
   database as it was when the move began, so the applications are pointed back
   at it. What was written to the new instance since is not carried back (an
   older server cannot load a newer one's dump), so rolling back takes a dump of
   it first and says where it is. The old instance is stopped, not removed:
   removing it is the operator's decision, when the new one has proved itself.

Redis and Valkey are not moved this way: their snapshot is the whole instance
and Noust does not install one into a container.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from noust.core.db_instance_store import InstanceRecord
from noust.core.db_migration_store import (
    DONE,
    FAILED,
    ROLLED_BACK,
    ROLLING_BACK,
    DbMigrationStore,
    MigrationRecord,
    MovedDatabase,
)
from noust.core.exceptions import (
    DatabaseBackupError,
    DatabaseEngineError,
    DatabaseNotFoundError,
    NoustError,
    ValidationError,
)
from noust.managers.database.instance_catalog import resolve_pin, spec_for
from noust.managers.database.instance_records import key_of
from noust.managers.database.instances import is_instance_key
from noust.managers.database.managed import InstancePlan, ManagedInstances
from noust.managers.database.moves import MOVABLE, DatabaseMover, MovePlan, release_of
from noust.managers.database.registry import DatabaseRegistry
from noust.validators.db_instance import check_instance_name

__all__ = [
    "InstanceMigrations",
    "MigrationOutcome",
    "MigrationPlan",
    "RollbackOutcome",
]


@dataclass
class MigrationPlan:
    """
    What moving an engine to a newer version would do, decided before anything is touched.

    Attributes:
        source: The engine key its databases leave.
        instance: The instance's name when the source is one; None for the host's engine.
        flavour: What both run.
        display_name: The flavour for a person.
        from_version: The version they leave.
        to_version: The version they go to.
        target: The instance that will be made.
        new_instance: What making it would do, as
            :meth:`~noust.managers.database.managed.InstancePlan.to_dict` says.
        databases: One plan per database that will be moved.
        apps: Every application that will be restarted on the new instance.
        blockers: What stops the move. Nothing is changed while any is listed.
        warnings: What the operator should know before going ahead.
    """

    source: str
    instance: str | None
    flavour: str
    display_name: str
    from_version: str | None
    to_version: str
    target: str
    new_instance: dict[str, Any]
    databases: list[dict[str, Any]] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "source": self.source,
            "instance": self.instance,
            "flavour": self.flavour,
            "display_name": self.display_name,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "target": self.target,
            "new_instance": dict(self.new_instance),
            "databases": list(self.databases),
            "apps": list(self.apps),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass
class MigrationOutcome:
    """
    An engine moved to a newer version.

    Attributes:
        migration: The move's number, which ``rollback`` takes.
        source: The engine the databases left.
        target: The instance they are on.
        moved: What each database's move did.
        kept: What stays where it was, for the way back.
        warnings: What did not go as planned and does not stop the move.
    """

    migration: int
    source: str
    target: str
    moved: list[dict[str, Any]] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "migration": self.migration,
            "source": self.source,
            "target": self.target,
            "moved": list(self.moved),
            "kept": list(self.kept),
            "warnings": list(self.warnings),
        }


@dataclass
class RollbackOutcome:
    """
    A move rolled back.

    Attributes:
        migration: The move's number.
        source: The engine the applications are pointed at again.
        target: The instance they left, now stopped.
        repointed: The databases and the applications pointed back.
        dumps: The dumps of the new instance taken first, so what was written
            to it since the move is kept.
        warnings: What the operator should know.
    """

    migration: int
    source: str
    target: str
    repointed: list[dict[str, Any]] = field(default_factory=list)
    dumps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "migration": self.migration,
            "source": self.source,
            "target": self.target,
            "repointed": list(self.repointed),
            "dumps": list(self.dumps),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class _Source:
    """
    The engine whose databases are to move.

    Attributes:
        key: Its key.
        instance: Its row when it is an instance Noust runs.
        flavour: What it runs.
        version: Its version.
        display_name: Its engine's name.
    """

    key: str
    instance: InstanceRecord | None
    flavour: str
    version: str | None
    display_name: str


class InstanceMigrations:
    """
    Move an engine's databases to a new instance of a newer version, and back.

    Args:
        managed: The manager of instances, which makes the new one.
        mover: Moves each database; one over the same service by default.
    """

    def __init__(self, managed: ManagedInstances, mover: DatabaseMover | None = None) -> None:
        self.managed = managed
        self._mover = mover

    @property
    def mover(self) -> DatabaseMover:
        """What moves each database."""
        if self._mover is None:
            self._mover = DatabaseMover(self.managed.service)
        return self._mover

    @property
    def records(self) -> DbMigrationStore:
        """The moves written down."""
        return DbMigrationStore(self.managed.service.store)

    @property
    def logger(self) -> Any:
        """Where progress is reported."""
        return self.managed.logger

    # ------------------------------------------------------------ questions

    def all(self) -> list[dict[str, Any]]:
        """
        List the moves, newest first.

        Returns:
            One entry per move, with whether it can still be rolled back.
        """
        entries = []
        for record in self.records.all():
            entry = record.to_dict()
            entry["can_roll_back"] = self._can_roll_back(record)
            entries.append(entry)
        return entries

    def _can_roll_back(self, record: MigrationRecord) -> bool:
        """
        Say whether a move can still be undone.

        Args:
            record: The move.

        Returns:
            True when it finished (or a rollback was left half done) and the
            engine its databases left is still there.
        """
        if record.state not in (DONE, ROLLING_BACK):
            return False
        if is_instance_key(record.source_key):
            return self.managed.records.get(record.source_key.partition("@")[2]) is not None
        return True

    def _migration(self, reference: int | str) -> MigrationRecord:
        """
        Find a move by its number, or by the instance it made.

        Args:
            reference: ``3``, or ``pg17``.

        Returns:
            The move.

        Raises:
            DatabaseNotFoundError: There is no such move.
        """
        text = str(reference).strip()
        record = self.records.get(int(text)) if text.isdigit() else self.records.of_target(text)
        if record is None:
            raise DatabaseNotFoundError(
                f"There is no move to a newer version called {text}",
                details="List them with: noust db instance migrations",
            )
        return record

    # ------------------------------------------------------------- planning

    def _source(self, source: str) -> _Source:
        """
        Resolve the engine to move from.

        Args:
            source: An instance's name or key, or the host's engine.

        Returns:
            The engine, running.

        Raises:
            DatabaseError: It is unknown, not installed or not running.
        """
        service = self.managed.service
        if "@" in source or DatabaseRegistry.canonical(source) is None:
            record = self.managed.get(source.partition("@")[2] or source)
            self.managed._require_ready(record, "migrate")
            manager = service.running(key_of(record))
            return _Source(
                key_of(record), record, record.flavour, record.version, manager.DISPLAY_NAME
            )
        manager = service.running(source)
        flavour = manager.installed_flavour() or manager.engine_type
        return _Source(
            manager.ENGINE_NAME, None, flavour, manager.get_version(), manager.DISPLAY_NAME
        )

    def plan(
        self,
        source: str,
        to_version: str,
        *,
        name: str | None = None,
        memory: object = None,
        port: int | None = None,
    ) -> MigrationPlan:
        """
        Decide what moving an engine to a newer version would do, changing nothing.

        Args:
            source: The instance (``pg16``) or the host's engine (``postgresql``).
            to_version: The version to go to: one the catalog offers, newer than
                the one it leaves.
            name: What to call the new instance; the flavour and the version
                when None.
            memory: Its memory limit; the old instance's, or the engine's
                suggestion for the host's.
            port: Its port on 127.0.0.1; a free one when None.

        Returns:
            The plan; ``blockers`` says whether it can go ahead.

        Raises:
            ValidationError: The new instance cannot be had (Docker missing,
                a version the catalog lacks, more memory than is left).
            DatabaseError: The engine is unknown, not installed or not running.
        """
        found = self._source(source)
        spec = spec_for(found.flavour)
        pin = resolve_pin(spec, to_version)
        target = name or f"{spec.flavour}{pin.version.replace('.', '')}"
        check_instance_name(target)
        size = memory or (found.instance.memory_limit_mb if found.instance else None)
        size = size or spec.default_memory_mb
        proposed: InstancePlan = self.managed.plan(
            target, spec.flavour, version=pin.version, memory=size, port=port
        )
        plan = MigrationPlan(
            source=found.key,
            instance=found.instance.name if found.instance else None,
            flavour=spec.flavour,
            display_name=spec.display_name,
            from_version=found.version,
            to_version=pin.version,
            target=target,
            new_instance=proposed.to_dict(),
        )
        blockers, warnings = plan.blockers, plan.warnings

        if spec.engine not in MOVABLE:
            blockers.append(
                f"A {spec.display_name} snapshot is the whole instance and Noust does not load "
                "one into a container: make a new instance and let the application fill it"
            )
        before = release_of(spec.engine, found.version)
        after = release_of(spec.engine, pin.version)
        if before and after and after <= before:
            blockers.append(
                f"{spec.display_name} {pin.version} is not newer than {found.version}: "
                "a move goes to a newer version"
            )

        origin = self.managed.service.running(found.key)
        listing = self.managed.service.listing(found.key)
        if listing.problems:
            blockers.append(
                f"{found.key}'s databases could not be listed: {listing.problems[0].message}"
            )
        apps: set[str] = set()
        generic = ""
        for view in listing.databases:
            if view.missing:
                continue
            one = MovePlan(found.key, target, view.name, origin.engine_type)
            self.mover.inspect(one, origin)
            plan.databases.append(one.to_dict())
            apps.update(one.apps)
            blockers.extend(f"{view.name}: {blocker}" for blocker in one.blockers)
            for warning in one.warnings:
                if "is dumped and loaded again" in warning:
                    generic = warning
                else:
                    warnings.append(f"{view.name}: {warning}")
        if generic:
            warnings.append(
                "Each database is dumped and loaded again: writes made to it while that runs "
                "are not carried over. Stop what writes to the databases for the length of "
                "the move"
            )
        plan.apps = sorted(apps)
        if not plan.databases:
            warnings.append(f"{found.key} holds no database: only the new instance is made")
        warnings.append(
            f"{found.key} is left as it is, with every database as it was, so the move can be "
            "rolled back"
            + (
                " (it is stopped once the applications are on the new instance)."
                if found.instance
                else "."
            )
        )
        return plan

    # ------------------------------------------------------------------ run

    def run(
        self,
        source: str,
        to_version: str,
        *,
        name: str | None = None,
        memory: object = None,
        port: int | None = None,
    ) -> MigrationOutcome:
        """
        Move an engine's databases to a new instance of a newer version.

        Args:
            source: The instance or the host's engine.
            to_version: The version to go to.
            name: What to call the new instance.
            memory: Its memory limit.
            port: Its port on 127.0.0.1.

        Returns:
            What was done; ``migration`` is the number ``rollback`` takes.

        Raises:
            ValidationError: The plan has blockers; nothing was changed.
            DatabaseError: The new instance could not be made, or a database
                could not be moved; everything was undone (the error says what
                could not be).
            DeploymentError: An application did not come up on the new
                instance; everything was undone.
        """
        plan = self.plan(source, to_version, name=name, memory=memory, port=port)
        if plan.blockers:
            self.managed.service.audit("db.instance.migrate", plan.source, "denied", to=plan.target)
            raise ValidationError(
                f"{plan.source} cannot be moved to {plan.display_name} {plan.to_version}",
                details="\n".join(f"- {blocker}" for blocker in plan.blockers),
            )
        record = self.records.begin(
            source_key=plan.source,
            target_name=plan.target,
            flavour=plan.flavour,
            from_version=plan.from_version,
            to_version=plan.to_version,
            created_by=self.managed._actor(),
        )
        target_key = plan.new_instance["key"]
        self.logger.info(
            f"Moving {plan.source} to {plan.display_name} {plan.to_version} as {plan.target}"
        )
        moved: list[MovedDatabase] = []
        outcomes: list[dict[str, Any]] = []
        try:
            self.managed.create(
                plan.target,
                plan.flavour,
                version=plan.to_version,
                memory=plan.new_instance["memory_limit_mb"],
                port=plan.new_instance["host_port"],
            )
            for entry in plan.databases:
                outcome = self.mover.move(plan.source, entry["database"], target_key)
                moved.append(MovedDatabase(outcome.database, tuple(outcome.apps)))
                outcomes.append(outcome.to_dict())
                self.records.set_moved(record.id, moved)
        except (NoustError, OSError) as exc:
            problems = self._give_back(plan.source, target_key, plan.target, moved)
            self.records.set_state(record.id, FAILED)
            self.managed.service.audit(
                "db.instance.migrate", plan.source, "failure", to=plan.target
            )
            if problems and isinstance(exc, NoustError):
                exc.details = "\n".join(
                    part
                    for part in (
                        exc.details,
                        "These could not be undone and need a look: " + "; ".join(problems),
                    )
                    if part
                )
            raise

        warnings: list[str] = []
        kept = [f"{plan.source} with every database as it was"]
        if plan.instance is not None:
            try:
                self.managed.stop(plan.instance)
                kept.append(f"{plan.instance} is stopped, not removed")
            except NoustError as exc:
                warnings.append(f"{plan.instance} could not be stopped: {exc.message}")
        self.records.set_state(record.id, DONE)
        self.managed.service.audit(
            "db.instance.migrate",
            plan.source,
            to=plan.target,
            version=plan.to_version,
            databases=len(moved),
        )
        return MigrationOutcome(record.id, plan.source, plan.target, outcomes, kept, warnings)

    def _give_back(
        self, source_key: str, target_key: str, target: str, moved: list[MovedDatabase]
    ) -> list[str]:
        """
        Undo a move that failed: the applications back on the source, the new instance removed.

        Args:
            source_key: The engine the databases left.
            target_key: The new instance's key.
            target: The new instance's name.
            moved: The databases already carried across.

        Returns:
            What could not be undone, as sentences; empty when all went.
        """
        problems: list[str] = []
        if moved:
            self.logger.warning(f"Pointing the applications back at {source_key}")
        for entry in reversed(moved):
            try:
                self.mover.move(target_key, entry.database, source_key, carry=False)
            except NoustError as exc:
                problems.append(f"{entry.database} could not be pointed back: {exc.message}")
        if problems:
            return problems
        if self.managed.records.get(target) is None:
            return problems
        try:
            self.managed.remove(target, confirm=True)
        except NoustError as exc:
            problems.append(f"{target} could not be removed: {exc.message}")
        return problems

    # ------------------------------------------------------------- rollback

    def rollback(self, reference: int | str) -> RollbackOutcome:
        """
        Point the applications back at the engine a move left.

        The engine still holds every database as it was when the move began.
        Whatever was written to the new instance since is not carried back: an
        older server cannot load a newer one's dump. It is dumped first, so it
        is kept, and the new instance is stopped, not removed.

        Args:
            reference: The move's number, or the name of the instance it made.

        Returns:
            What was done.

        Raises:
            DatabaseNotFoundError: There is no such move.
            DatabaseEngineError: It is not a move that can be rolled back, or
                the engine it left is gone.
            DeploymentError: An application did not come up on the engine it
                left; it has its previous variables back.
        """
        record = self._migration(reference)
        if not self._can_roll_back(record):
            raise DatabaseEngineError(
                f"The move {record.id} cannot be rolled back: it is {record.state}",
                details=(
                    "Only a finished move can be rolled back, and only while the engine it left "
                    "is still there. If that instance was removed, its data is in the dumps "
                    "taken before the move."
                ),
            )
        service = self.managed.service
        source_key = record.source_key
        target = self.managed.get(record.target_name)
        target_key = key_of(target)
        if is_instance_key(source_key):
            old = self.managed.get(source_key.partition("@")[2])
            self.managed.start(old.name)
        self.records.set_state(record.id, ROLLING_BACK)
        warnings: list[str] = []
        dumps: list[str] = []
        try:
            dumps = self.managed.snapshot(target.name)
        except DatabaseBackupError as exc:
            warnings.append(
                f"{target.name} could not be dumped first, so what was written to it since the "
                f"move is not saved anywhere: {exc.message}"
            )
        repointed: list[dict[str, Any]] = []
        for entry in reversed(record.moved):
            outcome = self.mover.move(target_key, entry.database, source_key, carry=False)
            repointed.append({"database": entry.database, "apps": list(outcome.apps)})
        blockers = self.managed.plan_remove(target.name)["blockers"]
        if blockers:
            warnings.append(
                f"{target.name} is not stopped, because something else uses it: "
                + "; ".join(blockers)
            )
        else:
            try:
                self.managed.stop(target.name)
            except NoustError as exc:
                warnings.append(f"{target.name} could not be stopped: {exc.message}")
        self.records.set_state(record.id, ROLLED_BACK)
        service.audit("db.instance.rollback", source_key, to=target.name, databases=len(repointed))
        warnings.append(
            f"What was written to {target.name} since the move is not on {source_key}"
            + (f"; it is in the dumps {', '.join(dumps)}" if dumps else "")
            + f". {target.name} is stopped, not removed: remove it with 'noust db instance "
            f"remove {target.name}' when you are done with it."
        )
        return RollbackOutcome(record.id, source_key, target.name, repointed, dumps, warnings)
