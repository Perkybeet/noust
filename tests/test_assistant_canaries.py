# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
No secret reaches the model, whatever the assistant reads, and none is kept anywhere (item 171).

Canaries are planted where a secret can be: in an application's variables, in its journal, in the log of
a build, in a page of the documentation and in what the person typed. A turn then reads all of it, asks
the model about it and proposes a call. Nothing of the canaries may be in what the **model received**, in what
the **conversation kept** (messages, turns, steps and proposals), in what the console's **event stream**
carried, or on the **audit trail**.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.core.assistant.docsearch import DocIndex
from noust.web.assistant import loop as loop_module
from tests.support.assistant_lab import KEY, SHOP, FakeServices, Lab, build_lab
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets

#: The value of a variable Noust knows is secret: it has no shape of a credential, and is known by its name.
VARIABLE = "kT3-plainly-a-password-4491"
#: Things that have the shape of a credential.
BEARER = "Bearer q8w7e6r5t4y3u2i1o0p9a8s7d6f5g4h3"
TOKEN = "ghp_0123456789abcdefghijklmnopqrstuvwxyz"
URL_PASSWORD = "postgres://shop:Zx9-urlpass-77@db.internal/shop"
# Put together here so that the file holds nothing with the whole shape of a live key: a scanner of
# repositories cannot tell an invented one from a real one, and should not have to.
TYPED = "sk_" + "live_" + "typedbytheperson0123456789ab"
DOCS = "AKIAIOSFODNN7EXAMPLEKEY"

CANARIES = (VARIABLE, "q8w7e6r5t4y3u2i1o0p9a8s7d6f5g4h3", TOKEN, "Zx9-urlpass-77", TYPED, KEY)


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


def stored_text(lab: Lab) -> str:
    """Everything the store holds of the conversations, as one text."""
    parts: list[str] = []
    with lab.store._transaction() as cursor:
        for table in (
            "assistant_messages",
            "assistant_turns",
            "assistant_proposals",
            "assistant_conversations",
            "assistant_usage",
            "assistant_actions",
        ):
            cursor.execute(f"SELECT * FROM {table}")  # noqa: S608 - fixed names
            parts.extend(json.dumps(dict(row), default=str) for row in cursor.fetchall())
    return "\n".join(parts)


def test_a_planted_secret_is_nowhere_the_assistant_looked_or_wrote(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lab.configure()
    lab.write_env(SHOP, f"DB_PASSWORD={VARIABLE}\nNODE_ENV=production\n")
    FakeServices.journal = (
        f"2026-11-12 ERROR password authentication failed for shop: {VARIABLE}\n"
        f"Authorization: {BEARER}\n"
        f"cloned with {TOKEN}\n"
        f"connecting {URL_PASSWORD}\n"
        "client 203.0.113.7 asked for /admin\n"
    )
    deployment = lab.failed_deployment(
        SHOP, f"building\nDB_PASSWORD={VARIABLE}\nfetch {URL_PASSWORD}\nbuild failed\n"
    )
    pages = tmp_path / "docs"
    pages.mkdir()
    (pages / "assistant.md").write_text(
        f"# The assistant\n\n## Keys\n\nSend `Authorization: Bearer {DOCS}` to read it. A backup is a copy.\n"
    )
    monkeypatch.setattr(loop_module, "shared_docs", lambda: DocIndex([pages]))
    lab.model.script(
        Reply.call("application_logs", {"domain": SHOP}),
        Reply.call("search_docs", {"query": "keys bearer"}),
        Reply.call("list_environment_variables", {"domain": SHOP}),
        Reply.call(
            "call_operation",
            {
                "method": "PATCH",
                "path": "/api/apps/{domain}/limits",
                "parameters": {"domain": SHOP},
                "body": {"memory_max_mb": 512},
            },
        ),
        Reply.say("It is the database password. [[S1:1-2]]"),
    )
    conversation = lab.conversation()
    turn = lab.ask(
        conversation,
        f"Why does it fail? I tried {TYPED} and {BEARER} and the address 203.0.113.7",
        page={"kind": "deployment", "id": deployment},
    )

    assert turn["state"] == "done"
    assert [s["tool"] for s in turn["steps"]][-4:] == [
        "application_logs",
        "search_docs",
        "list_environment_variables",
        "call_operation",
    ]
    # Something was hidden, and the person is told how many.
    assert sum(s.get("masked") or 0 for s in turn["steps"]) >= 3

    received = lab.model.everything_received()
    for canary in CANARIES:
        assert canary not in received, f"the model received {canary}"
    assert DOCS not in received
    # Personal data went disguised: the address is a marker, the person reads it back.
    assert "203.0.113.7" not in received and "<ip-1>" in received

    kept = stored_text(lab)
    for canary in (*CANARIES, DOCS):
        assert canary not in kept, f"the store kept {canary}"
    # The person's own address is theirs to read in their own conversation.
    shown = json.dumps(turn)
    for canary in (*CANARIES, DOCS):
        assert canary not in shown, f"the console was shown {canary}"
    again = lab.marta.get(f"/api/assistant/conversations/{conversation}")
    for canary in (*CANARIES, DOCS):
        assert canary not in again.text

    streamed = "\n".join(lab.frames)
    for canary in (*CANARIES, DOCS):
        assert canary not in streamed, f"the event stream carried {canary}"

    trail = "\n".join(json.dumps(e) for e in lab.audit_events())
    for canary in (*CANARIES, DOCS):
        assert canary not in trail, f"the audit trail has {canary}"
    # What the audit trail does say: the assistant acted, for a person.
    mine = [e for e in lab.audit_events() if e.get("action", "").startswith("assistant.")]
    assert {e["action"] for e in mine} >= {
        "assistant.tool",
        "assistant.result",
        "assistant.summary",
    }


def test_the_providers_key_is_in_no_request_but_the_authorization_header(lab: Lab) -> None:
    lab.configure()
    lab.model.script(Reply.say(f"my key is {KEY}"))
    turn = lab.ask(lab.conversation(), "who are you?")
    for request in lab.model.chat_requests():
        assert KEY not in request.raw.decode()
        assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in json.dumps(turn)
