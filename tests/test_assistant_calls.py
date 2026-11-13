# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the assistant does with a call it prepares: read it, do it, propose it, or not offer it (item 171).

The class of the call is computed from what the API publishes of the operation (never from the
model's words), and the decision is the policy of who calls (:mod:`noust.core.caller_policy`) made
with that class and the environment of the resource. Where the policy says "does it" the
assistant still only proposes what carries a value that is not the person's, and everything once
text from outside was read in the conversation.
"""

from __future__ import annotations

import pytest

from noust.core.assistant import calls
from noust.core.assistant.calls import ASK, READ, REFUSED, TWO, YOU, Decision, decide
from noust.core.caller_policy import ALONE, NEVER, OTHER, PRODUCTION, PROPOSE, CallerPolicy
from noust.core.operation_classes import OperationClass, classify_parts
from noust.core.store import NoustStore

PERM = "apps.operate"


@pytest.fixture
def policy(tmp_path):
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    try:
        yield CallerPolicy(store, production_floor=False)
    finally:
        store.close()
        NoustStore.reset_instance()


def op(method: str, path: str, permission: str, **kw) -> OperationClass:
    return classify_parts(method, path, permission, **kw)


RESTART = op("POST", "/api/apps/{domain}/restart", PERM)
LIMITS = op("PUT", "/api/apps/{domain}/limits", "apps.manage", elevation=True)
DELETE = op("DELETE", "/api/apps/{domain}", "apps.manage", elevation=True, approval="always")
LOGS = op("GET", "/api/apps/{domain}/logs", "apps.read")


def decision(operation, needed, policy, **kw) -> Decision:
    kw.setdefault("environment", PRODUCTION)
    kw.setdefault("foreign", False)
    kw.setdefault("tainted", False)
    kw.setdefault("second_person", False)
    return decide(operation, needed, policy=policy, **kw)


class TestReads:
    def test_a_read_runs_whatever_the_table_says(self, policy: CallerPolicy) -> None:
        got = decision(LOGS, ["apps.read"], policy)
        assert (got.action, got.klass) == ("run", READ)

    def test_a_read_of_what_it_never_holds_is_not_offered(self, policy: CallerPolicy) -> None:
        audit = op("GET", "/api/audit", "audit.read")
        got = decision(audit, ["audit.read"], policy)
        assert (got.action, got.klass, got.floor) == ("refuse", REFUSED, True)

    def test_the_callers_own_things_are_not_offered_at_all(self, policy: CallerPolicy) -> None:
        mine = op("GET", "/api/auth/tokens", "self")
        assert decision(mine, ["self"], policy).action == "refuse"


class TestWhatChangesSomething:
    def test_by_default_it_is_proposed_for_a_person_to_approve(self, policy: CallerPolicy) -> None:
        got = decision(RESTART, [PERM], policy)
        assert (got.action, got.klass, got.why) == ("propose", ASK, "policy_propose")

    def test_where_the_policy_says_alone_it_is_done(self, policy: CallerPolicy) -> None:
        policy.set_cells([("assistant", PRODUCTION, PERM, ALONE)], by="t")
        got = decision(RESTART, [PERM], policy)
        assert (got.action, got.klass, got.why) == ("run", "alone", "policy_alone")

    def test_the_environment_is_the_resources_and_the_table_is_by_environment(
        self, policy: CallerPolicy
    ) -> None:
        policy.set_cells([("assistant", OTHER, PERM, ALONE)], by="t")
        assert decision(RESTART, [PERM], policy, environment=OTHER).action == "run"
        assert decision(RESTART, [PERM], policy, environment=PRODUCTION).action == "propose"

    def test_a_cell_that_says_never_is_not_offered_and_is_not_the_floor(
        self, policy: CallerPolicy
    ) -> None:
        policy.set_cells([("assistant", PRODUCTION, PERM, NEVER)], by="t")
        got = decision(RESTART, [PERM], policy)
        assert (got.action, got.klass, got.floor) == ("refuse", REFUSED, False)
        assert got.verdict.value == NEVER

    def test_a_foreign_value_makes_it_a_proposal_even_where_it_would_be_done(
        self, policy: CallerPolicy
    ) -> None:
        policy.set_cells([("assistant", PRODUCTION, PERM, ALONE)], by="t")
        got = decision(RESTART, [PERM], policy, foreign=True)
        assert (got.action, got.klass, got.why) == ("propose", ASK, "foreign_value")

    def test_so_does_having_read_text_from_outside(self, policy: CallerPolicy) -> None:
        policy.set_cells([("assistant", PRODUCTION, PERM, ALONE)], by="t")
        got = decision(RESTART, [PERM], policy, tainted=True)
        assert (got.action, got.why) == ("propose", "read_outside")

    def test_and_so_does_the_hours_limit_on_changes(self, policy: CallerPolicy) -> None:
        policy.set_cells([("assistant", PRODUCTION, PERM, ALONE)], by="t")
        got = decision(RESTART, [PERM], policy, actions_allowed=False)
        assert (got.action, got.why) == ("propose", "actions_limit")

    def test_a_foreign_value_does_not_make_a_refusal_a_proposal(self, policy: CallerPolicy) -> None:
        policy.set_cells([("assistant", PRODUCTION, PERM, NEVER)], by="t")
        assert decision(RESTART, [PERM], policy, foreign=True).action == "refuse"


class TestWhatAsksForAPerson:
    def test_sudo_mode_is_a_proposal_the_person_does_with_their_own_session(
        self, policy: CallerPolicy
    ) -> None:
        got = decision(LIMITS, ["apps.manage"], policy)
        assert (got.action, got.klass, got.why) == ("propose", YOU, "needs_sudo")
        assert got.verdict.value == NEVER

    def test_a_second_person_is_one_too_when_approvals_ask_for_it(
        self, policy: CallerPolicy
    ) -> None:
        got = decision(DELETE, ["apps.manage"], policy, second_person=True)
        assert (got.action, got.klass, got.why) == ("propose", TWO, "needs_second")

    def test_without_the_four_eyes_rule_on_it_is_only_sudo_mode(self, policy: CallerPolicy) -> None:
        got = decision(DELETE, ["apps.manage"], policy, second_person=False)
        assert got.klass == YOU

    def test_the_floor_is_still_the_floor_whatever_the_route_asks(
        self, policy: CallerPolicy
    ) -> None:
        setting = op("PUT", "/api/config", "settings.manage", elevation=True)
        got = decision(setting, ["settings.manage"], policy)
        assert (got.action, got.floor) == ("refuse", True)

    def test_the_policys_own_cell_is_not_asked_for_a_proposal_that_is_a_persons_to_do(
        self, policy: CallerPolicy
    ) -> None:
        # What asks for sudo mode is a proposal whether the cell would have said "proposes"
        # or not: the cell is not even offered "alone" on a key row.
        got = decision(LIMITS, ["apps.manage"], policy)
        assert got.verdict.reason == "assistant_strong"


def test_every_class_a_proposal_may_have_is_named() -> None:
    assert {ASK, YOU, TWO} <= set(calls.PROPOSAL_CLASSES)
    assert PROPOSE and ALONE
