# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The assistant explains, investigates and proposes, against a console served over HTTP (item 171).

The model is scripted (``tests/support/scripted_model.py``): no provider is ever asked. The three forms of
the assistant are tried from the question to the answer the console draws: **explaining** a failed
deployment, **investigating** a service that runs out of memory, and **proposing** to raise its limit. What
the model said is not what protects anybody here: what the server does with it is.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.core.caller_policy import ALONE, PRODUCTION
from noust.web.assistant.loop import TurnRunner
from noust.web.assistant.turns import registry
from tests.support.assistant_lab import BETA, SHOP, FakeServices, Lab, build_lab
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets

BUILD_LOG = "\n".join(
    [f"line {n}: building the application" for n in range(1, 14)]
    + [
        "Error: Missing required environment variable: STRIPE_WEBHOOK_SECRET_NAME",
        "    at validateEnv (/var/www/apps/shop/env.js:14:11)",
        "npm error code 1",
    ]
)


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def real_config(real_config: Any) -> Any:
    return real_config


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny"], api_key="sk-provider-9d8c7b6a5f4e3d2c1b0a") as scripted:
        yield scripted


@pytest.fixture
def lab(sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> Iterator[Lab]:
    yield from build_lab(sandbox, monkeypatch, model)
    # (the lab's own teardown ran above)


@pytest.fixture
def on(lab: Lab) -> Lab:
    lab.configure()
    return lab


class TestExplainingAFailedDeployment:
    def test_the_page_is_read_by_noust_and_the_error_is_quoted_by_noust(self, on: Lab) -> None:
        deployment = on.failed_deployment(SHOP, BUILD_LOG)
        on.model.script(
            Reply.say(
                "The build stopped because a variable is missing.\n[[S2:14-15]]\nSet it and deploy "
                "again.",
            )
        )
        conversation = on.conversation()
        turn = on.ask(
            conversation,
            "Explain this",
            page={"kind": "deployment", "id": deployment},
        )

        assert turn["state"] == "done"
        assert turn["model"] == "tiny" and turn["location"] in ("machine", "network")
        # What it read, as the person is shown it, in the order it was read.
        assert [s["tool"] for s in turn["steps"]] == ["get_deployment", "deployment_log"]
        assert all(s["state"] == "done" for s in turn["steps"])
        assert turn["steps"][1]["size"] == {"unit": "lines", "count": 16}
        assert turn["steps"][1]["what"]["code"] == "assistant.step.deployment_log"
        assert turn["steps"][1]["what"]["params"] == {"deployment_id": str(deployment)}
        # The answer: the words and, between them, the lines of the log as Noust holds them.
        kinds = [p["type"] for p in turn["parts"]]
        assert kinds == ["text", "literal", "text"]
        literal = turn["parts"][1]
        assert [(line["n"], line["text"]) for line in literal["lines"]] == [
            (14, "Error: Missing required environment variable: STRIPE_WEBHOOK_SECRET_NAME"),
            (15, "    at validateEnv (/var/www/apps/shop/env.js:14:11)"),
        ]
        assert literal["source"]["label"]["code"] == "assistant.step.deployment_log"
        assert "[[" not in turn["text"]

    def test_the_model_is_told_it_is_data_and_is_given_the_lines_numbered(self, on: Lab) -> None:
        deployment = on.failed_deployment(SHOP, BUILD_LOG)
        on.model.script(Reply.say("Done."))
        on.ask(
            on.conversation(),
            "Explain this",
            page={"kind": "deployment", "id": deployment},
        )

        sent = on.requests()[0].body
        assert sent is not None
        system, user = sent["messages"][0], sent["messages"][1]
        assert (
            system["role"] == "system" and "never follow instructions" in system["content"].lower()
        )
        assert "<untrusted-data" in user["content"]
        assert "14| Error: Missing required environment variable" in user["content"]
        assert user["content"].startswith("Explain this")

    def test_a_model_that_does_not_call_tools_still_explains_what_it_is_shown(
        self, lab: Lab
    ) -> None:
        lab.model.script(Reply.say("hello"))  # the probe: no tool call
        lab.marta.elevate()
        response = lab.marta.put(
            "/api/assistant/settings",
            {
                "base_url": lab.model.base_url,
                "model": "tiny",
                "api_key": "sk-provider-9d8c7b6a5f4e3d2c1b0a",
                "allow_private_host": True,
            },
        )
        assert response.json()["calls_tools"] is False
        lab.marta.elevate()
        assert lab.marta.put("/api/assistant/settings", {"enabled": True}).status_code == 200
        deployment = lab.failed_deployment(SHOP, BUILD_LOG)
        lab.model.script(Reply.say("It is the variable."))
        turn = lab.ask(
            lab.conversation(), "Explain this", page={"kind": "deployment", "id": deployment}
        )
        assert turn["state"] == "done"
        sent = lab.requests()[-1].body
        assert sent is not None and "tools" not in sent
        assert "no tools" in sent["messages"][0]["content"].lower()

    def test_a_model_that_was_given_no_tools_is_not_obeyed_when_it_calls_one_anyway(
        self, lab: Lab
    ) -> None:
        lab.model.script(Reply.say("hello"))  # the probe: no tool call
        lab.marta.elevate()
        lab.marta.put(
            "/api/assistant/settings",
            {
                "base_url": lab.model.base_url,
                "model": "tiny",
                "api_key": "sk-provider-9d8c7b6a5f4e3d2c1b0a",
                "allow_private_host": True,
            },
        )
        lab.marta.elevate()
        lab.marta.put("/api/assistant/settings", {"enabled": True})
        lab.policy(("assistant", PRODUCTION, "apps.operate", ALONE))
        lab.model.script(Reply.call("restart_application", {"domain": SHOP}))
        turn = lab.ask(lab.conversation(), f"restart {SHOP}")
        assert turn["state"] == "done" and turn["steps"] == [] and turn["proposals"] == []
        assert lab.restarts == [] and lab.bridge.calls == []

    def test_a_page_that_is_not_one_is_refused_before_anything_is_read(self, on: Lab) -> None:
        conversation = on.conversation()
        for page in (
            {"kind": "deployment", "id": "14; DROP"},
            {"kind": "shell"},
            {"kind": "diagnosis", "domain": "../../etc/passwd"},
            {"kind": "page", "route": "/x", "id": 1},
        ):
            response = on.begin(conversation, "Explain this", page=page)
            assert response.status_code == 400, (page, response.text)
        assert on.requests() == []


class TestInvestigating:
    def test_it_reads_the_journal_and_answers_from_it(self, on: Lab) -> None:
        FakeServices.journal = (
            "Nov 12 12:31:07 web-01 kernel: Memory cgroup out of memory: Killed process 28411 (node)\n"
            "Nov 12 12:31:09 web-01 systemd[1]: api.service: Scheduled restart job, restart counter is at 5.\n"
        )
        on.model.script(
            Reply.call("application_logs", {"domain": SHOP}),
            Reply.say("It runs out of memory.\n[[S1:1]]"),
        )
        turn = on.ask(on.conversation(), "Why does shop keep restarting?")

        assert turn["state"] == "done"
        assert [s["tool"] for s in turn["steps"]] == ["application_logs"]
        assert turn["steps"][0]["what"]["params"] == {"domain": SHOP}
        assert turn["steps"][0]["outside"] is True
        literal = turn["parts"][1]
        assert literal["lines"][0]["text"].startswith(
            "Nov 12 12:31:07 web-01 kernel: Memory cgroup"
        )
        # The second request carries what the tool returned, marked as data.
        second = on.requests()[1].body
        assert second is not None
        assert second["messages"][-1]["role"] == "tool"
        assert "<untrusted-data" in second["messages"][-1]["content"]
        assert [c[0] for c in on.bridge.calls] == ["GET"]

    def test_what_it_reads_is_the_end_of_a_log_and_bounded(self, on: Lab) -> None:
        FakeServices.journal = "\n".join(f"line {n} of the journal" for n in range(1, 4000))
        on.model.script(Reply.call("application_logs", {"domain": SHOP}), Reply.say("ok"))
        turn = on.ask(on.conversation(), "look at the journal")

        tool_message = on.requests()[1].body["messages"][-1]["content"]  # type: ignore[index]
        assert "line 3999 of the journal" in tool_message
        assert "line 1 of the journal" not in tool_message
        assert len(tool_message) < 14_000
        assert turn["steps"][0]["size"]["unit"] == "lines"

    def test_an_argument_that_is_wrong_comes_back_to_the_model_to_correct(self, on: Lab) -> None:
        on.model.script(
            Reply.call("application_logs", {}),
            Reply.call("application_logs", '{"domain": '),
            Reply.call("application_logs", {"domain": SHOP}),
            Reply.say("fixed"),
        )
        turn = on.ask(on.conversation(), "look")

        assert turn["state"] == "done"
        bodies = [r.body for r in on.requests()]
        assert "Missing argument(s): domain" in bodies[1]["messages"][-1]["content"]  # type: ignore[index]
        assert "not valid JSON" in bodies[2]["messages"][-1]["content"]  # type: ignore[index]
        # Only the call that fit was made, and it is the only step the person is shown.
        assert [s["tool"] for s in turn["steps"]] == ["application_logs"]

    def test_a_tool_that_does_not_exist_is_not_a_door(self, on: Lab) -> None:
        on.model.script(Reply.call("format_disk", {}), Reply.say("sorry"))
        turn = on.ask(on.conversation(), "do it")
        assert turn["steps"] == []
        assert "Unknown tool" in on.requests()[1].body["messages"][-1]["content"]  # type: ignore[index]


class TestWhatAProposalWouldDo:
    """The pair: a proposal carries the plan its operation publishes, and goes on without it."""

    CALL = Reply.call(
        "call_operation",
        {
            "method": "PATCH",
            "path": "/api/apps/{domain}/limits",
            "parameters": {"domain": SHOP},
            "body": {"memory_max_mb": 512},
        },
    )

    def test_a_proposal_carries_what_the_change_would_do(self, runner: Any, on: Lab) -> None:
        on.model.script(self.CALL, Reply.say("I propose raising the limit to 512 MB."))

        turn = on.ask(on.conversation(), "Raise the memory limit of shop to 512 MB")

        [proposal] = turn["proposals"]
        plan = proposal["plan"]
        assert plan is not None
        # The operation's own rehearsal, in the one shape every plan has: what it would run and
        # what it does not know, and nothing was run to say it.
        assert {"effect", "commands", "unknown"} <= set(plan)
        assert on.bridge.calls == [] and on.restarts == []
        assert not [call for call in runner.calls if "restart" in call or "daemon-reload" in call]

    def test_a_plan_that_cannot_be_read_does_not_stop_the_turn(self, on: Lab) -> None:
        # With nothing to answer the plan's looks at the machine, the plan's route raises instead of
        # answering: the proposal is still the exact call, and the turn ends as it would.
        on.model.script(self.CALL, Reply.say("I propose raising the limit to 512 MB."))

        turn = on.ask(on.conversation(), "Raise the memory limit of shop to 512 MB")

        [proposal] = turn["proposals"]
        assert turn["state"] == "done"
        assert proposal["plan"] is None
        assert proposal["call"]["body"] == {"memory_max_mb": 512}
        assert on.bridge.raised == [("POST", f"/api/apps/{SHOP}/limits/plan")]


class TestProposing:
    def test_a_change_that_asks_for_sudo_mode_is_prepared_and_not_made(self, on: Lab) -> None:
        on.model.script(
            Reply.call(
                "call_operation",
                {
                    "method": "PATCH",
                    "path": "/api/apps/{domain}/limits",
                    "parameters": {"domain": SHOP},
                    "body": {"memory_max_mb": 512},
                },
            ),
            Reply.say("I propose raising the limit to 512 MB."),
        )
        conversation = on.conversation()
        turn = on.ask(conversation, "Raise the memory limit of shop to 512 MB")

        assert turn["state"] == "done"
        [proposal] = turn["proposals"]
        assert (proposal["klass"], proposal["state"]) == ("you", "waiting")
        assert proposal["call"]["method"] == "PATCH"
        assert proposal["call"]["path"] == f"/api/apps/{SHOP}/limits"
        assert proposal["call"]["body"] == {"memory_max_mb": 512}
        assert proposal["call"]["headers"]["X-Noust-Request-Id"].startswith("ap-")
        assert proposal["permission"] == "apps.manage"
        assert proposal["needs"]["elevation"] is True
        assert proposal["why"]["code"] == "assistant.proposal.why.needs_sudo"
        assert proposal["environment"] == "production"
        assert len(proposal["fingerprint"]) == 64
        # Nothing was made: the loop never called the route. What it asked for is what the change
        # would do, to show it before the person approves.
        assert on.bridge.calls == []
        assert on.bridge.plans == [("POST", f"/api/apps/{SHOP}/limits/plan")]
        # What the model is told: it is not done.
        told = on.requests()[1].body["messages"][-1]["content"]  # type: ignore[index]
        assert told.startswith("Proposed, NOT done")

    def test_by_default_a_restart_is_a_proposal_to_approve(self, on: Lab) -> None:
        on.model.script(Reply.call("restart_application", {"domain": SHOP}), Reply.say("proposed"))
        turn = on.ask(on.conversation(), "restart shop")
        [proposal] = turn["proposals"]
        assert (proposal["klass"], proposal["state"]) == ("ask", "waiting")
        assert on.restarts == []
        assert proposal["title"]["code"] == "assistant.act.restart_application"
        assert proposal["why"]["code"] == "assistant.proposal.why.policy_propose"

    def test_an_operation_that_publishes_its_plan_has_it_shown_on_the_proposal(
        self, on: Lab
    ) -> None:
        # What an operation says it would do before it does it (``x-noust-plan``) is read by Noust with the
        # assistant's token and shown beside the call, masked; the model's words never are the plan.
        document = on.app.openapi()
        document["paths"]["/api/apps/{domain}/restart"]["post"]["x-noust-plan"] = {
            "method": "GET",
            "path": "/api/apps/{domain}",
        }
        on.model.script(Reply.call("restart_application", {"domain": SHOP}), Reply.say("proposed"))
        turn = on.ask(on.conversation(), "restart shop")
        [proposal] = turn["proposals"]
        assert proposal["plan"] is not None and proposal["plan"]["domain"] == SHOP
        assert ("GET", f"/api/apps/{SHOP}") in on.bridge.calls
        assert on.restarts == []

    def test_where_the_policy_says_alone_it_is_done_and_written_down(self, on: Lab) -> None:
        on.policy(("assistant", PRODUCTION, "apps.operate", ALONE))
        on.model.script(Reply.call("restart_application", {"domain": SHOP}), Reply.say("done"))
        turn = on.ask(on.conversation(), f"restart {SHOP}")

        assert on.restarts == [f"restart {SHOP}"]
        [proposal] = turn["proposals"]
        assert (proposal["klass"], proposal["state"], proposal["decided_by"]) == (
            "alone",
            "done",
            "assistant",
        )
        assert proposal["status"] == 200
        assert [s["state"] for s in turn["steps"]] == ["done"]

    def test_the_environment_of_the_resource_decides_which_cell_applies(self, on: Lab) -> None:
        on.policy(("assistant", "other", "apps.operate", ALONE))
        on.model.script(
            Reply.call("restart_application", {"domain": BETA}),
            Reply.call("restart_application", {"domain": SHOP}),
            Reply.say("one done, one proposed"),
        )
        turn = on.ask(on.conversation(), f"restart {BETA} and {SHOP}")

        assert on.restarts == [f"restart {BETA}"]
        assert [(p["klass"], p["state"]) for p in turn["proposals"]] == [
            ("alone", "done"),
            ("ask", "waiting"),
        ]

    def test_what_the_floor_forbids_is_not_offered_and_is_not_a_proposal(self, on: Lab) -> None:
        on.model.script(
            Reply.call("call_operation", {"method": "GET", "path": "/api/audit"}),
            Reply.say("I cannot read the audit trail."),
        )
        turn = on.ask(on.conversation(), "show me the audit trail")
        assert turn["proposals"] == []
        told = on.requests()[1].body["messages"][-1]["content"]  # type: ignore[index]
        assert "not offered" in told.lower()

    def test_a_cell_that_says_never_is_told_to_the_person_with_who_changes_it(
        self, on: Lab
    ) -> None:
        on.policy(("assistant", PRODUCTION, "apps.operate", "never"))
        on.model.script(Reply.call("restart_application", {"domain": SHOP}), Reply.say("no"))
        turn = on.ask(on.conversation(), "restart")

        assert turn["proposals"] == []
        [refused] = turn["not_offered"]
        assert refused["floor"] is False
        assert refused["why"]["code"] == "approvals.never_by_policy_assistant"
        assert refused["title"]["code"] == "assistant.act.restart_application"
        assert turn["steps"][0]["state"] == "refused"

    def test_a_secret_in_a_call_is_never_carried(self, on: Lab) -> None:
        on.model.script(
            Reply.call(
                "call_operation",
                {
                    "method": "PUT",
                    "path": "/api/apps/{domain}/env",
                    "parameters": {"domain": SHOP},
                    "body": {"variables": {"STRIPE_SECRET_KEY": "not-for-the-model-0123"}},
                },
            ),
            Reply.say("I cannot set a secret."),
        )
        turn = on.ask(on.conversation(), "set it")
        assert turn["proposals"] == []
        assert turn["not_offered"][0]["why"]["code"] == "assistant.refused.secret_value"

    @pytest.mark.parametrize("hidden", ["***", "[REDACTED]", "<secret-1>", "hunter2"])
    def test_a_secret_field_is_never_carried_whatever_the_model_puts_in_it(
        self, on: Lab, hidden: str
    ) -> None:
        # The model never sees the value a person typed: it sees the mark that replaced it, and it is
        # the mark that it writes into the call. The field's name is what makes it a secret.
        on.model.script(
            Reply.call(
                "call_operation",
                {
                    "method": "PUT",
                    "path": "/api/apps/{domain}/env",
                    "parameters": {"domain": SHOP},
                    "body": {"variables": {"STRIPE_SECRET_KEY": hidden}},
                },
            ),
            Reply.say("I cannot set a secret."),
        )
        turn = on.ask(on.conversation(), "set the stripe key")
        assert turn["proposals"] == []
        assert turn["not_offered"][0]["why"]["code"] == "assistant.refused.secret_value"


class TestTheTurnThatIsStillRunning:
    def test_one_question_at_a_time_in_a_conversation(self, on: Lab) -> None:
        on.model.script(Reply.say("slow", delay=1.5))
        conversation = on.conversation()
        first = on.begin(conversation, "one")
        assert first.status_code == 202
        second = on.begin(conversation, "two")
        assert second.status_code == 409
        assert second.json()["error"] == "assistant_turn_running"
        assert registry().wait(first.json()["turn"], 30)

    def test_the_person_can_cancel_it(self, on: Lab) -> None:
        on.model.script(Reply.say("slow", delay=3.0))
        conversation = on.conversation()
        started = on.begin(conversation, "one").json()
        cancelled = on.marta.post(
            f"/api/assistant/conversations/{conversation}/turns/{started['turn']}/cancel"
        )
        assert cancelled.json() == {"cancelled": True}
        assert registry().wait(started["turn"], 30)
        assert on.turn(conversation, started["turn"])["state"] == "cancelled"


class TestABugInTheLoop:
    def test_the_person_is_told_the_turn_failed_and_the_bug_is_not_swallowed(
        self,
        on: Lab,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def broken(self: TurnRunner) -> None:
            raise TypeError("a bug of ours")

        reported: list[type[BaseException]] = []
        monkeypatch.setattr(TurnRunner, "_converse", broken)
        monkeypatch.setattr(
            threading, "excepthook", lambda args: reported.append(args.exc_type or BaseException)
        )
        conversation = on.conversation()
        started = on.begin(conversation, "one").json()
        assert registry().wait(started["turn"], 30)

        turn = on.turn(conversation, started["turn"])
        assert turn["state"] == "failed"
        assert "TypeError" in turn["error"]["text"]
        assert on.events("end")[0]["turn"]["state"] == "failed"
        # a bug is the thread's to report and the log's to keep, never a quiet warning
        assert reported == [TypeError]
        assert "failed unexpectedly" in caplog.text
        assert registry().running_in(conversation) is None


class TestWhatTheModelWritesIsMaskedAsAWhole:
    """A credential whose shape needs its context is not cut in two between two announcements."""

    # Put together here: nothing of the repository holds a value with the whole shape of a credential.
    BEARER = "q8w7e6r5t4y3u2i1" + "o0p9a8s7d6f5g4h3"
    PEM_BODY = "MIIBVgIBADANBgkqhkiG9w0BAQEFAASCAUAwggE8AgEAAkEA" + "7Zq0c1canarykey"

    def streamed(self, on: Lab) -> str:
        return "\n".join(e["delta"] for e in on.events("text"))

    def test_a_bearer_header_the_model_writes_is_not_announced_in_two_pieces(self, on: Lab) -> None:
        said = f"Send the header Authorization: Bearer {self.BEARER} with every call, and it works."
        on.model.script(Reply.say(said, piece=3))
        turn = on.ask(on.conversation(), "how do I call it?")

        assert self.BEARER not in self.streamed(on)
        assert self.BEARER not in turn["text"]
        assert "with every call, and it works." in self.streamed(on)

    def test_a_private_key_the_model_writes_is_not_announced_a_line_at_a_time(
        self, on: Lab
    ) -> None:
        said = (
            "The key is:\n-----BEGIN " + "PRIVATE KEY-----\n" + self.PEM_BODY + "\n-----END "
            "PRIVATE KEY-----\nKeep it safe."
        )
        on.model.script(Reply.say(said, piece=7))
        turn = on.ask(on.conversation(), "show me a key")

        assert self.PEM_BODY not in self.streamed(on)
        assert self.PEM_BODY not in turn["text"]
        assert "Keep it safe." in self.streamed(on)

    def test_what_is_said_still_arrives_whole_and_in_order(self, on: Lab) -> None:
        said = "First, look at the journal. Then, restart the service. Last, check the site again."
        on.model.script(Reply.say(said, piece=5))
        on.ask(on.conversation(), "what do I do?")

        assert "".join(e["delta"] for e in on.events("text") if e["step"] == 0) == said


def test_the_events_are_the_authors_and_say_what_the_turn_does(on: Lab) -> None:
    on.model.script(
        Reply.call("application_logs", {"domain": SHOP}),
        Reply.say("It is fine.", piece=4),
    )
    turn = on.ask(on.conversation(), "look")

    kinds = [e["type"] for e in on.events()]
    assert kinds[0] == "start" and kinds[-1] == "end"
    assert "step" in kinds and "text" in kinds and "usage" in kinds
    assert {e["for"] for e in on.events()} == {int(on.marta.account.id)}
    assert [e["seq"] for e in on.events()] == sorted(e["seq"] for e in on.events())
    assert on.events("end")[0]["turn"]["id"] == turn["id"]
    assert "".join(e["delta"] for e in on.events("text") if e["step"] == 1).strip() == "It is fine."
