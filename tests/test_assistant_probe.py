# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The probe: what a provider turned out to be able to do, said as sentences with a code (item 171).

It asks for the models the provider has and for one call to a toy tool, and what it finds is what
the assistant is allowed to do: a model that does not make the call well is only offered the part
that explains, and a model on this machine gets an honest word about its memory.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from noust.core.assistant.client import ProviderClient
from noust.core.assistant.probe import Machine, ProbeResult, run_probe
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets

KEY = "sk-provider-9d8c7b6a5f4e3d2c1b0a"
GOOD = Reply.call("get_status", {"service": "probe"})


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny", "other"]) as scripted:
        yield scripted


def client(model: ScriptedModel, **options: object) -> ProviderClient:
    options.setdefault("allowed_hosts", ["127.0.0.1"])
    options.setdefault("max_retries", 0)
    return ProviderClient(model.base_url, "tiny", **options)  # type: ignore[arg-type]


def said(result: ProbeResult) -> list[str]:
    return [sentence.code for sentence in result.sentences]


class TestAModelThatCallsTools:
    def test_it_is_found_to_answer_to_have_the_model_and_to_call_tools(
        self, model: ScriptedModel
    ) -> None:
        model.script(GOOD)
        result = run_probe(client(model))
        assert result.reachable is True
        assert result.model_found is True
        assert result.tools is True
        assert result.models == ["tiny", "other"]
        assert said(result) == [
            "assistant.probe.answers",
            "assistant.probe.tools_ok",
        ]

    def test_what_it_asks_is_the_toy_and_nothing_of_this_server(self, model: ScriptedModel) -> None:
        model.script(GOOD)
        run_probe(client(model, api_key=KEY))
        sent = model.everything_received()
        assert KEY not in sent
        body = model.chat_requests()[0].body
        assert [t["function"]["name"] for t in body["tools"]] == ["get_status"]
        assert len(body["messages"]) == 1

    def test_the_result_is_plain_data_for_the_store(self, model: ScriptedModel) -> None:
        model.script(GOOD)
        result = run_probe(client(model))
        document = result.to_dict()
        assert json.loads(json.dumps(document)) == document
        assert document["tools"] is True
        assert document["sentences"][0]["code"] == "assistant.probe.answers"
        assert "text" in document["sentences"][0]
        assert ProbeResult.from_dict(document).tools is True


class TestAModelThatDoesNot:
    @pytest.mark.parametrize(
        "reply",
        [
            Reply.say("I would call get_status with service probe."),
            Reply.call("get_status", '{"service": probe'),
            Reply.call("get_status", '["probe"]'),
            Reply.call("something_else", {"service": "probe"}),
            Reply.call("get_status", {"wrong": 1}),
            Reply.call("get_status", {"service": 7}),
        ],
        ids=["text", "broken-json", "not-an-object", "wrong-tool", "wrong-field", "wrong-type"],
    )
    def test_only_a_well_formed_call_to_the_toy_counts(
        self, model: ScriptedModel, reply: Reply
    ) -> None:
        model.script(reply)
        result = run_probe(client(model))
        assert result.reachable is True
        assert result.tools is False
        assert "assistant.probe.no_tools" in said(result)

    def test_the_sentence_says_that_it_can_only_explain(self, model: ScriptedModel) -> None:
        model.script(Reply.say("no"))
        result = run_probe(client(model))
        sentence = next(s for s in result.sentences if s.code == "assistant.probe.no_tools")
        assert "explain" in sentence.text
        assert sentence.params["model"] == "tiny"

    def test_a_provider_that_refuses_tools_outright_is_one_that_does_not_call_them(
        self, model: ScriptedModel
    ) -> None:
        model.script(
            Reply.error(400, body='{"error": {"message": "this model does not support tools"}}')
        )
        result = run_probe(client(model))
        assert result.reachable is True
        assert result.tools is False
        assert "assistant.probe.no_tools" in said(result)


class TestAModelThatIsNotThere:
    def test_a_model_the_provider_does_not_list_is_said(self, model: ScriptedModel) -> None:
        model.models = ["other"]
        model.script(Reply.error(404, body='{"error": {"message": "model not found"}}'))
        result = run_probe(client(model))
        assert result.model_found is False
        assert "assistant.probe.model_missing" in said(result)

    def test_a_provider_with_no_list_of_models_is_judged_by_the_call(
        self, model: ScriptedModel
    ) -> None:
        model.models_status = 404
        model.script(GOOD)
        result = run_probe(client(model))
        assert result.reachable is True
        assert result.model_found is None
        assert result.tools is True
        assert result.models == []


class TestAProviderThatDoesNotAnswer:
    def test_a_refused_key_is_the_reason_and_nothing_else_is_tried(
        self, model: ScriptedModel
    ) -> None:
        model.api_key = "the-right-key"
        result = run_probe(client(model, api_key="the-wrong-key"))
        assert result.reachable is False
        assert result.tools is None
        assert said(result) == ["assistant.provider.unauthorized"]
        assert model.chat_requests() == []

    def test_a_port_nobody_listens_on(self) -> None:
        with ScriptedModel() as gone:
            url = gone.base_url
        result = run_probe(ProviderClient(url, "tiny", allowed_hosts=["127.0.0.1"], max_retries=0))
        assert result.reachable is False
        assert said(result) == ["assistant.provider.unreachable"]

    def test_an_address_that_may_not_be_called_is_the_reason(self, model: ScriptedModel) -> None:
        result = run_probe(ProviderClient(model.base_url, "tiny", allowed_hosts=[]))
        assert result.reachable is False
        assert said(result) == ["assistant.provider.destination_refused"]
        assert model.requests == []


class TestTheMachinesMemory:
    def test_a_model_on_a_small_machine_without_a_gpu_is_warned_with_the_machines_own_figure(
        self, model: ScriptedModel
    ) -> None:
        model.script(GOOD)
        result = run_probe(client(model), local=True, machine=Machine(memory_gb=8.0, gpu=False))
        warning = next(s for s in result.sentences if s.code == "assistant.probe.memory_low")
        assert warning.params["memory_gb"] == 8.0
        assert "faster" not in warning.text.lower()
        assert "slower" not in warning.text.lower()

    def test_not_with_enough_memory_a_gpu_or_a_model_that_is_elsewhere(
        self, model: ScriptedModel
    ) -> None:
        for machine, local in (
            (Machine(memory_gb=32.0, gpu=False), True),
            (Machine(memory_gb=4.0, gpu=True), True),
            (Machine(memory_gb=4.0, gpu=False), False),
            (None, True),
        ):
            model.script(GOOD)
            result = run_probe(client(model), local=local, machine=machine)
            assert "assistant.probe.memory_low" not in said(result)

    def test_sixteen_gigabytes_is_enough_to_say_nothing(self, model: ScriptedModel) -> None:
        model.script(GOOD)
        result = run_probe(client(model), local=True, machine=Machine(memory_gb=16.0, gpu=False))
        assert "assistant.probe.memory_low" not in said(result)
