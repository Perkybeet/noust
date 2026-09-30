"""
Why an application's metrics tab is empty, or that it is not.

The console used to say "No readings in the last 24 hours" for every application
without data, and worked out on its own that a static site and a Compose stack
had none. The answer is now computed once, here, from the application's
:class:`~noust.monitor.plan.SamplingPlan` and the state of the collector, and the
API hands it over: a machine-readable reason, the fix, and the system's own
output verbatim.

The reasons, in the order they are decided:

1. the plan's own (a static site has no process, a stopped unit has nothing to
   measure, accounting is off, the cgroup is not there...);
2. history is not being recorded at all (the monitor is not installed, not
   enabled, not running, or stalled);
3. everything is in place but nothing has been recorded yet (the first seconds).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from noust.monitor.collector import RecordingStatus
from noust.monitor.plan import (
    KIND_PHP_FPM,
    KIND_STATIC,
    PlanBuilder,
    Reason,
    SamplingPlan,
    TargetStatus,
    target_status,
)
from noust.monitor.timeseries import MetricsStore
from noust.monitor.traffic import traffic_metric_names

#: A series is "recent" for this many intervals; older, and something stopped.
FRESH_INTERVALS = 3

#: ...but never less than this, so a slow tick is not called a stall.
FRESH_MIN_SECONDS = 30.0

REASON_NO_SAMPLES_YET = "no_samples_yet"


@dataclass(frozen=True)
class AppMetricsStatus:
    """
    What is true about one application's metrics.

    Attributes:
        domain: The application.
        kind: The plan kind (``unit``, ``legacy``, ``blue_green``,
            ``monorepo``, ``compose``, ``php_fpm``, ``static``).
        sampled: True when its CPU and memory are being recorded now.
        source: Where they come from: ``cgroup``, ``docker``, ``fpm`` or
            ``none``.
        reason: Why they are not, or None.
        units: What each unit or container looks like right now.
        series: The metric names of the series that exist for this kind of
            application (``cpu``, ``memory``, ``requests``, ``errors_5xx``).
        traffic: Whether requests are being counted, and why not.
        last_sample_at: The newest sample of any of its series, or None.
        collector: Whether history is being recorded at all.
    """

    domain: str
    kind: str
    sampled: bool
    source: str
    reason: Reason | None
    units: list[TargetStatus] = field(default_factory=list)
    series: dict[str, str] = field(default_factory=dict)
    traffic: dict[str, Any] = field(default_factory=dict)
    last_sample_at: int | None = None
    collector: RecordingStatus | None = None


def series_names(plan: SamplingPlan) -> dict[str, str]:
    """
    Name the series that exist for an application of this kind.

    Args:
        plan: The application's plan.

    Returns:
        Role to metric name: ``cpu`` and ``memory`` for anything with a
        process, ``requests`` and ``errors_5xx`` for the sites whose traffic is
        counted from the access log.
    """
    names: dict[str, str] = {}
    if plan.kind != KIND_STATIC:
        names["cpu"] = f"app.{plan.domain}.cpu.percent"
        names["memory"] = f"app.{plan.domain}.mem.bytes"
    if plan.kind in (KIND_STATIC, KIND_PHP_FPM):
        requests, errors = traffic_metric_names(plan.domain)
        names["requests"] = requests
        names["errors_5xx"] = errors
    return names


def app_metrics_status(
    app: Any,
    store: MetricsStore,
    planner: PlanBuilder,
    recording: RecordingStatus,
) -> AppMetricsStatus:
    """
    Work out why an application's metrics are, or are not, being recorded.

    Args:
        app: The application row.
        store: The metrics store, to see when it was last sampled.
        planner: Builds the application's sampling plan, asking systemd afresh.
        recording: Whether history is being recorded at all.

    Returns:
        The status, with the reason decided as the module docstring orders.
    """
    plan = planner.build_one(app)
    names = series_names(plan)
    last = store.last_sample_at(list(names.values()))
    now = store.now()
    fresh = last is not None and now - last <= max(
        FRESH_INTERVALS * recording.interval_s, FRESH_MIN_SECONDS
    )

    reason = plan.reason
    if reason is None and not recording.recording:
        reason = recording.reason
    if reason is None and not fresh:
        reason = Reason(
            code=REASON_NO_SAMPLES_YET,
            message="Nothing has been recorded for this application yet.",
            fix="It is read every few seconds: reload in a moment.",
        )

    traffic_reason = plan.traffic_reason
    if plan.traffic_log is not None and not recording.recording:
        traffic_reason = recording.reason
    traffic: dict[str, Any] = {
        "available": plan.traffic_log is not None and recording.recording,
        "log": str(plan.traffic_log) if plan.traffic_log is not None else None,
        "reason": traffic_reason.to_dict() if traffic_reason is not None else None,
    }

    return AppMetricsStatus(
        domain=app.domain,
        kind=plan.kind,
        sampled=plan.measures_resources and reason is None,
        source=plan.source,
        reason=reason,
        units=[target_status(target) for target in plan.targets],
        series=names,
        traffic=traffic,
        last_sample_at=last,
        collector=recording,
    )
