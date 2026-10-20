# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an alert rule is: a metric, a limit, how long, and how often to say so.

A rule says "tell me when *this* has been *above/below* *that* for *this long*":

``metric``
    One of the measurements Noust records (:data:`METRICS`): the server's CPU,
    memory, swap, disk, load or network, or an application's CPU, memory,
    requests, 5xx responses, disk and network traffic.
``app_domain``
    Which application, for an application metric; empty for every application.
    A server metric is the server's.
``comparator`` and ``threshold``
    ``>``, ``>=``, ``<`` or ``<=`` a number in the metric's unit (percent for
    CPU, memory, swap and disk; bytes for an application's memory; bytes per
    second for traffic).
``duration_s``
    The limit must be breached for this long, without a gap, before the alert
    fires: a spike is not an alert. At least a minute (the monitor looks once
    a minute), at most a day.
``hysteresis``
    How far back inside the limit the metric must come to clear: a CPU alert
    for ">90" with a hysteresis of 5 clears at 85 or below, so a value that
    hovers at 90 does not fire and clear all night.
``cooldown_s``
    The least time between two messages for the same rule and subject. A rule
    that fires again inside it is silent (and stays quiet when it clears). With
    ``remind`` it is also how often a rule that keeps firing says so again.
``severity``
    ``warning`` or ``failed``: the state the notification carries, and so
    which channels' routes (``min_severity``) hear it.

Pure: this module imports nothing that reads the machine.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import ValidationError

#: How a rule may be named: a short identifier for the CLI and the API.
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,47}")

#: The comparators a rule accepts.
COMPARATORS: tuple[str, ...] = (">", ">=", "<", "<=")

#: Severities a rule accepts.
SEVERITIES: tuple[str, ...] = ("warning", "failed")

#: Bounds of the settings, in seconds.
MIN_DURATION_S = 60
MAX_DURATION_S = 86_400
MAX_COOLDOWN_S = 30 * 86_400

DEFAULT_DURATION_S = 300
DEFAULT_COOLDOWN_S = 3_600

#: The most rules a server may have: a handful that matter, not a dashboard.
MAX_RULES = 100

#: Units a metric is measured in.
PERCENT = "percent"
BYTES = "bytes"
BYTES_PER_SECOND = "bytes_s"
NUMBER = "number"
PER_MINUTE = "per_min"
MILLISECONDS = "ms"
UNITS: tuple[str, ...] = (PERCENT, BYTES, BYTES_PER_SECOND, NUMBER, PER_MINUTE, MILLISECONDS)


@dataclass(frozen=True)
class MetricSpec:
    """
    One measurement a rule may watch.

    Attributes:
        id: What a rule names (``cpu``, ``app_5xx``).
        scope: ``server`` or ``app``.
        unit: One of :data:`UNITS`.
        series: The metrics-store series; ``{app}`` stands for the domain.
        of_ceiling: The value is the series as a percentage of its ceiling
            (used memory against total memory).
    """

    id: str
    scope: str
    unit: str
    series: str
    of_ceiling: bool = False

    def series_for(self, app: str | None) -> str:
        """
        Args:
            app: The application, for an application metric.

        Returns:
            The series name in the metrics store.
        """
        return self.series.format(app=app) if app else self.series


def _specs(*specs: MetricSpec) -> dict[str, MetricSpec]:
    return {spec.id: spec for spec in specs}


#: Every metric a rule can watch.
METRICS: dict[str, MetricSpec] = _specs(
    MetricSpec("cpu", "server", PERCENT, "cpu.percent"),
    MetricSpec("memory", "server", PERCENT, "mem.used_bytes", of_ceiling=True),
    MetricSpec("swap", "server", PERCENT, "swap.used_bytes", of_ceiling=True),
    MetricSpec("disk", "server", PERCENT, "disk.used_bytes", of_ceiling=True),
    MetricSpec("load", "server", NUMBER, "load.1m"),
    MetricSpec("net_rx", "server", BYTES_PER_SECOND, "net.rx_bytes_s"),
    MetricSpec("net_tx", "server", BYTES_PER_SECOND, "net.tx_bytes_s"),
    MetricSpec("app_cpu", "app", PERCENT, "app.{app}.cpu.percent"),
    MetricSpec("app_memory", "app", BYTES, "app.{app}.mem.bytes"),
    MetricSpec("app_requests", "app", PER_MINUTE, "app.{app}.http.requests_per_min"),
    MetricSpec("app_5xx", "app", PER_MINUTE, "app.{app}.http.errors_5xx_per_min"),
    MetricSpec("app_latency_p95", "app", MILLISECONDS, "app.{app}.http.latency_p95_ms"),
    MetricSpec("app_disk_read", "app", BYTES_PER_SECOND, "app.{app}.io.read_bytes_s"),
    MetricSpec("app_disk_write", "app", BYTES_PER_SECOND, "app.{app}.io.write_bytes_s"),
    MetricSpec("app_net_rx", "app", BYTES_PER_SECOND, "app.{app}.net.rx_bytes_s"),
    MetricSpec("app_net_tx", "app", BYTES_PER_SECOND, "app.{app}.net.tx_bytes_s"),
)

_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
_NUMBER = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z%/]*)\s*")


def parse_threshold(text: str | float | int, unit: str) -> float:
    """
    Read a limit as an operator types it.

    Args:
        text: ``90``, ``90%``, ``512MB``, ``2GB/s``, ``1.5``.
        unit: The metric's unit, which says what a suffix may be.

    Returns:
        The number in the metric's own unit: a percentage, bytes, bytes per
        second, or a plain number.

    Raises:
        ValidationError: When it is not a number, or has a suffix the unit
            does not take.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        value = float(text)
    else:
        match = _NUMBER.fullmatch(str(text))
        if match is None:
            raise ValidationError(
                f"Not a limit: {text!r}",
                details="Write a number, with a unit where the metric has one: 90, 90%, 512MB, 2MB/s.",
                field="threshold",
            )
        value = float(match.group(1))
        suffix = match.group(2).lower().removesuffix("/s")
        per_second = match.group(2).lower().endswith("/s")
        if suffix in ("", "%") and unit in (PERCENT, NUMBER, PER_MINUTE):
            pass
        elif unit in (BYTES, BYTES_PER_SECOND) and suffix in _SIZE_UNITS:
            value *= _SIZE_UNITS[suffix]
            if per_second and unit == BYTES:
                raise ValidationError(
                    f"{text!r} is a rate and the metric is a size", field="threshold"
                )
        elif suffix == "" and unit in (BYTES, BYTES_PER_SECOND):
            pass
        elif unit == MILLISECONDS and suffix in ("", "ms", "s"):
            if suffix == "s":
                value *= 1000
        else:
            raise ValidationError(
                f"The unit of {text!r} does not fit this metric",
                details=_hint(unit),
                field="threshold",
            )
    if not math.isfinite(value) or (unit == MILLISECONDS and value < 0):
        raise ValidationError(f"Not a limit: {text!r}", field="threshold")
    return value


_DURATION = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*")
_DURATION_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str | int | float) -> int:
    """
    Read a length of time as an operator types it.

    Args:
        text: ``300``, ``90s``, ``5m``, ``2h``, ``1d``; a bare number is seconds.

    Returns:
        Whole seconds.

    Raises:
        ValidationError: When it is not a length of time.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return int(text)
    match = _DURATION.fullmatch(str(text))
    if match is None:
        raise ValidationError(
            f"Not a length of time: {text!r}",
            details="Write seconds, or a number with s, m, h or d: 300, 5m, 2h, 1d.",
        )
    return int(float(match.group(1)) * _DURATION_UNITS[match.group(2)])


def format_duration_short(seconds: int) -> str:
    """
    Say a length of time the way ``parse_duration`` reads it.

    Args:
        seconds: Whole seconds.

    Returns:
        ``90s``, ``5m``, ``2h``, ``1d``: the largest unit that divides it exactly.
    """
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def format_value(unit: str, value: float, *, limit: bool = False) -> str:
    """
    Say a reading, or a limit, the way a person reads it.

    Args:
        unit: The metric's unit.
        value: The number.
        limit: It is the rule's limit: a whole percentage reads ``90%``, not
            ``90.0%``.

    Returns:
        ``93.4%``, ``512.0 MB``, ``2.0 MB/s``, ``1.50``, ``120/min``, ``250 ms``, ``1.5 s``.
    """
    from noust.core.notifications.formatting import format_bytes

    if unit == PERCENT:
        return f"{value:g}%" if limit else f"{value:.1f}%"
    if unit == BYTES:
        return format_bytes(max(int(value), 0))
    if unit == BYTES_PER_SECOND:
        return f"{format_bytes(max(int(value), 0))}/s"
    if unit == PER_MINUTE:
        return f"{value:.0f}/min"
    if unit == MILLISECONDS:
        if value < 1:
            return "<1 ms"
        return f"{value:.0f} ms" if value < 1000 else f"{round(value / 1000, 1):g} s"
    return f"{value:g}" if limit else f"{value:.2f}"


def _hint(unit: str) -> str:
    """
    Args:
        unit: A metric's unit.

    Returns:
        What to write for it.
    """
    return {
        PERCENT: "This metric is a percentage: write 90 or 90%.",
        BYTES: "This metric is a size: write bytes, or 512MB, 2GB.",
        BYTES_PER_SECOND: "This metric is a rate: write bytes per second, or 2MB/s.",
        NUMBER: "This metric is a plain number.",
        PER_MINUTE: "This metric is a count per minute: write a number.",
        MILLISECONDS: "This metric is a time: write milliseconds, or 500ms, 2s.",
    }[unit]


@dataclass(frozen=True)
class AlertRule:
    """
    One rule, as stored.

    Attributes:
        name: Its identifier.
        metric: A key of :data:`METRICS`.
        comparator: One of :data:`COMPARATORS`.
        threshold: The limit, in the metric's unit.
        app_domain: The application an application metric is about; None for
            every application (and for a server metric).
        duration_s: How long the limit must be breached before it fires.
        hysteresis: How far inside the limit the metric must come to clear.
        cooldown_s: The least time between two messages for one subject.
        severity: ``warning`` or ``failed``.
        enabled: Whether the monitor evaluates it.
        remind: Say it again every cooldown while it keeps firing.
        note: Free text for whoever reads the rule later.
        id: The row, once stored.
        created_at: Epoch seconds, once stored.
        updated_at: Epoch seconds, once stored.
    """

    name: str
    metric: str
    comparator: str
    threshold: float
    app_domain: str | None = None
    duration_s: int = DEFAULT_DURATION_S
    hysteresis: float = 0.0
    cooldown_s: int = DEFAULT_COOLDOWN_S
    severity: str = "warning"
    enabled: bool = True
    remind: bool = False
    note: str = ""
    id: int | None = None
    created_at: float | None = None
    updated_at: float | None = None

    @property
    def spec(self) -> MetricSpec:
        """The metric this rule watches."""
        return METRICS[self.metric]

    def breached(self, value: float) -> bool:
        """
        Args:
            value: A reading in the metric's unit.

        Returns:
            Whether it is on the wrong side of the limit.
        """
        return _compare(value, self.comparator, self.threshold)

    def cleared(self, value: float) -> bool:
        """
        Args:
            value: A reading in the metric's unit.

        Returns:
            Whether it is back inside the limit by at least the hysteresis.
        """
        margin = abs(self.hysteresis)
        if self.comparator in (">", ">="):
            return value <= self.threshold - margin
        return value >= self.threshold + margin

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The rule as the API and the CLI show it.
        """
        return {
            "id": self.id,
            "name": self.name,
            "metric": self.metric,
            "scope": self.spec.scope,
            "unit": self.spec.unit,
            "comparator": self.comparator,
            "threshold": self.threshold,
            "app_domain": self.app_domain,
            "duration_s": self.duration_s,
            "hysteresis": self.hysteresis,
            "cooldown_s": self.cooldown_s,
            "severity": self.severity,
            "enabled": self.enabled,
            "remind": self.remind,
            "note": self.note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _compare(value: float, comparator: str, threshold: float) -> bool:
    """
    Args:
        value: A reading.
        comparator: One of :data:`COMPARATORS`.
        threshold: The limit.

    Returns:
        ``value <comparator> threshold``.
    """
    return {
        ">": value > threshold,
        ">=": value >= threshold,
        "<": value < threshold,
        "<=": value <= threshold,
    }[comparator]


def validate_rule(rule: AlertRule) -> AlertRule:
    """
    Check a rule before it is stored.

    Args:
        rule: The rule.

    Returns:
        The rule, unchanged.

    Raises:
        ValidationError: Naming the field and how to fix it.
    """
    if not NAME_PATTERN.fullmatch(rule.name):
        raise ValidationError(
            f"Invalid rule name: {rule.name!r}",
            details="Use lower-case letters, digits, '-' and '_', starting with a letter (48 at most).",
            field="name",
        )
    if rule.metric not in METRICS:
        raise ValidationError(
            f"Unknown metric: {rule.metric!r}",
            details=f"The metrics are: {', '.join(METRICS)}.",
            field="metric",
        )
    spec = rule.spec
    if rule.app_domain and spec.scope != "app":
        raise ValidationError(
            f"{rule.metric} is a server metric and cannot be about an application",
            details="Leave the application out, or pick an application metric.",
            field="app_domain",
        )
    if rule.app_domain is not None and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", rule.app_domain
    ):
        raise ValidationError(f"Not an application domain: {rule.app_domain!r}", field="app_domain")
    if rule.comparator not in COMPARATORS:
        raise ValidationError(
            f"Unknown comparator: {rule.comparator!r}",
            details=f"Use one of: {', '.join(COMPARATORS)}.",
            field="comparator",
        )
    if not math.isfinite(rule.threshold):
        raise ValidationError("The threshold must be a number", field="threshold")
    if spec.unit == PERCENT and not -1000 <= rule.threshold <= 1000:
        raise ValidationError(
            "A percentage limit is a number between 0 and 100 (CPU of several cores may pass it)",
            field="threshold",
        )
    if not MIN_DURATION_S <= rule.duration_s <= MAX_DURATION_S:
        raise ValidationError(
            f"The duration must be between {MIN_DURATION_S} seconds and a day",
            details="The monitor looks once a minute, so a shorter wait cannot be kept.",
            field="duration",
        )
    if not 0 <= rule.cooldown_s <= MAX_COOLDOWN_S:
        raise ValidationError("The cooldown must be between 0 and 30 days", field="cooldown")
    if not math.isfinite(rule.hysteresis) or rule.hysteresis < 0:
        raise ValidationError("The hysteresis must be zero or more", field="hysteresis")
    if rule.severity not in SEVERITIES:
        raise ValidationError(
            f"Unknown severity: {rule.severity!r}",
            details=f"Use one of: {', '.join(SEVERITIES)}.",
            field="severity",
        )
    if len(rule.note) > 500:
        raise ValidationError("The note is too long (500 characters at most)", field="note")
    return rule


@dataclass
class Reading:
    """
    What the engine found when it looked at one subject of a rule.

    Attributes:
        subject: The application, or empty for the server.
        values: The readings of the window, oldest first, None where nothing
            was recorded.
    """

    subject: str
    values: list[float | None] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """The share of the window that has a reading."""
        return (
            sum(1 for v in self.values if v is not None) / len(self.values) if self.values else 0.0
        )

    @property
    def latest(self) -> float | None:
        """The newest reading there is."""
        return next((v for v in reversed(self.values) if v is not None), None)
