# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's unit is tuned with beyond memory, CPU and tasks, as the store keeps it.

One row per application in ``app_unit_tuning`` (``schema_v14.RUNTIME_SQL``): the advanced limits
systemd offers (a soft memory limit, swap, CPU and I/O weight, a set of CPUs), the ulimits of the
process, the capabilities it may keep and the devices it may open (checklist B-57, B-60). The
options are one JSON document, validated by :mod:`noust.managers.unit_tuning` before it gets here,
so a new option is a key and not a migration. No row means the unit is as it always was.

A table of its own, not columns of ``apps``, for the reason ``app_hardening`` is one: the full-row
write of a redeploy cannot put back what an operator changed meanwhile. This is the only module
that reads or writes it (rule 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class TuningRecord:
    """
    What an application's unit is tuned with.

    Attributes:
        options: The validated options, by name; a name that is absent is not set.
        changed_by: Who set it last.
        changed_at: When, as an ISO timestamp.
    """

    options: dict[str, Any] = field(default_factory=dict)
    changed_by: str | None = None
    changed_at: str | None = None


def get_record(domain: str, *, store: NoustStore | None = None) -> TuningRecord | None:
    """
    Read an application's tuning.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        Its record, or None for an application whose unit was never tuned.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT t.options, t.changed_by, t.changed_at "
            "FROM app_unit_tuning t JOIN apps a ON a.id = t.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    try:
        loaded = json.loads(row[0] or "{}")
    except (ValueError, TypeError):
        loaded = {}
    options = loaded if isinstance(loaded, dict) else {}
    return TuningRecord(options=options, changed_by=row[1], changed_at=row[2])


def set_record(
    domain: str,
    options: dict[str, Any],
    *,
    changed_by: str | None,
    store: NoustStore | None = None,
) -> bool:
    """
    Store an application's tuning.

    The options are validated by the caller (:mod:`noust.managers.unit_tuning` owns what a unit
    may be given); here they only have to be JSON.

    Args:
        domain: The application's domain.
        options: The options; an empty mapping is the same as :func:`forget_record`.
        changed_by: Who changed it, for the record.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.
    """
    if not options:
        forget_record(domain, store=store)
        return (store or get_store()).get_app(domain) is not None
    target = store or get_store()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        found = cursor.fetchone()
        if found is None:
            return False
        cursor.execute(
            "INSERT INTO app_unit_tuning (app_id, options, changed_by, changed_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
            "options = excluded.options, changed_by = excluded.changed_by, "
            "changed_at = excluded.changed_at",
            (found[0], json.dumps(options, sort_keys=True), changed_by, datetime.now().isoformat()),
        )
    return True


def forget_record(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget an application's tuning, as if its unit was never tuned.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        True when there was a row.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_unit_tuning WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        return cursor.rowcount > 0
