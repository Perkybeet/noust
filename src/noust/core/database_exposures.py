# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which databases are deliberately reachable from the network, and how.

A database is private unless a row here says otherwise. The row is written
*before* the first thing is changed, with ``state`` ``opening``, and says
``closing`` before the first thing is undone, so an interrupted action (a
console that died, a server that rebooted) is finished or undone from what the
row records instead of from a guess: ``make private`` removes whatever the row
says was made, and whatever it says was not is left alone.

Every write goes through the store's transaction, so ``--dry-run`` rehearses it
and rolls it back like any other change of the store.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from noust.core.exceptions import DatabaseExistsError
from noust.core.store import NoustStore, get_store

#: The states a row goes through: being opened, open, being closed.
OPENING = "opening"
EXPOSED = "exposed"
CLOSING = "closing"
STATES = (OPENING, EXPOSED, CLOSING)

#: Whether the account must use TLS.
TLS_REQUIRED = "required"
TLS_OPTIONAL = "optional"


@dataclass(frozen=True)
class Exposure:
    """
    One database that is reachable from the network.

    Attributes:
        id: Row id.
        engine: The engine's name.
        db_name: The database (a slot number for Redis).
        port: The public port.
        bind_address: The address it listens on (``0.0.0.0``, ``::`` or one).
        sources: The addresses allowed in, as CIDRs; ``any`` for everyone.
        tls: ``required`` or ``optional``.
        remote_user: The account made for it, which is what is given out.
        profile: The access it has: ``owner``, ``read_write`` or ``read_only``.
        unit: The socket and service unit's name, without the suffix.
        firewall: What was done to the firewall: ``{"backend": ..., "rules":
            [{"source": ..., "created": bool}]}``; None before it was touched.
        state: ``opening``, ``exposed`` or ``closing``.
        created_at: When it was opened.
        created_by: Who opened it.
    """

    id: int
    engine: str
    db_name: str
    port: int
    bind_address: str
    sources: tuple[str, ...]
    tls: str
    remote_user: str
    profile: str
    unit: str
    state: str
    created_at: str
    firewall: dict[str, Any] | None = None
    created_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "engine": self.engine,
            "database": self.db_name,
            "port": self.port,
            "bind_address": self.bind_address,
            "sources": list(self.sources),
            "tls": self.tls,
            "remote_user": self.remote_user,
            "profile": self.profile,
            "unit": self.unit,
            "firewall": self.firewall,
            "state": self.state,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


def _now() -> str:
    """
    Returns:
        The time, in the store's own spelling: ISO 8601 without an offset.
    """
    return datetime.now().isoformat(timespec="seconds")


def _build(row: sqlite3.Row) -> Exposure:
    """
    Build an exposure from its row.

    Args:
        row: A ``database_exposures`` row.

    Returns:
        The exposure.
    """
    firewall = json.loads(row["firewall"]) if row["firewall"] else None
    return Exposure(
        id=row["id"],
        engine=row["engine"],
        db_name=row["db_name"],
        port=row["port"],
        bind_address=row["bind_address"],
        sources=tuple(json.loads(row["sources"])),
        tls=row["tls"],
        remote_user=row["remote_user"],
        profile=row["profile"],
        unit=row["unit"],
        firewall=firewall,
        state=row["state"],
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


class ExposureStore:
    """
    Read and write the ``database_exposures`` table.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            self._store = get_store()
        return self._store

    def all(self) -> list[Exposure]:
        """
        List every exposure.

        Returns:
            The rows, by engine and database.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM database_exposures ORDER BY engine, db_name")
            return [_build(row) for row in cursor.fetchall()]

    def get(self, engine: str, db_name: str) -> Exposure | None:
        """
        Find a database's exposure.

        Args:
            engine: The engine.
            db_name: The database.

        Returns:
            The exposure, or None when the database is private.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM database_exposures WHERE engine = ? AND db_name = ?",
                (engine, db_name),
            )
            row = cursor.fetchone()
        return _build(row) if row is not None else None

    def on_port(self, port: int) -> Exposure | None:
        """
        Find the exposure that holds a public port.

        Args:
            port: The port.

        Returns:
            The exposure, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM database_exposures WHERE port = ?", (port,))
            row = cursor.fetchone()
        return _build(row) if row is not None else None

    def create(
        self,
        *,
        engine: str,
        db_name: str,
        port: int,
        bind_address: str,
        sources: tuple[str, ...],
        tls: str,
        remote_user: str,
        profile: str,
        unit: str,
        created_by: str | None,
    ) -> Exposure:
        """
        Record that a database is being made reachable, before anything is changed.

        Args:
            engine: The engine.
            db_name: The database.
            port: The public port.
            bind_address: Where it listens.
            sources: Who may reach it.
            tls: ``required`` or ``optional``.
            remote_user: The account made for it.
            profile: The access that account has.
            unit: The unit's name without a suffix.
            created_by: Who is doing it.

        Returns:
            The row, ``opening``.

        Raises:
            DatabaseExistsError: When the database already has one, or the
                port is held by another.
        """
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO database_exposures (engine, db_name, port, bind_address, "
                    "sources, tls, remote_user, profile, unit, state, created_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        engine,
                        db_name,
                        port,
                        bind_address,
                        json.dumps(list(sources)),
                        tls,
                        remote_user,
                        profile,
                        unit,
                        OPENING,
                        _now(),
                        created_by,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DatabaseExistsError(
                f"{engine}/{db_name} is already reachable, or port {port} is taken",
                details="See what is open with: noust db public list",
            ) from exc
        created = self.get(engine, db_name)
        if created is None:  # a rehearsal rolls the insert back
            return Exposure(
                id=0,
                engine=engine,
                db_name=db_name,
                port=port,
                bind_address=bind_address,
                sources=sources,
                tls=tls,
                remote_user=remote_user,
                profile=profile,
                unit=unit,
                state=OPENING,
                created_at=_now(),
                created_by=created_by,
            )
        return created

    def set_state(self, engine: str, db_name: str, state: str) -> None:
        """
        Say how far opening or closing got.

        Args:
            engine: The engine.
            db_name: The database.
            state: One of :data:`STATES`.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE database_exposures SET state = ? WHERE engine = ? AND db_name = ?",
                (state, engine, db_name),
            )

    def set_firewall(self, engine: str, db_name: str, firewall: dict[str, Any] | None) -> None:
        """
        Record what was done to the firewall, so it can be undone exactly.

        Args:
            engine: The engine.
            db_name: The database.
            firewall: ``{"backend": ..., "rules": [...]}``, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE database_exposures SET firewall = ? WHERE engine = ? AND db_name = ?",
                (json.dumps(firewall) if firewall is not None else None, engine, db_name),
            )

    def delete(self, engine: str, db_name: str) -> None:
        """
        Forget an exposure: the database is private again.

        Args:
            engine: The engine.
            db_name: The database.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM database_exposures WHERE engine = ? AND db_name = ?",
                (engine, db_name),
            )
