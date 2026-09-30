# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Metrics history endpoints.

The charts get their live points over the ``/events`` stream; this is where they
load the past from. It is a thin read of
:class:`~noust.monitor.timeseries.MetricsStore` - the collector in
``noust-monitor`` writes it, this translates a window into a range and hands the
grid back.

``GET /api/metrics/query`` is the read the console uses: several metrics at
once, over a window (``1h``, ``24h``, ``7d``, ``30d``...) or an explicit
``from``/``to`` (a zoom), as **one regular grid** over exactly the requested
domain. Where nothing was recorded a cell is ``null``, so a chart draws the
window that was asked for and a gap as a gap. ``resolution`` names the tier the
data was read from and ``step`` the seconds per cell; both describe the data
actually read, never the window's width. Next to the data it says whether
history is being recorded at all and, when it is not, why and how to fix it.

``GET /api/metrics/{metric}`` is the single-metric read the console used before;
it stays, answered from the same store, until the console reads ranges (3.2).

``GET /api/apps/{domain}/metrics`` says why an application's metrics are, or are
not, there: a reason code, the fix, and the system's own output.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.runner import get_runner
from noust.core.store import get_store
from noust.monitor.appstatus import app_metrics_status
from noust.monitor.plan import PlanBuilder
from noust.monitor.timeseries import (
    MAX_RANGE_SERIES,
    default_metrics_db_path,
)
from noust.web import metrics_collector
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain

router = APIRouter(route_class=NoustErrorRoute)

#: Routes under ``/api/apps``: the status of one application's metrics.
app_router = APIRouter(route_class=NoustErrorRoute)

#: The windows the single-metric read offers.
WINDOWS: dict[str, int] = {
    "1h": 3_600,
    "24h": 86_400,
    "7d": 7 * 86_400,
    "30d": 30 * 86_400,
}

#: The windows a range read offers: the console's four, and the wider views the
#: 400-day hourly tier makes possible.
QUERY_WINDOWS: dict[str, int] = {
    "1h": 3_600,
    "6h": 6 * 3_600,
    "24h": 86_400,
    "7d": 7 * 86_400,
    "30d": 30 * 86_400,
    "90d": 90 * 86_400,
    "1y": 365 * 86_400,
}

#: What the single-metric read calls each tier. ``10m`` has no old name: the
#: console's own vocabulary has no word for it, and says nothing rather than a
#: wrong "minute".
_LEGACY_RESOLUTION = {"raw": "raw", "1m": "minute", "10m": "10m", "1h": "hour"}

#: The authentication dependency. Reading metrics needs nothing beyond the
#: ``read`` scope the chokepoint already enforces for every GET.
Session = Annotated[dict, Depends(get_current_session)]


class ReasonModel(BaseModel):
    """
    Why something is not being measured, and what to do about it.

    Attributes:
        code: Machine-readable and stable; the console translates by it.
        message: One sentence, in English, saying what is true.
        fix: What to do, a command verbatim when there is one; null when there
            is nothing to fix.
        evidence: The system's own output that led here, verbatim.
        params: Values the console puts into its own sentence.
    """

    code: str
    message: str
    fix: str | None = None
    evidence: str | None = None
    params: dict[str, str] = Field(default_factory=dict)


class CollectorModel(BaseModel):
    """
    Whether history is being recorded, and by whom.

    Attributes:
        recording: True while a collector holds a live lease.
        host: ``daemon`` (``noust-monitor``) or ``console``; null when nobody
            is recording.
        since: When the collector started, epoch seconds.
        last_sample_at: Its newest tick, epoch seconds.
        interval_s: Seconds between its ticks.
        last_error: The newest failure it logged, verbatim.
        reason: Why nothing is being recorded, with the fix.
        advice: A hint when recording works but could be better (it only runs
            while the console does).
        retention_days: How long the hourly tier is kept.
    """

    recording: bool
    host: str | None = None
    since: int | None = None
    last_sample_at: int | None = None
    interval_s: float = 5.0
    last_error: str | None = None
    reason: ReasonModel | None = None
    advice: ReasonModel | None = None
    retention_days: int = 400


class MetricsListResponse(BaseModel):
    """
    Every metric name the store has data for, and how it is being recorded.

    Attributes:
        metrics: The names.
        windows: The windows a range read accepts.
        database: Where the history lives.
        collector: Whether it is being recorded.
    """

    metrics: list[str]
    windows: list[str]
    database: str
    collector: CollectorModel


class MetricHistoryResponse(BaseModel):
    """One metric's points over a window, oldest first."""

    metric: str
    window: str
    resolution: str
    points: list[tuple[int, float]]


class SeriesModel(BaseModel):
    """
    One metric of a range read.

    Attributes:
        metric: Metric name.
        points: ``[timestamp, mean, maximum]`` for every cell of the grid,
            oldest first. ``mean`` and ``maximum`` are both null for a cell
            where nothing was recorded; every series has the same timestamps.
        ceiling: What the metric is drawn against (total memory for used
            memory), or null.
    """

    metric: str
    points: list[tuple[int, float | None, float | None]]
    ceiling: float | None = None


class MetricQueryResponse(BaseModel):
    """
    A range read: the requested domain and one grid of cells for all series.

    Attributes:
        from_: Start of the requested domain, epoch seconds (``from`` on the
            wire).
        to: End of the requested domain.
        step: Seconds per cell.
        resolution: The tier the data was read from: ``raw`` (5 s), ``1m``,
            ``10m`` or ``1h``. A cell wider than the tier is a mean over
            several of its buckets.
        window: The named window asked for, or null for an explicit range.
        first_sample_at: The oldest sample any requested metric has, in any
            tier: where "history since" starts.
        last_sample_at: The newest.
        series: One entry per requested metric, in the order asked.
        collector: Whether history is being recorded, and why not.
    """

    from_: int = Field(alias="from")
    to: int
    step: int
    resolution: str
    window: str | None = None
    first_sample_at: int | None = None
    last_sample_at: int | None = None
    series: list[SeriesModel]
    collector: CollectorModel


class UnitStatusModel(BaseModel):
    """
    One unit or container an application's metrics are read from.

    Attributes:
        name: The unit's name, or the container's.
        kind: ``unit`` or ``container``.
        active_state: systemd's ``ActiveState``, or null.
        control_group: systemd's ``ControlGroup`` verbatim, or null.
        cgroup_exists: Whether that cgroup exists right now.
        memory_current: ``memory.current`` in bytes, or null when unreadable.
        cpu_stat: Whether ``cpu.stat`` is readable.
    """

    name: str
    kind: str
    active_state: str | None = None
    control_group: str | None = None
    cgroup_exists: bool = False
    memory_current: int | None = None
    cpu_stat: bool = False


class TrafficModel(BaseModel):
    """
    Whether requests are being counted from the access log.

    Attributes:
        available: True when they are being recorded now.
        log: The access log they are read from.
        reason: Why not, when the site should have one.
    """

    available: bool
    log: str | None = None
    reason: ReasonModel | None = None


class AppMetricsResponse(BaseModel):
    """
    Why an application's metrics are, or are not, there.

    Attributes:
        domain: The application.
        kind: ``unit``, ``legacy``, ``blue_green``, ``monorepo``, ``compose``,
            ``php_fpm`` or ``static``.
        sampled: True when its CPU and memory are being recorded now.
        source: Where they come from: ``cgroup``, ``docker``, ``fpm`` or
            ``none``.
        reason: Why they are not; null when they are.
        units: What each unit or container looks like right now.
        series: Role (``cpu``, ``memory``, ``requests``, ``errors_5xx``) to the
            metric name that holds it, for the roles that exist for this kind
            of application.
        traffic: Whether requests are being counted.
        last_sample_at: The newest sample of any of its series.
        collector: Whether history is being recorded at all.
    """

    domain: str
    kind: str
    sampled: bool
    source: str
    reason: ReasonModel | None = None
    units: list[UnitStatusModel] = Field(default_factory=list)
    series: dict[str, str] = Field(default_factory=dict)
    traffic: TrafficModel
    last_sample_at: int | None = None
    collector: CollectorModel


@router.get("", response_model=MetricsListResponse)
def list_metrics(session: Session) -> MetricsListResponse:
    """
    Name every metric that has data, and say whether history is being recorded.

    Args:
        session: Authenticated session, injected.

    Returns:
        The metric names, the windows a range read accepts, where the history
        lives and whether a collector is recording it.
    """
    store = metrics_collector.get_metrics_store()
    return MetricsListResponse(
        metrics=store.list_metrics(),
        windows=list(QUERY_WINDOWS),
        database=str(getattr(store, "db_path", default_metrics_db_path())),
        collector=CollectorModel(**metrics_collector.history_status().to_dict()),
    )


@router.get("/query", response_model=MetricQueryResponse, response_model_by_alias=True)
def query_metrics(
    session: Session,
    metric: Annotated[
        list[str],
        Query(description="Metric name; repeat it for several series in one read."),
    ],
    window: Annotated[
        Literal["1h", "6h", "24h", "7d", "30d", "90d", "1y"],
        Query(description="A named window ending now. Ignored when from and to are given."),
    ] = "24h",
    from_: Annotated[
        int | None,
        Query(alias="from", description="Start of an explicit range, epoch seconds."),
    ] = None,
    to: Annotated[
        int | None,
        Query(description="End of an explicit range, epoch seconds."),
    ] = None,
    step: Annotated[
        int | None,
        Query(description="Seconds per cell: a multiple of a tier's step. Chosen when omitted."),
    ] = None,
) -> Any:
    """
    Read several metrics over a window or a range, as one grid.

    The tier is the finest whose retention reaches back to the start of the
    range; the response says which it read. The grid covers exactly
    ``[from, to]``, with nulls where nothing was recorded.

    Args:
        session: Authenticated session, injected.
        metric: The metrics, at most 40.
        window: A named window ending now; ignored when ``from`` and ``to`` are
            both given.
        from_: Start of an explicit range, for a zoom.
        to: End of an explicit range.
        step: Seconds per cell.

    Returns:
        The grid and the series.

    Raises:
        ValidationError: When only one of ``from`` and ``to`` is given, or the
            range, step or metric list is not acceptable.
    """
    if (from_ is None) != (to is None):
        raise ValidationError(
            "Give both from and to, or neither",
            details="An explicit range needs both ends; use window for one ending now.",
        )
    if len(metric) > MAX_RANGE_SERIES:
        raise ValidationError(
            f"At most {MAX_RANGE_SERIES} metrics per query, got {len(metric)}",
            details="Split the read into several queries.",
        )
    store = metrics_collector.get_metrics_store()
    if from_ is not None and to is not None:
        start, end, named = from_, to, None
    else:
        end = store.now()
        start, named = end - QUERY_WINDOWS[window], window
    result = store.query_range(metric, start=start, end=end, step=step)
    payload: dict[str, Any] = {
        "from": result.start,
        "to": result.end,
        "step": result.step,
        "resolution": result.resolution,
        "window": named,
        "first_sample_at": result.first_sample_at,
        "last_sample_at": result.last_sample_at,
        "series": [
            {"metric": s.metric, "points": s.points, "ceiling": s.ceiling} for s in result.series
        ],
        "collector": metrics_collector.history_status().to_dict(),
    }
    return MetricQueryResponse(**payload)


@router.get("/{metric:path}", response_model=MetricHistoryResponse)
def metric_history(
    metric: str,
    session: Session,
    window: Annotated[Literal["1h", "24h", "7d", "30d"], Query()] = "1h",
) -> MetricHistoryResponse:
    """
    Read one metric over a named window, oldest point first.

    The read the console used before ``/query``: the means of the recorded
    cells, without the gaps. Kept until the console reads ranges.

    Args:
        metric: Metric name, e.g. ``cpu.percent`` or
            ``app.example.com.mem.bytes``.
        session: Authenticated session, injected.
        window: Which window to read. Anything outside the fixed vocabulary
            is refused by validation before this runs.

    Returns:
        The metric, the window, the tier the points were read from (``raw``,
        ``minute``, ``10m`` or ``hour``) and ``[ts, value]`` pairs. A metric
        nothing has recorded returns an empty list rather than a 404: "no data
        yet" is a normal chart state, not a missing resource.
    """
    store = metrics_collector.get_metrics_store()
    end = store.now()
    result = store.query_range([metric], start=end - WINDOWS[window], end=end)
    return MetricHistoryResponse(
        metric=metric,
        window=window,
        resolution=_LEGACY_RESOLUTION.get(result.resolution, result.resolution),
        points=[(ts, mean) for ts, mean, _peak in result.series[0].points if mean is not None],
    )


@app_router.get("/{domain}/metrics", response_model=AppMetricsResponse)
def app_metrics(domain: str, session: Session) -> Any:
    """
    Say why an application's metrics are, or are not, there.

    Args:
        domain: The application's domain.
        session: Authenticated session, injected.

    Returns:
        Whether its CPU and memory are being recorded, where they come from,
        the reason and the fix when they are not, what each unit or container
        looks like right now, and whether history is being recorded at all.

    Raises:
        HTTPException: 404 when there is no such application.
    """
    validated = strict_domain(domain)
    app = get_store().get_app(validated)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    status = app_metrics_status(
        app,
        metrics_collector.get_metrics_store(),
        PlanBuilder(runner=get_runner()),
        metrics_collector.history_status(),
    )
    collector = status.collector or metrics_collector.history_status()
    return AppMetricsResponse(
        domain=status.domain,
        kind=status.kind,
        sampled=status.sampled,
        source=status.source,
        reason=ReasonModel(**status.reason.to_dict()) if status.reason else None,
        units=[UnitStatusModel(**vars(unit)) for unit in status.units],
        series=status.series,
        traffic=TrafficModel(**status.traffic),
        last_sample_at=status.last_sample_at,
        collector=CollectorModel(**collector.to_dict()),
    )
