# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application placed on several servers looks like right now (checklist F-18).

The central asks each member for its applications and compares: which commit each one serves,
which one most of them agree on, and who is not there, down, or on another commit. It reads;
nothing here changes a server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.fleet.aggregate import Asker, set_aggregator
from noust.fleet.placement_status import placement_status
from noust.fleet.placements import Placements
from tests.fleet_views_support import Central, build_central

DOMAIN = "shop.example.com"
ASKER = Asker(actor="cli:test@nas", scope="admin")


def app(
    commit: str | None, *, status: str = "running", finished: str = "2026-10-10T10:00:00+00:00"
):
    return {
        "name": DOMAIN,
        "domain": DOMAIN,
        "status": status,
        "branch": "main",
        "layout": "releases",
        "app_type": "nodejs",
        "port": 3000,
        "last_deployment": (
            {"id": 7, "status": "success", "git_commit": commit, "finished_at": finished}
            if commit
            else None
        ),
    }


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Central:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    return build_central(tmp_path, names=("web-1", "web-2", "web-3"))


def status(central: Central, **save: Any) -> dict[str, Any]:
    placements = Placements(central.store)
    placement = placements.save(
        DOMAIN, nodes=save.pop("nodes", ["web-1", "web-2", "web-3"]), **save
    )
    set_aggregator(None)
    return placement_status(placement, manager=central.manager, asker=ASKER)


def rows(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["node"]: row for row in result["servers"]}


def test_servers_on_one_commit_are_in_sync(central: Central) -> None:
    for node in central.nodes.values():
        node.apps = [app("abc1234")]

    result = status(central)

    assert result["in_sync"] is True
    assert result["reference_commit"] == "abc1234"
    assert {row["sync"] for row in result["servers"]} == {"current"}
    assert rows(result)["web-2"]["status"] == "running"
    assert rows(result)["web-2"]["href"] == f"/n/web-2/apps/{DOMAIN}"


def test_the_commit_most_servers_agree_on_is_the_reference(central: Central) -> None:
    central.nodes["web-1"].apps = [app("abc1234")]
    central.nodes["web-2"].apps = [app("abc1234")]
    central.nodes["web-3"].apps = [app("def5678")]

    result = status(central)

    assert result["reference_commit"] == "abc1234"
    assert result["in_sync"] is False
    assert {n: r["sync"] for n, r in rows(result).items()} == {
        "web-1": "current",
        "web-2": "current",
        "web-3": "differs",
    }


def test_a_tie_goes_to_the_commit_deployed_last(central: Central) -> None:
    central.nodes["web-1"].apps = [app("old1111", finished="2026-10-01T10:00:00+00:00")]
    central.nodes["web-2"].apps = [app("new2222", finished="2026-10-09T10:00:00+00:00")]
    central.nodes["web-3"].apps = []

    result = status(central, nodes=["web-1", "web-2"])

    assert result["reference_commit"] == "new2222"


def test_a_server_that_does_not_have_the_application_is_missing(central: Central) -> None:
    central.nodes["web-1"].apps = [app("abc1234")]
    central.nodes["web-2"].apps = [app("abc1234")]
    central.nodes["web-3"].apps = []

    result = status(central)

    assert rows(result)["web-3"]["sync"] == "missing"
    assert rows(result)["web-3"]["present"] is False
    assert "placement copy" in rows(result)["web-3"]["message"]
    assert result["in_sync"] is False


def test_a_server_that_does_not_answer_is_unreachable_and_not_counted_for_the_reference(
    central: Central,
) -> None:
    central.nodes["web-1"].apps = [app("abc1234")]
    central.nodes["web-2"].apps = [app("def5678")]
    central.nodes["web-3"].down = True

    result = status(central)

    assert rows(result)["web-3"]["sync"] == "unreachable"
    assert rows(result)["web-3"]["reachable"] is False
    assert rows(result)["web-3"]["message"]
    assert result["reference_commit"] in ("abc1234", "def5678")
    assert result["in_sync"] is False


def test_an_application_that_failed_is_said_whatever_its_commit(central: Central) -> None:
    for node in central.nodes.values():
        node.apps = [app("abc1234")]
    central.nodes["web-2"].apps = [app("abc1234", status="failed")]

    result = status(central)

    assert rows(result)["web-2"]["sync"] == "current"
    assert rows(result)["web-2"]["status"] == "failed"
    assert result["in_sync"] is False, "in sync is the same commit and all of them running"


def test_the_members_are_the_label_too(central: Central) -> None:
    from noust.fleet.labels import NodeLabels

    NodeLabels(central.store).change("web-3", set_labels={"role": "web"})
    for node in central.nodes.values():
        node.apps = [app("abc1234")]
    placements = Placements(central.store)
    placement = placements.save(DOMAIN, nodes=["web-1"], selector={"role": "web"})
    set_aggregator(None)

    result = placement_status(placement, manager=central.manager, asker=ASKER)

    assert result["members"] == ["web-1", "web-3"]


def test_other_applications_of_the_same_server_are_not_in_the_answer(central: Central) -> None:
    other = {**app("zzz9999"), "domain": "blog.example.com", "name": "blog.example.com"}
    for node in central.nodes.values():
        node.apps = [other, app("abc1234")]

    result = status(central)

    assert result["reference_commit"] == "abc1234"
    assert len(result["servers"]) == 3


def test_an_application_without_a_deployment_has_no_commit_to_compare(central: Central) -> None:
    central.nodes["web-1"].apps = [app(None)]
    central.nodes["web-2"].apps = [app("abc1234")]
    central.nodes["web-3"].apps = [app("abc1234")]

    result = status(central)

    assert rows(result)["web-1"]["sync"] == "unknown"
    assert rows(result)["web-1"]["commit"] is None
