# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The client of a model provider: what it sends, what it makes of what comes back, and where it is
not allowed to go (item 171).

Every case is a scripted model (``tests/support/scripted_model.py``) answering what the test
dictates; no real provider is ever asked.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator

import pytest

from noust import __version__
from noust.core import outbound
from noust.core.assistant.client import (
    CancelToken,
    Completion,
    Finish,
    MalformedArguments,
    ProviderClient,
    ProviderError,
    TextDelta,
    ToolCall,
    Usage,
    check_address,
    estimate_tokens,
)
from tests.support.scripted_model import Reply, ScriptedCall, ScriptedModel

pytestmark = pytest.mark.allow_sockets

LOOPBACK = ["127.0.0.1"]
HELLO = [{"role": "user", "content": "Why is my application down?"}]
TOOL = {
    "type": "function",
    "function": {
        "name": "list_applications",
        "description": "List applications.",
        "parameters": {"type": "object", "properties": {}},
    },
}


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny", "other"]) as scripted:
        yield scripted


class Pauses:
    """A sleeper that records how long it was asked to wait and waits for nothing."""

    def __init__(self) -> None:
        self.waited: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waited.append(seconds)


def make(model: ScriptedModel, **options: object) -> ProviderClient:
    defaults: dict[str, object] = {"allowed_hosts": LOOPBACK, "sleeper": Pauses()}
    defaults.update(options)
    return ProviderClient(model.base_url, "tiny", **defaults)  # type: ignore[arg-type]


def collect(client: ProviderClient, **options: object) -> Completion:
    return client.complete(HELLO, [TOOL], **options)  # type: ignore[arg-type]


class TestWhatIsSent:
    def test_a_streamed_request_asks_for_the_usage_and_names_the_client(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.say("Hello"))
        client = make(model, api_key="sk-test-key-0123456789")
        collect(client)
        request = model.chat_requests()[0]
        assert request.method == "POST"
        assert request.path == "/v1/chat/completions"
        assert request.body["model"] == "tiny"
        assert request.body["stream"] is True
        assert request.body["stream_options"] == {"include_usage": True}
        assert request.body["messages"] == HELLO
        assert request.body["tools"] == [TOOL]
        assert request.headers["authorization"] == "Bearer sk-test-key-0123456789"
        assert request.headers["user-agent"] == f"noust/{__version__}"
        assert request.headers["content-type"] == "application/json"

    def test_no_key_means_no_authorization_header(self, model: ScriptedModel) -> None:
        model.script(Reply.say("Hello"))
        collect(make(model))
        assert "authorization" not in model.chat_requests()[0].headers

    def test_the_key_is_only_ever_in_the_authorization_header(self, model: ScriptedModel) -> None:
        model.script(Reply.say("Hello"))
        key = "sk-test-key-0123456789"
        collect(make(model, api_key=key))
        assert key not in model.everything_received()
        assert key not in repr(make(model, api_key=key))

    def test_a_request_without_tools_sends_none(self, model: ScriptedModel) -> None:
        model.script(Reply.say("Hello"))
        make(model).complete(HELLO, [])
        assert "tools" not in model.chat_requests()[0].body

    def test_an_output_ceiling_is_sent_as_max_tokens(self, model: ScriptedModel) -> None:
        model.script(Reply.say("Hello"))
        collect(make(model, max_output_tokens=512))
        assert model.chat_requests()[0].body["max_tokens"] == 512


class TestWhatComesBack:
    def test_text_arrives_in_pieces_and_the_usage_is_the_providers(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.say("The unit failed to start.", piece=4, usage=(120, 30)))
        chunks = list(make(model).stream(HELLO, [TOOL]))
        deltas = [c.text for c in chunks if isinstance(c, TextDelta)]
        assert len(deltas) > 3
        assert "".join(deltas) == "The unit failed to start."
        finish = chunks[-1]
        assert isinstance(finish, Finish)
        assert finish.reason == "stop"
        assert finish.usage == Usage(120, 30, estimated=False)

    def test_a_tool_call_split_in_the_middle_of_a_string_is_one_call(
        self, model: ScriptedModel
    ) -> None:
        arguments = {"domain": "shop.example.com", "lines": 50}
        model.script(Reply.call("application_logs", arguments, piece=3))
        done = collect(make(model))
        assert [c.name for c in done.tool_calls] == ["application_logs"]
        assert done.tool_calls[0].arguments_object() == arguments
        assert done.finish_reason == "tool_calls"

    def test_a_call_is_only_handed_over_once_it_is_whole(self, model: ScriptedModel) -> None:
        model.script(Reply.call("application_logs", {"domain": "a.example.com"}, piece=2))
        chunks = list(make(model).stream(HELLO, [TOOL]))
        kinds = [type(c).__name__ for c in chunks]
        assert kinds == ["ToolCall", "Finish"]

    def test_two_calls_that_both_say_index_zero_are_told_apart_by_their_id(
        self, model: ScriptedModel
    ) -> None:
        model.script(
            Reply.calls_of(
                ScriptedCall("get_application", {"domain": "a.example.com"}, id="call_a"),
                ScriptedCall("get_application", {"domain": "b.example.com"}, id="call_b"),
                same_index=True,
            )
        )
        done = collect(make(model))
        assert [c.id for c in done.tool_calls] == ["call_a", "call_b"]
        assert [c.arguments_object()["domain"] for c in done.tool_calls] == [
            "a.example.com",
            "b.example.com",
        ]

    def test_a_server_that_repeats_the_name_in_every_piece_does_not_double_it(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.call("list_applications", {"limit": 10}, repeat_name=True))
        assert [c.name for c in collect(make(model)).tool_calls] == ["list_applications"]

    def test_two_calls_with_one_id_get_distinct_ones(self, model: ScriptedModel) -> None:
        model.script(
            Reply.calls_of(
                ScriptedCall("list_jobs", {}, id="same"),
                ScriptedCall("list_backups", {}, id="same"),
            )
        )
        ids = [c.id for c in collect(make(model)).tool_calls]
        assert len(set(ids)) == 2

    def test_arguments_that_are_not_json_come_back_as_a_error_the_model_can_correct(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.call("get_application", '{"domain": shop'))
        call = collect(make(model)).tool_calls[0]
        with pytest.raises(MalformedArguments) as caught:
            call.arguments_object()
        assert "JSON" in str(caught.value)
        assert "object" in str(caught.value)

    def test_arguments_that_are_json_but_not_an_object_are_refused_too(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.call("get_application", '["a.example.com"]'))
        with pytest.raises(MalformedArguments):
            collect(make(model)).tool_calls[0].arguments_object()

    def test_a_call_without_arguments_takes_an_empty_object(self, model: ScriptedModel) -> None:
        model.script(Reply.call("list_applications", ""))
        assert collect(make(model)).tool_calls[0].arguments_object() == {}

    def test_text_and_calls_in_one_answer(self, model: ScriptedModel) -> None:
        model.script(
            Reply.calls_of(ScriptedCall("list_jobs", {}), text="Let me look.", usage=(50, 9))
        )
        done = collect(make(model))
        assert done.text == "Let me look."
        assert [c.name for c in done.tool_calls] == ["list_jobs"]

    def test_a_reply_cut_for_length_says_so(self, model: ScriptedModel) -> None:
        model.script(Reply.say("It was", finish_reason="length"))
        assert collect(make(model)).finish_reason == "length"


class TestUsage:
    def test_without_usage_it_is_an_estimate_and_says_so(self, model: ScriptedModel) -> None:
        model.script(Reply.say("x" * 400, usage=None))
        done = collect(make(model))
        assert done.usage.estimated is True
        assert done.usage.completion_tokens == 100
        sent = json.dumps(HELLO) + json.dumps([TOOL])
        assert done.usage.prompt_tokens >= len(sent) // 4

    def test_an_estimate_is_the_characters_over_four(self) -> None:
        assert estimate_tokens("abcdefgh") == 2
        assert estimate_tokens("a") == 1
        assert estimate_tokens("") == 0

    def test_a_usage_a_server_sends_on_a_plain_answer_is_taken(self, model: ScriptedModel) -> None:
        model.script(Reply.say("Hi", stream=False, usage=(77, 3)))
        assert collect(make(model)).usage == Usage(77, 3, estimated=False)


class TestAServerThatDoesNotStream:
    def test_one_json_document_for_a_streamed_request_is_read_as_the_answer(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.call("list_jobs", {"limit": 3}, stream=False))
        done = collect(make(model))
        assert done.tool_calls[0].arguments_object() == {"limit": 3}

    def test_a_server_that_refuses_streaming_with_tools_gets_the_plain_request_once(
        self, model: ScriptedModel
    ) -> None:
        model.script(
            Reply.call("list_jobs", {}, refuse_stream=True),
            Reply.say("Fine", refuse_stream=True),
        )
        client = make(model)
        assert collect(client).tool_calls[0].name == "list_jobs"
        assert collect(client).text == "Fine"
        sent = [r.body["stream"] for r in model.chat_requests()]
        # The first request streams and is refused, the second is the plain one; the client
        # remembers and does not try to stream again.
        assert sent == [True, False, False]

    def test_a_server_that_rejects_the_usage_option_is_asked_again_without_it(
        self, model: ScriptedModel
    ) -> None:
        model.script(
            Reply.error(400, body='{"error": {"message": "unknown field stream_options"}}'),
            Reply.say("Hello"),
        )
        client = make(model)
        assert collect(client).text == "Hello"
        bodies = [r.body for r in model.chat_requests()]
        assert "stream_options" in bodies[0]
        assert "stream_options" not in bodies[1]


class TestWhenTheProviderFails:
    def test_a_429_is_waited_out_by_its_retry_after_and_tried_again(
        self, model: ScriptedModel
    ) -> None:
        pauses = Pauses()
        model.script(Reply.error(429, retry_after="2"), Reply.say("Back"))
        assert collect(make(model, sleeper=pauses)).text == "Back"
        assert pauses.waited == [2.0]

    def test_a_retry_after_as_a_date_is_read(self, model: ScriptedModel) -> None:
        pauses = Pauses()
        model.script(Reply.error(503, retry_after="Wed, 21 Oct 2015 07:28:00 GMT"), Reply.say("Up"))
        assert collect(make(model, sleeper=pauses)).text == "Up"
        # A date in the past is not a reason to wait.
        assert pauses.waited == [0.0]

    def test_a_server_error_is_retried_with_a_growing_wait(self, model: ScriptedModel) -> None:
        pauses = Pauses()
        model.script(Reply.error(502), Reply.error(500), Reply.say("Up"))
        assert collect(make(model, sleeper=pauses)).text == "Up"
        assert pauses.waited == [1.0, 2.0]

    def test_it_gives_up_after_its_tries_and_says_which_failure_it_was(
        self, model: ScriptedModel
    ) -> None:
        model.script(*[Reply.error(429) for _ in range(4)])
        with pytest.raises(ProviderError) as caught:
            collect(make(model, max_retries=3))
        assert caught.value.code == "assistant.provider.rate_limited"
        assert len(model.chat_requests()) == 4

    def test_a_wait_longer_than_it_will_give_is_not_slept(self, model: ScriptedModel) -> None:
        pauses = Pauses()
        model.script(Reply.error(429, retry_after="3600"))
        with pytest.raises(ProviderError) as caught:
            collect(make(model, sleeper=pauses, max_wait=30))
        assert pauses.waited == []
        assert caught.value.code == "assistant.provider.rate_limited"
        assert caught.value.retry_after == 3600

    def test_a_refused_key_is_not_retried_and_the_providers_words_are_kept(
        self, model: ScriptedModel
    ) -> None:
        model.api_key = "the-right-key"
        with pytest.raises(ProviderError) as caught:
            collect(make(model, api_key="the-wrong-key"))
        assert caught.value.code == "assistant.provider.unauthorized"
        assert caught.value.status == 401
        assert "Incorrect API key provided" in caught.value.message
        assert len(model.chat_requests()) == 1

    def test_the_providers_error_text_never_carries_the_key_back(
        self, model: ScriptedModel
    ) -> None:
        key = "sk-live-aaaaaaaaaaaaaaaaaaaaaaaa"
        model.script(
            Reply.error(400, body=json.dumps({"error": {"message": f"bad request for {key}"}}))
        )
        with pytest.raises(ProviderError) as caught:
            collect(make(model, api_key=key))
        assert key not in caught.value.message
        assert key not in str(caught.value)

    def test_an_unknown_model_is_a_404_in_the_providers_words(self, model: ScriptedModel) -> None:
        model.script(Reply.error(404, body='{"error": {"message": "model \'tiny\' not found"}}'))
        with pytest.raises(ProviderError) as caught:
            collect(make(model))
        assert caught.value.code == "assistant.provider.not_found"
        assert "model 'tiny' not found" in caught.value.message

    def test_a_provider_that_does_not_answer_the_port(self) -> None:
        with ScriptedModel() as gone:
            url = gone.base_url
        with pytest.raises(ProviderError) as caught:
            ProviderClient(url, "tiny", allowed_hosts=LOOPBACK).complete(HELLO, [])
        assert caught.value.code == "assistant.provider.unreachable"


class TestACutAnswer:
    def test_a_cut_in_the_middle_of_a_call_hands_over_no_half_call(
        self, model: ScriptedModel
    ) -> None:
        model.script(
            Reply.cut(
                6,
                text="",
                calls=(ScriptedCall("get_application", {"domain": "a.example.com"}),),
                piece=4,
            )
        )
        handed: list[object] = []
        with pytest.raises(ProviderError) as caught:
            for chunk in make(model).stream(HELLO, [TOOL]):
                handed.append(chunk)
        assert caught.value.code == "assistant.provider.cut"
        assert not any(isinstance(c, ToolCall) for c in handed)

    def test_a_cut_in_the_middle_of_a_sentence_keeps_what_was_already_said(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.cut(4, text="The unit crashed because of a missing variable", piece=5))
        said: list[str] = []
        with pytest.raises(ProviderError) as caught:
            for chunk in make(model).stream(HELLO, [TOOL]):
                if isinstance(chunk, TextDelta):
                    said.append(chunk.text)
        assert caught.value.code == "assistant.provider.cut"
        assert "".join(said).startswith("The unit")

    def test_a_stream_that_ends_after_its_finish_without_done_is_whole(
        self, model: ScriptedModel
    ) -> None:
        # [DONE] is the last event and the usage the one before it: cutting after the finish
        # event (the third) loses the usage and nothing else.
        model.script(Reply.cut(3, text="Hi", piece=2, usage=(10, 2)))
        done = collect(make(model))
        assert done.text == "Hi"
        assert done.usage.estimated is True


class TestTimeAndCancellation:
    def test_a_provider_that_goes_quiet_is_a_timeout_not_a_wait_forever(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.say("slow", delay=2.0))
        started = time.monotonic()
        with pytest.raises(ProviderError) as caught:
            collect(make(model, read_timeout=0.3))
        assert caught.value.code == "assistant.provider.timeout"
        assert time.monotonic() - started < 1.5

    def test_a_provider_that_trickles_is_stopped_by_the_total_deadline(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.say("a" * 400, piece=2, gap=0.05))
        started = time.monotonic()
        with pytest.raises(ProviderError) as caught:
            collect(make(model, read_timeout=5, total_timeout=0.4))
        assert caught.value.code == "assistant.provider.timeout"
        assert time.monotonic() - started < 2.5

    def test_cancelling_closes_the_connection_and_stops_the_read(
        self, model: ScriptedModel
    ) -> None:
        model.script(Reply.say("a" * 400, piece=2, gap=0.05))
        cancel = CancelToken()
        threading.Timer(0.2, cancel.set).start()
        started = time.monotonic()
        with pytest.raises(ProviderError) as caught:
            collect(make(model, read_timeout=5, total_timeout=30), cancel=cancel)
        assert caught.value.code == "assistant.provider.cancelled"
        assert time.monotonic() - started < 2.5

    def test_a_token_already_cancelled_sends_nothing(self, model: ScriptedModel) -> None:
        cancel = CancelToken()
        cancel.set()
        with pytest.raises(ProviderError) as caught:
            collect(make(model), cancel=cancel)
        assert caught.value.code == "assistant.provider.cancelled"
        assert model.requests == []


class TestWhereItMayGo:
    def test_a_private_address_is_refused_unless_the_operator_listed_it(
        self, model: ScriptedModel
    ) -> None:
        client = ProviderClient(model.base_url, "tiny", allowed_hosts=[])
        with pytest.raises(ProviderError) as caught:
            client.complete(HELLO, [])
        assert caught.value.code == "assistant.provider.destination_refused"
        assert model.requests == []

    def test_a_name_that_resolves_inside_the_network_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outbound, "resolve_host", lambda host: ("10.1.2.3",))
        with pytest.raises(ProviderError) as caught:
            check_address("https://models.example.org/v1", allowed_hosts=[])
        assert caught.value.code == "assistant.provider.destination_refused"

    def test_a_name_with_one_public_and_one_private_record_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outbound, "resolve_host", lambda host: ("203.0.113.9", "192.168.1.5"))
        with pytest.raises(ProviderError):
            check_address("https://models.example.org/v1", allowed_hosts=[])

    def test_a_name_that_changes_where_it_resolves_between_saving_and_using_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answers = iter([("203.0.113.9",), ("169.254.169.254",)])
        monkeypatch.setattr(outbound, "resolve_host", lambda host: next(answers))
        url = "https://models.example.org/v1"
        check_address(url, allowed_hosts=[])
        client = ProviderClient(url, "tiny", allowed_hosts=[])
        with pytest.raises(ProviderError) as caught:
            client.complete(HELLO, [])
        assert caught.value.code == "assistant.provider.destination_refused"

    def test_the_connection_goes_to_the_address_that_was_judged(
        self, model: ScriptedModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A listed name that resolves to this machine: what is judged is what is dialled, with
        # no second look at DNS in between.
        calls: list[str] = []

        def resolve(host: str) -> tuple[str, ...]:
            calls.append(host)
            return ("127.0.0.1",)

        monkeypatch.setattr(outbound, "resolve_host", resolve)
        model.script(Reply.say("Hello"))
        url = f"http://models.internal.example:{model.port}/v1"
        client = ProviderClient(url, "tiny", allowed_hosts=["models.internal.example"])
        assert client.complete(HELLO, []).text == "Hello"
        assert calls == ["models.internal.example"]
        assert model.chat_requests()[0].headers["host"].startswith("models.internal.example")

    def test_a_redirect_is_never_followed(self, model: ScriptedModel) -> None:
        model.script(Reply(status=302, headers={"Location": "http://169.254.169.254/latest"}))
        with pytest.raises(ProviderError) as caught:
            collect(make(model))
        assert caught.value.code == "assistant.provider.redirected"
        assert len(model.requests) == 1

    def test_plain_http_is_only_for_a_host_the_operator_listed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outbound, "resolve_host", lambda host: ("203.0.113.9",))
        with pytest.raises(ProviderError) as caught:
            check_address("http://models.example.org/v1", allowed_hosts=[])
        assert caught.value.code == "assistant.provider.insecure_address"
        check_address("http://models.example.org/v1", allowed_hosts=["models.example.org"])

    @pytest.mark.parametrize(
        "address",
        [
            "ftp://models.example.org/v1",
            "file:///etc/passwd",
            "https://user:pass@models.example.org/v1",
            "https://models.example.org/v1?key=abc",
            "https://models.example.org/v1#frag",
            "https://",
            "not a url",
            "https://models.example.org/v1\nHost: evil",
            "https://models.example.org/../admin",
            "",
        ],
    )
    def test_an_address_that_is_not_a_base_address_is_refused(
        self, address: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outbound, "resolve_host", lambda host: ("203.0.113.9",))
        with pytest.raises(ProviderError) as caught:
            check_address(address, allowed_hosts=[])
        assert caught.value.code == "assistant.provider.bad_address"

    def test_removing_the_entry_from_the_live_setting_blocks_a_client_already_made(
        self, model: ScriptedModel
    ) -> None:
        from noust.core.config import Config

        Config().set("security.allow_private_hosts", ["127.0.0.1"])
        model.script(Reply.say("one"), Reply.say("two"))
        client = ProviderClient(model.base_url, "tiny")
        assert client.complete(HELLO, []).text == "one"
        Config().set("security.allow_private_hosts", [])
        with pytest.raises(ProviderError) as caught:
            client.complete(HELLO, [])
        assert caught.value.code == "assistant.provider.destination_refused"


class TestTheModelList:
    def test_the_models_are_listed_by_id(self, model: ScriptedModel) -> None:
        assert make(model).list_models() == ["tiny", "other"]
        request = model.requests[0]
        assert request.method == "GET"
        assert request.path == "/v1/models"

    def test_a_provider_with_no_list_says_so(self, model: ScriptedModel) -> None:
        model.models_status = 404
        with pytest.raises(ProviderError) as caught:
            make(model).list_models()
        assert caught.value.code == "assistant.provider.not_found"


class TestHelpers:
    def test_usage_adds_up(self) -> None:
        assert Usage(10, 4).total_tokens == 14
        assert Usage(10, 4).plus(Usage(1, 2, estimated=True)) == Usage(11, 6, estimated=True)

    def test_a_tool_call_knows_its_arguments_only_when_they_parse(self) -> None:
        assert ToolCall("c1", "t", '{"a": 1}').arguments_object() == {"a": 1}
        with pytest.raises(MalformedArguments):
            ToolCall("c1", "t", "{").arguments_object()
