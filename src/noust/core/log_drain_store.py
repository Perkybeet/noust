# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the logs of applications are shipped to, as the store keeps it.

One row per destination in ``log_drains`` (``schema_v14.RUNTIME_SQL``): a Loki, an
Axiom dataset, a New Relic account, an HTTP endpoint, an OTLP collector or a remote syslog. What is not
secret is in ``settings``; the token or password is in the secret store, under
:func:`secret_name`. ``cursors`` records how far each source was shipped, written
only by the shipper after a destination accepted a batch: a destination that was
down receives what it missed when it is back (checklist B-73).

This is the only module that reads or writes the table (rule 3). What a drain may
be told is :mod:`noust.managers.log_drains`; how logs travel to it is
:mod:`noust.managers.log_shipping`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from noust.core.store import NoustStore, get_store

KINDS = ("loki", "axiom", "newrelic", "http", "otlp", "syslog")

#: A drain's name: it becomes part of a secret's name.
NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 40

#: The namespace of the secret store this module's drains keep their token in.
SECRET_NAMESPACE = "log-drains"  # noqa: S105 - a namespace, not a password


def secret_name(name: str) -> str:
    """
    Name the secret a drain keeps its token or password under.

    Args:
        name: The drain's name.

    Returns:
        ``log-drains/<name>``.
    """
    return f"{SECRET_NAMESPACE}/{name}"


@dataclass(frozen=True)
class Drain:
    """
    One destination of logs.

    Attributes:
        name: Its name.
        kind: ``loki``, ``axiom``, ``newrelic``, ``http``, ``otlp`` or ``syslog``.
        settings: What is not secret: URL, labels, transport...
        apps: The applications it receives the logs of; empty is every one.
        enabled: Whether the shipper sends to it.
        cursors: How far each source was shipped.
        last_ok_at: When a batch last reached it.
        last_error: Why the last attempt failed, or None.
        last_error_at: When.
        shipped: How many lines it has received in all.
        created_at: When it was made.
        created_by: Who made it.
        updated_at: When it last changed.
    """

    name: str
    kind: str
    settings: dict[str, Any] = field(default_factory=dict)
    apps: tuple[str, ...] = ()
    enabled: bool = True
    cursors: dict[str, str] = field(default_factory=dict)
    last_ok_at: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    shipped: int = 0
    created_at: str | None = None
    created_by: str | None = None
    updated_at: str | None = None


_SELECT = (
    "SELECT name, kind, settings, apps, enabled, cursors, last_ok_at, last_error, "
    "last_error_at, shipped, created_at, created_by, updated_at FROM log_drains"
)


def _loads(raw: str | None, default: Any) -> Any:
    """
    Decode a JSON column, falling back when it is not what it should be.

    Args:
        raw: The column.
        default: What to return for an empty or damaged one.

    Returns:
        The decoded value.
    """
    try:
        value = json.loads(raw or "")
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _from_row(row: tuple[Any, ...]) -> Drain:
    """
    Build a drain from a selected row.

    Args:
        row: The columns of :data:`_SELECT`.

    Returns:
        The drain.
    """
    return Drain(
        name=row[0],
        kind=row[1],
        settings=_loads(row[2], {}),
        apps=tuple(str(app) for app in _loads(row[3], [])),
        enabled=bool(row[4]),
        cursors={str(k): str(v) for k, v in _loads(row[5], {}).items()},
        last_ok_at=row[6],
        last_error=row[7],
        last_error_at=row[8],
        shipped=int(row[9] or 0),
        created_at=row[10],
        created_by=row[11],
        updated_at=row[12],
    )


def list_drains(*, store: NoustStore | None = None) -> list[Drain]:
    """
    List every destination.

    Args:
        store: The store; the process-wide one when None.

    Returns:
        The drains by name.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(_SELECT + " ORDER BY name")
        return [_from_row(tuple(row)) for row in cursor.fetchall()]


def get_drain(name: str, *, store: NoustStore | None = None) -> Drain | None:
    """
    Read one destination.

    Args:
        name: Its name.
        store: The store; the process-wide one when None.

    Returns:
        The drain, or None.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(_SELECT + " WHERE name = ?", (name,))
        row = cursor.fetchone()
    return _from_row(tuple(row)) if row is not None else None


def put_drain(drain: Drain, *, updated_by: str | None, store: NoustStore | None = None) -> None:
    """
    Store a destination, replacing the one of that name.

    What the shipper records (cursors, outcome, count) is kept when a drain is
    changed: only :func:`record_delivery` and :func:`record_failure` write it.

    Args:
        drain: The destination; validated by the caller.
        updated_by: Who changed it, for the record.
        store: The store; the process-wide one when None.
    """
    now = datetime.now().isoformat()
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "INSERT INTO log_drains (name, kind, settings, apps, enabled, created_at, created_by, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET kind = excluded.kind, settings = excluded.settings, "
            "apps = excluded.apps, enabled = excluded.enabled, updated_at = excluded.updated_at",
            (
                drain.name,
                drain.kind,
                json.dumps(drain.settings, sort_keys=True),
                json.dumps(sorted(drain.apps)),
                1 if drain.enabled else 0,
                now,
                updated_by,
                now,
            ),
        )


def delete_drain(name: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget a destination.

    Args:
        name: Its name.
        store: The store; the process-wide one when None.

    Returns:
        True when there was one.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute("DELETE FROM log_drains WHERE name = ?", (name,))
        return cursor.rowcount > 0


def set_enabled(name: str, enabled: bool, *, store: NoustStore | None = None) -> bool:
    """
    Turn a destination on or off.

    Args:
        name: Its name.
        enabled: Whether the shipper sends to it.
        store: The store; the process-wide one when None.

    Returns:
        True when there was one.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "UPDATE log_drains SET enabled = ?, updated_at = ? WHERE name = ?",
            (1 if enabled else 0, datetime.now().isoformat(), name),
        )
        return cursor.rowcount > 0


def record_delivery(
    name: str, cursors: dict[str, str], lines: int, *, store: NoustStore | None = None
) -> None:
    """
    Record that a destination accepted a batch: how far it got, and how much.

    Args:
        name: The drain.
        cursors: How far each source was shipped; merged over what was.
        lines: How many lines the batch carried.
        store: The store; the process-wide one when None.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute("SELECT cursors FROM log_drains WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row is None:
            return
        merged = {**_loads(row[0], {}), **cursors}
        cursor.execute(
            "UPDATE log_drains SET cursors = ?, shipped = shipped + ?, last_ok_at = ?, "
            "last_error = NULL, last_error_at = NULL WHERE name = ?",
            (json.dumps(merged, sort_keys=True), lines, datetime.now().isoformat(), name),
        )


def record_failure(name: str, error: str, *, store: NoustStore | None = None) -> None:
    """
    Record why a destination did not accept what it was sent.

    Args:
        name: The drain.
        error: What the destination (or the network) said.
        store: The store; the process-wide one when None.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "UPDATE log_drains SET last_error = ?, last_error_at = ? WHERE name = ?",
            (error[:2000], datetime.now().isoformat(), name),
        )
