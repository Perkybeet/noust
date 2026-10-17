# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The metrics a scraper reads (checklist G-06): Prometheus text, OpenMetrics, the endpoint and the command.

What is defended: that every described series comes out with the name, the unit and
the labels a dashboard expects, that a series nobody described is never exported, that
the text is valid (one HELP and one TYPE per family, escaped labels, a terminator in
OpenMetrics), that stale values are left out, and that the endpoint needs a session.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.monitor import exposition
from noust.monitor.timeseries import MetricsStore
from noust.web import metrics_collector
from noust.web.api.auth import get_current_session
from noust.web.api.metrics import router as metrics_router

NOW = 1_700_002_800

#: A sample line of the text format: name, optional labels, a number.
SAMPLE_LINE = re.compile(r'[a-z_]+(\{([a-z_]+="(?:[^"\\]|\\.)*",?)*\})? -?[0-9.e+-]+')


def labels_of(sample: exposition.Sample) -> dict[str, str]:
    return dict(sample.labels)


class TestWhatIsExported:
    def test_the_machine_has_its_own_names_and_units(self) -> None:
        found = exposition.samples(
            {
                "cpu.percent": 12.5,
                "mem.used_bytes": 4096.0,
                "mem.total_bytes": 8192.0,
                "swap.used_bytes": 0.0,
                "disk.used_bytes": 10.0,
                "disk.total_bytes": 100.0,
                "net.rx_bytes_s": 1.5,
                "net.tx_bytes_s": 2.5,
                "load.1m": 0.4,
            }
        )

        assert {s.family.name for s in found} == {
            "noust_cpu_percent",
            "noust_memory_used_bytes",
            "noust_memory_total_bytes",
            "noust_swap_used_bytes",
            "noust_disk_used_bytes",
            "noust_disk_total_bytes",
            "noust_network_receive_bytes_per_second",
            "noust_network_transmit_bytes_per_second",
            "noust_load1",
        }
        assert all(not s.labels for s in found)

    def test_an_application_is_a_label_not_part_of_the_name(self) -> None:
        (sample,) = exposition.samples({"app.shop.example.com.mem.bytes": 2048.0})

        assert sample.family.name == "noust_app_memory_bytes"
        assert labels_of(sample) == {"app": "shop.example.com"}

    def test_every_application_series_is_described(self) -> None:
        values = {f"app.shop.example.com.{suffix}": 1.0 for suffix in exposition.APPLICATION}

        found = exposition.samples(values)

        assert len(found) == len(exposition.APPLICATION)
        assert {s.family.name for s in found} == {f.name for f in exposition.APPLICATION.values()}

    def test_a_container_has_an_app_and_a_container_label(self) -> None:
        (sample,) = exposition.samples(
            {"app.stack.example.com.ctr.stack-web-1.io.read_bytes_s": 7.0}
        )

        assert sample.family.name == "noust_container_disk_read_bytes_per_second"
        assert labels_of(sample) == {"app": "stack.example.com", "container": "stack-web-1"}

    def test_a_containers_series_is_not_mistaken_for_an_applications(self) -> None:
        found = exposition.samples({"app.stack.example.com.ctr.web.cpu.percent": 1.0})

        assert [s.family.name for s in found] == ["noust_container_cpu_percent"]

    def test_a_database_has_an_engine_and_maybe_a_database(self) -> None:
        engine, database = exposition.samples(
            {"db.postgresql.connections": 12.0, "db.postgresql.shop_db.size": 4096.0}
        )

        assert engine.family.name == "noust_database_connections"
        assert labels_of(engine) == {"engine": "postgresql"}
        assert database.family.name == "noust_database_size_bytes"
        assert labels_of(database) == {"engine": "postgresql", "database": "shop_db"}

    def test_an_engine_instance_keeps_its_at_sign(self) -> None:
        (sample,) = exposition.samples({"db.postgresql@pg16.connections": 3.0})

        assert labels_of(sample) == {"engine": "postgresql@pg16"}

    def test_a_series_nobody_described_is_not_exported(self) -> None:
        assert (
            exposition.samples(
                {
                    "something.else": 1.0,
                    "app.shop.example.com.invented.thing": 1.0,
                    "db.postgresql.shop_db.unheard_of": 1.0,
                }
            )
            == []
        )

    def test_a_value_that_is_not_a_number_is_not_exported(self) -> None:
        assert exposition.samples({"cpu.percent": float("nan"), "load.1m": float("inf")}) == []


class TestTheText:
    def values(self) -> list[exposition.Sample]:
        return exposition.samples(
            {
                "cpu.percent": 12.5,
                "app.a.example.com.cpu.percent": 1.0,
                "app.b.example.com.cpu.percent": 2.0,
            }
        )

    def test_a_family_has_one_help_and_one_type_however_many_samples(self) -> None:
        text = exposition.render(self.values())

        assert text.count("# HELP noust_app_cpu_percent ") == 1
        assert text.count("# TYPE noust_app_cpu_percent gauge") == 1
        assert 'noust_app_cpu_percent{app="a.example.com"} 1' in text
        assert 'noust_app_cpu_percent{app="b.example.com"} 2' in text
        assert "noust_cpu_percent 12.5" in text

    def test_every_line_is_valid(self) -> None:
        text = exposition.render(
            self.values(), info={"version": "3.4.0", "server": 'a "b"\nc'}, recording=True
        )

        for line in text.strip().split("\n"):
            assert (
                line.startswith("# HELP ")
                or line.startswith("# TYPE ")
                or SAMPLE_LINE.fullmatch(line)
            ), line

    def test_a_label_value_is_escaped(self) -> None:
        text = exposition.render([], info={"version": "1", "server": 'we"ird\\name\nx'})

        assert 'server="we\\"ird\\\\name\\nx"' in text

    def test_the_classic_text_has_no_terminator_and_openmetrics_does(self) -> None:
        assert "# EOF" not in exposition.render(self.values())
        assert exposition.render(self.values(), openmetrics=True).endswith("# EOF\n")

    def test_the_output_is_deterministic(self) -> None:
        assert exposition.render(self.values()) == exposition.render(list(reversed(self.values())))

    def test_whole_numbers_have_no_decimal_point(self) -> None:
        text = exposition.render(exposition.samples({"mem.used_bytes": 4096.0, "load.1m": 0.25}))

        assert "noust_memory_used_bytes 4096\n" in text
        assert "noust_load1 0.25\n" in text

    def test_whether_it_is_recording_is_a_metric(self) -> None:
        assert "noust_metrics_recording 1\n" in exposition.render([], recording=True)
        assert "noust_metrics_recording 0\n" in exposition.render([], recording=False)
        assert "noust_metrics_recording" not in exposition.render([])

    def test_the_accept_header_chooses_the_format(self) -> None:
        assert exposition.wants_openmetrics(
            "application/openmetrics-text; version=1.0.0,text/plain"
        )
        assert not exposition.wants_openmetrics("text/plain")
        assert not exposition.wants_openmetrics(None)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MetricsStore:
    instance = MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW)
    monkeypatch.setattr(metrics_collector, "get_metrics_store", lambda: instance)
    monkeypatch.setattr(metrics_collector, "cached_monitor_probe", lambda: None)
    return instance


@pytest.fixture
def client(
    store: MetricsStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    app = FastAPI()
    app.include_router(metrics_router, prefix="/api/metrics")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}
    try:
        yield TestClient(app)
    finally:
        Config.reset_instance()


class TestTheEndpoint:
    def test_it_answers_in_the_prometheus_text_format(
        self, client: TestClient, store: MetricsStore
    ) -> None:
        store.record("cpu.percent", 12.5)
        store.record("app.shop.example.com.mem.bytes", 2048.0)

        response = client.get("/api/metrics/prometheus")

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
        assert "noust_cpu_percent 12.5" in response.text
        assert 'noust_app_memory_bytes{app="shop.example.com"} 2048' in response.text
        assert "noust_info{version=" in response.text

    def test_it_answers_openmetrics_when_asked_to(
        self, client: TestClient, store: MetricsStore
    ) -> None:
        store.record("cpu.percent", 12.5)

        response = client.get(
            "/api/metrics/prometheus",
            headers={"Accept": "application/openmetrics-text; version=1.0.0"},
        )

        assert response.headers["content-type"].startswith("application/openmetrics-text")
        assert response.text.endswith("# EOF\n")

    def test_a_value_that_stopped_being_recorded_is_left_out(
        self, client: TestClient, store: MetricsStore
    ) -> None:
        store.record("cpu.percent", 12.5, ts=NOW - 10)
        store.record("app.gone.example.com.mem.bytes", 1.0, ts=NOW - 600)

        text = client.get("/api/metrics/prometheus").text

        assert "noust_cpu_percent" in text
        assert "gone.example.com" not in text

    def test_it_says_when_nothing_is_being_recorded(self, client: TestClient) -> None:
        text = client.get("/api/metrics/prometheus").text

        assert "noust_metrics_recording 0" in text

    def test_the_route_is_not_taken_for_a_metric_called_prometheus(
        self, client: TestClient, store: MetricsStore
    ) -> None:
        # /api/metrics/{metric} reads any name; this route is declared first.
        assert (
            client.get("/api/metrics/prometheus").headers["content-type"].startswith("text/plain")
        )

    def test_it_demands_a_session(self, tmp_path: Path) -> None:
        from noust.web.auth import SecurityConfig
        from noust.web.server import create_app

        anonymous = TestClient(
            create_app(SecurityConfig(state_dir=tmp_path / "state")), client=("testclient", 50000)
        )

        assert anonymous.get("/api/metrics/prometheus").status_code == 401


class TestTheCommand:
    @pytest.fixture
    def history(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MetricsStore:
        path = tmp_path / "metrics.db"
        store = MetricsStore(path, clock=lambda: NOW)
        monkeypatch.setattr("noust.cli.commands.monitor.default_metrics_db_path", lambda: path)
        monkeypatch.setattr(
            "noust.cli.commands.monitor.MetricsStore", lambda p: MetricsStore(p, clock=lambda: NOW)
        )
        monkeypatch.setattr("noust.cli.commands.monitor._history_status", lambda: None)
        return store

    def test_it_prints_the_exposition(
        self, history: MetricsStore, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml"
        )
        Config.reset_instance()
        history.record("cpu.percent", 7.0)

        result = CliRunner().invoke(root_cli, ["monitor", "export"])

        assert result.exit_code == 0, result.output
        assert "noust_cpu_percent 7" in result.output
        assert "# EOF" not in result.output

    def test_openmetrics_ends_with_its_terminator(
        self, history: MetricsStore, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml"
        )
        Config.reset_instance()
        history.record("cpu.percent", 7.0)

        result = CliRunner().invoke(root_cli, ["monitor", "export", "--format", "openmetrics"])

        assert result.output.endswith("# EOF\n")

    def test_without_a_history_it_says_how_to_start_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.cli.commands.monitor.default_metrics_db_path", lambda: tmp_path / "none.db"
        )

        result = CliRunner().invoke(root_cli, ["monitor", "export"])

        assert result.exit_code == 1
        assert "noust monitor enable" in result.output
