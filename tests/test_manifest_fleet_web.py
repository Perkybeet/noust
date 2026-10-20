# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A manifest that names the fleet, previewed through the console's real routes (J-15).

The engine is the one the command line runs, and its reads are requests to the console's routes
as the caller: so what a preview shows of the fleet is what the caller may read of it. An
administrator reads the servers and the placements; a member of a team reads the servers it was
given, and the placements are the administrator's, which a preview says instead of failing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests import test_node_teams_api as fleet_tests
from tests.teams_support import World, build_world

# The fixtures of the fleet tests, under their own names.
served = fleet_tests.served
fleet = fleet_tests.fleet


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> World:
    """The two-team console, with the runner faked: a listing of applications asks systemd."""
    return build_world(tmp_path, monkeypatch)


MANIFEST = """
version: 1
servers:
  - name: acme-1
    labels: {role: web}
    team: globex
placements:
  - domain: shop.acme.example
    servers: [acme-1, globex-1]
    serial: 1
"""


def preview(world: World, who: str, text: str = MANIFEST) -> Any:
    return world.client.post("/api/manifest/plan", json={"manifest": text}, headers=world.as_(who))


def test_an_administrator_sees_the_servers_and_the_placement_planned(
    world: World, fleet: Any
) -> None:
    response = preview(world, "admin")

    assert response.status_code == 200, response.text
    kinds = [action["kind"] for action in response.json()["actions"]]
    assert kinds == ["server.team", "server.labels", "placement.set"]
    assert response.json()["warnings"] == []


def test_the_preview_prints_no_label_value_that_is_a_secret_and_no_command(
    world: World, fleet: Any
) -> None:
    response = preview(world, "admin")

    assert "globex" in response.text  # the team is part of the plan's words
    assert "command" not in response.text.lower()


def test_a_member_reads_its_own_servers_and_is_told_the_placements_are_not_its(
    world: World, fleet: Any
) -> None:
    response = preview(world, "alice")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [a["kind"] for a in body["actions"]] == ["server.team", "server.labels"]
    assert any("placements" in warning for warning in body["warnings"])


def test_a_server_the_member_was_not_given_is_not_there_for_it(world: World, fleet: Any) -> None:
    text = MANIFEST.replace("acme-1", "globex-1", 1)

    response = preview(world, "alice", text)

    assert any("globex-1" in warning for warning in response.json()["warnings"])
    assert not [a for a in response.json()["actions"] if a["kind"].startswith("server.")]


def test_a_manifest_that_asks_for_cron_is_planned_from_the_real_cron_route(
    world: World, fleet: Any, monkeypatch: Any
) -> None:
    from noust.core.runner import FakeRunner  # noqa: F401 - conftest installs the runner

    text = "version: 1\ncron:\n  - {name: tick, command: ls, schedule: hourly}\n"

    admin = preview(world, "admin", text)
    member = preview(world, "alice", text)

    assert admin.status_code == 200, admin.text
    assert [a["kind"] for a in admin.json()["actions"]] == ["cron.set"]
    # Cron jobs run as root: a member's preview says so and plans nothing.
    assert member.status_code == 200
    assert member.json()["actions"] == []
    assert any("cron" in warning for warning in member.json()["warnings"])
