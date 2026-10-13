# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The TCP and UDP ports Noust publishes in front of a backend.

One table of the networking fragment of schema v14, ``stream_exposures``. A row is
written before anything on the machine changes and says how far the opening or the
closing got (``state``), so an interrupted one is finished from the row, and
``firewall`` records exactly what Noust opened for it, which is all it will close.

:class:`StreamStore` is the only reader and writer; what may be exposed, and how, is
:mod:`noust.managers.stream_exposure`'s.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from noust.core.store import NoustStore, get_store

#: The record exists and nothing, or only part of it, is on the machine yet.
OPENING = "opening"

#: The port is published.
OPEN = "open"

#: The port is being taken down.
CLOSING = "closing"


@dataclass(frozen=True)
class StreamExposure:
    """
    A published TCP or UDP port.

    Attributes:
        name: What it is called; also the name of its nginx file.
        protocol: ``tcp`` or ``udp``.
        listen_port: The port visitors reach, on every address.
        target_host: Where traffic goes: an IP address.
        target_port: The backend's port.
        sources: Addresses or networks that may reach it; empty is anyone.
        timeout_seconds: How long an idle connection (TCP) or session (UDP) is kept.
        app_domain: The application it is for, when it is for one.
        firewall: What Noust opened in the firewall for it:
            ``{"backend", "active", "made": [{"port", "proto", "source"}], "note"}``.
        state: ``opening``, ``open`` or ``closing``.
        created_at: When it was recorded.
        created_by: Who asked.
    """

    name: str
    protocol: str
    listen_port: int
    target_host: str
    target_port: int
    sources: tuple[str, ...] = ()
    timeout_seconds: int = 3600
    app_domain: str | None = None
    firewall: dict[str, Any] = field(default_factory=dict)
    state: str = OPENING
    created_at: str = ""
    created_by: str | None = None

    @property
    def target(self) -> str:
        """The backend as nginx writes it: ``host:port``, an IPv6 address in brackets."""
        host = f"[{self.target_host}]" if ":" in self.target_host else self.target_host
        return f"{host}:{self.target_port}"

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The exposure for the API and the CLI.
        """
        return {
            "name": self.name,
            "protocol": self.protocol,
            "listen_port": self.listen_port,
            "target": self.target,
            "target_host": self.target_host,
            "target_port": self.target_port,
            "sources": list(self.sources),
            "timeout_seconds": self.timeout_seconds,
            "app_domain": self.app_domain,
            "firewall": self.firewall,
            "state": self.state,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


class StreamStore:
    """
    Read and write the published ports.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def all(self) -> list[StreamExposure]:
        """
        Returns:
            Every exposure, by port.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM stream_exposures ORDER BY listen_port, protocol")
            .fetchall()
        )
        return [_exposure(row) for row in rows]

    def get(self, name: str) -> StreamExposure | None:
        """
        Args:
            name: The exposure's name.

        Returns:
            It, or None when there is none of that name.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM stream_exposures WHERE name = ?", (name,))
            .fetchone()
        )
        return None if row is None else _exposure(row)

    def on_port(self, port: int, protocol: str) -> StreamExposure | None:
        """
        Args:
            port: A port.
            protocol: ``tcp`` or ``udp``.

        Returns:
            The exposure that holds it, if any.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM stream_exposures WHERE listen_port = ? AND protocol = ?",
                (port, protocol),
            )
            .fetchone()
        )
        return None if row is None else _exposure(row)

    def ports(self) -> set[int]:
        """
        Returns:
            Every port an exposure holds, whatever its protocol.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT listen_port FROM stream_exposures")
            .fetchall()
        )
        return {int(row["listen_port"]) for row in rows}

    def create(self, exposure: StreamExposure) -> StreamExposure:
        """
        Record an exposure that is being opened.

        Args:
            exposure: What to keep; its ``created_at`` is set here.

        Returns:
            The recorded exposure.

        Raises:
            sqlite3.IntegrityError: The name or the port and protocol are held.
        """
        created = exposure.created_at or _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO stream_exposures
                    (name, app_domain, protocol, listen_port, target_host, target_port,
                     sources, timeout_seconds, firewall, state, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    exposure.name,
                    exposure.app_domain,
                    exposure.protocol,
                    exposure.listen_port,
                    exposure.target_host,
                    exposure.target_port,
                    json.dumps(list(exposure.sources)),
                    exposure.timeout_seconds,
                    json.dumps(exposure.firewall, sort_keys=True),
                    exposure.state,
                    created,
                    exposure.created_by,
                ),
            )
        return replace(exposure, created_at=created)

    def set_state(
        self, name: str, state: str, firewall: dict[str, Any] | None = None
    ) -> StreamExposure | None:
        """
        Record how far an opening or a closing got.

        Args:
            name: The exposure.
            state: ``opening``, ``open`` or ``closing``.
            firewall: What Noust opened, when that changed.

        Returns:
            The exposure now, or None when there is none of that name.
        """
        with self.store._transaction() as cursor:
            if firewall is None:
                cursor.execute(
                    "UPDATE stream_exposures SET state = ? WHERE name = ?", (state, name)
                )
            else:
                cursor.execute(
                    "UPDATE stream_exposures SET state = ?, firewall = ? WHERE name = ?",
                    (state, json.dumps(firewall, sort_keys=True), name),
                )
        return self.get(name)

    def delete(self, name: str) -> bool:
        """
        Forget an exposure.

        Args:
            name: The exposure.

        Returns:
            True when it existed.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM stream_exposures WHERE name = ?", (name,))
            return cursor.rowcount > 0


def _exposure(row: sqlite3.Row) -> StreamExposure:
    """
    Args:
        row: A ``stream_exposures`` row.

    Returns:
        The exposure it holds.
    """
    return StreamExposure(
        name=row["name"],
        app_domain=row["app_domain"],
        protocol=row["protocol"],
        listen_port=int(row["listen_port"]),
        target_host=row["target_host"],
        target_port=int(row["target_port"]),
        sources=tuple(json.loads(row["sources"] or "[]")),
        timeout_seconds=int(row["timeout_seconds"]),
        firewall=json.loads(row["firewall"] or "{}"),
        state=row["state"],
        created_at=row["created_at"],
        created_by=row["created_by"],
    )


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 UTC.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
