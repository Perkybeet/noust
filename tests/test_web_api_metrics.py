# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the metrics history API.

The charts load their past from here and their present from the ``/events``
stream. What is defended:

- **The endpoints demand a session.** Metric names alone reveal every
  application on the machine.
- **A range read answers the window that was asked for**: the requested domain
  explicitly, one grid for every series, ``null`` where nothing was recorded, and
  the tier that was actually read.
- **The read says whether history is being recorded at all**, and when it is
  not, why and how to fix it (the "selector changes nothing" report was largely
  a collector that only ran while the console did).
- **An application's tab says why it is empty**: a machine-readable reason, the
  fix and the system's own output.
- **The single-metric read still works** until the console reads ranges.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.monitor.plan import PLAN_PROPERTIES, PlanBuilder
from noust.monitor.timeseries import MetricsStore
from noust.web import metrics_collector
from noust.web.api import metrics as metrics_api
from noust.web.api.auth import get_current_session
from noust.web.api.metrics import QUERY_WINDOWS, WINDOWS
from noust.web.api.metrics import app_router as app_metrics_router
from noust.web.api.metrics import router as metrics_router

#: A fixed "now" the store's injected clock reports.
NOW = 1_700_002_800

HOUR = 3_600
DAY = 86_400


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MetricsStore:
    """
    Put the process-wide metrics store on a throwaway database.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The store the API under test will read.
    """
    instance = MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)
    monkeypatch.setattr(metrics_collector, "get_metrics_store", lambda: instance)
    monkeypatch.setattr(metrics_collector, "cached_monitor_probe", lambda: None)
    return instance


@pytest.fixture
def client(store: MetricsStore) -> TestClient:
    """
    Build a client for the metrics routers with authentication stubbed.

    Args:
        store: The seeded store fixture.

    Returns:
        A client whose requests are already authenticated.
    """
    app = FastAPI()
    app.include_router(metrics_router, prefix="/api/metrics")
    app.include_router(app_metrics_router, prefix="/api/apps")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}
    return TestClient(app)


def test_the_metric_list_names_what_has_data(client: TestClient, store: MetricsStore) -> None:
    """GET /api/metrics answers with the names the collector has recorded."""
    store.record("cpu.percent", 12.5)
    store.record("app.example.com.mem.bytes", 4096.0)

    response = client.get("/api/metrics")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metrics"] == ["app.example.com.mem.bytes", "cpu.percent"]
    assert set(body["windows"]) == set(QUERY_WINDOWS)
    assert body["database"].endswith("metrics.db")
    assert body["collector"]["recording"] is False


# ------------------------------------------------------------------ the batch


def test_a_batch_read_returns_one_grid_for_every_series(
    client: TestClient, store: MetricsStore
) -> None:
    """Several metrics in one request, every series on the same timestamps."""
    store.record("cpu.percent", 10.0, ts=NOW - 20)
    store.record("mem.used_bytes", 2048.0, ts=NOW - 20)
    store.record("mem.total_bytes", 4096.0, ts=NOW - 20)

    response = client.get(
        "/api/metrics/query",
        params=[("metric", "cpu.percent"), ("metric", "mem.used_bytes"), ("window", "1h")],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from"] == NOW - HOUR
    assert body["to"] == NOW
    assert body["window"] == "1h"
    assert body["resolution"] == "raw"
    assert body["step"] == 5
    cpu, memory = body["series"]
    assert [ts for ts, _m, _p in cpu["points"]] == [ts for ts, _m, _p in memory["points"]]
    assert cpu["points"][-1 - 4] == [NOW - 20, 10.0, 10.0]
    assert memory["ceiling"] == 4096.0
    assert body["first_sample_at"] == NOW - 20
    assert body["last_sample_at"] == NOW - 20


@pytest.mark.parametrize(
    ("window", "resolution", "step"),
    [("1h", "raw", 5), ("24h", "1m", 60), ("7d", "10m", 600), ("30d", "1h", 3_600)],
)
def test_each_selector_window_is_answered_from_one_tier_and_says_which(
    client: TestClient, window: str, resolution: str, step: int
) -> None:
    """A chart can label its axis honestly: the tier read, and the seconds per cell."""
    response = client.get("/api/metrics/query", params={"metric": "cpu.percent", "window": window})

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["resolution"], body["step"]) == (resolution, step)
    assert body["to"] - body["from"] == QUERY_WINDOWS[window]


def test_a_window_the_data_does_not_fill_is_still_the_whole_window(
    client: TestClient, store: MetricsStore
) -> None:
    """The domain is the request, not the span of the data: gaps are nulls."""
    for offset in range(0, 1_800, 5):
        store.record("cpu.percent", 5.0, ts=NOW - offset)
    store.consolidate()

    body = client.get(
        "/api/metrics/query", params={"metric": "cpu.percent", "window": "24h"}
    ).json()

    points = body["series"][0]["points"]
    assert points[0][0] == NOW - DAY
    assert points[-1][0] == NOW
    assert points[0][1] is None
    assert points[-1][1] == 5.0
    assert body["first_sample_at"] == NOW - 1_795


def test_an_explicit_range_is_a_zoom(client: TestClient, store: MetricsStore) -> None:
    """from/to ask for exactly that stretch, at the finest tier that still holds it."""
    for offset in range(0, 3 * HOUR, 5):
        store.record("cpu.percent", 1.0, ts=NOW - offset)

    body = client.get(
        "/api/metrics/query",
        params={"metric": "cpu.percent", "from": NOW - 900, "to": NOW - 300},
    ).json()

    assert (body["from"], body["to"]) == (NOW - 900, NOW - 300)
    assert body["window"] is None
    assert body["resolution"] == "raw"
    assert len(body["series"][0]["points"]) == 121


@pytest.mark.parametrize(
    "params",
    [
        {"metric": "cpu.percent", "from": NOW - 60},
        {"metric": "cpu.percent", "to": NOW},
        {"metric": "cpu.percent", "from": NOW, "to": NOW - 60},
        {"metric": "cpu.percent", "window": "2h"},
        {"metric": "cpu.percent", "step": 7},
    ],
)
def test_an_impossible_range_is_refused(client: TestClient, params: dict[str, object]) -> None:
    """Half a range, a backwards one, an unknown window and a step no tier has."""
    response = client.get("/api/metrics/query", params=params)

    assert response.status_code in (400, 422), response.text


def test_a_batch_needs_at_least_one_metric(client: TestClient) -> None:
    """An empty query is a client mistake, not an empty chart."""
    assert client.get("/api/metrics/query").status_code == 422


def test_a_batch_is_bounded(client: TestClient) -> None:
    """The API cannot be asked for the whole database in one request."""
    response = client.get("/api/metrics/query", params=[("metric", f"m{i}") for i in range(41)])

    assert response.status_code in (400, 422), response.text


def test_query_is_not_read_as_a_metric_name(client: TestClient) -> None:
    """The static path must be declared before the catch-all metric path."""
    response = client.get("/api/metrics/query", params={"metric": "cpu.percent"})

    assert "series" in response.json()


# ------------------------------------------------------- is history recorded


def test_the_read_says_when_a_collector_is_recording(
    client: TestClient, store: MetricsStore
) -> None:
    """A live lease: recording, by whom, and since when."""
    store.acquire_collector("monitor", kind="daemon", interval_s=5)

    collector = client.get(
        "/api/metrics/query", params={"metric": "cpu.percent", "window": "1h"}
    ).json()["collector"]

    assert collector["recording"] is True
    assert collector["host"] == "daemon"
    assert collector["reason"] is None
    assert collector["since"] == NOW


def test_the_read_says_why_nothing_is_recorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No collector and no monitor unit: the reason has a code and a command."""
    monkeypatch.setattr(
        metrics_collector,
        "cached_monitor_probe",
        lambda: {"installed": False, "enabled": False, "active": False},
    )

    collector = client.get(
        "/api/metrics/query", params={"metric": "cpu.percent", "window": "1h"}
    ).json()["collector"]

    assert collector["recording"] is False
    assert collector["reason"]["code"] == "monitor_not_installed"
    assert collector["reason"]["fix"] == "noust monitor enable"


def test_a_console_only_collector_is_recording_with_advice(
    client: TestClient, store: MetricsStore
) -> None:
    """The console samples when no daemon does, and says the history is fragile."""
    store.acquire_collector("web", kind="console", interval_s=5)

    collector = client.get(
        "/api/metrics/query", params={"metric": "cpu.percent", "window": "1h"}
    ).json()["collector"]

    assert collector["recording"] is True
    assert collector["host"] == "console"
    assert collector["advice"]["code"] == "console_only"


# ------------------------------------------------------- the single-metric read


def test_a_metric_window_comes_back_as_timestamped_points(
    client: TestClient, store: MetricsStore
) -> None:
    """The shape the charts consumed: [ts, value] pairs, oldest first."""
    store.record("cpu.percent", 10.0, ts=NOW - 20)
    store.record("cpu.percent", 30.0, ts=NOW - 10)

    response = client.get("/api/metrics/cpu.percent?window=1h")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "metric": "cpu.percent",
        "window": "1h",
        "resolution": "raw",
        "points": [[NOW - 20, 10.0], [NOW - 10, 30.0]],
    }


def test_a_week_is_answered_from_ten_minute_means(client: TestClient, store: MetricsStore) -> None:
    """window=7d falls in the ten-minute tier, and the label says so."""
    store.record("cpu.percent", 42.0, ts=NOW - 5 * DAY)
    store.consolidate()

    response = client.get("/api/metrics/cpu.percent?window=7d")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window"] == "7d"
    assert body["resolution"] == "10m"
    assert [value for _ts, value in body["points"]] == [42.0]


def test_the_window_defaults_to_an_hour(client: TestClient, store: MetricsStore) -> None:
    """A client that says nothing gets the raw tier."""
    store.record("cpu.percent", 10.0, ts=NOW - 10)

    response = client.get("/api/metrics/cpu.percent")

    assert response.status_code == 200, response.text
    assert response.json()["window"] == "1h"


@pytest.mark.parametrize("window", ["2h", "60", "", "1h; DROP TABLE samples"])
def test_a_window_outside_the_vocabulary_is_refused(client: TestClient, window: str) -> None:
    """
    The windows are a closed vocabulary.

    Args:
        window: A window name the API does not offer.
    """
    response = client.get("/api/metrics/cpu.percent", params={"window": window})

    assert response.status_code == 422, response.text


def test_a_metric_with_no_data_is_an_empty_chart_not_a_404(client: TestClient) -> None:
    """Every metric starts empty; that is a normal state, not a missing resource."""
    response = client.get("/api/metrics/app.example.com.cpu.percent?window=24h")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["points"] == []
    assert body["resolution"] == "minute"


@pytest.mark.parametrize(
    ("window", "resolution"),
    [("1h", "raw"), ("24h", "minute"), ("7d", "10m"), ("30d", "hour")],
)
def test_the_response_states_its_resolution(
    client: TestClient, window: str, resolution: str
) -> None:
    """Every window says which tier answered it, so a chart can label its axis honestly."""
    response = client.get(f"/api/metrics/cpu.percent?window={window}")

    assert response.status_code == 200, response.text
    assert response.json()["resolution"] == resolution


def test_every_window_lands_on_a_native_tier() -> None:
    """The vocabulary and the store's tiers must not drift apart."""
    from noust.monitor.timeseries import HOUR as HOUR_TIER
    from noust.monitor.timeseries import MINUTE, RAW, TEN_MINUTES

    assert WINDOWS["1h"] <= (RAW.retention or 0)
    assert (RAW.retention or 0) < WINDOWS["24h"] <= (MINUTE.retention or 0)
    assert (MINUTE.retention or 0) < WINDOWS["7d"] <= (TEN_MINUTES.retention or 0)
    assert (TEN_MINUTES.retention or 0) < WINDOWS["30d"]
    assert HOUR_TIER.retention is None


def test_the_endpoints_demand_a_session(tmp_path: Path) -> None:
    """Metric names alone reveal every application on the machine."""
    from noust.web.auth import SecurityConfig
    from noust.web.server import create_app

    app = create_app(SecurityConfig(state_dir=tmp_path / "state"))
    anonymous = TestClient(app, client=("testclient", 50000))

    assert anonymous.get("/api/metrics").status_code == 401
    assert anonymous.get("/api/metrics/cpu.percent").status_code == 401
    assert anonymous.get("/api/metrics/query?metric=cpu.percent").status_code == 401
    assert anonymous.get("/api/apps/example.com/metrics").status_code == 401


# --------------------------------------------------------- an application's tab


@pytest.fixture
def noust_store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Give the application endpoint a store of its own.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The store the application is looked up in.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def cgroups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Point the plan builder the API creates at a fake cgroup tree.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The fake mount of the unified hierarchy.
    """
    mount = tmp_path / "cgroup"
    mount.mkdir()
    (mount / "cgroup.controllers").write_text("cpu memory\n")
    monkeypatch.setattr(
        metrics_api, "PlanBuilder", lambda runner: PlanBuilder(runner=runner, cgroup_mount=mount)
    )
    return mount


def deploy(store: NoustStore, domain: str, **fields: object) -> App:
    """
    Record an application.

    Args:
        store: The store.
        domain: Its domain.
        **fields: Fields to override.

    Returns:
        The stored application.
    """
    values: dict[str, object] = {
        "domain": domain,
        "app_type": "nextjs",
        "source": "https://github.com/you/app",
        "port": 3000,
        "app_path": f"/var/www/apps/{domain}",
        "status": "running",
        "is_static": False,
    }
    values.update(fields)
    return store.create_app(App(**values))  # type: ignore[arg-type]


def script_unit(runner: FakeRunner, unit: str, **properties: str) -> None:
    """
    Script ``systemctl show`` for one unit.

    Args:
        runner: The fake runner.
        unit: The unit, without ``.service``.
        **properties: Properties systemd answers with.
    """
    body = {"Id": f"{unit}.service", "LoadState": "loaded", **properties}
    runner.script(
        ["systemctl", "show", "--no-pager", "-p", ",".join(PLAN_PROPERTIES), "--"],
        stdout="\n".join(f"{key}={value}" for key, value in body.items()) + "\n",
    )


def test_a_measured_application_says_it_is_sampled(
    client: TestClient,
    store: MetricsStore,
    noust_store: NoustStore,
    runner: FakeRunner,
    cgroups: Path,
) -> None:
    """A running unit with a readable cgroup and fresh samples has no reason."""
    from noust.core.store import Service

    deploy(noust_store, "shop.example.com")
    noust_store.create_service(Service(name="shop-example-com", status="active"))
    unit_dir = cgroups / "system.slice" / "shop-example-com.service"
    unit_dir.mkdir(parents=True)
    (unit_dir / "memory.current").write_text("4096\n")
    (unit_dir / "cpu.stat").write_text("usage_usec 1\n")
    script_unit(
        runner,
        "shop-example-com",
        ActiveState="active",
        ControlGroup="/system.slice/shop-example-com.service",
        MemoryAccounting="yes",
    )
    store.acquire_collector("monitor", kind="daemon", interval_s=5)
    store.record("app.shop.example.com.mem.bytes", 4096.0, ts=NOW - 5)

    body = client.get("/api/apps/shop.example.com/metrics").json()

    assert body["sampled"] is True
    assert body["reason"] is None
    assert body["kind"] == "unit"
    assert body["source"] == "cgroup"
    assert body["units"][0]["name"] == "shop-example-com"
    assert body["units"][0]["cgroup_exists"] is True
    assert body["units"][0]["memory_current"] == 4096
    assert body["series"] == {
        "cpu": "app.shop.example.com.cpu.percent",
        "memory": "app.shop.example.com.mem.bytes",
    }
    assert body["last_sample_at"] == NOW - 5
    assert body["collector"]["recording"] is True


def test_a_stopped_application_says_so_and_how_to_start_it(
    client: TestClient,
    noust_store: NoustStore,
    runner: FakeRunner,
    cgroups: Path,
) -> None:
    """The unit is inactive: the reason is stopped, with systemd's own words as evidence."""
    deploy(noust_store, "shop.example.com")
    script_unit(
        runner,
        "shop-example-com",
        ActiveState="inactive",
        SubState="dead",
        ControlGroup="",
        InactiveEnterTimestamp="Tue 2026-09-29 03:12:00 UTC",
    )

    body = client.get("/api/apps/shop.example.com/metrics").json()

    assert body["sampled"] is False
    assert body["reason"]["code"] == "stopped"
    assert "ActiveState=inactive" in body["reason"]["evidence"]
    assert body["reason"]["params"]["since"] == "Tue 2026-09-29 03:12:00 UTC"


def test_a_static_site_says_it_has_no_process_and_counts_its_traffic(
    client: TestClient,
    store: MetricsStore,
    noust_store: NoustStore,
    runner: FakeRunner,
    cgroups: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to measure but requests: the reason is static and traffic is available."""
    from noust.monitor import plan as plan_module

    log_dir = tmp_path / "nginx"
    log_dir.mkdir()
    (log_dir / "docs.example.com.access.log").write_text("")
    monkeypatch.setattr(plan_module, "ACCESS_LOG_DIRS", {"nginx": (log_dir,)})
    deploy(noust_store, "docs.example.com", app_type="static", is_static=True, port=None)
    store.acquire_collector("monitor", kind="daemon", interval_s=5)

    body = client.get("/api/apps/docs.example.com/metrics").json()

    assert body["kind"] == "static"
    assert body["sampled"] is False
    assert body["reason"]["code"] == "static"
    assert body["reason"]["fix"] is None
    assert body["traffic"]["available"] is True
    assert body["traffic"]["log"].endswith("docs.example.com.access.log")
    assert set(body["series"]) == {"requests", "errors_5xx"}


def test_nothing_recorded_because_the_monitor_is_off_is_the_reason(
    client: TestClient,
    noust_store: NoustStore,
    runner: FakeRunner,
    cgroups: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A perfectly healthy unit, and no collector: the reason is the monitor, not the app."""
    from noust.core.store import Service

    deploy(noust_store, "shop.example.com")
    noust_store.create_service(Service(name="shop-example-com", status="active"))
    unit_dir = cgroups / "system.slice" / "shop-example-com.service"
    unit_dir.mkdir(parents=True)
    (unit_dir / "memory.current").write_text("4096\n")
    script_unit(
        runner,
        "shop-example-com",
        ActiveState="active",
        ControlGroup="/system.slice/shop-example-com.service",
    )
    monkeypatch.setattr(
        metrics_collector,
        "cached_monitor_probe",
        lambda: {"installed": True, "enabled": False, "active": False},
    )

    body = client.get("/api/apps/shop.example.com/metrics").json()

    assert body["sampled"] is False
    assert body["reason"]["code"] == "monitor_disabled"
    assert body["reason"]["fix"] == "noust monitor enable"


def test_an_unknown_application_is_a_404(client: TestClient, noust_store: NoustStore) -> None:
    """There is nothing to say about an application that does not exist."""
    assert client.get("/api/apps/nowhere.example.com/metrics").status_code == 404


def test_a_domain_that_is_not_one_is_refused(client: TestClient, noust_store: NoustStore) -> None:
    """The path segment is validated before it reaches the store."""
    response = client.get("/api/apps/evil..example/metrics")

    assert response.status_code in (400, 404, 422), response.text
