# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How many copies of its web process an application runs, and on which ports (checklist B-68).

One row per application in ``app_replicas`` (``schema_v14.RUNTIME_SQL``) once it has
been scaled beyond its own unit: the ports of the extra copies, in the order they were
given, and how many of them run. No row means one copy, the application's own unit on
the application's port. The ports are chosen once and kept, so scaling down and up again
brings the same ones back and no other application is handed a port a copy of this one
uses: :meth:`~noust.core.store.NoustStore.ports_owned_by_apps` reads them from here.

A table of its own, not columns of ``apps``, so the full-row write of a deploy cannot put
back what an operator changed meanwhile. This is the only module that reads or writes it
(rule 3); what a copy is and how the traffic reaches it is
:mod:`noust.managers.app_replicas`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

#: The most copies of a web process one application runs on a server: the application's
#: own unit and seven more. More than that is a second server's job.
MAX_COPIES = 8


@dataclass(frozen=True)
class ReplicaRecord:
    """
    The extra copies of an application's web process.

    Attributes:
        ports: The ports reserved for the extra copies, in order.
        running: How many of them run (the first ``running`` ports).
        changed_by: Who set it last.
        changed_at: When, as an ISO timestamp.
    """

    ports: tuple[int, ...]
    running: int
    changed_by: str | None = None
    changed_at: str | None = None

    @property
    def active_ports(self) -> tuple[int, ...]:
        """The ports of the copies that run."""
        return self.ports[: self.running]

    @property
    def copies(self) -> int:
        """The copies in all: the application's own unit and the extra ones that run."""
        return 1 + self.running


def check_copies(copies: int) -> int:
    """
    Accept a number of copies.

    Args:
        copies: The copies in all, the application's own unit included.

    Returns:
        The same number.

    Raises:
        ValidationError: It is not a whole number from 1 to :data:`MAX_COPIES`.
    """
    if isinstance(copies, bool) or not isinstance(copies, int) or not 1 <= copies <= MAX_COPIES:
        raise ValidationError(
            f"{copies!r} is not a number of copies",
            details=f"An application runs from 1 to {MAX_COPIES} copies of its web process.",
            field="copies",
        )
    return copies


def get_record(domain: str, *, store: NoustStore | None = None) -> ReplicaRecord | None:
    """
    Read an application's extra copies.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The record, or None for an application that runs its own unit alone.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT r.ports, r.running, r.changed_by, r.changed_at "
            "FROM app_replicas r JOIN apps a ON a.id = r.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    try:
        ports = tuple(int(port) for port in json.loads(row[0] or "[]"))
    except (ValueError, TypeError):
        ports = ()
    return ReplicaRecord(
        ports=ports, running=min(int(row[1]), len(ports)), changed_by=row[2], changed_at=row[3]
    )


def set_record(
    domain: str,
    ports: tuple[int, ...],
    running: int,
    *,
    changed_by: str | None,
    store: NoustStore | None = None,
) -> bool:
    """
    Record the extra copies of an application.

    Args:
        domain: The application's domain.
        ports: The ports reserved for the extra copies.
        running: How many of them run.
        changed_by: Who changed it, for the record.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.

    Raises:
        ValidationError: A port is not one a process can listen on, or more copies are
            running than ports were reserved.
    """
    if any(not 1 <= port <= 65535 for port in ports) or len(set(ports)) != len(ports):
        raise ValidationError("A copy needs a port of its own", field="ports")
    if not 0 <= running <= len(ports):
        raise ValidationError("More copies run than ports were reserved", field="copies")
    with (store or get_store())._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        found = cursor.fetchone()
        if found is None:
            return False
        cursor.execute(
            "INSERT INTO app_replicas (app_id, ports, running, changed_by, changed_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET ports = excluded.ports, "
            "running = excluded.running, changed_by = excluded.changed_by, "
            "changed_at = excluded.changed_at",
            (found[0], json.dumps(list(ports)), running, changed_by, datetime.now().isoformat()),
        )
    return True


def delete_record(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget an application's extra copies, and the ports reserved for them.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        True when there was a record.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_replicas WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        return cursor.rowcount > 0


def reserved_ports(*, store: NoustStore | None = None) -> set[int]:
    """
    List every port reserved for an extra copy, running or not.

    Args:
        store: The store; the process-wide one when None.

    Returns:
        The ports.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute("SELECT ports FROM app_replicas")
        rows = cursor.fetchall()
    taken: set[int] = set()
    for (raw,) in rows:
        try:
            taken.update(int(port) for port in json.loads(raw or "[]"))
        except (ValueError, TypeError):
            continue
    return taken
