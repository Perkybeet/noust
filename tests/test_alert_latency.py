# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A slow application raises an alert (the gap the parity audit found in the traffic analytics).

An application that was slow and answered 200 raised no alert and drew no chart, because nothing
measured how long requests took. The sampler now records the p95 of each minute's requests, the
metric an alert rule can watch is ``app_latency_p95``, and its limit is written the way a person
writes one: ``500ms`` or ``2s``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.alerts.model import METRICS, MILLISECONDS, UNITS, format_value, parse_threshold
from noust.core.exceptions import ValidationError
from noust.monitor.exposition import APPLICATION
from noust.monitor.plan import SamplingPlan
from noust.monitor.timeseries import MetricsStore
from noust.monitor.traffic import TrafficSampler, latency_metric_name

NOW = 1_700_002_800
DOMAIN = "docs.example.com"


class TestTheMetric:
    def test_a_rule_can_watch_the_p95_of_an_application(self) -> None:
        spec = METRICS["app_latency_p95"]

        assert spec.scope == "app"
        assert spec.unit == MILLISECONDS
        assert spec.series_for(DOMAIN) == latency_metric_name(DOMAIN)

    def test_the_unit_is_one_of_the_units(self) -> None:
        assert MILLISECONDS in UNITS

    def test_the_series_is_exposed_to_the_scrapers_beside_the_others(self) -> None:
        assert "http.latency_p95_ms" in APPLICATION


class TestTheLimit:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("500", 500.0),
            ("500ms", 500.0),
            (" 250 MS ", 250.0),
            ("2s", 2000.0),
            ("1.5s", 1500.0),
            (750, 750.0),
        ],
    )
    def test_a_limit_is_milliseconds_unless_it_says_seconds(
        self, text: object, expected: float
    ) -> None:
        assert parse_threshold(text, MILLISECONDS) == expected  # type: ignore[arg-type]

    @pytest.mark.parametrize("text", ["fast", "90%", "2MB", "5m", "-1", "nan", "1e400", ""])
    def test_what_is_not_a_time_is_refused(self, text: str) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_threshold(text, MILLISECONDS)
        assert caught.value.field == "threshold"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.4, "<1 ms"),
            (120.0, "120 ms"),
            (999.0, "999 ms"),
            (1500.0, "1.5 s"),
            (12000.0, "12 s"),
        ],
    )
    def test_a_reading_is_said_in_the_unit_that_reads_best(
        self, value: float, expected: str
    ) -> None:
        assert format_value(MILLISECONDS, value) == expected
        assert format_value(MILLISECONDS, value, limit=True) == expected


class TestTheSampler:
    def line(self, tail: str) -> str:
        return (
            '203.0.113.9 - - [29/Sep/2026:10:00:01 +0000] "GET / HTTP/1.1" 200 512 "-" "UA"'
            f"{tail}\n"
        )

    @pytest.fixture
    def store(self, tmp_path: Path) -> MetricsStore:
        return MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)

    def run(self, store: MetricsStore, tmp_path: Path, written: str) -> dict[str, float]:
        log = tmp_path / "docs.access.log"
        log.write_text("")
        sampler = TrafficSampler(store)
        plans = {DOMAIN: SamplingPlan(domain=DOMAIN, kind="static", traffic_log=log)}
        sampler.sample(plans, 100.0)
        with log.open("a") as handle:
            handle.write(written)
        return dict(sampler.sample(plans, 130.0))

    def test_the_p95_of_the_minute_is_a_metric(self, store: MetricsStore, tmp_path: Path) -> None:
        written = self.line(" rt=0.030") * 90 + self.line(" rt=4.000") * 10

        pairs = self.run(store, tmp_path, written)

        assert 2500 <= pairs[latency_metric_name(DOMAIN)] <= 5000

    def test_a_fast_site_reads_fast(self, store: MetricsStore, tmp_path: Path) -> None:
        pairs = self.run(store, tmp_path, self.line(" rt=0.020") * 40)

        assert pairs[latency_metric_name(DOMAIN)] <= 50

    def test_lines_without_the_time_make_no_reading_instead_of_a_zero(
        self, store: MetricsStore, tmp_path: Path
    ) -> None:
        pairs = self.run(store, tmp_path, self.line("") * 10)

        assert latency_metric_name(DOMAIN) not in pairs
        assert pairs["app.docs.example.com.http.requests_per_min"] == 20.0

    def test_a_quiet_site_makes_no_reading(self, store: MetricsStore, tmp_path: Path) -> None:
        pairs = self.run(store, tmp_path, "")

        assert latency_metric_name(DOMAIN) not in pairs
