# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The alert rules and where each one stands, in the store.

:class:`AlertRules` is the only reader and writer of ``alert_rules`` and
``alert_states`` (store v14). The CLI, the API and the monitor all go through
it, so a rule is validated in one place (:func:`~noust.core.alerts.model.validate_rule`)
and a state change is one transaction.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from noust.core.alerts.model import MAX_RULES, AlertRule, validate_rule
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

FIRING = "firing"
OK = "ok"

#: The longest name kept for whoever took an alert.
MAX_ACKNOWLEDGER = 80


@dataclass(frozen=True)
class AlertState:
    """
    Where one subject of one rule stands.

    Attributes:
        rule_id: The rule.
        subject: The application, or empty for the server.
        state: ``firing`` or ``ok``.
        since: Epoch seconds the state began.
        last_value: The reading that last decided it.
        announced: Whether the current firing was told to the channels.
        announced_at: The last time a firing message went out for this subject.
        acknowledged_at: When somebody took the firing, or None. Only a firing
            has one: it ends with the firing.
        acknowledged_by: Who took it.
    """

    rule_id: int
    subject: str
    state: str
    since: float
    last_value: float | None = None
    announced: bool = False
    announced_at: float | None = None
    acknowledged_at: float | None = None
    acknowledged_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The state as the API shows it.
        """
        return {
            "rule_id": self.rule_id,
            "subject": self.subject,
            "state": self.state,
            "since": self.since,
            "last_value": self.last_value,
            "announced": self.announced,
            "announced_at": self.announced_at,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
        }


def _rule(row: sqlite3.Row) -> AlertRule:
    """
    Args:
        row: A row of ``alert_rules``.

    Returns:
        The rule.
    """
    return AlertRule(
        id=int(row["id"]),
        name=str(row["name"]),
        metric=str(row["metric"]),
        app_domain=row["app_domain"],
        comparator=str(row["comparator"]),
        threshold=float(row["threshold"]),
        duration_s=int(row["duration_s"]),
        hysteresis=float(row["hysteresis"]),
        cooldown_s=int(row["cooldown_s"]),
        severity=str(row["severity"]),
        enabled=bool(row["enabled"]),
        remind=bool(row["remind"]),
        note=str(row["note"] or ""),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _state(row: sqlite3.Row) -> AlertState:
    """
    Args:
        row: A row of ``alert_states``.

    Returns:
        The state.
    """
    return AlertState(
        rule_id=int(row["rule_id"]),
        subject=str(row["subject"]),
        state=str(row["state"]),
        since=float(row["since"]),
        last_value=float(row["last_value"]) if row["last_value"] is not None else None,
        announced=bool(row["announced"]),
        announced_at=float(row["announced_at"]) if row["announced_at"] is not None else None,
        acknowledged_at=(
            float(row["acknowledged_at"]) if row["acknowledged_at"] is not None else None
        ),
        acknowledged_by=row["acknowledged_by"],
    )


class AlertRules:
    """The store's alert rules and their states."""

    def __init__(
        self, store: NoustStore | None = None, *, clock: Callable[[], float] = time.time
    ) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
            clock: Epoch seconds. Tests move it.
        """
        self._store = store
        self._clock = clock

    @property
    def store(self) -> NoustStore:
        """The store the rules live in."""
        return self._store if self._store is not None else get_store()

    # -- rules ---------------------------------------------------------------

    def rules(self) -> list[AlertRule]:
        """
        Returns:
            Every rule, by name.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM alert_rules ORDER BY name")
            return [_rule(row) for row in cursor.fetchall()]

    def get(self, name: str) -> AlertRule | None:
        """
        Args:
            name: A rule's name.

        Returns:
            The rule, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM alert_rules WHERE name = ?", (name,))
            row = cursor.fetchone()
        return _rule(row) if row is not None else None

    def save(self, rule: AlertRule, *, actor: str | None = None) -> AlertRule:
        """
        Create a rule, or replace the one of the same name.

        A replaced rule keeps its row (and so what it remembers), but clears
        the states that were decided by the old limit: a rule edited from 90
        to 95 must not carry a "firing" that 92 would no longer be.

        Args:
            rule: The rule.
            actor: Who made the change, for the record.

        Returns:
            The rule as stored.

        Raises:
            ValidationError: The rule is not acceptable, or there are already
                :data:`~noust.core.alerts.model.MAX_RULES`.
        """
        validate_rule(rule)
        now = self._clock()
        with self.store._transaction() as cursor:
            cursor.execute("SELECT id FROM alert_rules WHERE name = ?", (rule.name,))
            existing = cursor.fetchone()
            if existing is None:
                cursor.execute("SELECT COUNT(*) FROM alert_rules")
                if int(cursor.fetchone()[0]) >= MAX_RULES:
                    raise ValidationError(
                        f"There are already {MAX_RULES} alert rules",
                        details="Remove one that is no longer useful before adding another.",
                    )
                cursor.execute(
                    "INSERT INTO alert_rules (name, metric, app_domain, comparator, threshold, "
                    "duration_s, hysteresis, cooldown_s, severity, enabled, remind, note, "
                    "created_at, created_by, updated_at, updated_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rule.name,
                        rule.metric,
                        rule.app_domain,
                        rule.comparator,
                        rule.threshold,
                        rule.duration_s,
                        rule.hysteresis,
                        rule.cooldown_s,
                        rule.severity,
                        int(rule.enabled),
                        int(rule.remind),
                        rule.note or None,
                        now,
                        actor,
                        now,
                        actor,
                    ),
                )
            else:
                cursor.execute(
                    "UPDATE alert_rules SET metric = ?, app_domain = ?, comparator = ?, "
                    "threshold = ?, duration_s = ?, hysteresis = ?, cooldown_s = ?, severity = ?, "
                    "enabled = ?, remind = ?, note = ?, updated_at = ?, updated_by = ? "
                    "WHERE id = ?",
                    (
                        rule.metric,
                        rule.app_domain,
                        rule.comparator,
                        rule.threshold,
                        rule.duration_s,
                        rule.hysteresis,
                        rule.cooldown_s,
                        rule.severity,
                        int(rule.enabled),
                        int(rule.remind),
                        rule.note or None,
                        now,
                        actor,
                        int(existing[0]),
                    ),
                )
                cursor.execute("DELETE FROM alert_states WHERE rule_id = ?", (int(existing[0]),))
        saved = self.get(rule.name)
        if saved is None:  # pragma: no cover - the row was just written
            raise ValidationError(f"The rule {rule.name!r} could not be stored")
        return saved

    def set_enabled(
        self, name: str, enabled: bool, *, actor: str | None = None
    ) -> AlertRule | None:
        """
        Turn a rule on or off, keeping the rest of it.

        Args:
            name: The rule.
            enabled: Whether the monitor evaluates it.
            actor: Who made the change.

        Returns:
            The rule as it is now, or None when there is none of that name.
        """
        rule = self.get(name)
        if rule is None:
            return None
        if rule.enabled == enabled:
            return rule
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE alert_rules SET enabled = ?, updated_at = ?, updated_by = ? WHERE name = ?",
                (int(enabled), self._clock(), actor, name),
            )
            if not enabled:
                # A rule that is off is not firing: it would say so forever.
                cursor.execute("DELETE FROM alert_states WHERE rule_id = ?", (rule.id,))
        return self.get(name)

    def remove(self, name: str) -> bool:
        """
        Delete a rule and its states.

        Args:
            name: The rule.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM alert_rules WHERE name = ?", (name,))
            return cursor.rowcount > 0

    # -- states ----------------------------------------------------------------

    def states(self, rule_id: int | None = None) -> list[AlertState]:
        """
        Args:
            rule_id: Only this rule's; every rule's when None.

        Returns:
            The states, firing ones first.
        """
        with self.store._transaction() as cursor:
            if rule_id is None:
                cursor.execute(
                    "SELECT * FROM alert_states ORDER BY CASE state WHEN 'firing' THEN 0 ELSE 1 END, since"
                )
            else:
                cursor.execute(
                    "SELECT * FROM alert_states WHERE rule_id = ? ORDER BY CASE state WHEN 'firing' THEN 0 ELSE 1 END, since",
                    (rule_id,),
                )
            return [_state(row) for row in cursor.fetchall()]

    def state(self, rule_id: int, subject: str) -> AlertState | None:
        """
        Args:
            rule_id: The rule.
            subject: The application, or empty for the server.

        Returns:
            Where that subject stands, or None when the rule never judged it.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM alert_states WHERE rule_id = ? AND subject = ?", (rule_id, subject)
            )
            row = cursor.fetchone()
        return _state(row) if row is not None else None

    def put_state(self, state: AlertState) -> None:
        """
        Write where a subject stands.

        Whoever took a firing is not the engine's to say: a firing that goes on
        keeps what is stored, and one that clears or starts again loses it, in the
        same statement, so a scan racing an acknowledgement cannot undo it.

        Args:
            state: The state.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO alert_states (rule_id, subject, state, since, last_value, "
                "announced, announced_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(rule_id, subject) DO UPDATE SET state = excluded.state, "
                "since = excluded.since, last_value = excluded.last_value, "
                "announced = excluded.announced, announced_at = excluded.announced_at, "
                "acknowledged_at = CASE WHEN alert_states.state = 'firing' "
                "AND excluded.state = 'firing' THEN alert_states.acknowledged_at END, "
                "acknowledged_by = CASE WHEN alert_states.state = 'firing' "
                "AND excluded.state = 'firing' THEN alert_states.acknowledged_by END",
                (
                    state.rule_id,
                    state.subject,
                    state.state,
                    state.since,
                    state.last_value,
                    int(state.announced),
                    state.announced_at,
                ),
            )

    def require(self, name: str) -> AlertRule:
        """
        Args:
            name: A rule's name.

        Returns:
            The rule.

        Raises:
            ValidationError: There is no such rule.
        """
        rule = self.get(name)
        if rule is None:
            raise ValidationError(
                f"No alert rule named {name!r}",
                details="`noust alert list` lists them.",
                field="name",
            )
        return rule

    def acknowledge(self, name: str, subject: str = "", *, by: str) -> tuple[AlertState, bool]:
        """
        Say that somebody took a firing alert.

        An acknowledged alert stops being repeated by the rule's reminder; it still
        clears, and says so, when the metric is back inside its limit. Taking one
        that is already taken changes nothing, so the first name stays.

        Args:
            name: The rule.
            subject: The application, or empty for the server.
            by: Who takes it (an account, a token's name or the command line's user).

        Returns:
            The state, acknowledged, and whether this call is the one that took it
            (False when somebody had).

        Raises:
            ValidationError: No such rule, or the subject is not firing now.
        """
        rule = self.require(name)
        who = " ".join(by.split())[:MAX_ACKNOWLEDGER] or "unknown"
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE alert_states SET acknowledged_at = ?, acknowledged_by = ? "
                "WHERE rule_id = ? AND subject = ? AND state = 'firing' "
                "AND acknowledged_at IS NULL",
                (self._clock(), who, rule.id, subject),
            )
            took = cursor.rowcount == 1
        state = self.state(rule.id, subject)
        if state is None or state.state != FIRING:
            raise ValidationError(
                f"{name} is not firing{f' for {subject}' if subject else ''}",
                details="Only an alert that is firing can be taken; `noust alert status` lists them.",
                field="subject",
            )
        return state, took

    def unacknowledge(self, name: str, subject: str = "") -> AlertState:
        """
        Hand a firing alert back: its reminder speaks again.

        Args:
            name: The rule.
            subject: The application, or empty for the server.

        Returns:
            The state, no longer acknowledged.

        Raises:
            ValidationError: No such rule, or the subject is not firing now.
        """
        rule = self.require(name)
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE alert_states SET acknowledged_at = NULL, acknowledged_by = NULL "
                "WHERE rule_id = ? AND subject = ? AND state = 'firing'",
                (rule.id, subject),
            )
        state = self.state(rule.id, subject)
        if state is None or state.state != FIRING:
            raise ValidationError(
                f"{name} is not firing{f' for {subject}' if subject else ''}",
                details="Only an alert that is firing can be handed back.",
                field="subject",
            )
        return state

    def forget(self, rule_id: int, subject: str) -> None:
        """
        Drop what a rule remembers of a subject (an application that is gone).

        Args:
            rule_id: The rule.
            subject: The application.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM alert_states WHERE rule_id = ? AND subject = ?", (rule_id, subject)
            )
