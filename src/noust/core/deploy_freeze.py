# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Freezing deploys: a stretch of time, or an incident, in which nothing is deployed.

A freeze says "do not change production now". It can cover one application or the
whole server, start now or at a time an operator announces (the end of a sale, a
holiday), end at a time or last until somebody lifts it, and it always says why. It
is a row of ``deploy_freezes`` (``schema_v14.ROLLOUT_SQL``); lifting one keeps the
row, so the history of what was held and by whom stays.

What a freeze holds and what it does not is :mod:`noust.core.deploy_guard`'s to say.
This module only keeps the rows and answers which ones apply (rule 3), and every
change to them is validated here and audited (rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from noust.core import audit
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import NoustStore, get_store

#: Longest reason a freeze keeps.
MAX_REASON = 300

#: The longest a freeze with an end may last. A longer one is a freeze with no end,
#: which asks for a person to lift it.
MAX_DAYS = 90

#: The scope word of a freeze that covers the whole server.
SERVER = "server"


@dataclass(frozen=True)
class Freeze:
    """
    One freeze.

    Attributes:
        id: Its number.
        domain: The application it covers; None for the whole server.
        reason: Why.
        starts_at: When it starts (UTC, ISO 8601); possibly in the future.
        ends_at: When it ends; None lasts until somebody lifts it.
        created_by: Who froze.
        created_at: When it was made.
        lifted_at: When somebody lifted it early; None while it was not.
        lifted_by: Who lifted it.
    """

    id: int
    domain: str | None
    reason: str
    starts_at: str
    ends_at: str | None
    created_by: str | None
    created_at: str
    lifted_at: str | None = None
    lifted_by: str | None = None

    def state(self, now: datetime | None = None) -> str:
        """
        Args:
            now: The moment to judge at; the clock when None.

        Returns:
            ``active``, ``scheduled`` (starts later), ``ended`` or ``lifted``.
        """
        moment = now or _now()
        if self.lifted_at is not None:
            return "lifted"
        if _parse(self.starts_at) > moment:
            return "scheduled"
        if self.ends_at is not None and _parse(self.ends_at) <= moment:
            return "ended"
        return "active"

    def to_dict(self, now: datetime | None = None) -> dict[str, Any]:
        """
        Describe the freeze for the API and ``--json``.

        Args:
            now: The moment to judge the state at.

        Returns:
            Every field, the scope as ``server`` or the domain, and the state.
        """
        return {
            "id": self.id,
            "scope": self.domain or SERVER,
            "domain": self.domain,
            "reason": self.reason,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "lifted_at": self.lifted_at,
            "lifted_by": self.lifted_by,
            "state": self.state(now),
        }


def _now() -> datetime:
    """
    Read the clock. A function so tests can move it.

    Returns:
        The current time in UTC.
    """
    return datetime.now(timezone.utc)


def _parse(text: str) -> datetime:
    """
    Args:
        text: A timestamp the store wrote.

    Returns:
        It as an aware UTC datetime.
    """
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    """
    Args:
        moment: An aware datetime.

    Returns:
        UTC, seconds, with the offset, as the store keeps every moment.
    """
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_moment(value: str | datetime, *, field: str) -> datetime:
    """
    Read a moment an operator typed.

    Args:
        value: ISO 8601 (``2026-12-24T18:00``, with or without an offset; none is
            the server's local time) or a datetime.
        field: The name to blame in a refusal.

    Returns:
        It as an aware UTC datetime.

    Raises:
        ValidationError: It is not a moment.
    """
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(
                f"Not a date and time: {value!r}",
                details="Write it like 2026-12-24T18:00 or 2026-12-24T18:00+01:00.",
                field=field,
            ) from exc
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc)


def _from_row(row: Any) -> Freeze:
    """
    Args:
        row: A ``deploy_freezes`` row joined to its application's domain.

    Returns:
        The freeze.
    """
    return Freeze(
        id=int(row[0]),
        domain=row[1],
        reason=str(row[2]),
        starts_at=str(row[3]),
        ends_at=row[4],
        created_by=row[5],
        created_at=str(row[6]),
        lifted_at=row[7],
        lifted_by=row[8],
    )


_SELECT = (
    "SELECT f.id, a.domain, f.reason, f.starts_at, f.ends_at, f.created_by, f.created_at, "
    "f.lifted_at, f.lifted_by FROM deploy_freezes f LEFT JOIN apps a ON a.id = f.app_id"
)


def create(
    reason: str,
    *,
    domain: str | None = None,
    starts_at: str | datetime | None = None,
    ends_at: str | datetime | None = None,
    until_hours: float | None = None,
    by: str | None = None,
    store: NoustStore | None = None,
) -> Freeze:
    """
    Freeze deploys.

    Args:
        reason: Why, in a sentence; required, because whoever meets the freeze
            reads it.
        domain: The application to freeze; None freezes the whole server.
        starts_at: When it starts; now when None.
        ends_at: When it ends; none lasts until somebody lifts it.
        until_hours: Instead of ``ends_at``, how long from its start.
        by: Who froze.
        store: The store; the process-wide one when None.

    Returns:
        The freeze.

    Raises:
        ValidationError: No reason, a reason too long, an end that is not after the
            start, or both ``ends_at`` and ``until_hours``.
        NoustError: The application is unknown.
    """
    text = (reason or "").strip()
    if not text:
        raise ValidationError(
            "A freeze needs a reason",
            details="Whoever tries to deploy meanwhile is told it, so make it one they "
            "can act on: 'incident 412, until the database is back'.",
            field="reason",
        )
    if len(text) > MAX_REASON:
        raise ValidationError(
            f"The reason is {len(text)} characters long; {MAX_REASON} is the most", field="reason"
        )
    if ends_at is not None and until_hours is not None:
        raise ValidationError("Give when it ends, or for how long, not both", field="ends_at")
    start = parse_moment(starts_at, field="starts_at") if starts_at is not None else _now()
    end: datetime | None = None
    if ends_at is not None:
        end = parse_moment(ends_at, field="ends_at")
    elif until_hours is not None:
        if isinstance(until_hours, bool) or until_hours <= 0:
            raise ValidationError("A freeze lasts for a positive number of hours", field="hours")
        end = start + timedelta(hours=until_hours)
    if end is not None:
        if end <= start:
            raise ValidationError(
                "A freeze must end after it starts",
                details=f"It starts {_iso(start)} and was asked to end {_iso(end)}.",
                field="ends_at",
            )
        if end - start > timedelta(days=MAX_DAYS):
            raise ValidationError(
                f"A freeze with an end lasts at most {MAX_DAYS} days",
                details="Leave the end out for one that lasts until somebody lifts it.",
                field="ends_at",
            )
    target = store or get_store()
    with target._transaction() as cursor:
        app_id = None
        if domain is not None:
            cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
            found = cursor.fetchone()
            if found is None:
                raise NoustError(
                    f"Application not found: {domain}",
                    details="Run 'noust list' to see what is deployed.",
                )
            app_id = found[0]
        cursor.execute(
            "INSERT INTO deploy_freezes (app_id, reason, starts_at, ends_at, created_by, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                app_id,
                text,
                _iso(start),
                None if end is None else _iso(end),
                by,
                _iso(_now()),
            ),
        )
        identifier = int(cursor.lastrowid or 0)
    freeze = get_freeze(identifier, store=target)
    if freeze is None:
        raise NoustError("The freeze was not recorded")
    audit.record(
        "deploys.freeze",
        target=f"app:{domain}" if domain else "server",
        details={
            "id": identifier,
            "reason": text,
            "starts_at": freeze.starts_at,
            "ends_at": freeze.ends_at,
        },
    )
    return freeze


def get_freeze(identifier: int, *, store: NoustStore | None = None) -> Freeze | None:
    """
    Args:
        identifier: The freeze's number.
        store: The store; the process-wide one when None.

    Returns:
        The freeze, or None.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(f"{_SELECT} WHERE f.id = ?", (identifier,))
        row = cursor.fetchone()
    return _from_row(row) if row is not None else None


def list_freezes(
    *, include_past: bool = False, store: NoustStore | None = None, now: datetime | None = None
) -> list[Freeze]:
    """
    List freezes, newest first.

    Args:
        include_past: Also the ones that ended or were lifted.
        store: The store; the process-wide one when None.
        now: The moment to judge at; the clock when None.

    Returns:
        The active and scheduled freezes, and with ``include_past`` the rest.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(f"{_SELECT} ORDER BY f.id DESC")
        rows = [_from_row(row) for row in cursor.fetchall()]
    if include_past:
        return rows
    moment = now or _now()
    return [freeze for freeze in rows if freeze.state(moment) in ("active", "scheduled")]


def active_for(
    domain: str, *, store: NoustStore | None = None, now: datetime | None = None
) -> Freeze | None:
    """
    Find the freeze that holds an application's deploys now.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.
        now: The moment to judge at; the clock when None.

    Returns:
        The one that ends last (the application's own over the server's when they end
        together), or None when nothing is frozen.
    """
    moment = now or _now()
    held = [
        freeze
        for freeze in list_freezes(store=store, now=moment)
        if freeze.state(moment) == "active" and freeze.domain in (None, domain)
    ]
    if not held:
        return None
    far = datetime.max.replace(tzinfo=timezone.utc)
    return max(
        held,
        key=lambda freeze: (
            _parse(freeze.ends_at) if freeze.ends_at else far,
            freeze.domain is not None,
            freeze.id,
        ),
    )


def lift(
    domain: str | None = None,
    *,
    identifier: int | None = None,
    by: str | None = None,
    store: NoustStore | None = None,
) -> list[Freeze]:
    """
    Lift freezes before they end.

    Args:
        domain: The application whose own freezes are lifted; None lifts the server's.
        identifier: Lift this one freeze instead.
        by: Who lifts.
        store: The store; the process-wide one when None.

    Returns:
        The freezes that were lifted; empty when nothing was frozen there.
    """
    target = store or get_store()
    moment = _now()
    candidates = [
        freeze
        for freeze in list_freezes(store=target, now=moment)
        if (freeze.id == identifier if identifier is not None else freeze.domain == domain)
    ]
    lifted: list[Freeze] = []
    with target._transaction() as cursor:
        for freeze in candidates:
            cursor.execute(
                "UPDATE deploy_freezes SET lifted_at = ?, lifted_by = ? "
                "WHERE id = ? AND lifted_at IS NULL",
                (_iso(moment), by, freeze.id),
            )
            if cursor.rowcount:
                lifted.append(freeze)
    for freeze in lifted:
        audit.record(
            "deploys.unfreeze",
            target=f"app:{freeze.domain}" if freeze.domain else "server",
            details={"id": freeze.id, "reason": freeze.reason, "by": by},
        )
    return lifted


__all__ = [
    "MAX_DAYS",
    "MAX_REASON",
    "SERVER",
    "Freeze",
    "active_for",
    "create",
    "get_freeze",
    "lift",
    "list_freezes",
    "parse_moment",
]
