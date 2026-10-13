# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Evaluating the alert rules against what the metrics store recorded.

The monitor calls :meth:`AlertEngine.evaluate` on every scan. For each enabled
rule and each of its subjects (the server, or the applications an application
rule covers) the engine reads the window the rule's ``duration`` spans from the
metrics store and decides:

- **Fire** when every reading of the window breaches the limit and the window
  is well covered by readings (a monitor that was down for ten minutes did not
  see ten calm minutes, and did not see ten bad ones either). Told to the
  channels unless the subject was announced less than ``cooldown`` ago.
- **Clear** when every reading of the latest minute is back inside the limit by
  the hysteresis and the alert was firing. Told, with how long it lasted, if the firing was.
- **Otherwise** leave things as they are: a reading inside the hysteresis band
  neither fires nor clears, and *not knowing* (a gap) is not a change.

It reads the history rather than keeping a counter of its own, so a restart of
the monitor loses nothing: the "for five minutes" is in the recorded series,
and whether an alert is firing and was announced is in ``alert_states``.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial

from noust.core.alerts.model import AlertRule, MetricSpec, Reading, format_value
from noust.core.alerts.rules import FIRING, OK, AlertRules, AlertState
from noust.core.exceptions import NoustError, ValidationError
from noust.core.messages import message
from noust.core.notifications.composers_ops import compose_alert_firing, compose_alert_resolved
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.monitor.timeseries import MetricsStore

log = logging.getLogger(__name__)

#: The share of a rule's window that must have readings for a decision.
MIN_COVERAGE = 0.8

#: How much of the end of a window decides that a metric has come back.
RECENT_SECONDS = 60

Publish = Callable[[Callable[[NotificationContext], Notification]], None]


@dataclass
class EvaluationReport:
    """
    What one evaluation did.

    Attributes:
        checked: Rule and subject pairs that were judged.
        fired: Alerts that started to fire.
        resolved: Alerts that cleared.
        announced: Messages sent (a firing inside its cooldown is silent).
        unknown: Pairs left alone for lack of readings.
    """

    checked: int = 0
    fired: int = 0
    resolved: int = 0
    announced: int = 0
    unknown: int = 0


def _rule_id(rule: AlertRule) -> int:
    """
    Args:
        rule: A rule.

    Returns:
        Its row.

    Raises:
        ValueError: When it was never stored (only stored rules are judged).
    """
    if rule.id is None:
        raise ValueError(f"The alert rule {rule.name!r} was never stored")
    return rule.id


def _label(rule: AlertRule, subject: str, ctx: NotificationContext) -> str:
    """
    Args:
        rule: The rule.
        subject: The application, or empty.
        ctx: The context, for the language.

    Returns:
        What the rule measures, in words and in the reader's language.
    """
    return message(f"ui.metric.{rule.metric}", ctx.locale, app=subject)


def _firing(
    rule: AlertRule,
    subject: str,
    value: float,
    since: float,
    now: float,
    ctx: NotificationContext,
) -> Notification:
    """
    Compose the notification of a rule that fires, or goes on firing.

    Args:
        rule: The rule.
        subject: The application, or empty for the server.
        value: The reading now.
        since: Epoch seconds the breach began.
        now: Epoch seconds now.
        ctx: The context.

    Returns:
        The notification.
    """
    unit = rule.spec.unit
    return compose_alert_firing(
        rule.name,
        _label(rule, subject, ctx),
        format_value(unit, value),
        format_value(unit, rule.threshold, limit=True),
        rule.comparator,
        max(now - since, rule.duration_s),
        ctx,
        failed=rule.severity == "failed",
        subject=subject or None,
        domain=subject or None,
        since=datetime.fromtimestamp(since, tz=timezone.utc),
    )


def _resolved(
    rule: AlertRule,
    subject: str,
    value: float,
    firing_for: float,
    ctx: NotificationContext,
) -> Notification:
    """
    Compose the notification that closes an alert.

    Args:
        rule: The rule.
        subject: The application, or empty for the server.
        value: The reading now.
        firing_for: Seconds it was firing.
        ctx: The context.

    Returns:
        The notification.
    """
    unit = rule.spec.unit
    return compose_alert_resolved(
        rule.name,
        _label(rule, subject, ctx),
        format_value(unit, value),
        format_value(unit, rule.threshold, limit=True),
        firing_for,
        ctx,
        subject=subject or None,
        domain=subject or None,
    )


class AlertEngine:
    """Judges every enabled rule against the metrics history."""

    def __init__(
        self,
        rules: AlertRules,
        metrics: MetricsStore,
        publish: Publish,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """
        Args:
            rules: Where the rules and their states are.
            metrics: The history to read.
            publish: Composes a notification from the context and delivers it.
            clock: Epoch seconds. Tests move it.
        """
        self._rules = rules
        self._metrics = metrics
        self._publish = publish
        self._clock = clock

    def evaluate(self) -> EvaluationReport:
        """
        Judge every enabled rule once.

        Returns:
            What was decided.
        """
        report = EvaluationReport()
        for rule in self._rules.rules():
            if not rule.enabled or rule.id is None:
                continue
            try:
                self._evaluate_rule(rule, report)
            except (NoustError, sqlite3.Error, OSError) as exc:
                # One rule that cannot be read must not stop the others.
                log.warning("Could not evaluate the alert rule %s: %s", rule.name, exc)
        return report

    def subjects(self, rule: AlertRule) -> list[str]:
        """
        List what a rule is about right now.

        Args:
            rule: The rule.

        Returns:
            ``[""]`` for a server metric; for an application metric the
            application it names, or every application that has recorded it.
        """
        spec = rule.spec
        if spec.scope == "server":
            return [""]
        if rule.app_domain:
            return [rule.app_domain]
        return self._apps_with(spec)

    def _apps_with(self, spec: MetricSpec) -> list[str]:
        """
        Args:
            spec: An application metric.

        Returns:
            The applications that have recorded it, by name.
        """
        pattern = re.compile(
            "^" + re.escape(spec.series).replace(re.escape("{app}"), "(?P<app>.+)") + "$"
        )
        found = {
            m.group("app") for name in self._metrics.list_metrics() if (m := pattern.match(name))
        }
        return sorted(found)

    def _read(self, rule: AlertRule, subject: str, now: float) -> tuple[Reading, int]:
        """
        Read the window of one subject.

        Args:
            rule: The rule.
            subject: The application, or empty.
            now: Epoch seconds now.

        Returns:
            The readings of the window in the metric's unit, and the seconds
            per reading.

        Raises:
            ValidationError: The store refused the range.
        """
        spec = rule.spec
        series = spec.series_for(subject or None)
        end = int(now)
        result = self._metrics.query_range([series], start=end - rule.duration_s, end=end)
        read = result.series[0]
        values: list[float | None] = []
        for _ts, mean, _peak in read.points:
            if mean is None:
                values.append(None)
            elif spec.of_ceiling:
                values.append(mean / read.ceiling * 100 if read.ceiling else None)
            else:
                values.append(mean)
        return Reading(subject, values), max(result.step, 1)

    def _evaluate_rule(self, rule: AlertRule, report: EvaluationReport) -> None:
        """
        Judge one rule over its subjects.

        Args:
            rule: The rule.
            report: Filled with what is decided.
        """
        rule_id = _rule_id(rule)
        now = self._clock()
        subjects = self.subjects(rule)
        for subject in subjects:
            try:
                reading, step = self._read(rule, subject, now)
            except ValidationError as exc:
                log.warning(
                    "The alert rule %s could not read %s: %s",
                    rule.name,
                    subject or "the server",
                    exc,
                )
                continue
            self._decide(rule, subject, reading, step, now, report)
        if rule.spec.scope == "app":
            for state in self._rules.states(rule_id):
                if state.subject and state.subject not in subjects:
                    # The application is gone (or stopped recording): an alert
                    # about nothing would stay "firing" for ever.
                    self._rules.forget(rule_id, state.subject)

    def _decide(
        self,
        rule: AlertRule,
        subject: str,
        reading: Reading,
        step: int,
        now: float,
        report: EvaluationReport,
    ) -> None:
        """
        Fire, clear or leave one subject alone.

        Args:
            rule: The rule.
            subject: The application, or empty.
            reading: The window.
            step: Seconds per reading.
            now: Epoch seconds now.
            report: Filled with what is decided.
        """
        if reading.coverage < MIN_COVERAGE or reading.latest is None:
            report.unknown += 1
            return
        report.checked += 1
        known = [v for v in reading.values if v is not None]
        sustained = all(rule.breached(v) for v in known)
        state = self._rules.state(_rule_id(rule), subject)
        firing = state is not None and state.state == FIRING

        if sustained:
            value = reading.latest
            if state is None or state.state != FIRING:
                self._start(rule, subject, state, value, now, report)
            else:
                self._continue(rule, subject, state, value, now, report)
            return

        # Clearing asks as much as firing: every reading of the last minute
        # back inside the limit, not their mean (one calm sample among bad ones
        # is a dip, not an end).
        recent_cells = max(1, RECENT_SECONDS // step)
        recent = [v for v in reading.values[-recent_cells:] if v is not None]
        if state is not None and firing and recent and all(rule.cleared(v) for v in recent):
            self._clear(rule, subject, state, sum(recent) / len(recent), now, report)

    def _start(
        self,
        rule: AlertRule,
        subject: str,
        previous: AlertState | None,
        value: float,
        now: float,
        report: EvaluationReport,
    ) -> None:
        """
        Start an episode: announce it unless the subject was announced lately.

        Args:
            rule: The rule.
            subject: The application, or empty.
            previous: What was known of this subject (an ``ok`` state), if anything.
            value: The reading now.
            now: Epoch seconds now.
            report: Filled with what is decided.
        """
        since = now - rule.duration_s
        last = previous.announced_at if previous is not None else None
        speak = last is None or now - last >= rule.cooldown_s
        if speak:
            self._publish(partial(_firing, rule, subject, value, since, now))
            report.announced += 1
        report.fired += 1
        self._rules.put_state(
            AlertState(
                _rule_id(rule),
                subject,
                FIRING,
                since,
                value,
                announced=speak,
                announced_at=now if speak else last,
            )
        )

    def _continue(
        self,
        rule: AlertRule,
        subject: str,
        state: AlertState,
        value: float,
        now: float,
        report: EvaluationReport,
    ) -> None:
        """
        An episode goes on: remind when the rule asks to, once per cooldown.

        Args:
            rule: The rule.
            subject: The application, or empty.
            state: The firing state.
            value: The reading now.
            now: Epoch seconds now.
            report: Filled with what is decided.
        """
        remind = (
            rule.remind
            and rule.cooldown_s > 0
            and state.announced
            and state.announced_at is not None
            and now - state.announced_at >= rule.cooldown_s
        )
        announced_at = state.announced_at
        if remind:
            self._publish(partial(_firing, rule, subject, value, state.since, now))
            report.announced += 1
            announced_at = now
        self._rules.put_state(
            AlertState(
                _rule_id(rule),
                subject,
                FIRING,
                state.since,
                value,
                announced=state.announced,
                announced_at=announced_at,
            )
        )

    def _clear(
        self,
        rule: AlertRule,
        subject: str,
        state: AlertState,
        value: float,
        now: float,
        report: EvaluationReport,
    ) -> None:
        """
        End an episode: say so when it was told.

        Args:
            rule: The rule.
            subject: The application, or empty.
            state: The firing state.
            value: The reading now.
            now: Epoch seconds now.
            report: Filled with what is decided.
        """
        if state.announced:
            self._publish(partial(_resolved, rule, subject, value, now - state.since))
            report.announced += 1
        report.resolved += 1
        self._rules.put_state(
            AlertState(
                _rule_id(rule),
                subject,
                OK,
                now,
                value,
                announced=False,
                announced_at=state.announced_at,
            )
        )


def subjects_of(rules: Iterable[AlertRule]) -> set[str]:
    """
    Args:
        rules: Alert rules.

    Returns:
        The applications the rules name; for the monitor to know which are watched.
    """
    return {rule.app_domain for rule in rules if rule.app_domain}
