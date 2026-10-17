# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Searching the logs of every server at once, from a central (checklist G-13).

The central re-implements nothing: it asks each server's own ``/api/logs/search`` with
the same words, labels every line with the server it came from, orders them newest
first, and says in a row of its own what a server could not search.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import gather, set_aggregator
from tests.fleet_views_support import build_central


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def line(app: str, message: str, ts: float, *, level: str = "err") -> dict:
    return {
        "ts": ts,
        "app": app,
        "source": "journal",
        "unit": f"{app.replace('.', '-')}.service",
        "process": "web",
        "level": level,
        "message": message,
    }


def answer(*lines: dict, notes: tuple[str, ...] = (), truncated: bool = False) -> tuple[int, dict]:
    return 200, {
        "lines": list(lines),
        "matched": len(lines),
        "scanned": len(lines),
        "truncated": truncated,
        "since": 0,
        "until": 0,
        "notes": list(notes),
        "apps": [],
    }


class TestTheLogsOfEveryServer:
    def test_lines_from_every_server_newest_first_each_saying_where_it_came_from(
        self, central
    ) -> None:
        central.nodes["web-2"].responses["/api/logs/search"] = answer(
            line("shop.example.com", "timeout paying", 1_000),
            line("shop.example.com", "timeout again", 3_000),
        )
        central.nodes["web-3"].responses["/api/logs/search"] = answer(
            line("blog.example.com", "timeout on feed", 2_000)
        )

        result = gather("logs", {"q": "timeout"}, manager=central.manager)

        assert [(row["node"], row["message"]) for row in result.items] == [
            ("web-2", "timeout again"),
            ("web-3", "timeout on feed"),
            ("web-2", "timeout paying"),
        ]
        first = result.items[0]
        assert first["page"] == "/apps/shop.example.com"
        assert first["href"] == "/n/web-2/apps/shop.example.com"
        assert first["type"] == "line" and first["level"] == "err"

    def test_every_server_is_asked_the_same_question(self, central) -> None:
        for name in ("web-2", "web-3"):
            central.nodes[name].responses["/api/logs/search"] = answer()

        gather(
            "logs",
            {
                "q": 'timeout "connection refused" -probe',
                "since": "2h",
                "level": "err",
                "limit": 20,
            },
            manager=central.manager,
        )

        for name in ("web-2", "web-3"):
            params = central.nodes[name].requests[-1].url.params
            assert params["q"] == 'timeout "connection refused" -probe'
            assert (params["since"], params["level"], params["limit"]) == ("2h", "err", "20")
            assert "until" not in params

    def test_the_limit_is_kept_in_bounds(self, central) -> None:
        central.nodes["web-2"].responses["/api/logs/search"] = answer()

        gather("logs", {"limit": 99999}, nodes=["web-2"], manager=central.manager)

        assert central.nodes["web-2"].requests[-1].url.params["limit"] == "500"

    def test_what_a_server_could_not_search_is_a_row_after_the_lines(self, central) -> None:
        central.nodes["web-2"].responses["/api/logs/search"] = answer(
            line("shop.example.com", "boom", 5),
            notes=("The journal could not be searched: Permission denied",),
            truncated=True,
        )
        central.nodes["web-3"].responses["/api/logs/search"] = answer()

        rows = gather("logs", {"q": "boom"}, manager=central.manager).items

        assert [row["type"] for row in rows] == ["line", "note", "note"]
        assert any("Permission denied" in row["message"] for row in rows)
        assert any("scan limit" in row["message"] for row in rows)

    def test_a_server_that_cannot_search_is_unsupported_and_the_rest_still_answer(
        self, central
    ) -> None:
        central.nodes["web-2"].responses["/api/logs/search"] = answer(line("a.example.com", "x", 1))
        central.nodes["web-3"].responses["/api/logs/search"] = (404, {"detail": "Not Found"})

        result = gather("logs", {"q": "x"}, manager=central.manager)

        assert [o.status for o in result.nodes] == ["ok", "unsupported"]
        assert [row["node"] for row in result.items] == ["web-2"]
        assert result.partial

    def test_a_server_that_refuses_the_operator_is_forbidden_not_an_error(self, central) -> None:
        central.nodes["web-2"].responses["/api/logs/search"] = (403, {"detail": "forbidden"})
        central.nodes["web-3"].responses["/api/logs/search"] = answer()

        result = gather("logs", {"q": "x"}, manager=central.manager)

        assert result.nodes[0].status == "forbidden"
