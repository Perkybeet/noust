# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A call the assistant proposed is carried out by the person's browser, and the server does not take its word (item 171).

The person approves a proposal by making the call it holds, with their own session and the identifier of the
proposal as ``X-Noust-Request-Id``, and then tells the conversation how it went. The server concedes nothing new:
the call crossed the whole stack as the person's own (sudo mode, approvals, audit), and what it says of the
outcome is checked against the audit trail. A proposal is decided once, and what came of it is told to the model
at its next turn.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.core.exceptions import ValidationError
from noust.web.api import apps as apps_api
from tests.support.assistant_lab import KEY, SHOP, Lab, build_lab
from tests.support.policy_people import Person
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
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


def proposed_restart(lab: Lab) -> tuple[str, dict[str, Any]]:
    lab.configure()
    lab.model.script(Reply.call("restart_application", {"domain": SHOP}), Reply.say("proposed"))
    conversation = lab.conversation()
    turn = lab.ask(conversation, f"restart {SHOP}")
    return conversation, turn["proposals"][0]  # type: ignore[no-any-return]


def carry_out(lab: Lab, proposal: dict[str, Any], **extra: str) -> Any:
    call = proposal["call"]
    return lab.marta.client.request(
        call["method"],
        call["path"],
        json=call["body"],
        headers={**lab.marta.headers(**call["headers"]), **extra},
    )


def outcome(lab: Lab, conversation: str, proposal: dict[str, Any], **body: Any) -> Any:
    return lab.marta.post(
        f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}/outcome",
        {"fingerprint": proposal["fingerprint"], **body},
    )


class TestCarryingItOut:
    def test_the_person_makes_the_call_and_the_audit_trail_proves_it(self, lab: Lab) -> None:
        conversation, proposal = proposed_restart(lab)
        response = carry_out(lab, proposal)
        assert response.status_code == 200, response.text
        assert lab.restarts == [f"restart {SHOP}"]

        reported = outcome(lab, conversation, proposal, status=200)
        assert reported.status_code == 200, reported.text
        body = reported.json()
        assert (body["state"], body["verified"], body["decided_by"]) == ("done", True, "marta")
        assert body["status"] == 200
        # Another tab of the person is told.
        moved = [e for e in lab.events("proposal") if e["proposal"]["state"] == "done"]
        assert moved and moved[-1]["seq"] == 0
        # The audit trail says who approved it and what came of it, never the conversation.
        done = [e for e in lab.audit_events() if e.get("action") == "assistant.outcome"]
        assert done and done[-1]["details"]["state"] == "done" and done[-1]["details"]["verified"]

    def test_what_came_of_it_is_told_to_the_model_once_at_the_next_turn(self, lab: Lab) -> None:
        conversation, proposal = proposed_restart(lab)
        carry_out(lab, proposal)
        outcome(lab, conversation, proposal, status=200)

        lab.model.script(Reply.say("Good."))
        lab.ask(conversation, "did it work?")
        told = lab.requests()[-1].body["messages"][-1]["content"]  # type: ignore[index]
        assert "<proposal-updates>" in told
        assert "Restart shop.example.com: approved and done." in told

        lab.model.script(Reply.say("Fine."))
        lab.ask(conversation, "and now?")
        assert "<proposal-updates>" not in lab.requests()[-1].body["messages"][-1]["content"]  # type: ignore[index]

    def limits_proposal(self, lab: Lab) -> tuple[str, dict[str, Any]]:
        lab.configure()
        lab.model.script(
            Reply.call(
                "call_operation",
                {
                    "method": "PATCH",
                    "path": "/api/apps/{domain}/limits",
                    "parameters": {"domain": SHOP},
                    "body": {"memory_max_mb": 512},
                },
            ),
            Reply.say("proposed"),
        )
        conversation = lab.conversation()
        proposal = lab.ask(conversation, "raise it")["proposals"][0]
        assert proposal["klass"] == "you"
        return conversation, proposal  # type: ignore[no-any-return]

    def test_a_call_that_asked_for_sudo_mode_is_made_in_the_persons_own_sudo_mode(
        self, lab: Lab, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conversation, proposal = self.limits_proposal(lab)
        changed: list[Any] = []

        def raised(domain: str, limits: Any, restart: bool = False) -> Any:
            changed.append((domain, limits.memory_max_mb))
            return SimpleNamespace(
                domain=domain, limits=limits, units=[], restart_required=False, restarted=False
            )

        monkeypatch.setattr(apps_api, "set_resource_limits", raised)
        # The person confirmed it was them (the console's dialog) before the call: that is what the route asks.
        lab.marta.elevate()
        made = carry_out(lab, proposal)
        assert made.status_code == 200, made.text
        assert changed == [(SHOP, 512)]
        done = outcome(lab, conversation, proposal, status=200)
        assert (done.json()["state"], done.json()["verified"]) == ("done", True)

    def test_what_the_api_refused_is_what_the_audit_trail_says_whatever_the_browser_reports(
        self, lab: Lab, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conversation, proposal = self.limits_proposal(lab)

        def refused(domain: str, limits: Any, restart: bool = False) -> Any:
            raise ValidationError("The memory limit is out of range", details="Use 64 to 65536.")

        monkeypatch.setattr(apps_api, "set_resource_limits", refused)
        lab.marta.elevate()
        made = carry_out(lab, proposal)
        assert made.status_code == 400
        # A browser that reports it as a success is told what the audit trail says: it was refused.
        failed = outcome(lab, conversation, proposal, status=200, output=made.text)
        assert failed.status_code == 200, failed.text
        assert (failed.json()["state"], failed.json()["status"]) == ("failed", 400)
        assert "out of range" in failed.json()["output"]


class TestTheServerDoesNotTakeTheBrowsersWord:
    def test_a_proposal_that_was_never_made_is_not_given_as_done(self, lab: Lab) -> None:
        conversation, proposal = proposed_restart(lab)
        response = outcome(lab, conversation, proposal, status=200)
        assert response.status_code == 400
        assert response.json()["fields"]["proposal"]
        assert lab.proposals(conversation)[0].state == "waiting"

    def test_a_call_made_with_another_identifier_is_not_the_proposals(self, lab: Lab) -> None:
        conversation, proposal = proposed_restart(lab)
        call = proposal["call"]
        lab.marta.client.request(
            call["method"],
            call["path"],
            headers=lab.marta.headers(**{"X-Noust-Request-Id": "somebody-elses"}),
        )
        assert outcome(lab, conversation, proposal, status=200).status_code == 400

    def test_it_must_be_the_exact_call(self, lab: Lab) -> None:
        conversation, proposal = proposed_restart(lab)
        carry_out(lab, proposal)
        wrong = lab.marta.post(
            f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}/outcome",
            {"fingerprint": "0" * 64, "status": 200},
        )
        assert wrong.status_code == 400
        assert wrong.json()["fields"]["fingerprint"]

    def test_a_proposal_is_decided_once(self, lab: Lab) -> None:
        conversation, proposal = proposed_restart(lab)
        carry_out(lab, proposal)
        assert outcome(lab, conversation, proposal, status=200).status_code == 200
        again = outcome(lab, conversation, proposal, status=200)
        assert again.status_code == 400
        rejected = lab.marta.post(
            f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}/reject"
        )
        assert rejected.status_code == 400


class TestRejecting:
    def test_nothing_is_done_and_the_model_is_told(self, lab: Lab) -> None:
        conversation, proposal = proposed_restart(lab)
        rejected = lab.marta.post(
            f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}/reject"
        )
        assert rejected.status_code == 200 and rejected.json()["state"] == "rejected"
        assert lab.restarts == []
        lab.model.script(Reply.say("Understood."))
        lab.ask(conversation, "ok, leave it")
        told = lab.requests()[-1].body["messages"][-1]["content"]  # type: ignore[index]
        assert "rejected; nothing was done" in told


class TestItIsTheAuthorsAlone:
    def test_another_account_reads_neither_the_proposal_nor_the_turns(self, lab: Lab) -> None:
        conversation, proposal = proposed_restart(lab)
        other = Person(lab.app, "tomas", "admin")
        for path in (
            f"/api/assistant/conversations/{conversation}/proposals",
            f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}",
            f"/api/assistant/conversations/{conversation}/turns",
        ):
            response = other.get(path)
            assert response.status_code == 404, path
            assert response.json()["error"] == "not_found"
        assert (
            other.post(
                f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}/reject"
            ).status_code
            == 404
        )
        assert (
            other.post(
                f"/api/assistant/conversations/{conversation}/turns", {"message": "hello"}
            ).status_code
            == 404
        )

    def test_the_events_of_a_turn_are_not_for_another_account(self, lab: Lab) -> None:
        proposed_restart(lab)
        from noust.web.events import _frame_audience

        audiences = {_frame_audience(f) for f in lab.frames if f.startswith("event: assistant")}
        assert audiences == {int(lab.marta.account.id)}
        assert lab.marta.account.id != 0


def test_a_conversation_that_is_deleted_takes_its_proposals_and_its_token_with_it(lab: Lab) -> None:
    conversation, proposal = proposed_restart(lab)
    assert lab.marta.client.delete(
        f"/api/assistant/conversations/{conversation}", headers=lab.marta.headers()
    ).json() == {"deleted": True}
    assert (
        lab.marta.get(f"/api/assistant/conversations/{conversation}/proposals").status_code == 404
    )
    assert proposal["id"]
