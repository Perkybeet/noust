# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Updating an application on all its servers follows its build server (checklist F-27).

Recording a build server (``noust fleet builder set``) says where the application is compiled, and
until now only ``fleet placement ship`` obeyed it: a rollout noted the build server and built on every
server anyway, which is the thing a server too small to compile it cannot do. The update everywhere
(``fleet placement rollout``, ``POST .../rollout``) is now one decision: build once there and send the
release, unless the build server cannot build that kind of update or the operator chose otherwise, and
then the plan says which. These tests pin the decision, that the shipped update builds once, and the
two clients (the terminal and the API) that ask it.
"""

# The fleet harness of the ship tests is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.runner import set_runner
from noust.fleet.builders import Builders
from noust.fleet.placements import Placements
from noust.web import fleet_jobs
from noust.web.auth import STATE_DIR_ENV
from noust.web.fleet_jobs import plan
from noust.web.fleet_ship_actions import builder_declined, update_request
from tests.fleet_views_support import Central
from tests.test_fleet_ship import (  # noqa: F401  (pytest resolves fixtures by name)
    ADMIN,
    DOMAIN,
    Builder,
    Target,
    builder,
    central,
    run,
    states,
    targets,
)

MEMBERS = ["web-1", "web-2"]


def placed(central: Central, **fields) -> object:
    return Placements(central.store).save(DOMAIN, nodes=MEMBERS, **fields)


def asked(central: Central, **options):
    placement = Placements(central.store).get(DOMAIN)
    assert placement is not None
    return update_request(placement, MEMBERS, manager=central.manager, **options)


class TestTheDecision:
    def test_an_application_with_a_build_server_is_built_there(self, central: Central) -> None:
        placed(central)
        Builders(central.store).set(DOMAIN, "build-1")

        fleet_request = asked(central)

        assert fleet_request.action == "placement_ship"
        assert fleet_request.nodes == MEMBERS

    def test_one_without_is_rolled_out_as_it_always_was(self, central: Central) -> None:
        placed(central)

        assert asked(central).action == "placement_rollout"

    def test_the_operator_can_have_each_server_build(self, central: Central) -> None:
        placed(central)
        Builders(central.store).set(DOMAIN, "build-1")

        fleet_request = asked(central, build_on_each=True)

        assert fleet_request.action == "placement_rollout"
        assert fleet_request.options["build_on_each"] is True

    @pytest.mark.parametrize(
        "options",
        [{"image_tag": "1.4"}, {"tag": "v1.4.0"}],
        ids=["a registry image tag", "a git tag"],
    )
    def test_what_a_build_server_does_not_build_is_rolled_out(
        self, central: Central, options: dict[str, str]
    ) -> None:
        placed(central)
        Builders(central.store).set(DOMAIN, "build-1")

        assert asked(central, **options).action == "placement_rollout"

    def test_a_commit_is_built_there_and_travels(self, central: Central) -> None:
        placed(central)
        Builders(central.store).set(DOMAIN, "build-1")

        fleet_request = asked(central, commit="feed123")

        assert fleet_request.action == "placement_ship"
        assert fleet_request.options["commit"] == "feed123"

    def test_a_build_server_that_is_also_a_member_updates_the_ordinary_way(
        self, central: Central
    ) -> None:
        Placements(central.store).save(DOMAIN, nodes=["build-1", "web-1"])
        Builders(central.store).set(DOMAIN, "build-1")
        placement = Placements(central.store).get(DOMAIN)

        fleet_request = update_request(placement, ["build-1", "web-1"], manager=central.manager)

        assert fleet_request.action == "placement_rollout"

    def test_the_placements_own_strategy_is_the_shipments(self, central: Central) -> None:
        placed(central, serial=2, max_failures=1, canary="web-2", rollback_on_failure=False)
        Builders(central.store).set(DOMAIN, "build-1")

        fleet_request = asked(central)

        assert (fleet_request.serial, fleet_request.max_failures, fleet_request.canary) == (
            2,
            1,
            "web-2",
        )
        assert fleet_request.options["rollback_on_failure"] is False

    def test_what_the_operator_says_wins_over_the_placements(self, central: Central) -> None:
        placed(central, serial=2, canary="web-2")
        Builders(central.store).set(DOMAIN, "build-1")

        fleet_request = asked(central, serial=1, canary="web-1", rollback_on_failure=True)

        assert (fleet_request.serial, fleet_request.canary) == (1, "web-1")
        assert fleet_request.options["rollback_on_failure"] is True

    @pytest.mark.parametrize(
        ("kwargs", "words"),
        [
            ({"build_on_each": True}, "you asked"),
            ({"image_tag": "1.4"}, "registry"),
            ({"tag": "v1"}, "--commit"),
            ({}, None),
        ],
    )
    def test_the_reason_is_in_words(self, kwargs: dict, words: str | None) -> None:
        reason = builder_declined("build-1", ["web-1"], **kwargs)

        assert (reason is None) if words is None else (words in str(reason))

    def test_a_builder_among_the_members_has_its_reason_too(self) -> None:
        reason = builder_declined("build-1", ["build-1", "web-1"])

        assert reason is not None and "updates the ordinary way" in reason


class TestThePlan:
    def test_a_followed_update_is_planned_as_a_shipment(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        placed(central)
        Builders(central.store).set(DOMAIN, "build-1")

        described = plan(asked(central), manager=central.manager, asker=ADMIN).to_dict()

        assert described["action"] == "placement_ship"
        assert any("build-1 builds" in note for note in described["notes"])

    def test_the_rollout_that_was_not_followed_says_why(
        self, central: Central, targets: dict[str, Target]
    ) -> None:
        placed(central)
        Builders(central.store).set(DOMAIN, "build-1")

        described = plan(
            asked(central, image_tag="1.4"), manager=central.manager, asker=ADMIN
        ).to_dict()

        note = next(n for n in described["notes"] if "build server" in n)
        assert "build-1" in note and "registry" in note
        assert "noust fleet placement ship" not in note

    def test_a_rollout_asked_for_each_server_says_so(
        self, central: Central, targets: dict[str, Target]
    ) -> None:
        placed(central)
        Builders(central.store).set(DOMAIN, "build-1")

        described = plan(
            asked(central, build_on_each=True), manager=central.manager, asker=ADMIN
        ).to_dict()

        note = next(n for n in described["notes"] if "build server" in n)
        assert "you asked for each server to build it" in note


class TestRunning:
    def test_the_update_builds_once_and_every_server_gets_that_build(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        placed(central)
        Builders(central.store).set(DOMAIN, "build-1")

        snapshot, _, _ = run(central, asked(central))

        assert snapshot["status"] == "succeeded", snapshot
        assert len(builder.builds) == 1
        assert states(snapshot) == {"web-1": "succeeded", "web-2": "succeeded"}
        assert targets["web-1"].receives and targets["web-2"].receives


# ---------------------------------------------------------------------------
# The terminal and the API
# ---------------------------------------------------------------------------


@pytest.fixture
def terminal(
    central: Central, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Central]:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: central.manager)
    monkeypatch.setattr(fleet_jobs, "POLL_SECONDS", 0)
    yield central
    set_runner(None)


def rollout(*args: str):
    return CliRunner().invoke(
        root_cli, ["fleet", "placement", "rollout", DOMAIN, "--plan", "--json", *args]
    )


class TestTheTerminal:
    def test_a_rollout_follows_the_build_server(
        self, terminal: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        placed(terminal)
        Builders(terminal.store).set(DOMAIN, "build-1")

        result = rollout()

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["plan"]["action"] == "placement_ship"

    def test_build_on_each_is_the_old_rollout(
        self, terminal: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        placed(terminal)
        Builders(terminal.store).set(DOMAIN, "build-1")

        result = rollout("--build-on-each")

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["plan"]["action"] == "placement_rollout"
