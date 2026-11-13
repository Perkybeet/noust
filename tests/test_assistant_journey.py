# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A person's whole way through the assistant, from "why does it keep restarting" to the limit raised (item 171).

One conversation against the served console, with a scripted model: the assistant investigates a service that
runs out of memory (it reads the journal and answers from it, quoting the kernel's line as the system wrote
it), proposes raising its memory limit (a change that asks for sudo mode, so the person does it with their own
session), and the person confirms it is them, carries the call out and tells the conversation. What came of it
reaches the model at the next question. Beside it, the same conversation on an alert page.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.web.api import apps as apps_api
from tests.support.assistant_lab import KEY, SHOP, FakeServices, Lab, build_lab
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets

KERNEL = "Nov 12 12:31:07 web-01 kernel: Memory cgroup out of memory: Killed process 28411 (node)"


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


def test_investigate_a_service_that_runs_out_of_memory_and_raise_its_limit(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    lab.configure()
    FakeServices.journal = f"listening on 3000\n{KERNEL}\nrestart counter is at 5\n"

    # 1. Investigating: it reads, answers and quotes the kernel's line from Noust's own copy of the journal.
    lab.model.script(
        Reply.call("application_logs", {"domain": SHOP}),
        Reply.say("It runs out of memory:\n[[S1:2]]\nA higher limit eases it."),
    )
    conversation = lab.conversation()
    first = lab.ask(conversation, "Why does the shop keep restarting?")
    assert first["state"] == "done"
    literal = next(p for p in first["parts"] if p["type"] == "literal")
    assert literal["lines"] == [{"n": 2, "text": KERNEL}]
    assert first["proposals"] == []

    # 2. Proposing: raising the limit asks for sudo mode, so it is prepared and not made.
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
        Reply.say("I propose 512 MB."),
    )
    second = lab.ask(conversation, "Raise the memory limit to 512 MB")
    [proposal] = second["proposals"]
    assert (proposal["klass"], proposal["state"]) == ("you", "waiting")
    assert lab.bridge.calls == [("GET", f"/api/apps/{SHOP}/logs")]

    # 3. The person confirms it is them, makes the call and tells the conversation.
    changed: list[Any] = []

    def raised(domain: str, limits: Any, restart: bool = False) -> Any:
        changed.append((domain, limits.memory_max_mb))
        return SimpleNamespace(
            domain=domain, limits=limits, units=[], restart_required=False, restarted=False
        )

    monkeypatch.setattr(apps_api, "set_resource_limits", raised)
    lab.marta.elevate()
    call = proposal["call"]
    made = lab.marta.client.request(
        call["method"],
        call["path"],
        json=call["body"],
        headers=lab.marta.headers(**call["headers"]),
    )
    assert made.status_code == 200, made.text
    assert changed == [(SHOP, 512)]
    reported = lab.marta.post(
        f"/api/assistant/conversations/{conversation}/proposals/{proposal['id']}/outcome",
        {
            "fingerprint": proposal["fingerprint"],
            "status": made.status_code,
        },
    )
    assert reported.status_code == 200, reported.text
    assert reported.json()["verified"] is True
    assert reported.json()["state"] == "done"

    # 4. What came of it reaches the model at the next question, once.
    lab.model.script(Reply.say("Good: it is raised."))
    lab.ask(conversation, "Did it work?")
    told = lab.requests()[-1].body["messages"][-1]["content"]  # type: ignore[index]
    assert "<proposal-updates>" in told
    assert "approved and done" in told


def test_an_alert_page_is_read_by_noust_and_the_question_is_about_it(lab: Lab) -> None:
    lab.configure()
    lab.model.script(Reply.say("The certificate expires soon."))
    turn = lab.ask(
        lab.conversation(), "Explain this", page={"kind": "alert", "rule": "certificate-expiring"}
    )
    assert turn["state"] == "done"
    assert [s["tool"] for s in turn["steps"]] == ["call_operation"]
    sent = lab.requests()[0].body["messages"][1]["content"]  # type: ignore[index]
    assert "the alert certificate-expiring" in sent
    assert lab.bridge.calls == [("GET", "/api/alerts/rules")]


def test_the_page_a_question_is_asked_from_is_a_page_the_person_may_read(lab: Lab) -> None:
    lab.configure()
    # A deployment that is not there reads as one that does not exist, and the question is still answered.
    lab.model.script(Reply.say("I could not read that page."))
    turn = lab.ask(lab.conversation(), "Explain this", page={"kind": "deployment", "id": 987654})
    assert turn["state"] == "done"
    assert all(s["state"] == "failed" for s in turn["steps"])
    assert "Noust could not read" not in lab.requests()[0].body["messages"][1]["content"]  # type: ignore[index]
