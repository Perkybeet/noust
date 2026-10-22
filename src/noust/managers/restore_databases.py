# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The databases no application owns, brought back on a new server.

``noust server migrate prepare`` dumps them and sends the dumps to a destination;
``noust restore-server`` brings the applications back from their own backups, which carry the
databases the applications own. These are the rest: a shared PostgreSQL that three applications
use, an analytics database nobody deployed with an application. Without this step they were
copied and never restored.

What decides each one:

* **Nothing is ever replaced.** A database the new server already has is left as it is (a second
  run, or a database someone made by hand), and a dump is loaded without dropping anything.
* **The newest dump wins wherever it is**: on this server, on the destinations its backup policy
  sends to, and on the one a move was prepared on.
* **What cannot be done here is said, with the command that does it**: an engine that is not
  installed, a database in an instance Noust makes again with ``noust db instance create``, a
  Redis snapshot, which replaces every key of the instance it is loaded into.
* **One failing does not stop the next.** The engine's own words are kept.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.store import Database, NoustStore

__all__ = ["DatabaseOutcome", "OrphanDatabases", "parse_moment"]

#: What a database's outcome says.
RESTORED = "restored"
READY = "ready"
PRESENT = "present"
NO_DUMP = "no_dump"
SKIPPED = "skipped"
FAILED = "failed"

#: Engines whose snapshot replaces every key of the instance it is loaded into.
_WHOLE_INSTANCE = frozenset({"redis", "valkey", "keydb"})


def parse_moment(value: str | None) -> datetime:
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


@dataclass
class DatabaseOutcome:
    """
    What happened to one database.

    Attributes:
        engine: The engine as the store names it.
        name: The database.
        status: ``restored``, ``ready`` (a plan: this dump would be loaded), ``present`` (the
            server already had it), ``no_dump`` (no dump anywhere), ``skipped`` (it cannot be
            done here: ``error`` says why) or ``failed``.
        source: Where the dump came from: ``local`` or a destination's name.
        dump: The dump's file name.
        error: Why it was not restored, verbatim when an engine said it.
        notes: Anything else the operator should know.
    """

    engine: str
    name: str
    status: str
    source: str | None = None
    dump: str | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The outcome as plain data, JSON-ready.
        """
        return asdict(self)


class DumpSource(Protocol):
    """What the restore needs of the database dumps: find them and load one."""

    def list_dumps(self, engine: str, database: str) -> list[Any]:
        """List the dumps on this server, newest first."""

    def destinations_of(self, engine: str, database: str) -> list[str]:
        """Name the destinations the database's backup policy sends to."""

    def remote_dumps(self, engine: str, database: str, destination: str) -> list[dict[str, Any]]:
        """List the dumps a destination holds of a database."""

    def restore(self, engine: str, database: str, file_name: str, **options: Any) -> Any:
        """Load a local dump."""

    def restore_remote(
        self, engine: str, database: str, destination: str, file_name: str, **options: Any
    ) -> Any:
        """Load a dump held on a destination."""


class _RealDumps:
    """The dump source a server has: :class:`~noust.managers.database.backups.DatabaseBackups`."""

    def __init__(self, service: Any, logger: Logger) -> None:
        from noust.managers.database.backups import DatabaseBackups

        self._backups = DatabaseBackups(service)
        self._logger = logger

    def list_dumps(self, engine: str, database: str) -> list[Any]:
        return list(self._backups.list_dumps(engine, database))

    def destinations_of(self, engine: str, database: str) -> list[str]:
        canonical = self._backups.service.manager(engine).ENGINE_NAME
        policy = self._backups.records.policy(canonical, database)
        return [str(entry["name"]) for entry in (policy.destinations if policy else [])]

    def remote_dumps(self, engine: str, database: str, destination: str) -> list[dict[str, Any]]:
        return self._backups.remote_dumps(engine, database, destination)

    def restore(self, engine: str, database: str, file_name: str, **options: Any) -> Any:
        return self._backups.restore(engine, database, file_name, **options)

    def restore_remote(
        self, engine: str, database: str, destination: str, file_name: str, **options: Any
    ) -> Any:
        return self._backups.restore_remote(engine, database, destination, file_name, **options)


class OrphanDatabases:
    """
    Bring back the databases no application owns.

    Args:
        logger: Where progress is reported.
        service: The database service that resolves an engine to its manager; the real one by
            default.
        backups: Finds and loads the dumps; the real ones by default.
    """

    def __init__(
        self,
        *,
        logger: Logger | None = None,
        service: Any = None,
        backups: DumpSource | None = None,
    ) -> None:
        self.logger = logger or Logger(verbose=False)
        self._service = service
        self._backups = backups

    @property
    def service(self) -> Any:
        """The database service."""
        if self._service is None:
            from noust.managers.database.service import DatabaseService

            self._service = DatabaseService(logger=self.logger)
        return self._service

    @property
    def backups(self) -> DumpSource:
        """Finds and loads the dumps."""
        if self._backups is None:
            self._backups = _RealDumps(self.service, self.logger)
        return self._backups

    def candidates(self, store: NoustStore) -> list[Database]:
        """
        List the databases no application owns.

        Args:
            store: The restored store.

        Returns:
            Their rows, by engine and name.
        """
        return sorted(
            (row for row in store.list_databases() if row.app_id is None),
            key=lambda row: (row.engine, row.name),
        )

    def restore(
        self, store: NoustStore, *, from_destination: str | None = None
    ) -> list[DatabaseOutcome]:
        """
        Bring each of those databases back from its newest dump.

        Args:
            store: The restored store.
            from_destination: The destination a move was prepared on, looked at besides the
                ones each database's policy sends to.

        Returns:
            One outcome per database. Never raises for a database: the next one goes on.
        """
        outcomes = []
        for row in self.candidates(store):
            self.logger.info(f"Bringing {row.engine}/{row.name} back")
            outcomes.append(self._one(row, from_destination, apply=True))
        return outcomes

    def plan(
        self, store: NoustStore, *, from_destination: str | None = None
    ) -> list[DatabaseOutcome]:
        """
        Say what :meth:`restore` would do to each of those databases, changing nothing.

        Args:
            store: The restored store.
            from_destination: See :meth:`restore`.

        Returns:
            One outcome per database; ``ready`` names the dump that would be loaded.
        """
        return [self._one(row, from_destination, apply=False) for row in self.candidates(store)]

    def _one(self, row: Database, from_destination: str | None, *, apply: bool) -> DatabaseOutcome:
        """
        Bring one database back, or say what bringing it back would do.

        Args:
            row: The database.
            from_destination: See :meth:`restore`.
            apply: Load the dump; False only reports which one would be.

        Returns:
            What happened.
        """
        engine, name = row.engine, row.name
        outcome = DatabaseOutcome(engine, name, FAILED)
        family = engine.partition("@")[0]
        if "@" in engine:
            outcome.status = SKIPPED
            outcome.error = (
                f"{engine} lives in a database instance Noust ran on the old server: make the "
                "instance again with 'noust db instance create', then restore this database "
                "into it with 'noust db restore'."
            )
            return outcome
        if family in _WHOLE_INSTANCE:
            outcome.status = SKIPPED
            outcome.error = (
                "A snapshot replaces every key of the instance it is loaded into, so it is "
                "never loaded by a restore of the whole server. Restore it with: "
                f"noust db restore {family} {name} FILE"
            )
            return outcome
        try:
            manager = self.service.manager(engine)
            if not manager.is_installed():
                outcome.status = SKIPPED
                outcome.error = f"{family} is not installed here. Run: noust db install {family}"
                return outcome
            if manager.database_exists(name):
                outcome.status = PRESENT
                outcome.notes.append("The server already has it; it was left as it is.")
                return outcome
            found = self._newest(engine, name, from_destination, outcome)
            if found is None:
                outcome.status = NO_DUMP
                outcome.error = (
                    "No dump of it was found on this server or on its destinations. Take one on "
                    f"the old server with: noust db backup {family} {name}"
                )
                return outcome
            source, file_name = found
            if not apply:
                outcome.status, outcome.source, outcome.dump = READY, source, file_name
                return outcome
            if source == "local":
                self.backups.restore(
                    engine, name, file_name, drop_existing=False, safety_backup=False
                )
            else:
                self.backups.restore_remote(
                    engine, name, source, file_name, drop_existing=False, safety_backup=False
                )
        except NoustError as exc:
            outcome.status = FAILED
            outcome.error = f"{exc.message}\n{exc.details}".strip() if exc.details else exc.message
            return outcome
        outcome.status, outcome.source, outcome.dump = RESTORED, source, file_name
        return outcome

    def _newest(
        self, engine: str, name: str, from_destination: str | None, outcome: DatabaseOutcome
    ) -> tuple[str, str] | None:
        """
        Find the newest dump of a database, here or on a destination.

        Args:
            engine: The engine as the store names it.
            name: The database.
            from_destination: A destination to look at besides its policy's.
            outcome: Where a destination that did not answer is noted.

        Returns:
            ``(source, file name)``, or None when there is no dump anywhere.
        """
        best: tuple[datetime, str, str] | None = None
        for view in self.backups.list_dumps(engine, name):
            moment = parse_moment(view.info.created.isoformat())
            if best is None or moment > best[0]:
                best = (moment, "local", view.name)
        names = list(self.backups.destinations_of(engine, name))
        if from_destination and from_destination not in names:
            names.append(from_destination)
        for destination in names:
            try:
                listed = self.backups.remote_dumps(engine, name, destination)
            except NoustError as exc:
                outcome.notes.append(f"{destination} could not be listed: {exc.message}")
                continue
            for entry in listed:
                moment = parse_moment(str(entry.get("modified") or entry.get("created") or ""))
                if best is None or moment > best[0]:
                    best = (moment, destination, str(entry["name"]))
        return (best[1], best[2]) if best else None
