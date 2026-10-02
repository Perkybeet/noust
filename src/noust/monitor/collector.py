"""
The sampler behind the charts: it runs in ``noust-monitor``, not in the console.

Until 3.1 the collector was a thread of the web process, so history existed only
while somebody had the console running: on a server where it is started on
demand, a month view was a handful of points. The collector now lives here, in
the package the monitor daemon already runs from, so it samples whether or not a
console is open. It imports no web framework.

**One collector at a time.** The metrics database carries a lease
(:meth:`~noust.monitor.timeseries.MetricsStore.acquire_collector`): whoever holds
it samples, the others wait and try again. The daemon outranks the console: the
console samples only when no daemon does (a container, a server that never
enabled the monitor) and hands over the moment one starts. That guard is here,
in :meth:`MetricsCollector.start`, so no caller can forget it.

**One tick, one plan.** Each tick reads the machine
(:mod:`noust.monitor.sampler`), every application through its
:class:`~noust.monitor.plan.SamplingPlan`
(:mod:`noust.monitor.appsampler`) and the access logs of the sites that have
one, and writes it all in one transaction stamped on the interval's grid, so
the series of a tick share a timestamp and a gap is a whole missing tick.

**Once a minute, the processes behind the numbers.** On the first tick of
each minute the busiest processes of the minute that just ended are ranked
(:mod:`noust.monitor.process_samples`) and kept beside the metrics, so a peak
on a chart can be explained afterwards. There is no such history before the
collector first ran.

**Failures are visible.** The tick is an error boundary: whatever a machine can
do to it, the thread logs it with its traceback, keeps the failure on the lease
row (where the console can show why history has holes) and ticks again. A thread
that died at 3 am and left the last snapshot frozen was the failure this
replaces.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import NoustError
from noust.monitor.appsampler import AppSampler
from noust.monitor.plan import PlanBuilder, Reason, SamplingPlan
from noust.monitor.process_samples import SAMPLE_SECONDS, ProcessSampler
from noust.monitor.sampler import SAMPLING_ERRORS, MachineSampler

if TYPE_CHECKING:
    from noust.managers.database.metrics import DatabaseSampler

from noust.monitor.timeseries import (
    DEFAULT_RETENTION_DAYS,
    MetricsStore,
    default_metrics_db_path,
    resolve_retention_days,
)

log = logging.getLogger(__name__)

#: What reading or writing the SQLite store can raise.
STORE_ERRORS: tuple[type[Exception], ...] = (sqlite3.Error, OSError, ValueError, NoustError)

#: Seconds between samples.
DEFAULT_INTERVAL_SECONDS = 5.0

#: How long the applications' plans are trusted before they are rebuilt. A
#: deploy mid-window shows up in its metrics half a minute late, which is
#: cheaper than a systemctl call per tick.
APPS_REFRESH_SECONDS = 30.0

#: Seconds between consolidations. It is a no-op until whole buckets have
#: completed, so running it on this timer keeps every tier fed and the database
#: small without a scheduler.
CONSOLIDATE_SECONDS = 300.0

#: A periodic consolidation only reads this far back: older buckets were done
#: by an earlier run. Six hours is well past the longest bucket (an hour), so a
#: machine that was suspended for a few hours still fills the coarse tiers.
#: Start-up reads everything, to catch up after a stop.
CONSOLIDATE_LOOKBACK_SECONDS = 6 * 3_600

#: How often a console collector that does not hold the lease looks again.
LEASE_RETRY_SECONDS = 30.0

#: A failure of the same kind is logged at most this often; every occurrence is
#: still kept as the collector's last error.
ERROR_LOG_EVERY_SECONDS = 300.0

#: The console's live feed accepts a reading this old, in intervals.
LATEST_MAX_AGE_INTERVALS = 3

#: Reasons history is not being recorded.
REASON_MONITOR_NOT_INSTALLED = "monitor_not_installed"
REASON_MONITOR_DISABLED = "monitor_disabled"
REASON_MONITOR_NOT_RUNNING = "monitor_not_running"
REASON_COLLECTOR_STALLED = "collector_stalled"
REASON_COLLECTOR_STOPPED = "collector_stopped"
REASON_CONSOLE_ONLY = "console_only"


class MetricsCollector:
    """
    Samples the machine and every application on a timer.

    The public surface is deliberately small: :meth:`start` and :meth:`stop`
    bracket the thread, :meth:`latest` hands the newest snapshot to the console's
    live feed, and :meth:`sample_once` is one tick, exposed so tests can drive
    the sampling deterministically without a thread or a wall clock.
    """

    def __init__(
        self,
        store: MetricsStore,
        *,
        kind: str = "daemon",
        interval_s: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        planner: PlanBuilder | None = None,
        apps_source: Callable[[], Sequence[Any]] | None = None,
        machine: MachineSampler | None = None,
        app_sampler: AppSampler | None = None,
        databases: DatabaseSampler | None = None,
        processes: ProcessSampler | None = None,
    ) -> None:
        """
        Args:
            store: Where samples are persisted.
            kind: ``daemon`` for the ``noust-monitor`` process, ``console`` for
                the web process sampling because no daemon does.
            interval_s: Seconds between ticks.
            clock: Monotonic time source for rate deltas and timers. Injected
                so tests never depend on real elapsed time.
            planner: Builds the applications' sampling plans. Defaults to one
                on the process-wide runner.
            apps_source: Lists the applications to sample. Defaults to the
                store's application table; injected so tests need no store.
            machine: Reads the machine. Defaults to :class:`MachineSampler`.
            app_sampler: Reads the applications. Defaults to
                :class:`AppSampler` on ``store``.
            databases: Reads the database engines once a minute. Defaults to
                :class:`~noust.managers.database.metrics.DatabaseSampler`.
            processes: Ranks the busiest processes once a minute. Defaults to
                :class:`~noust.monitor.process_samples.ProcessSampler`.
        """
        self.store = store
        self.kind = kind
        self.interval_s = float(interval_s)
        self._clock = clock
        self._planner = planner
        self._apps_source = apps_source or _stored_apps
        self._machine = machine or MachineSampler()
        self._apps = app_sampler or AppSampler(store)
        if databases is None:
            # Imported here: the database managers pull in the whole engine
            # layer, which the collector's module import should not.
            from noust.managers.database.metrics import DatabaseSampler

            databases = DatabaseSampler()
        self._databases = databases
        self._processes = processes or ProcessSampler()
        self._process_minute: int | None = None
        self._owner = f"{kind}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, float] = {}
        self._snapshot_lock = threading.Lock()
        self._last_tick: float | None = None
        self._plans: dict[str, SamplingPlan] = {}
        self._plans_at: float | None = None
        self._consolidated_at = self._clock()
        self._holding = False
        self._started = False
        self._errors: dict[str, float] = {}
        #: The newest operational failure, verbatim, for the lease row.
        self.last_error: str | None = None
        self._pending_error: str | None = None

    # ------------------------------------------------------------- lifecycle

    @property
    def holds_lease(self) -> bool:
        """Whether this collector is the one sampling."""
        return self._holding

    @property
    def plans(self) -> dict[str, SamplingPlan]:
        """The sampling plans of the last refresh, by domain."""
        return dict(self._plans)

    def start(self) -> None:
        """Start the sampling thread. Starting twice is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"noust-metrics-{self.kind}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the sampling thread, wait for its tick and give the lease up."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=self.interval_s + 5.0)
        self._thread = None
        if self._holding:
            try:
                self.store.release_collector(self._owner)
            except STORE_ERRORS:
                log.warning("the collector lease could not be released", exc_info=True)
        self._holding = False
        self._started = False

    def latest(self) -> dict[str, float]:
        """
        Return the newest complete snapshot.

        When another process holds the lease (the monitor daemon, while this is
        the console) the values are read from the store, where that process
        writes them: the console's live feed keeps working either way.

        Returns:
            Metric name to value, copied so the caller can hold it while the
            collector keeps ticking. Empty until the first tick lands.
        """
        with self._snapshot_lock:
            snapshot = dict(self._snapshot)
        if self._holding and snapshot:
            return snapshot
        try:
            return self.store.latest_values(max_age_s=self.interval_s * LATEST_MAX_AGE_INTERVALS)
        except STORE_ERRORS:
            log.debug("the newest readings could not be read", exc_info=True)
            return snapshot

    # ------------------------------------------------------------------ loop

    def _run(self) -> None:
        """Tick until asked to stop, sampling only while the lease is held."""
        next_at = self._clock()
        while not self._stop.is_set():
            if not self._hold_lease():
                if self._stop.wait(self._retry_seconds()):
                    break
                next_at = self._clock()
                continue
            if not self._started:
                self._startup()
            self._tick()
            next_at += self.interval_s
            delay = next_at - self._clock()
            if delay < -self.interval_s:
                # Fell behind (a suspended machine, a stalled disk): start over
                # from now rather than firing a burst of catch-up ticks.
                next_at = self._clock()
                delay = 0.0
            if self._stop.wait(max(0.0, delay)):
                break

    def _retry_seconds(self) -> float:
        """
        Say how long a collector without the lease waits before asking again.

        Returns:
            One interval for the daemon, which outranks the console and is only
            ever held back by its own predecessor's lease running out after a
            crash or a restart, and :data:`LEASE_RETRY_SECONDS` for the console,
            which is content to wait.
        """
        return self.interval_s if self.kind == "daemon" else LEASE_RETRY_SECONDS

    def _hold_lease(self) -> bool:
        """
        Take the lease, or confirm this collector still has it.

        Returns:
            True when this collector may sample.
        """
        if self._holding:
            return True
        try:
            self._holding = self.store.acquire_collector(
                self._owner, kind=self.kind, interval_s=self.interval_s
            )
        except STORE_ERRORS as exc:
            self._note("the collector lease could not be taken", exc)
            return False
        if self._holding:
            log.info(
                "metrics collector (%s) started sampling every %ss", self.kind, self.interval_s
            )
        return self._holding

    def _startup(self) -> None:
        """
        Catch up once, when the lease is first taken.

        Applies the configured retention and consolidates everything: the
        previous collector may have stopped hours ago, and its last buckets
        should not wait for the timer.
        """
        self._started = True
        try:
            self.store.retention_days = configured_retention_days()
            self.store.consolidate()
            self._consolidated_at = self._clock()
        except STORE_ERRORS as exc:
            self._note("the metrics store could not be consolidated at start-up", exc)

    def _tick(self) -> None:
        """
        Take one sample and heartbeat, as an error boundary.

        An unexpected failure is logged with its traceback and kept on the
        lease row; the loop carries on, because a collector that stops for a
        transient error is a chart that silently ends.
        """
        try:
            self.sample_once()
        except Exception as exc:
            log.exception("a metrics tick failed")
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._pending_error = self.last_error
        error, self._pending_error = self._pending_error, None
        try:
            if not self.store.heartbeat_collector(self._owner, error=error):
                log.info("metrics collector (%s) lost the lease and stops sampling", self.kind)
                self._holding = False
                self._started = False
        except STORE_ERRORS as exc:
            self._note("the collector heartbeat could not be written", exc)

    # ------------------------------------------------------------------ tick

    def sample_once(self) -> dict[str, float]:
        """
        Take one sample of everything and persist it.

        Operational failures are logged, kept as the last error and skipped,
        because this runs on an unattended thread whose death would silently end
        the metrics.

        Returns:
            The snapshot this tick produced.
        """
        now = self._clock()
        elapsed = now - self._last_tick if self._last_tick is not None else None
        self._last_tick = now

        pairs: list[tuple[str, float]] = []
        try:
            pairs.extend(self._machine.sample(elapsed))
        except SAMPLING_ERRORS as exc:
            self._note("the machine could not be sampled", exc)
        plans = self._refresh_plans(now)
        try:
            pairs.extend(self._apps.sample(plans, now, elapsed))
        except SAMPLING_ERRORS as exc:
            self._note("the applications could not be sampled", exc)
        try:
            pairs.extend(self._databases.sample(now))
        except SAMPLING_ERRORS as exc:
            self._note("the databases could not be sampled", exc)

        stamp = self._stamp()
        try:
            self.store.record_many(pairs, ts=stamp)
        except STORE_ERRORS as exc:
            self._note("a metrics tick could not be persisted", exc)
        self._sample_processes(stamp, now, plans)

        if now - self._consolidated_at >= CONSOLIDATE_SECONDS:
            try:
                self.store.consolidate(lookback=CONSOLIDATE_LOOKBACK_SECONDS)
                self._consolidated_at = now
            except STORE_ERRORS as exc:
                self._note("metrics consolidation failed", exc)

        snapshot = dict(pairs)
        with self._snapshot_lock:
            self._snapshot = snapshot
        return snapshot

    def _sample_processes(self, stamp: int, now: float, plans: dict[str, SamplingPlan]) -> None:
        """
        Rank the busiest processes on the first tick of each minute.

        The ranking measures CPU since the previous one, so it describes the
        minute that just ended and is stored under that minute, the bucket the
        per-minute metrics put the same moments in.

        Args:
            stamp: This tick's timestamp on the grid.
            now: The monotonic clock.
            plans: The applications' plans, which name the owners.
        """
        minute = stamp // SAMPLE_SECONDS * SAMPLE_SECONDS
        if minute == self._process_minute:
            return
        self._process_minute = minute
        try:
            samples = self._processes.sample(minute - SAMPLE_SECONDS, now, plans)
        except SAMPLING_ERRORS as exc:
            self._note("the processes could not be sampled", exc)
            return
        try:
            self.store.record_process_samples(samples)
        except STORE_ERRORS as exc:
            self._note("the process samples could not be persisted", exc)

    def _stamp(self) -> int:
        """
        Stamp a tick on the interval's grid.

        Returns:
            The store's clock rounded to the nearest multiple of the interval,
            so consecutive ticks land in consecutive cells however the loop
            drifts, and the series of a tick share one timestamp.
        """
        step = max(1, round(self.interval_s))
        return round(self.store.now() / step) * step

    def _refresh_plans(self, now: float) -> dict[str, SamplingPlan]:
        """
        Rebuild the applications' sampling plans on a slow timer.

        Args:
            now: The current monotonic time.

        Returns:
            The plans by domain. On an error the previous plans are kept: a
            transient failure must not blank every application's series.
        """
        if self._plans_at is not None and now - self._plans_at < APPS_REFRESH_SECONDS:
            return self._plans
        try:
            apps = [app for app in self._apps_source() if getattr(app, "domain", "")]
            planner = self._planner
            if planner is None:
                planner = self._planner = PlanBuilder()
            self._plans = planner.build(apps)
        except (NoustError, sqlite3.Error, OSError) as exc:
            self._note("the applications' sampling plans could not be built", exc)
        self._plans_at = now
        return self._plans

    def _note(self, what: str, exc: BaseException) -> None:
        """
        Keep a failure as the last error and log it, without flooding the journal.

        Args:
            what: What was being done.
            exc: What went wrong.
        """
        text = f"{what}: {exc}"
        self.last_error = text
        self._pending_error = text
        now = self._clock()
        last = self._errors.get(what)
        if last is None or now - last >= ERROR_LOG_EVERY_SECONDS:
            self._errors[what] = now
            log.warning("%s", text, exc_info=True)
        else:
            log.debug("%s", text, exc_info=True)


# ---------------------------------------------------------------------------
# What the console is told
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordingStatus:
    """
    Whether history is being recorded, and why not.

    Attributes:
        recording: True while a collector holds a live lease.
        host: Who is sampling: ``daemon`` (``noust-monitor``) or ``console``;
            None when nobody is.
        since: When the current collector took over, epoch seconds.
        last_sample_at: The newest tick, epoch seconds, or None if none was
            ever recorded.
        interval_s: Seconds between ticks of the current (or last) collector.
        last_error: The newest failure the collector logged, verbatim.
        reason: Why nothing is being recorded, with the fix.
        advice: A hint when recording works but could be better (only while the
            console runs).
        retention_days: How long the hourly tier is kept.
    """

    recording: bool
    host: str | None = None
    since: int | None = None
    last_sample_at: int | None = None
    interval_s: float = DEFAULT_INTERVAL_SECONDS
    last_error: str | None = None
    reason: Reason | None = None
    advice: Reason | None = None
    retention_days: int = DEFAULT_RETENTION_DAYS

    def to_dict(self) -> dict[str, Any]:
        """
        Render the status as the API carries it.

        Returns:
            A JSON-serialisable mapping.
        """
        return {
            "recording": self.recording,
            "host": self.host,
            "since": self.since,
            "last_sample_at": self.last_sample_at,
            "interval_s": self.interval_s,
            "last_error": self.last_error,
            "reason": self.reason.to_dict() if self.reason else None,
            "advice": self.advice.to_dict() if self.advice else None,
            "retention_days": self.retention_days,
        }


def recording_status(
    store: MetricsStore,
    *,
    monitor: Callable[[], dict[str, Any] | None] | None = None,
) -> RecordingStatus:
    """
    Say whether history is being recorded, and if not, why.

    Args:
        store: The metrics store the lease lives in.
        monitor: Asks what systemd knows about the ``noust-monitor`` unit
            (``installed``, ``enabled``, ``active``); None or a probe that
            returns None when systemd cannot be asked.

    Returns:
        The status. When nothing is recording, the reason is the first true one
        of: the monitor is not installed, it is installed but not enabled, it is
        enabled but not running, it runs but its collector stalled, or (no
        systemd to ask) the collector is simply not running.
    """
    now = store.now()
    lease = store.collector_lease()
    if lease is not None and lease.is_live(now):
        advice = None
        if lease.kind == "console":
            advice = Reason(
                code=REASON_CONSOLE_ONLY,
                message="History is recorded only while the console is running.",
                fix="noust monitor enable",
            )
        return RecordingStatus(
            recording=True,
            host=lease.kind,
            since=lease.started_at,
            last_sample_at=lease.heartbeat_at,
            interval_s=lease.interval_s,
            last_error=lease.last_error,
            advice=advice,
            retention_days=store.retention_days,
        )

    probe = monitor() if monitor is not None else None
    last_error = lease.last_error if lease is not None else None
    last_at = lease.heartbeat_at if lease is not None else None
    reason: Reason
    if probe is None:
        reason = Reason(
            code=REASON_COLLECTOR_STOPPED,
            message="No collector is recording metrics.",
            fix="noust monitor enable",
        )
    elif not probe.get("installed"):
        reason = Reason(
            code=REASON_MONITOR_NOT_INSTALLED,
            message="The monitor service that records metrics is not installed.",
            fix="noust monitor enable",
        )
    elif not probe.get("enabled") and not probe.get("active"):
        reason = Reason(
            code=REASON_MONITOR_DISABLED,
            message="The monitor service that records metrics is installed but disabled.",
            fix="noust monitor enable",
        )
    elif not probe.get("active"):
        reason = Reason(
            code=REASON_MONITOR_NOT_RUNNING,
            message="The monitor service that records metrics is enabled but not running.",
            fix="journalctl -u noust-monitor -n 50",
        )
    else:
        reason = Reason(
            code=REASON_COLLECTOR_STALLED,
            message="The monitor service is running but has stopped recording metrics.",
            fix="systemctl restart noust-monitor",
            evidence=last_error,
        )
    return RecordingStatus(
        recording=False,
        since=lease.started_at if lease is not None else None,
        last_sample_at=last_at,
        interval_s=lease.interval_s if lease is not None else DEFAULT_INTERVAL_SECONDS,
        last_error=last_error,
        reason=reason,
        retention_days=store.retention_days,
    )


def monitor_service_probe() -> dict[str, Any] | None:
    """
    Ask systemd about the ``noust-monitor`` unit.

    Returns:
        ``installed``, ``enabled`` and ``active``, from the one place that
        knows (:meth:`ProcessMonitor.get_service_status`); None when systemd
        cannot be asked.
    """
    from noust.monitor.process_monitor import ProcessMonitor

    try:
        return dict(ProcessMonitor(verbose=False).get_service_status())
    except (NoustError, OSError) as exc:
        log.debug("the monitor unit could not be probed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def configured_retention_days() -> int:
    """
    Read ``metrics.retention_days`` from the configuration.

    Returns:
        Days the hourly tier is kept; the default when the setting is absent,
        malformed, or the configuration cannot be read.
    """
    from noust.core.config import Config

    try:
        return resolve_retention_days(
            Config().get("metrics.retention_days", DEFAULT_RETENTION_DAYS)
        )
    except (NoustError, OSError) as exc:
        log.debug("could not read metrics.retention_days: %s", exc)
        return DEFAULT_RETENTION_DAYS


def open_store() -> MetricsStore:
    """
    Open the metrics database at its default location.

    Returns:
        The store, with the configured retention.
    """
    return MetricsStore(default_metrics_db_path(), retention_days=configured_retention_days())


def create_collector(kind: str = "daemon") -> MetricsCollector:
    """
    Build the collector of a process.

    Args:
        kind: ``daemon`` or ``console``.

    Returns:
        A collector on the default store; not started.
    """
    return MetricsCollector(open_store(), kind=kind)


def _stored_apps() -> list[Any]:
    """
    List the applications from the store, the default apps source.

    Returns:
        Every application the store records.
    """
    from noust.core.store import get_store

    return list(get_store().list_apps())
