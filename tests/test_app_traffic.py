# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the traffic of static sites, read from the access log.

A static site has no process; its only useful number is how many requests it
serves and how many fail. The work is bounded on purpose, so what is defended is
that: the log is read incrementally and never twice, a rotation is recognised, a
huge backlog is skipped and not replayed, a half-written line waits, and a log
seen for the first time contributes nothing but a starting point.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from noust.monitor import traffic as traffic_module
from noust.monitor.plan import SamplingPlan
from noust.monitor.timeseries import MetricsStore
from noust.monitor.traffic import (
    MAX_BACKLOG_BYTES,
    TrafficSampler,
    count_lines,
    read_log_delta,
    traffic_metric_names,
)

NOW = 1_700_002_800


def line(status: int = 200, path: str = "/", ip: str = "203.0.113.9") -> str:
    """One access log line in the combined format."""
    return (
        f'{ip} - - [29/Sep/2026:10:00:01 +0000] "GET {path} HTTP/1.1" {status} 512 '
        '"-" "Mozilla/5.0 (X11; Linux)"\n'
    )


# ------------------------------------------------------------------ counting


def test_requests_and_server_errors_are_counted_from_combined_lines() -> None:
    """A 5xx is an error; 4xx are requests that worked as intended."""
    data = (line(200) + line(404) + line(500) + line(502) + line(301)).encode()

    assert count_lines(data) == (5, 2)


def test_a_line_that_is_not_a_request_is_not_counted() -> None:
    """Garbage, blank lines and a truncated fragment are skipped, not counted."""
    data = b"\n\nnot a log line\n" + line(200).encode() + b"1.2.3.4 - -\n"

    assert count_lines(data) == (1, 0)


def test_an_ipv6_client_and_a_quoted_request_are_read() -> None:
    """The client may be an IPv6 address and the request may hold an escaped quote."""
    data = b'2001:db8::1 - - [29/Sep/2026:10:00:01 +0000] "GET /a\\"b HTTP/1.1" 503 0 "-" "-"\n'

    assert count_lines(data) == (1, 1)


def test_the_metric_names_are_stable() -> None:
    """The API and the collector agree on these; the console reads them by name."""
    assert traffic_metric_names("docs.example.com") == (
        "app.docs.example.com.http.requests_per_min",
        "app.docs.example.com.http.errors_5xx_per_min",
    )


# ------------------------------------------------------------- incremental read


def test_a_log_read_for_the_first_time_starts_at_its_end(tmp_path: Path) -> None:
    """Its history is not a burst of traffic that happened just now."""
    log = tmp_path / "a.access.log"
    log.write_text(line() * 100)

    delta = read_log_delta(log, None)

    assert delta.first_read
    assert (delta.requests, delta.errors_5xx) == (0, 0)
    assert delta.position == log.stat().st_size


def test_only_what_was_appended_is_counted(tmp_path: Path) -> None:
    """The cursor is where the last read stopped."""
    log = tmp_path / "a.access.log"
    log.write_text(line() * 3)
    first = read_log_delta(log, None)

    with log.open("a") as handle:
        handle.write(line(200) + line(500))
    second = read_log_delta(log, (first.inode, first.position))
    third = read_log_delta(log, (second.inode, second.position))

    assert (second.requests, second.errors_5xx) == (2, 1)
    assert (third.requests, third.errors_5xx) == (0, 0)
    assert third.position == second.position


def test_a_half_written_line_waits_for_its_end(tmp_path: Path) -> None:
    """The server may be mid-write: an incomplete line is read once it is complete."""
    log = tmp_path / "a.access.log"
    log.write_text("")
    start = read_log_delta(log, None)
    whole = line(200)
    log.write_text(whole + whole[:20])

    partial = read_log_delta(log, (start.inode, start.position))
    log.write_text(whole + whole)
    rest = read_log_delta(log, (partial.inode, partial.position))

    assert partial.requests == 1
    assert partial.position == len(whole)
    assert rest.requests == 1


def test_a_replaced_file_starts_over(tmp_path: Path) -> None:
    """logrotate renames the log and the server opens a new one: a new inode."""
    log = tmp_path / "a.access.log"
    log.write_text(line() * 10)
    first = read_log_delta(log, None)
    os.rename(log, tmp_path / "a.access.log.1")
    log.write_text(line(500) + line(200))

    delta = read_log_delta(log, (first.inode, first.position))

    assert delta.rotated
    assert (delta.requests, delta.errors_5xx) == (2, 1)


def test_a_truncated_file_starts_over(tmp_path: Path) -> None:
    """copytruncate keeps the inode but shrinks the size below the cursor."""
    log = tmp_path / "a.access.log"
    log.write_text(line() * 10)
    first = read_log_delta(log, None)
    log.write_text(line(503))

    delta = read_log_delta(log, (first.inode, first.position))

    assert delta.rotated
    assert (delta.requests, delta.errors_5xx) == (1, 1)


def test_a_huge_backlog_is_skipped_not_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A daemon that was down for a day does not read a day of traffic in one tick."""
    monkeypatch.setattr(traffic_module, "MAX_BACKLOG_BYTES", 2_000)
    log = tmp_path / "a.access.log"
    log.write_text(line() * 4)
    first = read_log_delta(log, None)
    with log.open("a") as handle:
        handle.write(line() * 200)

    delta = read_log_delta(log, (first.inode, first.position), max_bytes=1_000)

    assert delta.skipped > 0
    assert 0 < delta.requests <= 1_000 // len(line())
    assert first.position < delta.position <= log.stat().st_size


def test_the_default_backlog_limit_is_a_real_limit() -> None:
    """Eight megabytes: more than a busy site writes between two ticks by a long way."""
    assert MAX_BACKLOG_BYTES == 8 * 1024 * 1024


def test_one_read_is_bounded(tmp_path: Path) -> None:
    """A tick never reads more than its budget from one log."""
    log = tmp_path / "a.access.log"
    log.write_text(line())
    first = read_log_delta(log, None)
    with log.open("a") as handle:
        handle.write(line() * 1_000)

    delta = read_log_delta(log, (first.inode, first.position), max_bytes=2_048)

    assert delta.requests <= 2_048 // len(line())
    assert delta.position < log.stat().st_size, "the rest waits for the next tick"


def test_an_oversized_line_cannot_stall_the_reader(tmp_path: Path) -> None:
    """A budget's worth of bytes without a newline is abandoned, not waited on forever."""
    log = tmp_path / "a.access.log"
    log.write_text("")
    first = read_log_delta(log, None)
    log.write_text("x" * 5_000)

    delta = read_log_delta(log, (first.inode, first.position), max_bytes=1_000)

    assert delta.requests == 0
    assert delta.position == 1_000


def test_a_log_that_vanished_raises_for_the_caller_to_skip(tmp_path: Path) -> None:
    """The sampler treats OSError as 'nothing this tick'."""
    with pytest.raises(OSError):
        read_log_delta(tmp_path / "gone.log", None)


# --------------------------------------------------------------------- sampler


def plan_for(domain: str, log: Path) -> SamplingPlan:
    """A static site's plan."""
    return SamplingPlan(domain=domain, kind="static", traffic_log=log)


@pytest.fixture
def store(tmp_path: Path) -> MetricsStore:
    """A metrics store on a throwaway database."""
    return MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)


def test_the_first_tick_only_sets_the_baseline(store: MetricsStore, tmp_path: Path) -> None:
    """No history is counted, and no rate is invented from no interval."""
    log = tmp_path / "docs.access.log"
    log.write_text(line() * 50)
    sampler = TrafficSampler(store)

    pairs = sampler.sample({"docs.example.com": plan_for("docs.example.com", log)}, 0.0)

    assert pairs == []
    assert store.read_log_cursor(str(log)) is not None


def test_traffic_is_a_per_minute_rate_over_the_seconds_since_the_last_read(
    store: MetricsStore, tmp_path: Path
) -> None:
    """Thirty requests over thirty seconds is sixty a minute; two of them failed."""
    log = tmp_path / "docs.access.log"
    log.write_text("")
    sampler = TrafficSampler(store)
    plans = {"docs.example.com": plan_for("docs.example.com", log)}
    sampler.sample(plans, 100.0)
    with log.open("a") as handle:
        handle.write(line() * 28 + line(500) * 2)

    pairs = dict(sampler.sample(plans, 130.0))

    assert pairs["app.docs.example.com.http.requests_per_min"] == 60.0
    assert pairs["app.docs.example.com.http.errors_5xx_per_min"] == 4.0


def test_a_quiet_site_records_zero_not_nothing(store: MetricsStore, tmp_path: Path) -> None:
    """No requests is a real reading: the site is up and nobody came."""
    log = tmp_path / "docs.access.log"
    log.write_text("")
    sampler = TrafficSampler(store)
    plans = {"docs.example.com": plan_for("docs.example.com", log)}
    sampler.sample(plans, 100.0)

    pairs = dict(sampler.sample(plans, 105.0))

    assert pairs["app.docs.example.com.http.requests_per_min"] == 0.0


def test_the_cursor_survives_a_new_sampler(store: MetricsStore, tmp_path: Path) -> None:
    """A restart of the monitor neither recounts a line nor loses the position."""
    log = tmp_path / "docs.access.log"
    log.write_text("")
    plans = {"docs.example.com": plan_for("docs.example.com", log)}
    TrafficSampler(store).sample(plans, 100.0)
    with log.open("a") as handle:
        handle.write(line() * 5)

    reborn = TrafficSampler(store)
    assert reborn.sample(plans, 200.0) == [], "no interval to turn the count into a rate"
    again = dict(reborn.sample(plans, 260.0))

    assert again["app.docs.example.com.http.requests_per_min"] == 0.0, "counted once, not twice"


def test_an_unreadable_log_contributes_nothing(store: MetricsStore, tmp_path: Path) -> None:
    """Permission, a vanished file: skipped this tick, no exception."""
    sampler = TrafficSampler(store)

    pairs = sampler.sample({"a": plan_for("a", tmp_path / "gone.log")}, 1.0)

    assert pairs == []


def test_the_tick_budget_is_shared_and_fair(store: MetricsStore, tmp_path: Path) -> None:
    """When the budget runs out the logs left unread are read first next time."""
    logs = {}
    plans = {}
    for name in ("a", "b", "c"):
        path = tmp_path / f"{name}.access.log"
        path.write_text("")
        logs[name] = path
        plans[name] = plan_for(name, path)
    sampler = TrafficSampler(store, budget_bytes=len(line()) * 2)
    for step in (100.0, 101.0):
        sampler.sample(plans, step)
    for path in logs.values():
        with path.open("a") as handle:
            handle.write(line() * 50)

    counted: set[str] = set()
    for step in range(3):
        for metric, _value in sampler.sample(plans, 110.0 + step):
            counted.add(metric.split(".")[1])

    assert counted == {"a", "b", "c"}


def test_logs_that_are_no_longer_planned_lose_their_cursor(
    store: MetricsStore, tmp_path: Path
) -> None:
    """A deleted site stops costing a row."""
    log = tmp_path / "docs.access.log"
    log.write_text("")
    sampler = TrafficSampler(store)
    sampler.sample({"d": plan_for("d", log)}, 1.0)
    assert store.read_log_cursor(str(log)) is not None

    sampler.sample({}, 2.0)

    assert store.read_log_cursor(str(log)) is None
