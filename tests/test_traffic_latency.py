# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How long a site took to answer, and how many different visitors it had (the gap the parity audit
found in the traffic analytics).

Coolify's traffic page has the p50 and p95 of the latency and the unique visitors; Noust counted
requests, status codes, bytes, paths, networks and referrers, so an application that was slow and
answered 200 raised no alert and drew no chart. What is pinned: the sites Noust writes log the
time each request took, in the combined format with one word more (every reader of the combined
format keeps reading it), the digest turns it into a histogram that can be merged and read as
percentiles, visitors are a sketch that cannot be turned back into an address and can be merged
across hours, the store keeps both and answers a window, and the p95 of each minute is a metric
an alert rule can watch.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from noust.monitor import sketch, traffic_store
from noust.monitor import traffic_digest as digest_module
from noust.monitor.latency import BOUNDS_MS, Histogram
from noust.monitor.traffic_digest import analyse

STAMP = "[28/Sep/2026:18:13:20 +0000]"


def line(
    *,
    ip: str = "203.0.113.7",
    agent: str = "Mozilla/5.0",
    status: int = 200,
    tail: str = " rt=0.120",
    path: str = "/",
    stamp: str = STAMP,
) -> bytes:
    text = f'{ip} - - {stamp} "GET {path} HTTP/1.1" {status} 512 "-" "{agent}"{tail}'
    return text.encode()


class TestTheHistogram:
    def test_a_time_falls_in_the_first_bucket_that_holds_it(self) -> None:
        histogram = Histogram()

        histogram.add_ms(3)
        histogram.add_ms(5)
        histogram.add_ms(6)
        histogram.add_ms(10**9)

        assert histogram.counts[0] == 2  # up to 5 ms
        assert histogram.counts[1] == 1
        assert histogram.counts[-1] == 1  # past the last bound
        assert histogram.total == 4

    def test_percentiles_are_read_inside_the_bucket_that_holds_them(self) -> None:
        histogram = Histogram()
        for _ in range(100):
            histogram.add_ms(80)  # the 50 to 100 ms bucket

        assert 50 <= histogram.percentile(50) <= 100
        assert 50 <= histogram.percentile(95) <= 100

    def test_a_slow_tail_moves_the_p95_and_not_the_p50(self) -> None:
        histogram = Histogram()
        for _ in range(90):
            histogram.add_ms(20)
        for _ in range(10):
            histogram.add_ms(3000)

        assert histogram.percentile(50) < 50
        assert histogram.percentile(95) > 2500

    def test_nothing_measured_is_not_zero(self) -> None:
        assert Histogram().percentile(95) is None

    def test_histograms_merge_and_round_trip(self) -> None:
        first, second = Histogram(), Histogram()
        first.add_ms(10)
        second.add_ms(10)
        second.add_ms(900)

        first.merge(second)

        assert first.total == 3
        assert Histogram.from_text(first.to_text()).counts == first.counts

    def test_a_damaged_text_reads_as_empty(self) -> None:
        assert Histogram.from_text("not json").total == 0
        assert Histogram.from_text("[1, 2]").total == 0
        assert Histogram.from_text(None).total == 0

    def test_the_bounds_increase(self) -> None:
        assert list(BOUNDS_MS) == sorted(set(BOUNDS_MS))


class TestTheSketch:
    def test_distinct_visitors_are_counted_close_to_the_truth(self) -> None:
        counted = sketch.Sketch()
        for number in range(2000):
            counted.add(f"10.0.{number // 250}.{number % 250}|agent".encode())

        assert 1800 <= counted.estimate() <= 2200

    def test_the_same_visitor_again_is_not_another(self) -> None:
        counted = sketch.Sketch()
        for _ in range(500):
            counted.add(b"203.0.113.7|Mozilla")

        assert counted.estimate() == 1

    def test_a_small_number_is_exact(self) -> None:
        counted = sketch.Sketch()
        for number in range(37):
            counted.add(f"visitor-{number}".encode())

        assert counted.estimate() == 37

    def test_sketches_merge_into_the_union(self) -> None:
        first, second = sketch.Sketch(), sketch.Sketch()
        for number in range(300):
            first.add(f"v{number}".encode())
        for number in range(150, 450):
            second.add(f"v{number}".encode())

        first.merge(second)

        assert 410 <= first.estimate() <= 490

    def test_it_round_trips_sparse_and_dense(self) -> None:
        few, many = sketch.Sketch(), sketch.Sketch()
        for number in range(5):
            few.add(f"v{number}".encode())
        for number in range(5000):
            many.add(f"v{number}".encode())

        assert len(few.to_bytes()) < 100
        assert sketch.Sketch.from_bytes(few.to_bytes()).estimate() == few.estimate()
        assert sketch.Sketch.from_bytes(many.to_bytes()).estimate() == many.estimate()
        assert len(many.to_bytes()) <= 1 + 2**sketch.PRECISION

    def test_what_it_keeps_is_no_address(self) -> None:
        counted = sketch.Sketch()
        counted.add(b"203.0.113.7|Mozilla/5.0")

        assert b"203.0.113.7" not in counted.to_bytes()

    def test_a_damaged_blob_reads_as_empty(self) -> None:
        assert sketch.Sketch.from_bytes(b"").estimate() == 0
        assert sketch.Sketch.from_bytes(b"X123").estimate() == 0
        assert sketch.Sketch.from_bytes(None).estimate() == 0


class TestTheDigest:
    def test_the_time_the_request_took_is_read_in_seconds(self) -> None:
        result = analyse(line(tail=" rt=0.250") + b"\n")

        counts = result.hours[_hour_of(STAMP)]
        assert counts.latency.total == 1
        assert 100 <= counts.latency.percentile(50) <= 250

    def test_apache_gives_microseconds(self) -> None:
        result = analyse(line(tail=" rtus=250000") + b"\n")

        counts = next(iter(result.hours.values()))
        assert counts.latency.total == 1
        assert 100 <= counts.latency.percentile(50) <= 250

    def test_a_line_without_the_time_still_counts_as_a_request(self) -> None:
        result = analyse(line(tail="") + b"\n")

        counts = next(iter(result.hours.values()))
        assert result.requests == 1 and counts.requests == 1
        assert counts.latency.total == 0

    def test_a_time_that_is_not_one_is_ignored(self) -> None:
        text = b"\n".join(
            line(tail=tail) for tail in (" rt=abc", " rt=-1", " rt=nan", " rt=1e999", " rtus=x")
        )

        result = analyse(text + b"\n")

        counts = next(iter(result.hours.values()))
        assert counts.requests == 5 and counts.latency.total == 0

    def test_visitors_are_different_addresses_with_their_agents(self) -> None:
        text = b"\n".join(
            [
                line(ip="203.0.113.7", agent="A"),
                line(ip="203.0.113.7", agent="A"),
                line(ip="203.0.113.7", agent="B"),
                line(ip="198.51.100.9", agent="A"),
            ]
        )

        result = analyse(text + b"\n")

        counts = next(iter(result.hours.values()))
        assert counts.visitors.estimate() == 3

    def test_an_agent_with_quotes_does_not_break_the_line(self) -> None:
        result = analyse(line(agent='Mozilla \\"x\\" 5.0') + b"\n")

        assert result.requests == 1

    def test_the_old_combined_line_is_still_read(self) -> None:
        old = b'203.0.113.7 - - [28/Sep/2026:18:13:20 +0000] "GET / HTTP/1.1" 200 512 "-" "UA"\n'

        result = analyse(old)

        assert result.requests == 1
        assert next(iter(result.hours.values())).latency.total == 0

    def test_hours_merge_with_their_latency_and_visitors(self) -> None:
        first = analyse(line(ip="1.1.1.1") + b"\n").hours
        second = analyse(line(ip="2.2.2.2", tail=" rt=2.0") + b"\n").hours
        counts = next(iter(first.values()))

        counts.merge(next(iter(second.values())))

        assert counts.requests == 2
        assert counts.latency.total == 2
        assert counts.visitors.estimate() == 2


def _hour_of(stamp: str) -> int:
    seconds = digest_module.parse_time(stamp.strip("[]").encode())
    assert seconds is not None
    return seconds - seconds % 3600


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "metrics.db")
    connection.row_factory = sqlite3.Row
    traffic_store.create_tables(connection)
    return connection


class TestTheStore:
    def hours(self, *lines: bytes) -> dict:
        return analyse(b"\n".join(lines) + b"\n").hours

    def test_a_windows_percentiles_and_visitors_are_answered(
        self, conn: sqlite3.Connection
    ) -> None:
        lines = [line(ip=f"10.0.0.{n}", tail=" rt=0.050") for n in range(40)]
        lines += [line(ip="10.1.0.1", tail=" rt=3.000") for _ in range(4)]
        traffic_store.add(conn, "shop.example.com", self.hours(*lines))
        stamp = _hour_of(STAMP)

        found = traffic_store.summary(conn, "shop.example.com", stamp - 3600, stamp + 7200)

        assert found.totals.requests == 44
        assert 25 <= found.totals.latency_p50_ms <= 100
        assert found.totals.latency_p95_ms is not None
        assert found.totals.visitors == 41
        busy = next(point for point in found.points if point.requests)
        assert busy.visitors == 41
        assert busy.latency_p50_ms is not None

    def test_an_hour_with_no_measurement_has_no_latency(self, conn: sqlite3.Connection) -> None:
        traffic_store.add(conn, "a.example.com", self.hours(line(tail="")))
        stamp = _hour_of(STAMP)

        found = traffic_store.summary(conn, "a.example.com", stamp, stamp + 3600)

        assert found.totals.latency_p50_ms is None
        assert found.totals.latency_p95_ms is None
        assert found.totals.visitors == 1

    def test_reading_in_pieces_adds_up(self, conn: sqlite3.Connection) -> None:
        traffic_store.add(conn, "a.example.com", self.hours(line(ip="1.1.1.1")))
        traffic_store.add(conn, "a.example.com", self.hours(line(ip="2.2.2.2")))
        traffic_store.add(conn, "a.example.com", self.hours(line(ip="1.1.1.1")))
        stamp = _hour_of(STAMP)

        found = traffic_store.summary(conn, "a.example.com", stamp, stamp + 3600)

        assert found.totals.requests == 3
        assert found.totals.visitors == 2

    def test_the_days_of_a_long_window_merge_their_hours(self, conn: sqlite3.Connection) -> None:
        later = STAMP.replace("18:13:20", "19:13:20")
        traffic_store.add(conn, "a.example.com", self.hours(line(ip="1.1.1.1")))
        traffic_store.add(conn, "a.example.com", self.hours(line(ip="1.1.1.1", stamp=later)))
        traffic_store.add(conn, "a.example.com", self.hours(line(ip="2.2.2.2", stamp=later)))
        stamp = _hour_of(STAMP)

        found = traffic_store.summary(conn, "a.example.com", stamp - 86_400 * 3, stamp + 86_400)

        # Hourly points say two visitors in one hour and one in the other; the day, as a visitor
        # who came twice is one, says two.
        assert found.step == 86_400
        assert found.totals.visitors == 2
        assert max(point.visitors or 0 for point in found.points) == 2

    def test_the_sketches_are_kept_as_long_as_the_rankings_and_the_totals_longer(
        self, conn: sqlite3.Connection
    ) -> None:
        traffic_store.add(conn, "a.example.com", self.hours(line()))
        hour = _hour_of(STAMP)
        now = hour + (traffic_store.TOP_RETENTION_DAYS + 1) * 86_400

        traffic_store.purge(conn, now, 400)

        assert conn.execute("SELECT COUNT(*) FROM traffic_hourly").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM traffic_visitors").fetchone()[0] == 0
        found = traffic_store.summary(conn, "a.example.com", hour, hour + 3600)
        assert found.totals.requests == 1
        assert found.totals.visitors is None

    def test_a_database_from_before_the_latency_is_brought_up(self, tmp_path: Path) -> None:
        old = sqlite3.connect(tmp_path / "old.db")
        old.execute(
            "CREATE TABLE traffic_hourly (app TEXT NOT NULL, hour INTEGER NOT NULL, "
            "requests INTEGER NOT NULL, s2xx INTEGER NOT NULL, s3xx INTEGER NOT NULL, "
            "s4xx INTEGER NOT NULL, s5xx INTEGER NOT NULL, bytes INTEGER NOT NULL, "
            "PRIMARY KEY (app, hour)) WITHOUT ROWID"
        )
        old.execute("INSERT INTO traffic_hourly VALUES ('a', 3600, 5, 5, 0, 0, 0, 100)")
        old.row_factory = sqlite3.Row

        traffic_store.create_tables(old)
        found = traffic_store.summary(old, "a", 0, 7200)

        assert found.totals.requests == 5
        assert found.totals.latency_p95_ms is None
