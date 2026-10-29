# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server is sent when a channel's route cannot match anything on it.

Pure, like the rest of the plan: a channel is sent only where its route can match something
(:mod:`noust.fleet.settings.reach`), so a credential does not reach a machine that could never use
it. What is pinned: nothing is pushed, a newer version included; what the server already holds is
left exactly as it is (neither let go nor deleted); putting an overridden setting back does not
send it where it cannot match; a server that is independent is let go of everything whatever the
route says; and a route that depends on applications nobody could read sends nothing and says why.
"""

from __future__ import annotations

from typing import Any

from noust.fleet.settings.reconcile import plan_node
from tests.test_fleet_settings_reconcile import applied, steps, wanted


def test_a_setting_whose_route_cannot_match_here_is_not_pushed() -> None:
    plan = plan_node([wanted("ops", 3)], [], out_of_reach={("channel", "ops"): "server"})

    assert steps(plan) == [("channel", "ops", "keep", "out_of_reach")]
    assert plan[0].why == "server"
    assert plan[0].row is None


def test_a_newer_version_is_not_pushed_either_and_what_the_server_holds_is_left_alone() -> None:
    row = applied("ops", 2)

    plan = plan_node([wanted("ops", 7)], [row], out_of_reach={("channel", "ops"): "domain"})

    assert steps(plan) == [("channel", "ops", "keep", "out_of_reach")]
    assert plan[0].row == row  # it is neither let go nor deleted


def test_putting_it_back_does_not_send_it_where_it_cannot_match() -> None:
    row = applied("ops", 3, "overridden")

    plan = plan_node(
        [wanted("ops", 5)],
        [row],
        restore={"channel/ops"},
        restore_all=True,
        out_of_reach={("channel", "ops"): "tag"},
    )

    assert steps(plan) == [("channel", "ops", "keep", "out_of_reach")]


def test_only_the_settings_that_cannot_match_are_held_back() -> None:
    plan = plan_node(
        [wanted("ops", 3), wanted("pager", 4), wanted("REGION", 5, resource="variable")],
        [],
        out_of_reach={("channel", "pager"): "server"},
    )

    assert steps(plan) == [
        ("channel", "ops", "apply", "new"),
        ("channel", "pager", "keep", "out_of_reach"),
        ("variable", "REGION", "apply", "new"),
    ]


def test_a_server_that_is_independent_is_let_go_whatever_the_route_says() -> None:
    plan = plan_node(
        [wanted("ops", 3)],
        [applied("ops", 3)],
        independent=True,
        out_of_reach={("channel", "ops"): "server"},
    )

    assert steps(plan) == [("channel", "ops", "release", "independent")]


def test_a_route_that_depends_on_applications_nobody_could_read_sends_nothing() -> None:
    plan = plan_node([wanted("ops", 3)], [applied("ops", 2)], reach_unknown={("channel", "ops")})

    assert steps(plan) == [("channel", "ops", "keep", "reach_unknown")]


def test_a_setting_that_can_match_is_planned_as_ever() -> None:
    plan: list[Any] = plan_node([wanted("ops", 5)], [applied("ops", 3)], out_of_reach={})

    assert steps(plan) == [("channel", "ops", "apply", "outdated")]
