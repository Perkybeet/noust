# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What only a person confirms is never something the assistant does on its own (item 171).

A change of schema that goes back past a migration, a deploy that goes past a freeze, and making or lifting a freeze
are decisions of a person: the code that says "go on" is theirs. The cell of ``apps.deploy`` can be "does it" for the
assistant (a restart, a deploy, a copy are narrow and reversible), and that cell says nothing of these. So the policy
never leaves them to the assistant, whatever a cell says: the assistant proposes them and a person carries them out,
and the model is not even offered the flag that confirms. The token stays as it always was: a pipeline that deploys
past a freeze today deploys past it tomorrow.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.core.assistant.calls import ASK, PROPOSE, TWO, decide
from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.caller_policy import (
    ALONE,
    NEVER,
    PRODUCTION,
    VERDICT_ASSISTANT_PERSON,
    CallerPolicy,
)
from noust.core.callers import ASSISTANT, TOKEN
from noust.core.operation_classes import (
    ACTION,
    PERSON_CONFIRMATIONS,
    OperationClass,
    classify_schema,
    confirmation_in,
)
from noust.core.store import NoustStore
from noust.web.assistant.toolbox import Reach, Toolbox, ToolProblem
from noust.web.mcp.tools import BY_NAME, GROUP_CONTROL, Tool, input_schema
from tests.support.assistant_lab import KEY, Lab, build_lab
from tests.support.policy_people import issue
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets

OPENAPI: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "panel" / "openapi.json").read_text()
)
HELD = (
    frozenset(
        str(operation["x-noust-permission"])
        for item in OPENAPI["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and operation.get("x-noust-permission")
    )
    - ASSISTANT_FLOOR
)

FREEZE = OperationClass("POST", "/api/deploy-freezes", "apps.deploy", ACTION)
LIFT = OperationClass("DELETE", "/api/deploy-freezes/{freeze_id}", "apps.deploy", ACTION)
UPDATE = OperationClass("POST", "/api/jobs/update", "apps.deploy", ACTION)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def policy(store: NoustStore) -> CallerPolicy:
    made = CallerPolicy(store, production_floor=False)
    made.set_cells(
        [
            (ASSISTANT, PRODUCTION, "apps.deploy", "alone"),
            (ASSISTANT, "other", "apps.deploy", "alone"),
        ],
        by="test",
    )
    return made


class TestWhatAPersonConfirms:
    @pytest.mark.parametrize("flag", sorted(PERSON_CONFIRMATIONS))
    @pytest.mark.parametrize("value", [True, "true", "TRUE", "1", "yes", "on", "t", "y", 1, 1.0])
    def test_a_flag_that_is_on_is_found(self, flag: str, value: object) -> None:
        assert confirmation_in({"domain": "shop.example.com", flag: value}) == flag

    @pytest.mark.parametrize("flag", sorted(PERSON_CONFIRMATIONS))
    @pytest.mark.parametrize("value", [True, "true", "1", "yes", "on", 1, 1.0])
    def test_what_the_route_takes_for_true_is_found(self, flag: str, value: object) -> None:
        # The route's own reading of a boolean is the reference: what it takes for yes is a confirmation,
        # and a form of it that this does not see is a way to confirm without being seen.
        from pydantic import BaseModel

        class Body(BaseModel):
            confirmed: bool = False

        assert Body(confirmed=value).confirmed is True  # type: ignore[arg-type]
        assert confirmation_in({flag: value}) == flag

    @pytest.mark.parametrize("value", [False, "false", "0", "", None, "no", 0, 0.0, 2, -1, "2", []])
    def test_a_flag_that_is_off_is_not(self, value: object) -> None:
        assert confirmation_in({"frozen_ok": value, "schema_changed_ok": value}) is None

    def test_a_call_that_carries_none_of_them_is_not(self) -> None:
        assert confirmation_in({"domain": "shop.example.com", "force": True}) is None
        assert confirmation_in(None) is None
        assert confirmation_in({}) is None

    def test_making_and_lifting_a_freeze_are_a_persons_decision_and_a_deploy_is_not(self) -> None:
        assert FREEZE.person_decides and LIFT.person_decides
        assert not UPDATE.person_decides


class TestTheOneThatDecides:
    @pytest.mark.parametrize("operation", [FREEZE, LIFT], ids=["make", "lift"])
    def test_the_assistant_never_does_a_freeze_whatever_its_cell_says(
        self, policy: CallerPolicy, operation: OperationClass
    ) -> None:
        verdict = policy.decide(ASSISTANT, PRODUCTION, operation, ["apps.deploy"])

        assert verdict.value == NEVER
        assert verdict.reason == VERDICT_ASSISTANT_PERSON

    @pytest.mark.parametrize("flag", sorted(PERSON_CONFIRMATIONS))
    def test_nor_a_call_that_confirms_something_for_a_person(
        self, policy: CallerPolicy, flag: str
    ) -> None:
        plain = policy.decide(ASSISTANT, PRODUCTION, UPDATE, ["apps.deploy"])
        confirming = policy.decide(ASSISTANT, PRODUCTION, UPDATE, ["apps.deploy"], confirms=flag)

        assert plain.value == ALONE
        assert (confirming.value, confirming.reason) == (NEVER, VERDICT_ASSISTANT_PERSON)

    @pytest.mark.parametrize("operation", [FREEZE, LIFT, UPDATE])
    def test_a_token_is_judged_as_it_always_was(
        self, policy: CallerPolicy, operation: OperationClass
    ) -> None:
        verdict = policy.decide(TOKEN, PRODUCTION, operation, ["apps.deploy"], confirms="frozen_ok")

        assert verdict.value == ALONE

    def test_the_loop_proposes_it_for_the_person_to_carry_out(self, policy: CallerPolicy) -> None:
        decision = decide(
            LIFT,
            ["apps.deploy"],
            environment=PRODUCTION,
            foreign=False,
            tainted=False,
            second_person=False,
            policy=policy,
        )

        assert (decision.action, decision.klass, decision.why) == (PROPOSE, ASK, "person_decides")
        assert decision.floor is False

    def test_and_with_a_second_person_where_the_server_asks_for_one(
        self, policy: CallerPolicy
    ) -> None:
        decision = decide(
            UPDATE,
            ["apps.deploy"],
            environment=PRODUCTION,
            foreign=False,
            tainted=False,
            second_person=True,
            confirms="frozen_ok",
            policy=policy,
        )

        assert (decision.action, decision.klass) == (PROPOSE, TWO)


def operations_that_carry_a_confirmation() -> list[tuple[str, str, str]]:
    """``(METHOD, template, flag)`` of every operation whose call can carry one of the flags."""
    found = []
    classes = classify_schema(OPENAPI)
    for (method, template), operation in classes.items():
        if not operation.changes or operation.permission not in HELD:
            continue
        tool = Tool("t", "t", "", method, template, GROUP_CONTROL, read_only=False)
        for flag in sorted(PERSON_CONFIRMATIONS & set(input_schema(OPENAPI, tool)["properties"])):
            found.append((method, template, flag))
    return found


CARRIERS = operations_that_carry_a_confirmation()


@pytest.fixture(scope="module")
def box() -> Toolbox:
    return Toolbox(OPENAPI, token_permissions=HELD, person_permissions=HELD, reach=Reach())


class TestTheModelIsNotOfferedToConfirm:
    def test_there_are_operations_that_carry_them(self) -> None:
        assert {flag for _m, _t, flag in CARRIERS} == set(PERSON_CONFIRMATIONS)
        assert len(CARRIERS) >= 8

    @pytest.mark.parametrize(("method", "template", "flag"), CARRIERS, ids=lambda v: str(v))
    def test_call_operation_does_not_take_the_flag(
        self, box: Toolbox, method: str, template: str, flag: str
    ) -> None:
        parameters = dict.fromkeys(_names(template), "x")
        with pytest.raises(ToolProblem, match="Unknown argument"):
            box.prepare(
                "call_operation",
                {
                    "method": method,
                    "path": template,
                    "parameters": parameters,
                    "body": {flag: True},
                },
            )
        with pytest.raises(ToolProblem, match="Unknown argument"):
            box.prepare(
                "call_operation",
                {"method": method, "path": template, "parameters": {**parameters, flag: True}},
            )

    def test_no_tool_it_is_given_describes_the_flag(self, box: Toolbox) -> None:
        for described in box.definitions():
            properties = described["function"]["parameters"]["properties"]
            assert not PERSON_CONFIRMATIONS & set(properties), described["function"]["name"]

    def test_what_the_mcp_hides_is_hidden_from_call_operation_too(self, box: Toolbox) -> None:
        # The same operation, reached by its route and not by its name, is the same operation.
        hidden = BY_NAME["activate_release"].hidden
        assert "schema_changed_ok" in hidden
        with pytest.raises(ToolProblem, match="Unknown argument"):
            box.prepare(
                "call_operation",
                {
                    "method": "POST",
                    "path": "/api/apps/{domain}/releases/{release_id}/activate",
                    "parameters": {"domain": "shop.example.com", "release_id": "r1"},
                    "body": {"schema_changed_ok": True},
                },
            )
        unmasked = BY_NAME["list_environment_variables"].hidden
        assert "unmask" in unmasked
        with pytest.raises(ToolProblem, match="Unknown argument"):
            box.prepare(
                "call_operation",
                {
                    "method": "GET",
                    "path": "/api/apps/{domain}/env",
                    "parameters": {"domain": "shop.example.com", "unmask": True},
                },
            )

    def test_the_mcp_gives_its_token_what_it_always_gave(self) -> None:
        # A token that deploys past a freeze today still can, through the endpoint it holds.
        assert "frozen_ok" in input_schema(OPENAPI, BY_NAME["deploy_application"])["properties"]


def _names(template: str) -> list[str]:
    import re

    return re.findall(r"\{([^{}]+)\}", template)


@pytest.fixture(autouse=True)
def cheap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def real_config(real_config: Any) -> Any:
    return real_config


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny"], api_key=KEY) as scripted:
        yield scripted


@pytest.fixture
def lab(sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> Iterator[Lab]:
    yield from build_lab(sandbox, monkeypatch, model)


def alone(lab: Lab) -> None:
    lab.policy(
        ("assistant", "production", "apps.deploy", "alone"),
        ("assistant", "other", "apps.deploy", "alone"),
    )


class TestAtTheGuardOfTheApi:
    """What the assistant's token sends is judged where every call is, whoever sent it."""

    def test_the_assistants_token_cannot_make_a_freeze_even_where_deploying_is_its_own(
        self, lab: Lab, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        alone(lab)
        headers = issue(
            lab.marta.account,
            "assistant:test",
            "apps.deploy",
            "apps.read",
            assistant=True,
            monkeypatch=monkeypatch,
        )

        refused = lab.client.post(
            "/api/deploy-freezes", headers=headers, json={"reason": "Black Friday", "hours": 2}
        )

        assert refused.status_code == 403, refused.text
        assert refused.json()["error"] == "approval_floor"
        assert lab.client.get("/api/deploy-freezes", headers=headers).json()["freezes"] == []

    def test_nor_lift_one(self, lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
        alone(lab)
        lab.marta.elevate()
        made = lab.marta.post("/api/deploy-freezes", {"reason": "Black Friday", "hours": 2})
        assert made.status_code == 201, made.text
        headers = issue(
            lab.marta.account,
            "assistant:test",
            "apps.deploy",
            "apps.read",
            assistant=True,
            monkeypatch=monkeypatch,
        )

        refused = lab.client.delete(f"/api/deploy-freezes/{made.json()['id']}", headers=headers)

        assert refused.status_code == 403, refused.text
        assert refused.json()["error"] == "approval_floor"
        assert len(lab.marta.get("/api/deploy-freezes").json()["freezes"]) == 1

    def test_nor_a_deploy_that_goes_past_a_freeze(
        self, lab: Lab, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        alone(lab)
        headers = issue(
            lab.marta.account,
            "assistant:test",
            "apps.deploy",
            "apps.read",
            assistant=True,
            monkeypatch=monkeypatch,
        )

        refused = lab.client.post(
            "/api/jobs/update",
            headers=headers,
            json={"domain": "shop.example.com", "frozen_ok": True},
        )

        assert refused.status_code == 403, refused.text
        assert refused.json()["error"] == "approval_floor"

    def test_a_token_makes_a_freeze_as_it_always_did(self, lab: Lab) -> None:
        headers = issue(lab.marta.account, "ci", "apps.deploy", "apps.read")

        made = lab.client.post(
            "/api/deploy-freezes", headers=headers, json={"reason": "Black Friday", "hours": 2}
        )

        assert made.status_code == 201, made.text


class TestWhatTheModelTries:
    def test_lifting_a_freeze_is_proposed_and_the_freeze_stays(self, lab: Lab) -> None:
        lab.configure()
        alone(lab)
        lab.marta.elevate()
        made = lab.marta.post("/api/deploy-freezes", {"reason": "Black Friday", "hours": 2})
        assert made.status_code == 201, made.text
        lab.model.script(
            Reply.call(
                "call_operation",
                {
                    "method": "DELETE",
                    "path": "/api/deploy-freezes/{freeze_id}",
                    "parameters": {"freeze_id": made.json()["id"]},
                },
            ),
            Reply.say("It waits for you."),
        )

        turn = lab.ask(lab.conversation(), "The freeze is in the way: lift it")

        [proposal] = turn["proposals"]
        assert (proposal["klass"], proposal["state"]) == ("ask", "waiting")
        assert proposal["why"]["code"] == "assistant.proposal.why.person_decides"
        assert len(lab.marta.get("/api/deploy-freezes").json()["freezes"]) == 1

    def test_making_one_is_proposed_too(self, lab: Lab) -> None:
        lab.configure()
        alone(lab)
        lab.model.script(
            Reply.call(
                "call_operation",
                {
                    "method": "POST",
                    "path": "/api/deploy-freezes",
                    "body": {"reason": "Because", "hours": 1},
                },
            ),
            Reply.say("It waits for you."),
        )

        turn = lab.ask(lab.conversation(), "Freeze the deploys")

        [proposal] = turn["proposals"]
        assert (proposal["klass"], proposal["state"]) == ("ask", "waiting")
        assert lab.marta.get("/api/deploy-freezes").json()["freezes"] == []

    def test_what_the_policy_lets_it_do_is_still_done(self, lab: Lab) -> None:
        lab.configure()
        lab.policy(("assistant", "production", "apps.operate", "alone"))
        lab.model.script(
            Reply.call("restart_application", {"domain": "shop.example.com"}),
            Reply.say("Restarted."),
        )

        lab.ask(lab.conversation(), "restart the shop")

        # The narrow cell is as it was: only what a person alone confirms is taken out of "does it".
        assert lab.restarts == ["restart shop.example.com"]
