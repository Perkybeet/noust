# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When an application updates itself: the schedule the store keeps (checklist B-23).

One row per application in ``update_schedules`` (``schema_v14.ROLLOUT_SQL``). The row is
the truth about what the application's ``noust-update-<app>.timer`` is for: the calendar
expression, whether an update is refused when its backup does not happen, whether it is
skipped when there is nothing new, whether it is on, and how the last run ended. Writing
the timer is :mod:`noust.managers.update_scheduler`'s; this module only keeps the row and
validates what goes in it (rule 4), and is the only one that reads or writes the table
(rule 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core import audit
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import NoustStore, get_store

#: How a run ended, as the row says it.
UPDATED = "updated"
NOTHING_NEW = "nothing_new"
HELD = "held"
BACKUP_FAILED = "backup_failed"
FAILED = "failed"
OUTCOMES: tuple[str, ...] = (UPDATED, NOTHING_NEW, HELD, BACKUP_FAILED, FAILED)

#: The most of an outcome's detail that is kept.
MAX_DETAIL = 500


@dataclass(frozen=True)
class UpdateSchedule:
    """
    When one application updates itself.

    Attributes:
        domain: The application.
        calendar: The systemd ``OnCalendar`` expression, validated.
        enabled: Whether the timer is on.
        backup_first: The update is refused when its backup does not happen, whatever the
            application's own policy says.
        only_if_new: A run with nothing new to deploy does nothing.
        created_by: Who made it.
        created_at: When.
        updated_at: When it was last changed.
        last_run_at: When it last ran (UTC).
        last_outcome: How the last run ended (one of :data:`OUTCOMES`).
        last_detail: One sentence about it.
        last_deployment_id: The deployment the last run made, when it made one.
    """

    domain: str
    calendar: str
    enabled: bool = True
    backup_first: bool = True
    only_if_new: bool = True
    created_by: str | None = None
    created_at: str = ""
    updated_at: str = ""
    last_run_at: str | None = None
    last_outcome: str | None = None
    last_detail: str | None = None
    last_deployment_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the schedule for the API and ``--json``.

        Returns:
            Every field.
        """
        return {
            "domain": self.domain,
            "calendar": self.calendar,
            "enabled": self.enabled,
            "backup_first": self.backup_first,
            "only_if_new": self.only_if_new,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run_at": self.last_run_at,
            "last_outcome": self.last_outcome,
            "last_detail": self.last_detail,
            "last_deployment_id": self.last_deployment_id,
        }


def _now() -> str:
    """
    Returns:
        The current time in UTC, ISO 8601 with its offset.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SELECT = (
    "SELECT a.domain, s.calendar, s.enabled, s.backup_first, s.only_if_new, s.created_by, "
    "s.created_at, s.updated_at, s.last_run_at, s.last_outcome, s.last_detail, "
    "s.last_deployment_id FROM update_schedules s JOIN apps a ON a.id = s.app_id"
)


def _from_row(row: Any) -> UpdateSchedule:
    """
    Args:
        row: A row of :data:`_SELECT`.

    Returns:
        The schedule.
    """
    return UpdateSchedule(
        domain=str(row[0]),
        calendar=str(row[1]),
        enabled=bool(row[2]),
        backup_first=bool(row[3]),
        only_if_new=bool(row[4]),
        created_by=row[5],
        created_at=str(row[6]),
        updated_at=str(row[7]),
        last_run_at=row[8],
        last_outcome=row[9],
        last_detail=row[10],
        last_deployment_id=row[11],
    )


def get(domain: str, *, store: NoustStore | None = None) -> UpdateSchedule | None:
    """
    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        Its schedule, or None.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(f"{_SELECT} WHERE a.domain = ?", (domain,))
        row = cursor.fetchone()
    return _from_row(row) if row is not None else None


def list_all(*, store: NoustStore | None = None) -> list[UpdateSchedule]:
    """
    Args:
        store: The store; the process-wide one when None.

    Returns:
        Every schedule, by domain.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(f"{_SELECT} ORDER BY a.domain")
        return [_from_row(row) for row in cursor.fetchall()]


def save(
    domain: str,
    calendar: str,
    *,
    enabled: bool = True,
    backup_first: bool = True,
    only_if_new: bool = True,
    by: str | None = None,
    store: NoustStore | None = None,
) -> UpdateSchedule:
    """
    Make, or change, an application's schedule.

    Args:
        domain: The application's domain.
        calendar: The ``OnCalendar`` expression, already validated by the scheduler.
        enabled: Whether the timer is on.
        backup_first: Refuse the update when its backup does not happen.
        only_if_new: Do nothing when there is nothing new.
        by: Who changed it.
        store: The store; the process-wide one when None.

    Returns:
        The schedule as stored.

    Raises:
        ValidationError: An empty calendar, or a flag that is not true or false.
        NoustError: The application is unknown.
    """
    if not calendar or not calendar.strip():
        raise ValidationError("A schedule needs a calendar expression", field="calendar")
    for name, value in (
        ("enabled", enabled),
        ("backup_first", backup_first),
        ("only_if_new", only_if_new),
    ):
        if not isinstance(value, bool):
            raise ValidationError(f"{name} must be true or false", field=name)
    target = store or get_store()
    now = _now()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        cursor.execute(
            "INSERT INTO update_schedules (app_id, calendar, enabled, backup_first, "
            "only_if_new, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(app_id) DO UPDATE SET calendar = excluded.calendar, "
            "enabled = excluded.enabled, backup_first = excluded.backup_first, "
            "only_if_new = excluded.only_if_new, updated_at = excluded.updated_at",
            (
                row[0],
                calendar.strip(),
                int(enabled),
                int(backup_first),
                int(only_if_new),
                by,
                now,
                now,
            ),
        )
    saved = get(domain, store=target)
    if saved is None:
        raise NoustError("The schedule was not recorded")
    audit.record(
        "deploys.schedule",
        target=f"app:{domain}",
        details={
            "calendar": saved.calendar,
            "enabled": saved.enabled,
            "backup_first": saved.backup_first,
            "only_if_new": saved.only_if_new,
        },
    )
    return saved


def delete(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        True when there was a schedule.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM update_schedules WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        removed = cursor.rowcount > 0
    if removed:
        audit.record("deploys.schedule", target=f"app:{domain}", details={"removed": True})
    return removed


def record_run(
    domain: str,
    outcome: str,
    detail: str | None = None,
    *,
    deployment_id: int | None = None,
    store: NoustStore | None = None,
) -> None:
    """
    Remember how a run ended.

    Args:
        domain: The application's domain.
        outcome: One of :data:`OUTCOMES`.
        detail: One sentence about it.
        deployment_id: The deployment it made, when it made one.
        store: The store; the process-wide one when None.

    Raises:
        ValueError: ``outcome`` is not one of :data:`OUTCOMES`.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"not an outcome of a scheduled update: {outcome!r}")
    text = " ".join((detail or "").split())[:MAX_DETAIL] or None
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "UPDATE update_schedules SET last_run_at = ?, last_outcome = ?, last_detail = ?, "
            "last_deployment_id = ? WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (_now(), outcome, text, deployment_id, domain),
        )
    audit.record(
        "deploys.scheduled",
        target=f"app:{domain}",
        outcome="ok" if outcome in (UPDATED, NOTHING_NEW, HELD) else "failure",
        details={"outcome": outcome, "detail": text, "deployment": deployment_id},
    )


__all__ = [
    "BACKUP_FAILED",
    "FAILED",
    "HELD",
    "NOTHING_NEW",
    "OUTCOMES",
    "UPDATED",
    "UpdateSchedule",
    "delete",
    "get",
    "list_all",
    "record_run",
    "save",
]
