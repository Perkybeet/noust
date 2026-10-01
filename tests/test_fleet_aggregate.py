"""
The fleet aggregator: every node asked in parallel, partial answers, a cache.

What is pinned: rows labelled with their node and its page; a node that is
down, too old, or refuses the operator never fails the view, and is said so
with its own words; the last good answer is served, with its age, only for a
failure to reach a node; one request per node however many ask at once; the
deadline; the central's own rows only when its role is ``server``; the
registry's status kept by every read, and the operator told of an outage once.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from noust.core.store import NoustStore
from noust.fleet import aggregate
from noust.fleet.aggregate import Asker, Failure, gather, set_aggregator
from noust.fleet.labels import NodeLabels
from tests.fleet_views_support import Central, build_central


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def _requests(central: Central, name: str, path: str) -> int:
    return sum(1 for request in central.nodes[name].requests if request.url.path == path)


APPS = [
    {"domain": "shop.example.com", "name": "shop.example.com", "status": "running"},
    {"domain": "api.example.com", "name": "api.example.com", "status": "failed"},
]


class FakeLocal:
    """This central as a source of its own rows."""

    local = True
    name = "nas"
    cache_key = "local:nas"

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def get(self, path: str, params: Any = None) -> Any:
        self.asked.append(path)
        if path not in self.answers:
            raise Failure("unsupported", f"nas does not offer GET {path}", code="not_offered")
        return self.answers[path]


class TestRows:
    def test_every_node_is_asked_and_every_row_says_where_it_is(self, central):
        central.nodes["web-2"].apps = APPS
        central.nodes["web-3"].apps = [{"domain": "blog.example.com", "status": "stopped"}]

        result = gather("apps", manager=central.manager)

        assert [outcome.status for outcome in result.nodes] == ["ok", "ok"]
        assert not result.partial
        assert [(row["node"], row["domain"]) for row in result.items] == [
            ("web-2", "api.example.com"),
            ("web-2", "shop.example.com"),
            ("web-3", "blog.example.com"),
        ]
        shop = result.items[1]
        assert shop["page"] == "/apps/shop.example.com"
        assert shop["href"] == "/n/web-2/apps/shop.example.com"
        assert shop["local"] is False
        # The node's own fields travel verbatim.
        assert shop["status"] == "running"

    def test_the_asker_is_what_the_node_is_told(self, central):
        asker = Asker(actor="ana", scope="deploy", role="operator", elevated=False)

        gather("apps", asker=asker, manager=central.manager)

        request = central.nodes["web-2"].requests[-1]
        assert request.headers["X-Noust-Actor"] == "ana"
        assert request.headers["X-Noust-Actor-Scope"] == "deploy"
        assert request.headers["X-Noust-Actor-Role"] == "operator"
        assert "X-Noust-Elevated" not in request.headers

    def test_a_node_filter_asks_only_those(self, central):
        result = gather("apps", nodes=["web-3"], manager=central.manager)

        assert [outcome.name for outcome in result.nodes] == ["web-3"]
        assert _requests(central, "web-2", "/api/apps") == 0

    def test_certificates_soonest_first_across_nodes(self, central):
        central.nodes["web-2"].responses["/api/certs"] = (
            200,
            {"certificates": [{"domain": "a", "days_remaining": 40}]},
        )
        central.nodes["web-3"].responses["/api/certs"] = (
            200,
            {"certificates": [{"domain": "b", "days_remaining": 3}, {"domain": "c"}]},
        )

        rows = gather("certificates", manager=central.manager).items

        assert [row["domain"] for row in rows] == ["b", "a", "c"]
        assert rows[0]["href"] == "/n/web-3/domains"

    def test_backups_are_one_row_per_application_gaps_first(self, central):
        node = central.nodes["web-2"]
        node.apps = APPS
        node.responses["/api/backups"] = (
            200,
            {
                "backups": [
                    {
                        "backup_id": "b1",
                        "domain": "shop.example.com",
                        "timestamp": "2026-09-01",
                        "size": 10,
                        "verified_ok": True,
                    },
                    {
                        "backup_id": "b2",
                        "domain": "shop.example.com",
                        "timestamp": "2026-09-20",
                        "size": 20,
                        "verified_ok": None,
                    },
                ]
            },
        )
        node.responses["/api/backup-schedules"] = (
            200,
            {"schedules": [{"domain": "shop.example.com", "schedule": "daily"}]},
        )

        rows = gather("backups", nodes=["web-2"], manager=central.manager).items

        assert [row["domain"] for row in rows] == ["api.example.com", "shop.example.com"]
        gap, shop = rows
        assert (gap["last_backup"], gap["backups"], gap["scheduled"]) == (None, 0, False)
        assert shop["last_backup"]["backup_id"] == "b2"
        assert (shop["backups"], shop["size"], shop["verified"]) == (2, 30, "never")
        assert shop["schedule"]["schedule"] == "daily"

    def test_activity_newest_first_across_nodes(self, central):
        central.nodes["web-2"].responses["/api/audit"] = (
            200,
            {"items": [{"timestamp": "2026-09-29T10:00:00+00:00", "action": "apps.deploy"}]},
        )
        central.nodes["web-3"].responses["/api/audit"] = (
            200,
            {"items": [{"timestamp": "2026-09-29T11:00:00+00:00", "action": "apps.delete"}]},
        )

        rows = gather("activity", {"limit": 5}, manager=central.manager).items

        assert [(row["node"], row["action"]) for row in rows] == [
            ("web-3", "apps.delete"),
            ("web-2", "apps.deploy"),
        ]
        assert central.nodes["web-2"].requests[-1].url.params["limit"] == "5"

    def test_labels_travel_with_the_servers_rows(self, central):
        NodeLabels(central.store).change("web-2", set_labels={"env": "prod"})

        rows = gather("servers", manager=central.manager).items

        assert {row["node"]: row["labels"] for row in rows} == {
            "web-2": {"env": "prod"},
            "web-3": {},
        }
        assert rows[0]["href"] == "/n/web-2"
        assert rows[0]["access"] == {"level": "admin", "host_access": False}


class TestPartial:
    def test_a_node_that_is_down_is_unreachable_with_its_words(self, central):
        central.nodes["web-2"].apps = APPS
        central.nodes["web-3"].down = True

        result = gather("apps", manager=central.manager)

        up, down = result.nodes
        assert up.status == "ok"
        assert down.status == "unreachable"
        assert down.code == "node_unreachable"
        assert "Connection refused" in (down.error_verbatim or "")
        assert result.partial
        assert {row["node"] for row in result.items} == {"web-2"}
        assert central.store.get_node("web-3").status == "unreachable"

    def test_a_down_node_still_has_its_row_in_the_summary(self, central):
        central.nodes["web-3"].down = True

        rows = gather("summary", manager=central.manager).items

        assert [row["node"] for row in rows] == ["web-2", "web-3"]
        assert rows[1]["reachability"] == "unreachable"
        assert rows[1]["version"] == "3.1.0"

    def test_the_last_good_answer_is_served_with_its_age(self, central):
        central.nodes["web-3"].apps = APPS
        gather("apps", manager=central.manager)
        central.fleet.clock.advance(120)
        central.nodes["web-3"].down = True

        result = gather("apps", manager=central.manager)

        stale = result.outcome("web-3")
        assert stale is not None and stale.status == "stale"
        assert stale.age_seconds == 120
        assert "Connection refused" in (stale.error_verbatim or "")
        assert {row["domain"] for row in result.items if row["node"] == "web-3"} == {
            "shop.example.com",
            "api.example.com",
        }

    def test_nothing_is_served_stale_beyond_its_limit(self, central):
        gather("apps", manager=central.manager)
        central.fleet.clock.advance(3601)
        central.nodes["web-3"].down = True

        assert gather("apps", manager=central.manager).outcome("web-3").status == "unreachable"

    def test_a_refusal_is_never_served_from_the_cache(self, central):
        central.nodes["web-3"].apps = APPS
        gather("apps", manager=central.manager)
        central.fleet.clock.advance(60)
        central.nodes["web-3"].responses["/api/apps"] = (
            403,
            {"error": "permission_denied", "detail": "Your role cannot read applications here"},
        )

        result = gather("apps", manager=central.manager)

        refused = result.outcome("web-3")
        assert refused.status == "forbidden"
        assert refused.message == "Your role cannot read applications here"
        assert "permission_denied" in (refused.error_verbatim or "")
        assert not [row for row in result.items if row["node"] == "web-3"]

    def test_a_node_too_old_for_a_view_is_unsupported_not_an_error(self, central):
        # A 3.0 node: no audit endpoint of this shape here, a 404 for the path.
        central.nodes["web-3"].responses["/api/audit"] = (404, {"detail": "Not Found"})
        central.nodes["web-2"].responses["/api/audit"] = (200, {"items": []})

        result = gather("activity", manager=central.manager)

        old = result.outcome("web-3")
        assert old.status == "unsupported"
        assert old.code == "not_offered"
        assert old.missing == ("/api/audit",)

    def test_a_30_node_summary_says_what_it_lacks_and_shows_what_it_has(self, central):
        result = gather("summary", nodes=["web-2"], manager=central.manager)

        outcome = result.outcome("web-2")
        assert outcome.status == "unsupported"
        assert set(outcome.missing) == {"/api/overview", "/api/server/summary"}
        (row,) = result.items
        assert row["apps"] == {"running": 3, "failed": 1, "stopped": 0, "static": 2}
        assert row["units"] == {"running": 5, "failed": 1, "stopped": 0}
        # Counted from the certificate list, the older node's way.
        assert row["certificates_expiring"] == 1
        assert row["overview"] is None

    def test_a_31_node_summary_uses_its_overview(self, central):
        node = central.nodes["web-2"]
        node.responses["/api/overview"] = (
            200,
            {"certificates": {"expiring": 2, "expired": 1}, "apps": {"running": 1}},
        )
        node.responses["/api/server/summary"] = (200, {"updates": {"pending": 4}})

        result = gather("summary", nodes=["web-2"], manager=central.manager)

        assert result.outcome("web-2").status == "ok"
        (row,) = result.items
        assert row["certificates_expiring"] == 3
        assert row["server"] == {"updates": {"pending": 4}}
        assert _requests(central, "web-2", "/api/certs") == 0

    def test_a_part_that_fails_is_a_warning_beside_the_rest(self, central):
        central.nodes["web-2"].responses["/api/system/machine"] = (503, {"detail": "psutil"})

        result = gather("summary", nodes=["web-2"], manager=central.manager)

        outcome = result.outcome("web-2")
        assert outcome.status == "error"
        assert outcome.warnings and outcome.warnings[0].startswith("Machine snapshot:")
        assert result.items[0]["version"] == "3.1.0"

    def test_a_refused_token_is_an_error_and_the_node_is_not_asked_again(self, central):
        central.nodes["web-3"].revoked = True

        first = gather("apps", manager=central.manager).outcome("web-3")
        asked = len(central.nodes["web-3"].requests)
        second = gather("apps", manager=central.manager, refresh=True).outcome("web-3")

        assert (first.status, first.code) == ("error", "node_refused")
        assert second.code == "node_refused"
        assert len(central.nodes["web-3"].requests) == asked
        assert central.store.get_node("web-3").status == "refused"


class TestCacheAndDeadline:
    def test_a_fresh_answer_is_reused_and_refresh_asks_again(self, central):
        gather("apps", manager=central.manager)
        gather("apps", manager=central.manager)
        assert _requests(central, "web-2", "/api/apps") == 1

        gather("apps", manager=central.manager, refresh=True)
        assert _requests(central, "web-2", "/api/apps") == 2

        central.fleet.clock.advance(16)
        gather("apps", manager=central.manager)
        assert _requests(central, "web-2", "/api/apps") == 3

    def test_two_audiences_never_share_an_answer(self, central):
        gather("apps", asker=Asker(actor="a", role="viewer"), manager=central.manager)
        gather("apps", asker=Asker(actor="b", role="admin", scope="admin"), manager=central.manager)

        assert _requests(central, "web-2", "/api/apps") == 2

    def test_concurrent_views_ask_each_node_once(self, central):
        gate = threading.Event()
        central.nodes["web-2"].gate = gate
        results: list[Any] = []
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    gather("apps", manager=central.manager, deadline=None)
                )
            )
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        gate.set()
        for thread in threads:
            thread.join(10)

        assert len(results) == 4
        assert _requests(central, "web-2", "/api/apps") == 1

    def test_a_slow_node_is_reported_and_its_answer_warms_the_cache(self, central):
        gate = threading.Event()
        central.nodes["web-2"].gate = gate
        central.nodes["web-2"].apps = APPS

        # web-2 is held until the gate opens, so it times out whatever the
        # deadline; a second leaves web-3 time to answer under a loaded run.
        slow = gather("apps", manager=central.manager, deadline=1.0)

        outcome = slow.outcome("web-2")
        assert (outcome.status, outcome.code) == ("unreachable", "timeout")
        assert slow.outcome("web-3").status == "ok"
        gate.set()
        for _ in range(100):
            if not aggregate.get_aggregator()._inflight:
                break
            threading.Event().wait(0.05)
        warm = gather("apps", manager=central.manager)
        assert warm.outcome("web-2").status == "ok"
        assert _requests(central, "web-2", "/api/apps") == 1


class TestTheCentralItself:
    def test_its_rows_come_first_when_it_is_a_server(self, central, monkeypatch):
        monkeypatch.setattr(aggregate, "includes_central", lambda: True)
        local = FakeLocal({"/api/apps": {"apps": [{"domain": "local.example.com"}]}})

        result = gather("apps", manager=central.manager, local=local)

        assert result.nodes[0].name == "nas" and result.nodes[0].local
        row = result.items[0]
        assert (row["node"], row["local"], row["href"]) == ("nas", True, "/apps/local.example.com")

    def test_a_hub_has_no_rows_of_its_own(self, central, monkeypatch):
        monkeypatch.setattr(aggregate, "includes_central", lambda: False)
        local = FakeLocal({"/api/apps": {"apps": []}})

        result = gather("apps", manager=central.manager, local=local)

        assert [outcome.name for outcome in result.nodes] == ["web-2", "web-3"]
        assert local.asked == []

    def test_the_filter_names_it(self, central, monkeypatch):
        monkeypatch.setattr(aggregate, "includes_central", lambda: True)
        local = FakeLocal({"/api/apps": {"apps": []}})

        result = gather("apps", manager=central.manager, local=local, nodes=["@central"])

        assert [outcome.name for outcome in result.nodes] == ["nas"]


class TestOutages:
    def test_the_operator_is_told_once_and_again_when_it_is_back(self, central, monkeypatch):
        told: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "noust.core.notifications.fleet.notify_node_unreachable",
            lambda node, **kw: told.append(("down", node)),
        )
        monkeypatch.setattr(
            "noust.core.notifications.fleet.notify_node_recovered",
            lambda node, **kw: told.append(("up", node)),
        )
        central.nodes["web-3"].down = True

        gather("servers", manager=central.manager)
        assert told == []  # a blip is not an outage
        central.fleet.clock.advance(aggregate.UNREACHABLE_GRACE_SECONDS + 1)
        gather("servers", manager=central.manager)
        central.fleet.clock.advance(30)
        gather("servers", manager=central.manager)
        assert told == [("down", "web-3")]

        central.nodes["web-3"].down = False
        central.fleet.clock.advance(30)
        gather("servers", manager=central.manager)
        assert told == [("down", "web-3"), ("up", "web-3")]
        assert central.store.get_node("web-3").status == "reachable"

    def test_a_short_blip_tells_nobody(self, central, monkeypatch):
        told: list[str] = []
        monkeypatch.setattr(
            "noust.core.notifications.fleet.notify_node_recovered",
            lambda node, **kw: told.append(node),
        )
        central.nodes["web-3"].down = True
        gather("servers", manager=central.manager)
        central.nodes["web-3"].down = False
        central.fleet.clock.advance(11)
        gather("servers", manager=central.manager)

        assert told == []


def test_an_unknown_view_is_a_key_error(central):
    with pytest.raises(KeyError):
        gather("nope", manager=central.manager)


def test_the_envelope(central):
    body = gather("apps", manager=central.manager).to_dict()

    assert set(body) == {"resource", "generated_at", "partial", "nodes", "items"}
    assert set(body["nodes"][0]) == {
        "name",
        "local",
        "status",
        "code",
        "message",
        "hint",
        "error_verbatim",
        "age_seconds",
        "fetched_at",
        "elapsed_ms",
        "version",
        "missing",
        "warnings",
    }


def test_decode_answer_reads_the_contract():
    request = httpx.Request("GET", "http://node/api/x")
    with pytest.raises(Failure) as caught:
        from noust.fleet.aggregate import decode_answer

        decode_answer(
            "web-2",
            "GET",
            "/api/x",
            httpx.Response(
                500, json={"error": "boom", "detail": "It broke", "hint": "Fix it"}, request=request
            ),
        )
    assert (caught.value.kind, caught.value.code, caught.value.hint) == ("error", "boom", "Fix it")
    assert "It broke" in caught.value.message
