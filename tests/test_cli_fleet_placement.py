# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust fleet placement ...``: an application that runs on several servers, from a terminal.

The engines are tested on their own; these pin what the commands add: what is recorded and said,
the plan before anything runs, the exit status, the balancer's text, and that a rehearsal changes
nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.placements import Placements
from noust.web import fleet_jobs
from noust.web.auth import STATE_DIR_ENV
from tests.fleet_views_support import Central, build_central
from tests.placement_support import DOMAIN, Shop, Upstream

NAMES = ("web-1", "web-2", "web-3")


@pytest.fixture(autouse=True)
def default_runner_after_a_rehearsal() -> Iterator[None]:
    """--dry-run installs a runner that refuses to change anything; the next test must not get it."""
    yield
    set_runner(None)


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    built = build_central(tmp_path / "fleet", NAMES)
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
    monkeypatch.setattr(fleet_jobs, "POLL_SECONDS", 0)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


@pytest.fixture
def upstream() -> Upstream:
    return Upstream(head="aaa1111")


@pytest.fixture
def shops(central: Central, upstream: Upstream) -> dict[str, Shop]:
    return {name: Shop(central.nodes[name], upstream) for name in NAMES}


def invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["fleet", "placement", *args], input=input)


def test_a_placement_is_recorded_listed_and_forgotten(central) -> None:
    added = invoke("add", DOMAIN, "--nodes", "web-1,web-2", "--label", "role=web", "--serial", "2")
    listed = json.loads(invoke("list", "--json").output)
    shown = invoke("list")

    assert added.exit_code == 0, added.output
    assert listed["placements"][0]["nodes"] == ["web-1", "web-2"]
    assert listed["placements"][0]["labels"] == {"role": "web"}
    assert listed["placements"][0]["serial"] == 2
    assert DOMAIN in shown.output and "web-1, web-2" in shown.output
    assert invoke("remove", DOMAIN, "-y").exit_code == 0
    assert json.loads(invoke("list", "--json").output)["placements"] == []


def test_adding_again_changes_what_is_named_and_keeps_the_rest(central) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1", "--serial", "2", "--canary", "web-1")

    invoke("add", DOMAIN, "--nodes", "web-1,web-2")

    saved = Placements(central.store).get(DOMAIN)
    assert saved is not None
    assert saved.nodes == ("web-1", "web-2") and saved.serial == 2 and saved.canary == "web-1"


def test_a_placement_that_names_nothing_is_refused(central) -> None:
    result = invoke("add", DOMAIN)

    assert result.exit_code != 0
    assert "Say where the application runs" in str(result.exception)


def test_the_status_says_which_server_differs(central, shops) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1,web-2,web-3")
    shops["web-3"].commit = "old0000"
    shops["web-3"].node.apps = [shops["web-3"].row()]

    result = invoke("status", DOMAIN)
    as_json = json.loads(invoke("status", DOMAIN, "--json").output)

    assert result.exit_code == 0, result.output
    assert "aaa1111" in result.output and "differs" in result.output
    assert as_json["status"]["reference_commit"] == "aaa1111"
    assert as_json["status"]["in_sync"] is False


def test_the_rollout_shows_its_plan_and_runs_nothing_with_plan(central, shops) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1,web-2,web-3")

    result = invoke("rollout", DOMAIN, "--plan")

    assert result.exit_code == 0, result.output
    assert "Roll an application out on 3 servers" in result.output
    assert "web-1 first" in result.output
    assert all(shop.deploys == [] for shop in shops.values())


def test_the_rollout_asks_before_it_runs(central, shops) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1,web-2,web-3")

    result = invoke("rollout", DOMAIN, input="n\n")

    assert "Nothing was run." in result.output
    assert all(shop.deploys == [] for shop in shops.values())


def test_a_rollout_moves_every_server_to_one_commit(central, shops, upstream) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1,web-2,web-3")
    upstream.head = "bbb2222"

    result = invoke("rollout", DOMAIN, "-y")

    assert result.exit_code == 0, result.output
    assert {shop.commit for shop in shops.values()} == {"bbb2222"}
    assert "3 succeeded" in result.output


def test_a_rollout_that_stops_puts_back_and_exits_1(central, shops, upstream) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1,web-2,web-3")
    upstream.head = "bbb2222"
    shops["web-3"].fail_deploy = "migration failed"

    result = invoke("rollout", DOMAIN, "-y")

    assert result.exit_code == 1, result.output
    assert shops["web-1"].commit == "aaa1111" and shops["web-2"].commit == "aaa1111"
    assert "put back" in result.output.lower()


def test_no_rollback_leaves_the_servers_where_they_are(central, shops, upstream) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1,web-2,web-3")
    upstream.head = "bbb2222"
    shops["web-3"].fail_deploy = "migration failed"

    invoke("rollout", DOMAIN, "-y", "--no-rollback")

    assert shops["web-1"].commit == "bbb2222"


def test_a_commit_and_a_tag_cannot_both_be_asked(central, shops) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1")

    result = invoke("rollout", DOMAIN, "--commit", "bbb2222", "--tag", "v2", "--plan")

    assert result.exit_code != 0


def test_a_rehearsal_records_and_runs_nothing(central, shops) -> None:
    added = CliRunner().invoke(
        root_cli, ["--dry-run", "fleet", "placement", "add", DOMAIN, "--nodes", "web-1"]
    )

    assert added.exit_code == 0, added.output
    assert Placements(central.store).get(DOMAIN) is None


def test_the_balancers_text_is_printed(central, shops) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1,web-2,web-3")

    nginx = invoke("upstreams", DOMAIN)
    listed = invoke("upstreams", DOMAIN, "--format", "list", "--port", "8443")

    assert nginx.exit_code == 0, nginx.output
    assert "upstream shop_example_com {" in nginx.output
    assert ":8443" in listed.output and ":443" not in listed.output


def test_copy_plans_the_members_that_lack_it(central, shops, upstream) -> None:
    import httpx

    invoke("add", DOMAIN, "--nodes", "web-1,web-2,web-3")
    central.nodes["web-3"].handlers[("GET", "/api/apps")] = lambda r: httpx.Response(
        200, json={"apps": [], "total": 0}
    )

    result = invoke("copy", DOMAIN, "--plan")

    assert result.exit_code == 0, result.output
    assert "Copy an application to servers on 1 server" in result.output
    assert "web-3" in result.output


def test_copy_says_when_nothing_needs_copying(central, shops) -> None:
    invoke("add", DOMAIN, "--nodes", "web-1,web-2,web-3")

    result = invoke("copy", DOMAIN, "--plan")

    assert result.exit_code != 0
    assert "already has it" in str(result.exception)


def test_an_application_that_is_not_placed_is_said(central) -> None:
    result = invoke("status", DOMAIN)

    assert result.exit_code != 0
    assert "placement" in str(result.exception).lower()
