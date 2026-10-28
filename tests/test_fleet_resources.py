# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The resources of a fleet view beyond the first six, and how a refusal is told.

What is pinned: every resource the console shows per server (databases, sites, cron jobs, alert
rules) is a view of the aggregator, one row per thing with the server it is on and its page there;
a database engine that could not be read is a warning of its server, not a missing row; a node that
refuses the operator is told in its own sentence and the path it refused, never as the JSON it
answered with; and the index a search reads has one row per server, even for one that is down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import RESOURCES, gather, set_aggregator
from tests.support.fleet_views_support import Central, build_central


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


class TestRefusals:
    def test_a_refusal_is_the_nodes_sentence_and_the_path_it_refused(self, central: Central):
        central.nodes["web-3"].responses["/api/audit"] = (
            403,
            {
                "error": "permission_denied",
                "detail": "This needs the 'audit.read' permission, which viewer does not hold",
                "hint": "Ask a security officer for an account with a role that holds it.",
                "fields": None,
            },
        )
        central.nodes["web-2"].responses["/api/audit"] = (200, {"items": []})

        result = gather("activity", manager=central.manager)

        refused = result.outcome("web-3")
        assert refused is not None
        assert refused.status == "forbidden"
        assert refused.code == "permission_denied"
        assert refused.path == "/api/audit"
        assert refused.message == (
            "This needs the 'audit.read' permission, which viewer does not hold"
        )
        assert refused.hint == "Ask a security officer for an account with a role that holds it."

    def test_the_json_a_node_refused_with_is_never_what_a_person_is_shown(self, central: Central):
        central.nodes["web-3"].responses["/api/audit"] = (
            403,
            {"error": "permission_denied", "detail": "Not yours", "hint": "Ask", "fields": None},
        )

        refused = gather("activity", manager=central.manager).outcome("web-3")

        assert refused is not None
        assert refused.error_verbatim is None
        assert '"error"' not in str(refused.to_dict())

    def test_the_output_of_a_failing_tool_is_kept_as_the_node_gave_it(self, central: Central):
        central.nodes["web-3"].responses["/api/audit"] = (
            500,
            {
                "error": "weberror",
                "detail": "nginx rejected the configuration",
                "output": "nginx: [emerg] unexpected end of file",
            },
        )

        failed = gather("activity", manager=central.manager).outcome("web-3")

        assert failed is not None
        assert failed.status == "error"
        assert failed.error_verbatim == "nginx: [emerg] unexpected end of file"

    def test_an_answer_that_is_not_the_contract_keeps_its_words(self, central: Central):
        central.nodes["web-3"].responses["/api/audit"] = (403, {"detail": "forbidden"})

        refused = gather("activity", manager=central.manager).outcome("web-3")

        assert refused is not None
        assert refused.status == "forbidden"
        assert "forbidden" in (refused.error_verbatim or "")

    def test_a_part_the_node_refuses_is_forbidden_and_the_rest_is_still_shown(
        self, central: Central
    ):
        central.nodes["web-2"].responses["/api/server/summary"] = (
            403,
            {"error": "permission_denied", "detail": "Not yours", "hint": None},
        )

        result = gather("summary", nodes=["web-2"], manager=central.manager)

        outcome = result.outcome("web-2")
        assert outcome is not None
        assert outcome.status == "forbidden"
        assert outcome.path == "/api/server/summary"
        (row,) = result.items
        assert row["apps"] is not None, "the figures the node did give are kept"

    def test_a_node_that_does_not_refuse_has_no_path(self, central: Central):
        outcome = gather("servers", nodes=["web-2"], manager=central.manager).outcome("web-2")

        assert outcome is not None
        assert outcome.path is None


DATABASES = {
    "databases": [
        {"name": "shop", "engine": "postgresql", "size": "12 MB", "apps": ["shop.example.com"]},
        {"name": "blog", "engine": "mysql", "size": "3 MB", "apps": []},
    ],
    "total": 2,
    "problems": [
        {
            "engine": "mongodb",
            "display_name": "MongoDB",
            "message": "MongoDB did not answer",
            "hint": "Start it.",
            "output": "connection refused",
            "access": False,
            "kind": "host",
        }
    ],
}


class TestDatabases:
    def test_one_row_per_database_with_its_server_and_its_page(self, central: Central):
        central.nodes["web-2"].responses["/api/databases/databases"] = (200, DATABASES)
        central.nodes["web-3"].responses["/api/databases/databases"] = (
            200,
            {"databases": [{"name": "crm", "engine": "postgresql"}], "total": 1, "problems": []},
        )

        result = gather("databases", manager=central.manager)

        assert [(row["node"], row["name"]) for row in result.items] == [
            ("web-2", "blog"),
            ("web-2", "shop"),
            ("web-3", "crm"),
        ]
        shop = result.items[1]
        assert shop["page"] == "/databases/postgresql/shop"
        assert shop["href"] == "/n/web-2/databases/postgresql/shop"
        assert shop["local"] is False
        assert shop["size"] == "12 MB", "the node's own fields travel verbatim"

    def test_an_engine_that_could_not_be_read_is_a_warning_of_its_server(self, central: Central):
        central.nodes["web-2"].responses["/api/databases/databases"] = (200, DATABASES)
        central.nodes["web-3"].responses["/api/databases/databases"] = (
            200,
            {"databases": [], "total": 0, "problems": []},
        )

        result = gather("databases", manager=central.manager)

        web2 = result.outcome("web-2")
        assert web2 is not None
        assert web2.warnings == ("MongoDB: MongoDB did not answer",)
        assert web2.status == "ok", "its rows are complete; only an engine is missing"
        web3 = result.outcome("web-3")
        assert web3 is not None and web3.warnings == ()

    def test_a_node_too_old_for_the_list_is_unsupported(self, central: Central):
        central.nodes["web-2"].responses["/api/databases/databases"] = (
            404,
            {"detail": "Not Found"},
        )

        outcome = gather("databases", nodes=["web-2"], manager=central.manager).outcome("web-2")

        assert outcome is not None
        assert (outcome.status, outcome.code) == ("unsupported", "not_offered")


class TestSites:
    def test_one_row_per_site_with_its_page(self, central: Central):
        central.nodes["web-2"].responses["/api/sites"] = (
            200,
            {
                "sites": [
                    {"name": "shop.example.com", "webserver": "nginx", "server_names": ["a", "b"]},
                    {"name": "blog.example.com", "webserver": "nginx", "server_names": []},
                ],
                "total": 2,
                "webserver": "nginx",
            },
        )
        central.nodes["web-3"].responses["/api/sites"] = (
            200,
            {"sites": [], "total": 0, "webserver": "nginx"},
        )

        result = gather("sites", manager=central.manager)

        assert [(row["node"], row["name"]) for row in result.items] == [
            ("web-2", "blog.example.com"),
            ("web-2", "shop.example.com"),
        ]
        shop = result.items[1]
        assert shop["page"] == "/domains/sites/shop.example.com"
        assert shop["href"] == "/n/web-2/domains/sites/shop.example.com"
        assert shop["server_names"] == ["a", "b"]


class TestCron:
    def test_one_row_per_job_with_its_server(self, central: Central):
        central.nodes["web-2"].responses["/api/cron"] = (
            200,
            {"jobs": [{"name": "nightly", "schedule": "daily", "enabled": True}], "total": 1},
        )
        central.nodes["web-3"].responses["/api/cron"] = (
            200,
            {"jobs": [{"name": "cleanup", "schedule": "weekly", "enabled": False}], "total": 1},
        )

        result = gather("cron", manager=central.manager)

        assert [(row["node"], row["name"], row["page"]) for row in result.items] == [
            ("web-2", "nightly", "/cron"),
            ("web-3", "cleanup", "/cron"),
        ]
        assert result.items[0]["href"] == "/n/web-2/cron"


class TestAlerts:
    def test_one_row_per_rule_and_the_ones_firing_first(self, central: Central):
        quiet = {"name": "disk", "metric": "disk.percent", "states": []}
        firing = {
            "name": "cpu",
            "metric": "cpu.percent",
            "states": [{"state": "firing", "subject": "server"}],
        }
        central.nodes["web-2"].responses["/api/alerts/rules"] = (
            200,
            {"items": [quiet], "metrics": [], "firing": 0},
        )
        central.nodes["web-3"].responses["/api/alerts/rules"] = (
            200,
            {"items": [firing], "metrics": [], "firing": 1},
        )

        result = gather("alerts", manager=central.manager)

        assert [(row["node"], row["name"]) for row in result.items] == [
            ("web-3", "cpu"),
            ("web-2", "disk"),
        ]
        assert result.items[0]["firing"] is True
        assert result.items[1]["firing"] is False
        assert result.items[0]["page"] == "/alerts"


class TestTheIndexASearchReads:
    def test_one_row_per_thing_of_every_kind_and_one_per_server(self, central: Central):
        node = central.nodes["web-2"]
        node.apps = [{"domain": "shop.example.com", "name": "shop.example.com"}]
        node.responses["/api/databases/databases"] = (
            200,
            {"databases": [{"name": "shop", "engine": "postgresql"}], "total": 1, "problems": []},
        )
        node.responses["/api/sites"] = (
            200,
            {"sites": [{"name": "shop.example.com", "webserver": "nginx"}], "total": 1},
        )
        node.responses["/api/certs"] = (
            200,
            {"certificates": [{"domain": "shop.example.com", "days_remaining": 40}]},
        )
        node.responses["/api/cron"] = (200, {"jobs": [{"name": "nightly"}], "total": 1})

        result = gather("index", nodes=["web-2"], manager=central.manager)

        kinds = sorted((row["kind"], row["name"]) for row in result.items)
        assert kinds == [
            ("app", "shop.example.com"),
            ("certificate", "shop.example.com"),
            ("cron", "nightly"),
            ("database", "shop"),
            ("server", "web-2"),
            ("site", "shop.example.com"),
        ]
        database = next(row for row in result.items if row["kind"] == "database")
        assert database["page"] == "/databases/postgresql/shop"
        assert database["href"] == "/n/web-2/databases/postgresql/shop"
        assert database["node"] == "web-2"

    def test_a_server_that_is_down_can_still_be_found_by_name(self, central: Central):
        central.nodes["web-3"].down = True

        result = gather("index", nodes=["web-3"], manager=central.manager)

        assert [(row["kind"], row["name"], row["node"]) for row in result.items] == [
            ("server", "web-3", "web-3")
        ]
        outcome = result.outcome("web-3")
        assert outcome is not None and outcome.status == "unreachable"

    def test_the_labels_of_a_server_are_words_to_find_it_by(self, central: Central):
        from noust.fleet.labels import NodeLabels

        NodeLabels(central.store).change("web-2", set_labels={"env": "prod"}, replace=True)

        result = gather("index", nodes=["web-2"], manager=central.manager)

        server = next(row for row in result.items if row["kind"] == "server")
        assert server["labels"] == {"env": "prod"}


def test_every_resource_of_the_views_exists() -> None:
    assert {
        "apps",
        "databases",
        "sites",
        "certificates",
        "backups",
        "cron",
        "logs",
        "alerts",
        "activity",
        "index",
    } <= set(RESOURCES)
