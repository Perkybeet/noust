# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Updating Noust across the fleet to a version, and refreshing the package lists (F-16, item 81).

During a release the fleet runs mixed versions: a distribution's repository may serve the new
package while another's is still being built. What is pinned here is what the operator reads
before confirming (the version each server would run), who is skipped and why, the target
being met or the server being said to fall short of it, and the refresh of the lists that
makes a server's own update check look again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.web.fleet_jobs import FleetRequest, plan
from tests.fleet_views_support import SCHEMA_30, Central, build_central
from tests.test_fleet_actions import ADMIN, _run, _states


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-1", "web-2", "web-3"))
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def _version(central: Central, name: str, **fields: Any) -> None:
    """Make a node answer GET /api/system/version as it would mid-rollout."""
    body = {"current_version": "3.3.0", "update_state": "update_available", **fields}
    central.nodes[name].responses["/api/system/version"] = (200, body)


def _updating(central: Central, name: str, *, installed: str, indexed: str) -> None:
    """Make a node update from 3.3.0 to ``installed``, as its console does."""
    node = central.nodes[name]
    state = {"updated": False}
    node.handlers[("GET", "/api/system/version")] = lambda request: httpx.Response(
        200,
        json={
            "current_version": installed if state["updated"] else "3.3.0",
            "update_state": "update_available",
            "indexed_version": indexed,
        },
    )
    node.handlers[("POST", "/api/system/update")] = lambda request: node.queue(status="running")

    def status(request: httpx.Request) -> httpx.Response:
        state["updated"] = True
        return httpx.Response(
            200, json={"last_run": {"job_id": "j001", "status": "succeeded", "tail": []}}
        )

    node.handlers[("GET", "/api/system/update")] = status


class TestThePlanNamesTheVersion:
    def test_each_server_shows_the_version_it_would_run(self, central):
        _version(central, "web-1", indexed_version="3.3.1", announced_version="3.3.1")
        _version(central, "web-2", update_state="index_behind", announced_version="3.3.1")

        described = plan(
            FleetRequest("noust_update", nodes=["web-1", "web-2"]),
            manager=central.manager,
            asker=ADMIN,
        ).to_dict()

        steps = {node["node"]: node["step"] for node in described["nodes"]}
        assert steps == {"web-1": "Noust 3.3.0 -> 3.3.1", "web-2": "Noust 3.3.0 -> 3.3.1"}
        assert described["batches"] == [["web-1"], ["web-2"]]

    def test_a_package_still_being_published_says_which_version_is_on_the_way(self, central):
        _version(central, "web-1", update_state="on_the_way", published_version="3.3.1")

        described = plan(
            FleetRequest("noust_update", nodes=["web-1"]), manager=central.manager, asker=ADMIN
        ).to_dict()

        (node,) = described["nodes"]
        assert (node["state"], node["reason"]) == ("skipped", "not_needed")
        assert "3.3.1 is published" in node["step"]
        assert "has not built it yet" in node["step"]

    def test_a_server_on_the_newest_its_source_offers_is_skipped_as_before(self, central):
        _version(central, "web-1", update_state="up_to_date")

        described = plan(
            FleetRequest("noust_update", nodes=["web-1"]), manager=central.manager, asker=ADMIN
        ).to_dict()

        assert described["nodes"][0]["step"] == (
            "web-1 runs Noust 3.3.0, the newest its package source offers"
        )


class TestWithATarget:
    def test_each_server_is_judged_against_it(self, central):
        _version(central, "web-1", current_version="3.3.1", update_state="up_to_date")
        _version(central, "web-2", indexed_version="3.3.1", announced_version="3.3.1")
        _version(
            central,
            "web-3",
            indexed_version="3.3.0",
            latest_version="3.3.0",
            announced_version="3.3.0",
        )

        described = plan(
            FleetRequest(
                "noust_update",
                nodes=["web-1", "web-2", "web-3"],
                options={"target_version": "3.3.1"},
            ),
            manager=central.manager,
            asker=ADMIN,
        ).to_dict()

        by_node = {node["node"]: node for node in described["nodes"]}
        assert by_node["web-1"]["state"] == "skipped"
        assert "3.3.1 or newer" in by_node["web-1"]["step"]
        assert by_node["web-2"]["state"] == "queued"
        assert by_node["web-2"]["step"] == "Noust 3.3.0 -> 3.3.1"
        assert by_node["web-3"]["state"] == "skipped"
        assert "offers Noust 3.3.0, not 3.3.1" in by_node["web-3"]["step"]
        assert described["options"] == {"target_version": "3.3.1"}

    def test_a_server_whose_lists_are_behind_goes_ahead_because_the_update_refreshes_them(
        self,
        central,
    ):
        _version(central, "web-1", update_state="index_behind", published_version="3.3.1")

        described = plan(
            FleetRequest("noust_update", nodes=["web-1"], options={"target_version": "3.3.1"}),
            manager=central.manager,
            asker=ADMIN,
        ).to_dict()

        (node,) = described["nodes"]
        assert node["state"] == "queued"
        assert node["step"] == "Noust 3.3.0 -> 3.3.1"

    def test_a_server_whose_package_is_not_built_yet_is_skipped_with_the_reason(self, central):
        _version(central, "web-1", update_state="on_the_way", published_version="3.3.1")

        described = plan(
            FleetRequest("noust_update", nodes=["web-1"], options={"target_version": "3.3.1"}),
            manager=central.manager,
            asker=ADMIN,
        ).to_dict()

        assert "has not built it yet" in described["nodes"][0]["step"]

    @pytest.mark.parametrize("bad", ["latest", "3", "3.4.x", 3.4, "3.3.1; x", ["3.3.1"]])
    def test_something_that_is_not_a_version_is_refused(self, central, bad):
        with pytest.raises(ValidationError, match="target_version"):
            plan(
                FleetRequest("noust_update", nodes=["web-1"], options={"target_version": bad}),
                manager=central.manager,
                asker=ADMIN,
            )

    def test_an_update_that_stops_short_of_it_fails_and_says_how_to_go_on(self, central):
        _updating(central, "web-1", installed="3.3.1", indexed="3.4.0")

        snapshot, _, _ = _run(
            central,
            FleetRequest("noust_update", nodes=["web-1"], options={"target_version": "3.4.0"}),
        )

        (result,) = snapshot["nodes"]
        assert result["state"] == "failed", result
        assert "now runs Noust 3.3.1, not 3.4.0" in result["error"]["message"]
        assert "index_refresh" in result["error"]["hint"]

    def test_an_update_that_reaches_it_records_the_target(self, central):
        _updating(central, "web-1", installed="3.3.1", indexed="3.3.1")

        snapshot, _, _ = _run(
            central,
            FleetRequest("noust_update", nodes=["web-1"], options={"target_version": "3.3.1"}),
        )

        (result,) = snapshot["nodes"]
        assert result["state"] == "succeeded", result
        assert result["items"] == [
            {
                "from_version": "3.3.0",
                "to_version": "3.3.1",
                "target_version": "3.3.1",
                "state": "succeeded",
            }
        ]

    def test_a_server_that_reached_a_newer_version_than_the_target_is_fine(self, central):
        _updating(central, "web-1", installed="3.3.2", indexed="3.3.2")

        snapshot, _, _ = _run(
            central,
            FleetRequest("noust_update", nodes=["web-1"], options={"target_version": "3.3.1"}),
        )

        assert snapshot["nodes"][0]["state"] == "succeeded"


class TestRefreshingThePackageLists:
    def test_the_node_refreshes_and_the_job_says_what_its_update_check_reads_now(self, central):
        node = central.nodes["web-1"]
        node.handlers[("POST", "/api/server/updates/refresh")] = lambda request: node.queue()
        _version(central, "web-1", update_state="update_available", announced_version="3.3.1")

        snapshot, _, _ = _run(central, FleetRequest("index_refresh", nodes=["web-1"]))

        (result,) = snapshot["nodes"]
        assert result["state"] == "succeeded", result
        assert result["items"] == [
            {"update_state": "update_available", "announced_version": "3.3.1", "state": "succeeded"}
        ]
        assert "Package lists refreshed (update_available)" in result["step"]

    def test_a_node_that_cannot_refresh_is_skipped_as_unsupported(self, central):
        central.nodes["web-2"].schema = dict(SCHEMA_30)

        described = plan(
            FleetRequest("index_refresh", nodes=["web-1", "web-2"]),
            manager=central.manager,
            asker=ADMIN,
        ).to_dict()

        assert _states(described)["web-2"] == ("skipped", "unsupported")
