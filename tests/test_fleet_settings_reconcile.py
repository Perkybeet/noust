# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server needs, given what the central wants and what the server says it applied.

Pure: no node is asked. What is pinned: a setting the server lacks or holds at another version is
pushed; one it holds at the same version is left; one its own operator changed is left too (the
server rules on its own machine) unless the central is told to put it back, by whoever is applying
or by the server's operator asking; what the central no longer wants is let go, never deleted; a
server that is independent is let go of everything; and what another central manages is not this
central's to touch.
"""

from __future__ import annotations

from noust.fleet.settings.reconcile import plan_node
from tests.support.fleet_settings_reconcile_support import applied, steps, wanted


def test_a_setting_the_server_does_not_have_is_pushed() -> None:
    plan = plan_node([wanted("ops", 3)], [])

    assert steps(plan) == [("channel", "ops", "apply", "new")]
    assert plan[0].setting is not None and plan[0].setting.version == 3


def test_a_setting_at_the_same_version_is_left_alone() -> None:
    assert steps(plan_node([wanted("ops", 3)], [applied("ops", 3)])) == [
        ("channel", "ops", "keep", "in_sync")
    ]


def test_a_setting_at_another_version_is_pushed_again() -> None:
    assert steps(plan_node([wanted("ops", 5)], [applied("ops", 3)])) == [
        ("channel", "ops", "apply", "outdated")
    ]


def test_an_exception_added_for_the_server_is_a_new_version_for_it() -> None:
    fleet_version = applied("ops", 3)

    plan = plan_node([wanted("ops", 8, scope="node:web-2")], [fleet_version])

    assert steps(plan) == [("channel", "ops", "apply", "outdated")]


def test_what_the_servers_own_operator_changed_is_left_as_the_operator_made_it() -> None:
    plan = plan_node([wanted("ops", 5)], [applied("ops", 3, "overridden")])

    assert steps(plan) == [("channel", "ops", "keep", "overridden")]


def test_it_is_put_back_when_the_person_applying_says_so() -> None:
    row = applied("ops", 3, "overridden")

    assert steps(plan_node([wanted("ops", 5)], [row], restore={"channel/ops"})) == [
        ("channel", "ops", "apply", "restore")
    ]
    assert steps(plan_node([wanted("ops", 5)], [row], restore_all=True)) == [
        ("channel", "ops", "apply", "restore")
    ]
    assert steps(plan_node([wanted("ops", 5)], [row], restore={"channel/other"})) == [
        ("channel", "ops", "keep", "overridden")
    ]


def test_it_is_put_back_when_the_servers_own_operator_asked() -> None:
    row = applied("ops", 3, "overridden", restore_requested_at="2026-10-28T10:00:00+00:00")

    assert steps(plan_node([wanted("ops", 5)], [row])) == [("channel", "ops", "apply", "restore")]


def test_what_the_central_no_longer_wants_is_let_go_and_not_deleted() -> None:
    plan = plan_node([], [applied("old", 2), applied("gone", 4, "overridden")])

    assert steps(plan) == [
        ("channel", "gone", "release", "removed"),
        ("channel", "old", "release", "removed"),
    ]


def test_an_independent_server_is_let_go_of_everything_and_pushed_nothing() -> None:
    plan = plan_node([wanted("ops", 3)], [applied("ops", 3), applied("old", 1)], independent=True)

    assert steps(plan) == [
        ("channel", "old", "release", "independent"),
        ("channel", "ops", "release", "independent"),
    ]


def test_what_another_central_manages_is_not_this_centrals_to_touch() -> None:
    plan = plan_node([wanted("ops", 3)], [applied("ops", 2, central="other")], central="nas")

    assert steps(plan) == [("channel", "ops", "apply", "new")]
    assert plan_node([], [applied("ops", 2, central="other")], central="nas") == []


def test_the_steps_are_in_order_of_kind_then_name() -> None:
    plan = plan_node(
        [
            wanted("b", 1),
            wanted("a", 2, resource="variable"),
            wanted("a", 3),
            wanted("z", 4, resource="alert_rule"),
        ],
        [],
    )

    assert [(s.resource, s.name) for s in plan] == [
        ("alert_rule", "z"),
        ("channel", "a"),
        ("channel", "b"),
        ("variable", "a"),
    ]


class TestOnlyTheSettingsNamed:
    """``only`` limits what one apply does, at the one place that decides what is done."""

    def test_nothing_else_is_pushed_and_nothing_else_is_said(self) -> None:
        plan = plan_node(
            [wanted("a", 1), wanted("b", 2), wanted("a", 3, resource="variable")],
            [applied("b", 1)],
            only={"channel/b"},
        )

        assert steps(plan) == [("channel", "b", "apply", "outdated")]

    def test_it_names_a_kind_as_well_as_a_name(self) -> None:
        plan = plan_node(
            [wanted("a", 1), wanted("a", 3, resource="variable")], [], only={"variable/a"}
        )

        assert steps(plan) == [("variable", "a", "apply", "new")]

    def test_what_the_central_no_longer_wants_is_let_go_only_when_it_is_named(self) -> None:
        managed = [applied("old", 2), applied("older", 3)]

        assert steps(plan_node([], managed, only={"channel/old"})) == [
            ("channel", "old", "release", "removed")
        ]
        assert plan_node([], managed, only={"channel/other"}) == []

    def test_an_independent_server_is_let_go_of_only_what_is_named(self) -> None:
        plan = plan_node(
            [wanted("ops", 3)],
            [applied("ops", 3), applied("old", 1)],
            independent=True,
            only={"channel/ops"},
        )

        assert steps(plan) == [("channel", "ops", "release", "independent")]

    def test_an_empty_list_is_no_limit(self) -> None:
        plan = plan_node([wanted("a", 1), wanted("b", 2)], [], only=())

        assert [s.name for s in plan] == ["a", "b"]

    def test_putting_back_works_on_the_one_named(self) -> None:
        row = applied("ops", 3, "overridden")

        plan = plan_node(
            [wanted("ops", 5), wanted("other", 6)],
            [row],
            restore={"channel/ops"},
            only={"channel/ops"},
        )

        assert steps(plan) == [("channel", "ops", "apply", "restore")]
