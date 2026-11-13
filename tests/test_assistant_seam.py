# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The seam the investigating loop is built on, tried as a loop would use it (item 171).

The loop (the next lane) is not here: this plays its part in a few lines, the way it is meant to be
written, against the scripted model: the masker before every request, the meter before every step,
the client for the request, the store for what is kept. What it proves is what the base promises
whoever writes the loop:

- **no secret planted in a variable, a log or a message reaches the model**: not in the first
  request, not in the second that carries the first one's answers, and not in what is stored;
- **a limit reached cuts between steps**: the step that crossed it is whole and written down, and the
  next one is not made;
- **the person reads their own addresses back**, while the model only ever saw the markers;
- **nothing is asked of a node**: the assistant belongs to the console where the person is.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

from noust.core.assistant.client import ProviderClient, assistant_message, tool_message
from noust.core.assistant.conversations import Conversations
from noust.core.assistant.limits import Limits, UsageMeter
from noust.core.assistant.masking import Masker, mark_as_data
from noust.core.store import App, NoustStore
from tests.support import import_graph_support as graph
from tests.support.scripted_model import Reply, ScriptedCall, ScriptedModel

pytestmark = pytest.mark.allow_sockets

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "noust"
DOMAIN = "shop.example.com"
APP_SECRET = "pw-7f3a9c1e5b2d-planted"
PROVIDER_KEY = "sk-provider-9d8c7b6a5f4e3d2c1b0a"
LOG_SECRET = "tok-0b8d4e6a1c3f-in-a-log"
PASTED = f"Bearer {LOG_SECRET}abcdefghijklmnop"
ADDRESS = "203.0.113.7"
TOOL = {
    "type": "function",
    "function": {
        "name": "application_logs",
        "description": "Read a log.",
        "parameters": {"type": "object", "properties": {"domain": {"type": "string"}}},
    },
}


@pytest.fixture
def server(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    folder = tmp_path / "apps" / DOMAIN
    folder.mkdir(parents=True)
    (folder / ".env").write_text(f"DB_PASSWORD={APP_SECRET}\nNODE_ENV=production\n")
    store.create_app(App(domain=DOMAIN, app_path=str(folder)))
    try:
        yield store
    finally:
        store.close()
        NoustStore.reset_instance()


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny"], api_key=PROVIDER_KEY) as scripted:
        yield scripted


def a_turn(
    store: NoustStore,
    model: ScriptedModel,
    question: str,
    *,
    limits: Limits,
    log: str,
    mask_personal: bool = True,
) -> list[str]:
    """
    Play a loop's turn: ask, let the model read a log, say what was found. Returns what it said.
    """
    client = ProviderClient(
        model.base_url,
        "tiny",
        api_key=PROVIDER_KEY,
        allowed_hosts=["127.0.0.1"],
        sleeper=lambda seconds: None,
    )
    meter = UsageMeter(limits, {}, "EUR", store=store)
    chats = Conversations(store)
    masker = Masker.for_server(mask_personal=mask_personal, api_key=PROVIDER_KEY)
    chat = chats.create(1)
    messages: list[dict[str, object]] = []
    said: list[str] = []

    def keep(role: str, text: str, **extra: object) -> None:
        masked = masker.mask(text, domain=DOMAIN)
        messages.append({"role": role, "content": masked})
        chats.append(chat.id, 1, role, masked, **extra)  # type: ignore[arg-type]

    keep("user", question)
    for step in range(limits.steps_per_turn + 1):
        verdict = meter.check_step(chat.id, 1, step=step, model="tiny")
        if verdict.stop is not None:
            said.append(verdict.stop.code)
            break
        done = client.complete(messages, [TOOL])
        meter.record_step(chat.id, 1, "tiny", done.usage)
        messages.append(assistant_message(done))
        if not done.tool_calls:
            said.append(masker.restore(done.text))
            break
        for call in done.tool_calls:
            arguments = masker.restore_value(call.arguments_object())
            assert arguments["domain"] == DOMAIN
            answer = mark_as_data(masker.mask(log, domain=DOMAIN), "application logs")
            messages.append(tool_message(call.id, answer))
            chats.append(chat.id, 1, "tool", answer, tool_call_id=call.id)
    chats.save_markers(chat.id, 1, masker.markers)
    assert chats.markers(chat.id, 1) == masker.markers
    return said


class TestNoSecretReachesTheModel:
    def test_not_in_the_question_the_log_the_variable_or_the_second_request(
        self, server: NoustStore, model: ScriptedModel
    ) -> None:
        log = (
            f"2026-11-12 ERROR password authentication failed: {APP_SECRET}\n"
            f"Authorization: Bearer {LOG_SECRET}abcdefghijklmnop\n"
            f"client {ADDRESS} asked for /admin"
        )
        model.script(
            Reply.call("application_logs", {"domain": DOMAIN}, usage=(120, 20)),
            Reply.say("It is the database password.", usage=(300, 30)),
        )
        said = a_turn(
            server,
            model,
            f"Why is {DOMAIN} down? I tried {APP_SECRET} and {PASTED}",
            limits=Limits(),
            log=log,
        )
        assert said == ["It is the database password."]
        everything = model.everything_received()
        for secret in (APP_SECRET, LOG_SECRET, PROVIDER_KEY):
            assert secret not in everything, secret
        assert len(model.chat_requests()) == 2
        # The second request carries the log, marked as data and disguised.
        second = model.chat_requests()[1].raw.decode()
        assert "untrusted-data" in second
        assert ADDRESS not in second
        assert "<ip-1>" in second

    def test_nor_in_what_is_stored(self, server: NoustStore, model: ScriptedModel) -> None:
        model.script(Reply.call("application_logs", {"domain": DOMAIN}), Reply.say("Done."))
        a_turn(
            server,
            model,
            f"look at {APP_SECRET} {PASTED}",
            limits=Limits(),
            log=f"error {APP_SECRET}",
        )
        stored = " ".join(
            m.content
            for m in Conversations(server).messages(Conversations(server).recent(1)[0].id, 1)
        )
        for secret in (APP_SECRET, LOG_SECRET):
            assert secret not in stored

    def test_the_person_reads_their_own_address_back(
        self, server: NoustStore, model: ScriptedModel
    ) -> None:
        model.script(
            Reply.call("application_logs", {"domain": DOMAIN}),
            Reply.say("Block <ip-1> first."),
        )
        said = a_turn(
            server, model, "who is attacking?", limits=Limits(), log=f"client {ADDRESS} asked"
        )
        assert said == [f"Block {ADDRESS} first."]
        assert ADDRESS not in model.everything_received()


class TestALimitCutsBetweenSteps:
    def test_the_step_that_crossed_it_is_whole_and_the_next_one_is_not_made(
        self, server: NoustStore, model: ScriptedModel
    ) -> None:
        looping = Reply.call("application_logs", {"domain": DOMAIN}, usage=(500, 20))
        model.script(*[looping for _ in range(10)])
        said = a_turn(
            server,
            model,
            "keep looking",
            limits=Limits(steps_per_turn=3),
            log="nothing",
        )
        assert said == ["assistant.limit.steps"]
        assert len(model.chat_requests()) == 3
        assert UsageMeter(Limits(), {}, "EUR", store=server).report().month.steps == 3

    def test_the_tokens_of_the_conversation_stop_it_too(
        self, server: NoustStore, model: ScriptedModel
    ) -> None:
        model.script(
            *[
                Reply.call("application_logs", {"domain": DOMAIN}, usage=(60_000, 10))
                for _ in range(10)
            ]
        )
        said = a_turn(
            server,
            model,
            "keep looking",
            limits=Limits(input_tokens_per_conversation=100_000),
            log="nothing",
        )
        assert said == ["assistant.limit.input_tokens"]
        assert len(model.chat_requests()) == 2


class TestAProviderThatMisbehaves:
    def test_a_call_that_arrives_in_pieces_and_a_usage_that_never_comes(
        self, server: NoustStore, model: ScriptedModel
    ) -> None:
        model.script(
            Reply.calls_of(
                ScriptedCall("application_logs", {"domain": DOMAIN}, id="a"),
                same_index=True,
                piece=3,
                usage=None,
            ),
            Reply.say("Found it.", usage=None),
        )
        said = a_turn(server, model, "why?", limits=Limits(), log="x")
        assert said == ["Found it."]
        month = UsageMeter(Limits(), {}, "EUR", store=server).report().month
        assert month.estimated_steps == 2


@cache
def _edges() -> list[graph.Edge]:
    return graph.read_edges(PACKAGE)


def test_nothing_is_asked_of_a_node() -> None:
    # The assistant belongs to the console where the person is: a central with a node of 4.2 has
    # nothing to ask of it that it lacks, because the assistant asks it nothing. None of its modules
    # imports the fleet's client or the proxy to a node.
    mine = ("noust.core.assistant", "noust.web.assistant", "noust.web.api.assistant")
    for edge in _edges():
        if edge.source.startswith(mine):
            assert not edge.target.startswith(
                ("noust.fleet", "noust.web.api.node_proxy", "noust.web.fleet_jobs")
            ), f"{edge.source} -> {edge.target}"
