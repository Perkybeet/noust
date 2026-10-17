# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Comparing one metric of every server from a central (checklist G-05).

The central keeps no series and has no database for it: each server answers from its
own recorded history, the central puts the answers side by side, and a server that is
down is a gap in the comparison (said so, with its own words), not an error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import FLEET_METRICS, gather, set_aggregator
from tests.fleet_views_support import build_central


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def query(
    metric: str, points: list[list], *, ceiling: float | None = None, window: str = "24h"
) -> tuple[int, dict]:
    return 200, {
        "from": 0,
        "to": 3_600,
        "step": 60,
        "resolution": "1m",
        "window": window,
        "first_sample_at": 0,
        "last_sample_at": 3_600,
        "series": [{"metric": metric, "points": points, "ceiling": ceiling}],
        "collector": {"recording": True},
    }


class TestComparing:
    def test_each_server_is_a_row_with_its_points_and_its_summary(self, central) -> None:
        central.nodes["web-2"].responses["/api/metrics/query"] = query(
            "cpu.percent", [[0, 10.0, 20.0], [60, 30.0, 90.0], [120, None, None]]
        )
        central.nodes["web-3"].responses["/api/metrics/query"] = query(
            "cpu.percent", [[0, 50.0, 55.0]]
        )

        result = gather("metrics", {"metric": "cpu", "window": "24h"}, manager=central.manager)

        web2, web3 = result.items
        assert (web2["node"], web2["metric"], web2["unit"]) == ("web-2", "cpu", "percent")
        assert web2["points"] == [[0, 10.0, 20.0], [60, 30.0, 90.0], [120, None, None]]
        assert (web2["latest"], web2["average"], web2["peak"]) == (30.0, 20.0, 90.0)
        assert web2["href"] == "/n/web-2/server"
        assert (web3["latest"], web3["window"]) == (50.0, "24h")

    def test_every_server_is_asked_for_the_series_the_metric_is_recorded_as(self, central) -> None:
        for name in ("web-2", "web-3"):
            central.nodes[name].responses["/api/metrics/query"] = query("mem.used_bytes", [])

        gather("metrics", {"metric": "memory", "window": "7d"}, manager=central.manager)

        for name in ("web-2", "web-3"):
            params = central.nodes[name].requests[-1].url.params
            assert params["metric"] == "mem.used_bytes" and params["window"] == "7d"

    def test_memory_is_the_share_of_what_each_server_has(self, central) -> None:
        central.nodes["web-2"].responses["/api/metrics/query"] = query(
            "mem.used_bytes", [[0, 2e9, 4e9]], ceiling=8e9
        )
        central.nodes["web-3"].responses["/api/metrics/query"] = query(
            "mem.used_bytes", [[0, 2e9, 4e9]], ceiling=2e10
        )

        web2, web3 = gather("metrics", {"metric": "memory"}, manager=central.manager).items

        assert (web2["unit"], web2["latest"], web2["peak"]) == ("percent", 25.0, 50.0)
        assert (web3["latest"], web3["peak"]) == (10.0, 20.0)

    def test_a_rate_is_kept_as_it_is(self, central) -> None:
        central.nodes["web-2"].responses["/api/metrics/query"] = query(
            "net.rx_bytes_s", [[0, 1500.0, 2000.0]]
        )

        (row,) = gather(
            "metrics", {"metric": "net_rx"}, nodes=["web-2"], manager=central.manager
        ).items

        assert (row["unit"], row["latest"]) == ("bytes_s", 1500.0)

    def test_a_server_that_recorded_nothing_has_no_row_and_the_others_still_do(
        self, central
    ) -> None:
        central.nodes["web-2"].responses["/api/metrics/query"] = query("cpu.percent", [])
        central.nodes["web-2"].responses["/api/metrics/query"][1]["series"] = []
        central.nodes["web-3"].responses["/api/metrics/query"] = query(
            "cpu.percent", [[0, 5.0, 6.0]]
        )

        result = gather("metrics", {"metric": "cpu"}, manager=central.manager)

        assert [row["node"] for row in result.items] == ["web-3"]

    def test_a_server_that_is_down_is_a_gap_with_its_words(self, central) -> None:
        central.nodes["web-2"].responses["/api/metrics/query"] = query(
            "cpu.percent", [[0, 5.0, 6.0]]
        )
        central.nodes["web-3"].down = True

        result = gather("metrics", {"metric": "cpu"}, manager=central.manager)

        assert [row["node"] for row in result.items] == ["web-2"]
        down = result.nodes[1]
        assert down.status == "unreachable" and "Connection refused" in (down.error_verbatim or "")
        assert result.partial

    def test_a_server_too_old_to_offer_the_history_is_unsupported(self, central) -> None:
        central.nodes["web-2"].responses["/api/metrics/query"] = (404, {"detail": "Not Found"})
        central.nodes["web-3"].responses["/api/metrics/query"] = query(
            "cpu.percent", [[0, 1.0, 1.0]]
        )

        result = gather("metrics", {"metric": "cpu"}, manager=central.manager)

        assert result.nodes[0].status == "unsupported"
        assert [row["node"] for row in result.items] == ["web-3"]

    @pytest.mark.parametrize(
        "params", [{"metric": "gpu"}, {"metric": "cpu", "window": "1y"}, {"window": "tomorrow"}]
    )
    def test_a_metric_or_a_window_that_is_not_offered_is_refused(
        self, central, params: dict
    ) -> None:
        with pytest.raises(ValueError):
            gather("metrics", params, manager=central.manager)

    def test_every_metric_of_the_view_names_a_series_the_servers_record(self) -> None:
        from noust.core.alerts.model import METRICS

        recorded = {spec.series for spec in METRICS.values() if spec.scope == "server"}
        assert {series for series, _unit in FLEET_METRICS.values()} - {
            "swap.used_bytes"
        } <= recorded
