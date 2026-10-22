# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The endpoint and the command of the traffic page answer the latency and the visitors.

The digest, the store and the metric are pinned in ``test_traffic_latency.py``; here is what
``GET /api/apps/{domain}/traffic`` and ``noust app traffic`` hand over: the median and the p95 of
the time requests took, and how many different visitors there were, null where nothing says it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.monitor.timeseries import MetricsStore
from noust.monitor.traffic_digest import analyse
from noust.web import metrics_collector
from noust.web.api.auth import get_current_session
from noust.web.api.metrics import app_router

DOMAIN = "shop.example.com"
NOW = int(datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc).timestamp())


def timed(when: int, *, ip: str, took: str, status: int = 200) -> str:
    stamp = datetime.fromtimestamp(when, tz=timezone.utc).strftime("%d/%b/%Y:%H:%M:%S +0000")
    return f'{ip} - - [{stamp}] "GET / HTTP/1.1" {status} 512 "-" "UA" rt={took}\n'


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[MetricsStore]:
    metrics = MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)
    text = "".join(
        timed(NOW - 3600 + number, ip=f"203.0.113.{number}", took="0.040")
        for number in range(1, 31)
    ) + "".join(timed(NOW - 3500, ip="198.51.100.1", took="2.500") for _ in range(10))
    metrics.add_traffic(DOMAIN, analyse(text.encode()).hours)
    NoustStore.reset_instance()
    real = NoustStore(tmp_path / "noust.db")
    real.create_app(App(domain=DOMAIN, app_type="nodejs", port=3000))
    monkeypatch.setattr(metrics_collector, "get_metrics_store", lambda: metrics)
    monkeypatch.setattr(metrics_collector, "cached_monitor_probe", lambda: None)
    monkeypatch.setattr("noust.monitor.plan.log_directories", lambda _server: (tmp_path,))
    monkeypatch.setattr("noust.web.api.metrics.get_runner", lambda: FakeRunner())
    (tmp_path / f"{DOMAIN}.access.log").write_text("")
    monkeypatch.setattr(
        "noust.cli.commands.app_traffic.default_metrics_db_path", lambda: metrics.db_path
    )
    monkeypatch.setattr(
        "noust.cli.commands.app_traffic.MetricsStore", lambda p: MetricsStore(p, clock=lambda: NOW)
    )
    monkeypatch.setattr("noust.cli.commands.app_traffic.get_store", lambda: real)
    yield metrics
    NoustStore.reset_instance()


def test_the_endpoint_carries_the_median_the_p95_and_the_visitors(seeded: MetricsStore) -> None:
    app = FastAPI()
    app.include_router(app_router, prefix="/api/apps")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}

    body = TestClient(app).get(f"/api/apps/{DOMAIN}/traffic").json()

    assert body["totals"]["requests"] == 40
    assert body["totals"]["visitors"] == 31
    assert body["totals"]["latency_p50_ms"] < 100
    assert body["totals"]["latency_p95_ms"] > 1000
    measured = [point for point in body["points"] if point["requests"]]
    assert measured and measured[0]["latency_p95_ms"] is not None
    quiet = [point for point in body["points"] if not point["requests"]]
    assert quiet[0]["latency_p95_ms"] is None and quiet[0]["visitors"] is None


def test_the_command_says_them_in_words_and_in_json(seeded: MetricsStore) -> None:
    said = CliRunner().invoke(root_cli, ["app", "traffic", DOMAIN])
    as_data = CliRunner().invoke(root_cli, ["app", "traffic", DOMAIN, "--json"])

    assert said.exit_code == 0, said.output
    assert "Visitors" in said.output and "31" in said.output
    assert "median" in said.output and "p95" in said.output
    totals = json.loads(as_data.output)["totals"]
    assert totals["visitors"] == 31 and totals["latency_p95_ms"] > 1000
