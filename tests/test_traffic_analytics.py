# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A site's traffic by the hour, kept and asked for (checklist G-08).

The sampler writes what the access log said into the metrics database; the
store answers a window; ``GET /api/apps/{domain}/traffic`` and ``noust app
traffic`` hand it over. What is defended: counts are added and never replaced,
every step of a window is present, the rankings are bounded and ranked,
retention drops what is old, an application behind the proxy is read too, and
the endpoint answers for an application and for nobody else's.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.monitor.appstatus import series_names
from noust.monitor.plan import KIND_UNIT, SOURCE_CGROUP, PlanBuilder, SamplingPlan
from noust.monitor.timeseries import MetricsStore
from noust.monitor.traffic import TrafficSampler
from noust.monitor.traffic_digest import analyse
from noust.monitor.traffic_store import TOP_RETENTION_DAYS
from noust.web import metrics_collector
from noust.web.api.auth import get_current_session
from noust.web.api.metrics import app_router as app_metrics_router

HOUR = 3600
DAY = 86_400
#: 2026-09-29 12:00:00 UTC.
NOW = int(datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc).timestamp())
DOMAIN = "shop.example.com"


def line(
    status: int = 200,
    path: str = "/",
    *,
    when: int,
    ip: str = "203.0.113.9",
    sent: int = 512,
) -> str:
    stamp = datetime.fromtimestamp(when, tz=timezone.utc).strftime("%d/%b/%Y:%H:%M:%S +0000")
    return f'{ip} - - [{stamp}] "GET {path} HTTP/1.1" {status} {sent} "-" "Mozilla/5.0"\n'


@pytest.fixture
def store(tmp_path: Path) -> MetricsStore:
    return MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)


def add(store: MetricsStore, text: str, app: str = DOMAIN) -> None:
    store.add_traffic(app, analyse(text.encode()).hours)


class TestTheStore:
    def test_counts_are_added_to_the_hour_they_belong_to(self, store: MetricsStore) -> None:
        hour = NOW - 3 * HOUR
        add(store, line(200, when=hour + 10) + line(500, "/boom", when=hour + 20))
        add(store, line(200, when=hour + 30))  # the end of the same hour arrives later

        found = store.traffic_summary(DOMAIN, NOW - 6 * HOUR, NOW)

        assert found.totals.requests == 3
        assert (found.totals.s2xx, found.totals.s5xx) == (2, 1)
        assert found.totals.bytes == 3 * 512
        point = next(p for p in found.points if p.ts == hour)
        assert point.requests == 3

    def test_every_hour_of_the_window_is_a_point_even_when_nothing_happened(
        self, store: MetricsStore
    ) -> None:
        add(store, line(when=NOW - 2 * HOUR))

        found = store.traffic_summary(DOMAIN, NOW - 6 * HOUR, NOW)

        assert found.step == HOUR
        assert len(found.points) == 6
        assert sum(1 for p in found.points if p.requests) == 1

    def test_a_long_window_is_answered_by_the_day(self, store: MetricsStore) -> None:
        add(store, line(when=NOW - 3 * DAY))
        add(store, line(when=NOW - 3 * DAY + HOUR))

        found = store.traffic_summary(DOMAIN, NOW - 7 * DAY, NOW)

        assert found.step == DAY
        assert found.totals.requests == 2
        assert sum(p.requests for p in found.points) == 2
        assert max(p.requests for p in found.points) == 2  # both hours fell in one day

    def test_the_rankings_are_ordered_and_bounded(self, store: MetricsStore) -> None:
        text = "".join(line(200, "/", when=NOW - HOUR) for _ in range(5))
        text += "".join(line(200, "/pricing", when=NOW - HOUR) for _ in range(3))
        text += "".join(line(404, f"/x{i}", when=NOW - HOUR) for i in range(20))
        add(store, text)

        found = store.traffic_summary(DOMAIN, NOW - 6 * HOUR, NOW, top=3)

        assert found.top["path"][:2] == [("GET /", 5), ("GET /pricing", 3)]
        assert len(found.top["path"]) == 3
        assert found.top["status"] == [("404", 20), ("200", 8)]
        assert found.top["network"] == [("203.0.113.0/24", 28)]

    def test_rankings_of_several_hours_are_summed(self, store: MetricsStore) -> None:
        add(store, line(200, "/a", when=NOW - 3 * HOUR) * 2)
        add(store, line(200, "/a", when=NOW - 2 * HOUR) * 3)

        found = store.traffic_summary(DOMAIN, NOW - 6 * HOUR, NOW)

        assert found.top["path"] == [("GET /a", 5)]

    def test_one_application_never_sees_anothers_traffic(self, store: MetricsStore) -> None:
        add(store, line(when=NOW - HOUR), "a.example.com")
        add(store, line(when=NOW - HOUR) * 4, "b.example.com")

        found = store.traffic_summary("a.example.com", NOW - 6 * HOUR, NOW)

        assert found.totals.requests == 1
        assert found.top["path"] == [("GET /", 1)]

    def test_an_application_with_no_traffic_has_zeros_and_no_rankings(
        self, store: MetricsStore
    ) -> None:
        found = store.traffic_summary(DOMAIN, NOW - 6 * HOUR, NOW)

        assert found.totals.requests == 0
        assert all(ranking == [] for ranking in found.top.values())

    def test_retention_drops_the_old_and_keeps_the_rankings_for_less_than_the_totals(
        self, store: MetricsStore
    ) -> None:
        old = NOW - (TOP_RETENTION_DAYS + 5) * DAY
        add(store, line(when=old) + line(when=NOW - HOUR))
        store.retention_days = 400

        store.consolidate(now=NOW)

        found = store.traffic_summary(DOMAIN, old - DAY, NOW)
        assert found.totals.requests == 2  # the totals outlive the rankings
        assert [key for key, _ in found.top["path"]] == ["GET /"]
        assert sum(count for _key, count in found.top["path"]) == 1

        store.retention_days = 35
        store.consolidate(now=NOW)
        assert store.traffic_summary(DOMAIN, old - DAY, NOW).totals.requests == 1


class TestTheSampler:
    def plan(self, log: Path) -> dict[str, SamplingPlan]:
        return {
            DOMAIN: SamplingPlan(
                domain=DOMAIN, kind=KIND_UNIT, source=SOURCE_CGROUP, traffic_log=log
            )
        }

    def test_what_the_log_says_is_kept_by_the_hour(
        self, store: MetricsStore, tmp_path: Path
    ) -> None:
        log = tmp_path / f"{DOMAIN}.access.log"
        log.write_text(line(when=NOW - 600) * 2)
        sampler = TrafficSampler(store)
        plans = self.plan(log)
        sampler.sample(plans, 100.0)  # the first look only finds the end
        with log.open("a") as handle:
            handle.write(line(200, "/orders/9", when=NOW - 30) + line(502, "/", when=NOW - 20))

        pairs = dict(sampler.sample(plans, 105.0))

        assert pairs[f"app.{DOMAIN}.http.requests_per_min"] == 24.0
        found = store.traffic_summary(DOMAIN, NOW - 2 * HOUR, NOW + HOUR)
        assert found.totals.requests == 2
        assert (found.totals.s2xx, found.totals.s5xx) == (1, 1)
        assert ("GET /orders/:n", 1) in found.top["path"]

    def test_a_line_is_never_kept_twice(self, store: MetricsStore, tmp_path: Path) -> None:
        log = tmp_path / f"{DOMAIN}.access.log"
        log.write_text("")
        sampler = TrafficSampler(store)
        plans = self.plan(log)
        sampler.sample(plans, 100.0)
        log.write_text(line(when=NOW - 30))

        sampler.sample(plans, 105.0)
        sampler.sample(plans, 110.0)
        sampler.sample(plans, 115.0)

        assert store.traffic_summary(DOMAIN, NOW - HOUR, NOW + HOUR).totals.requests == 1

    def test_a_store_that_cannot_be_written_costs_the_digest_not_the_rates(
        self, store: MetricsStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        def broken(*_args: Any, **_kwargs: Any) -> None:
            raise sqlite3.OperationalError("database is locked")

        log = tmp_path / f"{DOMAIN}.access.log"
        log.write_text("")
        sampler = TrafficSampler(store)
        plans = self.plan(log)
        sampler.sample(plans, 100.0)
        log.write_text(line(when=NOW - 30))
        monkeypatch.setattr(store, "add_traffic", broken)

        pairs = dict(sampler.sample(plans, 105.0))

        assert pairs[f"app.{DOMAIN}.http.requests_per_min"] > 0


class TestAnApplicationBehindTheProxyIsReadToo:
    def test_its_plan_carries_the_log_when_there_is_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / f"{DOMAIN}.access.log"
        log.write_text("")
        monkeypatch.setattr("noust.monitor.plan.log_directories", lambda _server: (tmp_path,))
        app = App(domain=DOMAIN, app_type="nodejs", port=3000)

        plan = PlanBuilder(runner=FakeRunner()).build_one(app)

        assert plan.traffic_log == log
        assert plan.traffic_reason is None
        assert {"requests", "errors_5xx"} <= set(series_names(plan))

    def test_without_a_log_it_is_not_a_fault_of_a_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.monitor.plan.log_directories", lambda _server: (tmp_path,))
        app = App(domain=DOMAIN, app_type="nodejs", port=3000)

        plan = PlanBuilder(runner=FakeRunner()).build_one(app)

        assert plan.traffic_log is None and plan.traffic_reason is None
        assert "requests" not in series_names(plan)


@pytest.fixture
def api(
    store: MetricsStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    NoustStore.reset_instance()
    real = NoustStore(tmp_path / "noust.db")
    real.create_app(App(domain=DOMAIN, app_type="nodejs", port=3000))
    monkeypatch.setattr(metrics_collector, "get_metrics_store", lambda: store)
    monkeypatch.setattr(metrics_collector, "cached_monitor_probe", lambda: None)
    monkeypatch.setattr("noust.monitor.plan.log_directories", lambda _server: (tmp_path,))
    monkeypatch.setattr("noust.web.api.metrics.get_runner", lambda: FakeRunner())
    (tmp_path / f"{DOMAIN}.access.log").write_text("")
    app = FastAPI()
    app.include_router(app_metrics_router, prefix="/api/apps")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}
    try:
        yield TestClient(app)
    finally:
        NoustStore.reset_instance()


class TestTheEndpoint:
    def test_it_answers_the_window_with_totals_points_and_rankings(
        self, api: TestClient, store: MetricsStore
    ) -> None:
        add(store, line(200, "/", when=NOW - HOUR) * 3 + line(500, "/boom", when=NOW - HOUR))

        response = api.get(f"/api/apps/{DOMAIN}/traffic", params={"window": "24h", "top": 5})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["domain"] == DOMAIN and body["window"] == "24h" and body["step"] == HOUR
        assert body["totals"]["requests"] == 4 and body["totals"]["s5xx"] == 1
        assert len(body["points"]) == 24
        assert body["top"]["paths"][0] == {"key": "GET /", "count": 3}
        assert set(body["top"]) == {"paths", "networks", "statuses", "referrers"}
        assert body["log"].endswith(f"{DOMAIN}.access.log")
        assert body["available"] is False and body["collector"]["recording"] is False

    def test_it_is_available_while_the_history_is_being_recorded(
        self, api: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.monitor.collector import RecordingStatus

        monkeypatch.setattr(
            metrics_collector,
            "history_status",
            lambda: RecordingStatus(recording=True, host="daemon"),
        )

        body = api.get(f"/api/apps/{DOMAIN}/traffic").json()

        assert body["available"] is True and body["collector"]["host"] == "daemon"

    def test_a_week_is_answered_by_the_day(self, api: TestClient) -> None:
        body = api.get(f"/api/apps/{DOMAIN}/traffic", params={"window": "7d"}).json()

        assert body["step"] == DAY and len(body["points"]) in (7, 8)

    def test_an_application_with_no_log_says_so(self, api: TestClient, tmp_path: Path) -> None:
        (tmp_path / f"{DOMAIN}.access.log").unlink()

        body = api.get(f"/api/apps/{DOMAIN}/traffic").json()

        assert body["available"] is False
        assert body["totals"]["requests"] == 0

    def test_an_application_that_does_not_exist_is_a_404(self, api: TestClient) -> None:
        assert api.get("/api/apps/nope.example.com/traffic").status_code == 404

    def test_a_window_and_a_top_that_are_not_offered_are_refused(self, api: TestClient) -> None:
        assert api.get(f"/api/apps/{DOMAIN}/traffic", params={"window": "1y"}).status_code == 422
        assert api.get(f"/api/apps/{DOMAIN}/traffic", params={"top": 0}).status_code == 422
        assert api.get(f"/api/apps/{DOMAIN}/traffic", params={"top": 51}).status_code == 422


class TestTheCommand:
    @pytest.fixture
    def history(
        self, store: MetricsStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> MetricsStore:
        NoustStore.reset_instance()
        real = NoustStore(tmp_path / "noust.db")
        real.create_app(App(domain=DOMAIN, app_type="nodejs", port=3000))
        monkeypatch.setattr(
            "noust.cli.commands.app_traffic.default_metrics_db_path", lambda: store.db_path
        )
        monkeypatch.setattr(
            "noust.cli.commands.app_traffic.MetricsStore",
            lambda p: MetricsStore(p, clock=lambda: NOW),
        )
        monkeypatch.setattr("noust.cli.commands.app_traffic.get_store", lambda: real)
        yield store
        NoustStore.reset_instance()

    def test_it_prints_what_was_asked_for(self, history: MetricsStore) -> None:
        add(history, line(200, "/", when=NOW - HOUR) * 2 + line(404, "/x", when=NOW - HOUR))

        result = CliRunner().invoke(root_cli, ["app", "traffic", DOMAIN])

        assert result.exit_code == 0, result.output
        assert "Requests" in result.output and "3" in result.output
        assert "GET /" in result.output and "203.0.113.0/24" in result.output

    def test_json_is_the_same_numbers(self, history: MetricsStore) -> None:
        add(history, line(200, "/", when=NOW - HOUR) + line(500, "/", when=NOW - HOUR))

        result = CliRunner().invoke(root_cli, ["app", "traffic", DOMAIN, "--json"])

        body = json.loads(result.output)
        assert body["totals"]["requests"] == 2 and body["totals"]["s5xx"] == 1
        assert body["top"]["path"][0] == {"key": "GET /", "count": 2}

    def test_an_application_that_does_not_exist_is_refused(self, history: MetricsStore) -> None:
        result = CliRunner().invoke(root_cli, ["app", "traffic", "nope.example.com"])

        assert result.exit_code != 0
        assert "not found" in str(result.exception).lower()
