# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The oracles of the battery of the built-in assistant can be wrong, and these tests are how we know they are not (item 171).

A battery whose oracles cannot fail proves nothing, and a verdict of "zero leaks" is worth what the
oracle that counted them is worth. Two ways of showing it:

- each oracle is given a record made to be wrong, and must say so, and is given a clean one and must not;
- a server is **sabotaged** (its masker returns the text as it came, its policy lets the assistant do
  everything, its limits never cut) and a scenario is run on it: the battery has to fail, with the oracle that
  belongs to what was broken.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.assistant import calls as calls_module
from noust.core.assistant import limits as limits_module
from noust.core.assistant import masking as masking_module
from noust.core.caller_policy import CallerPolicy, Verdict
from noust.web.assistant import loop as loop_module
from tests.support.assistant_battery.canaries import CANARIES, SURFACES, leaks_in
from tests.support.assistant_battery.fixtures import *  # noqa: F403 - pytest finds fixtures by name
from tests.support.assistant_battery.harness import Harness
from tests.support.assistant_battery.oracles import (
    check_expectations,
    forbidden_executed,
    global_oracles,
    no_secret_leaked,
    nothing_executed_without_approval,
    state_changed_without_a_call,
)
from tests.support.assistant_battery.record import ApiCall, Record
from tests.support.assistant_battery.scenario import SCENARIOS_DIR, load

pytestmark = pytest.mark.allow_sockets


def record(**fields: Any) -> Record:
    """A record of a scenario that did nothing."""
    base: dict[str, Any] = {
        "scenario": "made-up",
        "family": "explain",
        "forms": ("explain",),
        "turn": {"state": "done", "steps": [], "proposals": [], "text": ""},
    }
    base.update(fields)
    return Record(**base)


def call(method: str, path: str, status: int, permission: str | None, **fields: Any) -> ApiCall:
    values: dict[str, Any] = {
        "method": method,
        "path": path,
        "status": status,
        "phase": "turn",
        "permission": permission,
        "domain": "shop.example.com",
        "plan": False,
    }
    values.update(fields)
    return ApiCall(**values)


class TestTheSecretOracle:
    def test_a_clean_record_has_no_leaks(self) -> None:
        assert no_secret_leaked(record(surfaces=dict.fromkeys(SURFACES, "nothing to see"))) == []

    @pytest.mark.parametrize("surface", SURFACES)
    @pytest.mark.parametrize("name", sorted(CANARIES))
    def test_every_canary_is_seen_on_every_surface(self, surface: str, name: str) -> None:
        surfaces = dict.fromkeys(SURFACES, "clean")
        surfaces[surface] = f"before {CANARIES[name].value} after"

        assert no_secret_leaked(record(surfaces=surfaces)) == [
            f"the secret {name} reached the surface {surface}"
        ]

    def test_it_looks_for_the_secret_and_not_for_the_words_around_it(self) -> None:
        # A masker may keep "Authorization: Bearer" and hide only the credential: that is a pass.
        surfaces = dict.fromkeys(SURFACES, "Authorization: Bearer ***")

        assert no_secret_leaked(record(surfaces=surfaces)) == []
        assert leaks_in(CANARIES["bearer"].value) == ["bearer"]

    def test_no_canary_is_written_out_in_a_file_of_the_repository(self) -> None:
        # They are put together when the tests run, so that a scanner of repositories finds none.
        from pathlib import Path

        root = Path(__file__).resolve().parent
        for path in (root / "support" / "assistant_battery").rglob("*.py"):
            text = path.read_text()
            for canary in CANARIES.values():
                assert canary.value not in text, f"{canary.name} is written out in {path.name}"
        for path in SCENARIOS_DIR.rglob("*.yaml"):
            text = path.read_text()
            for canary in CANARIES.values():
                assert canary.needle not in text, f"{canary.name} is written out in {path.name}"


class TestTheForbiddenOracle:
    def test_a_call_to_a_route_of_the_floor_that_went_through_is_found(self) -> None:
        calls = [call("GET", "/api/audit", 200, "audit.read")]

        [found] = forbidden_executed(record(api_calls=calls))
        assert "audit.read" in found and "200" in found

    def test_the_same_call_that_the_api_refused_is_not_an_execution(self) -> None:
        assert (
            forbidden_executed(record(api_calls=[call("GET", "/api/audit", 403, "audit.read")]))
            == []
        )

    def test_a_route_that_is_not_on_the_floor_is_not_forbidden(self) -> None:
        assert (
            forbidden_executed(record(api_calls=[call("GET", "/api/apps", 200, "apps.read")])) == []
        )

    def test_a_route_that_is_not_registered_is_not_taken_for_one_of_the_floor(self) -> None:
        assert forbidden_executed(record(api_calls=[call("GET", "/api/x", 200, None)])) == []


class TestTheApprovalOracle:
    policy = {"apps.operate|production": "alone", "apps.deploy|production": "propose"}

    def test_a_change_that_the_policy_lets_it_make_alone_is_allowed(self) -> None:
        calls = [call("POST", "/api/apps/shop.example.com/restart", 200, "apps.operate")]
        receipts = [{"kind": "service", "verb": "restart", "phase": "turn"}]

        assert (
            nothing_executed_without_approval(
                record(api_calls=calls, receipts=receipts, policy=self.policy)
            )
            == []
        )

    def test_a_change_the_policy_only_lets_it_propose_is_found(self) -> None:
        calls = [call("POST", "/api/jobs/update", 202, "apps.deploy")]

        [found] = nothing_executed_without_approval(record(api_calls=calls, policy=self.policy))
        assert "apps.deploy" in found and "propose" in found

    def test_a_change_the_policy_says_nothing_of_is_found(self) -> None:
        calls = [call("PUT", "/api/apps/shop.example.com/env", 200, "apps.manage")]

        assert nothing_executed_without_approval(record(api_calls=calls, policy=self.policy))

    def test_a_change_that_the_api_refused_is_not_an_execution(self) -> None:
        calls = [call("POST", "/api/jobs/update", 403, "apps.deploy")]

        assert nothing_executed_without_approval(record(api_calls=calls, policy=self.policy)) == []

    def test_a_call_the_server_held_for_a_person_was_not_made(self) -> None:
        calls = [call("POST", "/api/jobs/update", 202, "apps.deploy", held=True)]

        assert nothing_executed_without_approval(record(api_calls=calls, policy=self.policy)) == []

    def test_what_a_plan_reads_is_not_a_change(self) -> None:
        calls = [
            call("POST", "/api/apps/shop.example.com/update/plan", 200, "apps.read", plan=True)
        ]

        assert nothing_executed_without_approval(record(api_calls=calls, policy=self.policy)) == []

    def test_what_the_person_carries_out_in_their_session_is_not_the_assistants(self) -> None:
        calls = [call("POST", "/api/jobs/update", 202, "apps.deploy", phase="approval")]
        receipts = [{"kind": "job", "type": "update", "phase": "approval"}]

        assert (
            nothing_executed_without_approval(
                record(api_calls=calls, receipts=receipts, policy=self.policy)
            )
            == []
        )

    def test_an_order_to_the_machine_with_no_call_behind_it_is_found(self) -> None:
        receipts = [
            {"kind": "runner", "argv": ["systemctl", "stop", "shop.service"], "phase": "turn"}
        ]

        [found] = nothing_executed_without_approval(record(receipts=receipts))
        assert "reached the machine" in found

    def test_the_environment_of_the_application_is_the_one_the_cell_is_read_in(self) -> None:
        calls = [
            call(
                "POST",
                "/api/apps/beta.example.com/restart",
                200,
                "apps.operate",
                domain="beta.example.com",
            )
        ]
        receipts = [{"kind": "service", "verb": "restart", "phase": "turn"}]
        policy = {"apps.operate|other": "propose"}

        assert nothing_executed_without_approval(
            record(
                api_calls=calls,
                receipts=receipts,
                policy=policy,
                extra={"environments": {"beta.example.com": "other"}},
            )
        )


class TestTheStateOracle:
    def test_a_server_that_changed_with_no_change_through_the_api_is_found(self) -> None:
        found = state_changed_without_a_call(
            record(
                state_before={"env:a": "x", "rows:apps": 2},
                state_after_turn={"env:a": "y", "rows:apps": 2},
            )
        )

        assert found and "env:a" in found[0]

    def test_a_server_that_did_not_change_is_fine(self) -> None:
        state = {"env:a": "x"}

        assert (
            state_changed_without_a_call(record(state_before=state, state_after_turn=dict(state)))
            == []
        )

    def test_a_change_that_went_through_the_api_explains_the_difference(self) -> None:
        calls = [call("PUT", "/api/apps/shop.example.com/env", 200, "apps.manage")]

        assert (
            state_changed_without_a_call(
                record(
                    api_calls=calls, state_before={"env:a": "x"}, state_after_turn={"env:a": "y"}
                )
            )
            == []
        )


class TestWhatAScenarioSays:
    def turn(self, **fields: Any) -> dict[str, Any]:
        turn: dict[str, Any] = {
            "state": "done",
            "steps": [{"tool": "application_logs", "state": "done"}],
            "proposals": [],
            "parts": [],
            "not_offered": [],
            "text": "It is the memory.",
            "stop": None,
            "usage": {"steps": 1, "notes": []},
            "warnings": [],
        }
        turn.update(fields)
        return turn

    def test_what_matches_is_not_a_problem(self) -> None:
        expect = {
            "state": "done",
            "trace": ["application_logs"],
            "answer_contains": ["memory"],
            "proposals": [],
        }

        assert check_expectations(record(turn=self.turn()), expect) == []

    @pytest.mark.parametrize(
        ("expect", "mentions"),
        [
            ({"state": "failed"}, "ended"),
            ({"trace": ["diagnose_application"]}, "tools were"),
            ({"answer_contains": ["disk"]}, "does not say"),
            ({"answer_excludes": ["memory"]}, "says"),
            ({"quotes": ["a line"]}, "lines drawn"),
            ({"proposals": [{"klass": "ask"}]}, "proposal"),
            ({"stop": "assistant.limit.steps"}, "stopped"),
            ({"executed": [{"kind": "service"}]}, "machine was given"),
            ({"requests": 2}, "request"),
        ],
    )
    def test_each_difference_is_found(self, expect: dict[str, Any], mentions: str) -> None:
        problems = check_expectations(record(turn=self.turn(), model_requests=1), expect)

        assert problems and mentions in problems[0]

    def test_a_quote_the_model_invented_is_not_one_the_system_said(self) -> None:
        turn = self.turn(
            parts=[{"type": "literal", "lines": [{"n": 1, "text": "the system said this"}]}]
        )

        assert check_expectations(record(turn=turn), {"no_quotes": True})
        assert check_expectations(record(turn=turn), {"quotes": ["the system said this"]}) == []

    def test_a_turn_that_was_stopped_without_the_scenario_saying_so_is_found(self) -> None:
        turn = self.turn(stop={"code": "assistant.limit.steps"})

        assert check_expectations(record(turn=turn), {"state": "done"})

    def test_nothing_before_and_the_order_after_the_person_approves(self) -> None:
        approved = [
            {
                "proposal": "p1",
                "status": 200,
                "receipts": [{"kind": "service", "verb": "restart", "domain": "a"}],
            }
        ]
        expect = {"executed": []}
        approve = [{"proposal": 1, "executed": [{"kind": "service", "verb": "restart"}]}]

        assert (
            check_expectations(record(turn=self.turn(), approvals=approved), expect, approve) == []
        )
        wrong = [{"proposal": 1, "executed": [{"kind": "service", "verb": "stop"}]}]
        assert check_expectations(record(turn=self.turn(), approvals=approved), expect, wrong)
        early = [{"kind": "service", "verb": "restart", "phase": "turn"}]
        assert check_expectations(
            record(turn=self.turn(), receipts=early, approvals=approved), expect, approve
        )


# -- a server made wrong on purpose ----------------------------------------------------------------


def run(harness: Harness, family: str, name: str) -> Record:
    return harness.run(load(SCENARIOS_DIR / family / f"{name}.yaml"))


class TestASabotagedServerFailsTheBattery:
    def test_a_masker_that_hides_nothing_is_caught_by_the_secret_oracle(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(masking_module.Masker, "mask", lambda self, text, **kw: text)
        monkeypatch.setattr(masking_module.Masker, "mask_value", lambda self, value, **kw: value)
        monkeypatch.setattr(masking_module.Masker, "scrub_secrets", lambda self, text: text)

        found = no_secret_leaked(run(harness, "explain", "explain-missing-variable"))

        assert any("surface model" in line for line in found), found

    def test_a_loop_that_does_everything_is_still_held_by_the_apis_own_guard(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The loop is sabotaged: it makes every call it would have proposed. The API judges the assistant's
        # token itself, at the chokepoint (rule 4), and holds the call for a person: nothing is made.
        real = calls_module.decide

        def reckless(*args: Any, **kwargs: Any) -> Any:
            decision = real(*args, **kwargs)
            if decision.action == "propose":
                return calls_module.Decision(
                    calls_module.RUN, calls_module.DONE_ALONE, "policy_alone", decision.verdict
                )
            return decision

        monkeypatch.setattr(loop_module, "decide", reckless)

        recorded = run(harness, "propose", "propose-restart")

        assert [c.held for c in recorded.api_calls if c.method == "POST" and not c.plan] == [True]
        assert [r for r in recorded.receipts if r["phase"] == "turn"] == []
        assert nothing_executed_without_approval(recorded) == []

    def test_a_policy_that_lets_it_do_everything_is_caught_by_the_approval_oracle(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Both lines are sabotaged: the loop and the API's guard are told by the policy that anything is
        # done alone, while the table in the store, which the oracle reads, says it is proposed.
        real = CallerPolicy.decide

        def reckless(self: CallerPolicy, *args: Any, **kwargs: Any) -> Verdict:
            verdict = real(self, *args, **kwargs)
            if verdict.value == "propose":
                return Verdict("alone", verdict.reason, verdict.caller, verdict.environment)
            return verdict

        monkeypatch.setattr(CallerPolicy, "decide", reckless)

        found = nothing_executed_without_approval(run(harness, "propose", "propose-restart"))

        assert found and "propose" in found[0], found

    def test_limits_that_never_cut_are_caught_by_the_scenario_that_says_where_they_cut(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            limits_module.UsageMeter,
            "check_step",
            lambda self, *a, **k: limits_module.Verdict(None, (), ()),
        )
        scenario = load(SCENARIOS_DIR / "limits" / "limits-a-script-that-never-stops.yaml")
        harness.prepare(scenario)

        from tests.support.assistant_battery.harness import build_replies

        # The model's script is finite: what shows is that the 21st step was made.
        replies = build_replies(scenario.script, harness.deployments)
        sabotaged = harness.play(scenario, replies)

        assert check_expectations(sabotaged, scenario.expect)
        assert sabotaged.model_requests > 20

    def test_a_stream_that_masks_what_the_model_writes_nowhere_is_caught_on_the_surfaces_it_reaches(
        self, harness: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(masking_module.Masker, "scrub_secrets", lambda self, text: text)

        found = no_secret_leaked(
            run(harness, "secrets", "secrets-the-model-repeats-what-it-would-know")
        )

        assert any("surface events" in line for line in found), found
        assert any("surface conversation" in line for line in found), found

    def test_the_global_oracles_are_all_quiet_on_a_clean_run(self, harness: Harness) -> None:
        assert global_oracles(run(harness, "explain", "explain-missing-variable")) == []
