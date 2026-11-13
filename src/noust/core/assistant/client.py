# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one client of a model provider for the built-in assistant (item 171).

It speaks a single protocol, Chat Completions as OpenAI-compatible servers speak it:
``POST <base>/chat/completions`` answered as a stream of ``data:`` events that ends in ``[DONE]``
(or as one JSON document), tools offered in ``tools`` and called in ``tool_calls``, the cost in
``usage`` (asked for with ``stream_options.include_usage``), and ``GET <base>/models`` to know what
the provider has. It depends on none of ``tool_choice``, ``strict`` or ``parallel_tool_calls``,
which servers do not all honour. It uses the standard library alone, as the notifier does.

**Where it may go is judged here, on every request.** The address is resolved, every address it
resolves to is judged by :func:`noust.core.outbound.require_public_host` with the operator's
``security.allow_private_hosts`` (read when the request is made, so taking an entry away stops a
client that already exists), and the connection is then made to the address that was judged and to
no other, so a name that answers differently a moment later is not asked twice. A redirect is never
followed. Plain ``http://`` is only for a host the operator listed, because the key and what the
assistant reads of the server would travel in the clear to anyone else.

**What it does when the provider misbehaves**, each case with a test: a 429 or a 5xx is waited out
(``Retry-After`` honoured, up to a ceiling) and tried again a few times; a server that does not
stream, or refuses to stream with tools, gets the plain request and is remembered; a call to a tool
is handed over only when it is whole (a cut stream never yields half of one), pieces of a call are
put together by ``index`` and, when a server repeats ``index``, by ``id``; arguments that are not
JSON travel as they came and fail only when asked for (:meth:`ToolCall.arguments_object`), so the
loop can tell the model what was wrong; a provider that sends no ``usage`` is estimated, and the
estimate says so. A read that goes quiet is a timeout, the whole request has a deadline, and a
:class:`CancelToken` closes the connection from another thread.

**The key** is given to the constructor, goes only in the ``Authorization`` header, is not in
``repr`` and is scrubbed from any error text a provider sends back.
"""

from __future__ import annotations

import email.utils
import http.client
import json
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from noust import __version__
from noust.core import outbound
from noust.core.exceptions import IntegrationError, ValidationError
from noust.core.secret_detection import mask_secret_shapes
from noust.core.server_text import ServerText, say

__all__ = [
    "MAX_REPLY_BYTES",
    "USER_AGENT",
    "CancelToken",
    "Chunk",
    "Completion",
    "Endpoint",
    "Finish",
    "MalformedArguments",
    "ProviderAddressError",
    "ProviderClient",
    "ProviderError",
    "TextDelta",
    "ToolCall",
    "Usage",
    "assistant_message",
    "check_address",
    "estimate_tokens",
    "tool_message",
]

#: What every request says it is: the program and its version, nothing else.
USER_AGENT = f"noust/{__version__}"

#: The most a provider's answer may weigh. A conversation's reply is text and a few calls.
MAX_REPLY_BYTES = 8 * 1024 * 1024

#: The longest line of a stream that is believed.
_MAX_LINE = 1024 * 1024

#: The longest part of a provider's own words that is kept in a sentence.
_MAX_QUOTE = 400

#: What a status that says "try again" is.
_RETRYABLE = frozenset({429, 500, 502, 503, 504, 529})


class ProviderError(IntegrationError):
    """
    A request to a provider that was not made, or did not end well.

    Attributes:
        sentence: What went wrong, as a sentence of the catalog.
        code: The catalog code of the sentence (``assistant.provider.timeout``), which the console
            translates and a caller decides by.
        status: The HTTP status the provider answered with, when it answered.
        retry_after: Seconds the provider asked to wait, when it asked.
    """

    def __init__(
        self, text: ServerText, *, status: int | None = None, retry_after: float | None = None
    ) -> None:
        """
        Args:
            text: The sentence.
            status: The HTTP status, when there was one.
            retry_after: What the provider asked to wait, in seconds.
        """
        super().__init__(text.text, text.hint or "", coded=text)
        self.sentence = text
        self.status = status
        self.retry_after = retry_after

    @property
    def code(self) -> str:
        """The code of the sentence."""
        return self.sentence.code


class ProviderAddressError(ProviderError, ValidationError):
    """
    The address of a provider cannot be called: it is not a base address, is plain http to a host
    that was not listed, does not resolve or leads into the machine's own networks.

    What the person typed is what is wrong, so it answers as a request that was refused (400), where
    every other :class:`ProviderError` is the provider's fault (502).
    """


class MalformedArguments(ValueError):
    """The arguments of a tool call are not a JSON object; the message says it to the model."""


class CancelToken:
    """A flag another thread sets to stop a request that is being read."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        """Ask for the request to stop."""
        self._event.set()

    def is_set(self) -> bool:
        """Returns whether it was asked for."""
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """
        Args:
            seconds: How long to wait at most.

        Returns:
            True when it was set, False when the time passed.
        """
        return self._event.wait(seconds)


def estimate_tokens(value: object) -> int:
    """
    Estimate how many tokens a piece of text or a structure is, without a tokenizer.

    Args:
        value: Text, or anything JSON can hold.

    Returns:
        Its characters over four, rounded up. Only for when a provider reports no usage, and always
        said to be an estimate.
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return (len(text) + 3) // 4


@dataclass(frozen=True)
class Usage:
    """
    What a step cost, in tokens.

    Attributes:
        prompt_tokens: What was sent.
        completion_tokens: What the model said.
        estimated: True when the provider reported nothing and these are characters over four.
    """

    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        """Everything the step used."""
        return self.prompt_tokens + self.completion_tokens

    def plus(self, other: Usage) -> Usage:
        """
        Args:
            other: Another step's usage.

        Returns:
            The two added; an estimate if either was.
        """
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.estimated or other.estimated,
        )


@dataclass(frozen=True)
class TextDelta:
    """A piece of what the model is saying.

    Attributes:
        text: The piece.
    """

    text: str


@dataclass(frozen=True)
class ToolCall:
    """
    One call to a tool, whole.

    Attributes:
        id: The call's identifier, unique within the answer: what the tool's result is sent back
            under.
        name: The tool. Not checked here: the loop answers an unknown one with an error.
        arguments: The arguments exactly as the model sent them (JSON text; ``{}`` when empty).
    """

    id: str
    name: str
    arguments: str

    def arguments_object(self) -> dict[str, Any]:
        """
        Read the arguments.

        Returns:
            The JSON object the model sent.

        Raises:
            MalformedArguments: They are not JSON or not an object. The message is written to the
                model, so that it can send them again right.
        """
        try:
            decoded = json.loads(self.arguments or "{}")
        except ValueError as exc:
            raise MalformedArguments(
                f"The arguments of {self.name or 'the call'} are not valid JSON ({exc}). "
                "Send one JSON object with the tool's arguments."
            ) from exc
        if not isinstance(decoded, dict):
            raise MalformedArguments(
                f"The arguments of {self.name or 'the call'} must be a JSON object, not "
                f"{type(decoded).__name__}. Send one JSON object with the tool's arguments."
            )
        return decoded


@dataclass(frozen=True)
class Finish:
    """
    The end of an answer.

    Attributes:
        reason: ``stop``, ``tool_calls``, ``length`` or what the provider said.
        usage: What it cost.
    """

    reason: str
    usage: Usage


#: What :meth:`ProviderClient.stream` yields: the text as it comes, each call when it is whole,
#: and the end, in that order.
Chunk = TextDelta | ToolCall | Finish


@dataclass(frozen=True)
class Completion:
    """
    A whole answer.

    Attributes:
        text: What the model said.
        tool_calls: The calls it made, in order.
        finish_reason: Why it stopped.
        usage: What it cost.
    """

    text: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str
    usage: Usage


def assistant_message(done: Completion) -> dict[str, Any]:
    """
    Args:
        done: An answer.

    Returns:
        The message that puts it in the conversation for the next request.
    """
    message: dict[str, Any] = {"role": "assistant", "content": done.text or None}
    if done.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in done.tool_calls
        ]
    return message


def tool_message(call_id: str, content: str) -> dict[str, Any]:
    """
    Args:
        call_id: The id of the call being answered.
        content: What the tool returned, already masked.

    Returns:
        The message that gives it to the model.
    """
    return {"role": "tool", "tool_call_id": call_id, "content": content}


@dataclass(frozen=True)
class Endpoint:
    """
    An address that was judged: where to connect, and nothing else.

    Attributes:
        scheme: ``http`` or ``https``.
        host: The name as typed; what the certificate is checked against.
        port: The port.
        base_path: The path of the base address, without a trailing slash.
        addresses: The addresses the name resolved to, all of them judged.
    """

    scheme: str
    host: str
    port: int
    base_path: str
    addresses: tuple[str, ...]

    def path(self, tail: str) -> str:
        """
        Args:
            tail: What follows the base, such as ``/chat/completions``.

        Returns:
            The path to request, checked to stay on this host.
        """
        return outbound.api_path(f"{self.base_path}{tail}")


def _bad_address(address: str) -> ProviderError:
    """
    Args:
        address: What was typed.

    Returns:
        The refusal, quoting it safely.
    """
    shown = mask_secret_shapes(address.replace("\n", " ").replace("\r", " "))[:200]
    return ProviderAddressError(say("assistant.provider.bad_address", address=shown))


def check_address(
    address: str,
    *,
    allowed_hosts: Iterable[str] | None = None,
    resolver: Callable[[str], tuple[str, ...]] | None = None,
) -> Endpoint:
    """
    Judge a provider's base address: its shape, and where it leads.

    Args:
        address: The base address, such as ``https://api.example.org/v1``.
        allowed_hosts: The hosts the operator listed as allowed though internal; the live
            ``security.allow_private_hosts`` when None.
        resolver: How to resolve a name; the module's own, looked up now, when None.

    Returns:
        The endpoint: parsed, resolved and judged.

    Raises:
        ProviderError: The address is not a base address (``bad_address``), is plain http to a host
            that was not listed (``insecure_address``), does not resolve (``unresolved``) or
            leads into the machine's own networks (``destination_refused``).
    """
    if (
        not address
        or address != address.strip()
        or any(c.isspace() or ord(c) < 32 for c in address)
    ):
        raise _bad_address(address)
    try:
        parts = urlsplit(address)
        port = parts.port
    except ValueError as exc:
        raise _bad_address(address) from exc
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    if (
        scheme not in ("http", "https")
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "?" in address
        or "#" in address
        or "@" in parts.netloc
    ):
        raise _bad_address(address)
    base_path = parts.path.rstrip("/")
    try:
        outbound.api_path(f"{base_path}/chat/completions")
    except outbound.UnsafePath as exc:
        raise _bad_address(address) from exc
    allowed = frozenset(
        str(entry).lower()
        for entry in (outbound.trusted_hosts() if allowed_hosts is None else allowed_hosts)
    )
    # Judged on the addresses resolved here, once, and returned: what is dialled is what was judged.
    lookup = resolver or outbound.resolve_host
    try:
        addresses = tuple(lookup(host))
        outbound.require_public_host(host, allowed=allowed, resolver=lambda _name: addresses)
    except OSError as exc:
        raise ProviderAddressError(say("assistant.provider.unresolved", host=host)) from exc
    except outbound.DestinationRefused as exc:
        if exc.reason == "internal":
            raise ProviderAddressError(
                say("assistant.provider.destination_refused", host=host, address=exc.address or "")
            ) from exc
        raise ProviderAddressError(say("assistant.provider.unresolved", host=host)) from exc
    if not addresses:
        raise ProviderAddressError(say("assistant.provider.unresolved", host=host))
    if scheme == "http" and host.lower() not in allowed:
        raise ProviderAddressError(say("assistant.provider.insecure_address", host=host))
    return Endpoint(
        scheme=scheme,
        host=host,
        port=port or (443 if scheme == "https" else 80),
        base_path=base_path,
        addresses=addresses,
    )


def _dial(addresses: Sequence[str], port: int, timeout: float) -> socket.socket:
    """
    Connect to the first of the judged addresses that answers.

    Args:
        addresses: The addresses that were judged.
        port: The port.
        timeout: The read and connect timeout of the socket.

    Returns:
        The connected socket.

    Raises:
        OSError: None of them answered; the last error.
    """
    last: OSError | None = None
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout)
        except OSError as exc:
            last = exc
    raise last or OSError("there is no address to connect to")


class _PlainConnection(http.client.HTTPConnection):
    """A connection that dials the addresses it was given and never resolves the name again."""

    def __init__(self, endpoint: Endpoint, timeout: float) -> None:
        super().__init__(endpoint.host, endpoint.port, timeout=timeout)
        self._judged = endpoint.addresses
        self._read_timeout = timeout

    def connect(self) -> None:
        """Dial one of the judged addresses."""
        self.sock = _dial(self._judged, self.port, self._read_timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class _SecureConnection(http.client.HTTPSConnection):
    """The same over TLS, with the certificate checked against the name that was typed."""

    def __init__(self, endpoint: Endpoint, timeout: float) -> None:
        self._tls = ssl.create_default_context()
        super().__init__(endpoint.host, endpoint.port, timeout=timeout, context=self._tls)
        self._judged = endpoint.addresses
        self._read_timeout = timeout

    def connect(self) -> None:
        """Dial one of the judged addresses and start TLS for the name."""
        raw = _dial(self._judged, self.port, self._read_timeout)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = self._tls.wrap_socket(raw, server_hostname=self.host)


class _Watchdog:
    """
    Closes a connection when the whole request has run out of time or was cancelled.

    A socket timeout only bounds one read; a provider that trickles a byte a second never trips
    it. This thread is what bounds the request, and it also is what makes a cancellation from
    another thread stop a read that is blocked.
    """

    def __init__(self, deadline: float | None, cancel: CancelToken | None) -> None:
        self._deadline = deadline
        self._cancel = cancel
        self._done = threading.Event()
        self._connection: http.client.HTTPConnection | None = None
        self.reason: str | None = None
        self._thread = threading.Thread(target=self._run, name="assistant-watchdog", daemon=True)

    def start(self, connection: http.client.HTTPConnection) -> None:
        """
        Args:
            connection: What to close when the time is up.
        """
        self._connection = connection
        self._thread.start()

    def stop(self) -> None:
        """The request is over."""
        self._done.set()

    def _run(self) -> None:
        while not self._done.wait(0.05):
            if self._cancel is not None and self._cancel.is_set():
                self.reason = "cancelled"
            elif self._deadline is not None and time.monotonic() >= self._deadline:
                self.reason = "timeout"
            else:
                continue
            self._abort()
            return

    def _abort(self) -> None:
        # Only the socket is shut down: the thread that is reading wakes up with an end of file and
        # closes the connection itself. Closing it from here would pull the file out from under a
        # read in progress.
        sock = getattr(self._connection, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


@dataclass
class _Slot:
    """A tool call being put together from the pieces of a stream."""

    id: str = ""
    name: str = ""
    arguments: list[str] = field(default_factory=list)
    whole: str | None = None


class _Answer:
    """What a stream adds up to, piece by piece."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.slots: list[_Slot] = []
        self._by_index: dict[int, _Slot] = {}
        self.finish: str | None = None
        self.usage: Usage | None = None

    def take(self, payload: Mapping[str, Any]) -> str:
        """
        Add one event.

        Args:
            payload: The decoded ``data:`` of a chunk.

        Returns:
            The text it carried, to hand on at once.

        Raises:
            ProviderError: The provider reported an error in the middle of its answer.
        """
        error = payload.get("error")
        if error:
            raise ProviderError(say("assistant.provider.reported_error", message=_words(error, "")))
        usage = payload.get("usage")
        if isinstance(usage, Mapping):
            self.usage = _usage_of(usage)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            return ""
        choice = choices[0]
        if choice.get("finish_reason"):
            self.finish = str(choice["finish_reason"])
        delta = choice.get("delta")
        if not isinstance(delta, Mapping):
            return ""
        for piece in delta.get("tool_calls") or []:
            if isinstance(piece, Mapping):
                self.add_call(piece)
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.text.append(content)
            return content
        return ""

    def add_call(self, piece: Mapping[str, Any]) -> None:
        """
        Add a piece of a tool call, or a whole one.

        Args:
            piece: One element of a delta's ``tool_calls``, or of a message's.
        """
        raw_index = piece.get("index")
        index = raw_index if isinstance(raw_index, int) else None
        identifier = piece.get("id") if isinstance(piece.get("id"), str) else ""
        slot = self._by_index.get(index) if index is not None else None
        if slot is None and index is None and self.slots:
            slot = self.slots[-1]
        # Some servers say index 0 for every call: a new id at a place that already has one is
        # another call.
        if slot is not None and identifier and slot.id and identifier != slot.id:
            slot = None
        if slot is None:
            slot = _Slot()
            self.slots.append(slot)
            if index is not None:
                self._by_index[index] = slot
        if identifier and not slot.id:
            slot.id = str(identifier)
        function = piece.get("function")
        if not isinstance(function, Mapping):
            return
        name = function.get("name")
        if isinstance(name, str) and name and name != slot.name:
            slot.name += name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            slot.arguments.append(arguments)
        elif isinstance(arguments, Mapping):
            slot.whole = json.dumps(arguments)

    def calls(self) -> list[ToolCall]:
        """
        Returns:
            The calls, whole, with an id each that no other call of the answer has.
        """
        made: list[ToolCall] = []
        used: set[str] = set()
        for number, slot in enumerate(self.slots, start=1):
            arguments = slot.whole if slot.whole is not None else "".join(slot.arguments)
            if not slot.name and not arguments.strip():
                continue
            identifier = slot.id or f"call_{number}"
            while identifier in used:
                identifier = f"{identifier}_{number}"
            used.add(identifier)
            made.append(ToolCall(identifier, slot.name, arguments.strip() or "{}"))
        return made


def _usage_of(raw: Mapping[str, Any]) -> Usage | None:
    """
    Args:
        raw: A ``usage`` object.

    Returns:
        The usage it reports, or None when it reports no numbers.
    """
    prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
    if isinstance(prompt, int) and isinstance(completion, int) and not isinstance(prompt, bool):
        return Usage(max(prompt, 0), max(completion, 0), estimated=False)
    return None


def _words(error: object, key: str) -> str:
    """
    Take the provider's own words out of an error document.

    Args:
        error: What was decoded of an error body, or its ``error`` member.
        key: The API key, to be scrubbed out of what comes back.

    Returns:
        A short text: ``error.message`` or ``message`` or ``detail`` or the text itself, with
        credentials masked and the key removed.
    """
    text = ""
    if isinstance(error, Mapping):
        inner = error.get("error", error)
        if isinstance(inner, Mapping):
            text = str(inner.get("message") or inner.get("detail") or inner.get("type") or "")
        else:
            text = str(inner)
        if not text:
            text = str(error.get("message") or error.get("detail") or "")
    elif error is not None:
        text = str(error)
    text = " ".join(text.split())
    if key:
        text = text.replace(key, "***")
    return mask_secret_shapes(text)[:_MAX_QUOTE]


class ProviderClient:
    """A model provider, as far as the assistant is concerned."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        allowed_hosts: Iterable[str] | None = None,
        read_timeout: float = 60.0,
        total_timeout: float = 300.0,
        max_retries: int = 3,
        max_wait: float = 30.0,
        streaming: bool = True,
        max_output_tokens: int | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        """
        Args:
            base_url: The provider's base address, up to its version.
            model: The model's name as the provider calls it.
            api_key: The key, when the provider asks for one. Sent only in ``Authorization``.
            allowed_hosts: Hosts allowed though internal; the live ``security.allow_private_hosts``
                when None, read at every request.
            read_timeout: Seconds a read may wait for the provider.
            total_timeout: Seconds a whole request may take, retries and waits included.
            max_retries: How many times a 429 or a 5xx is tried again.
            max_wait: The longest wait between tries that is honoured; a provider that asks for
                more is given up on, with what it asked said.
            streaming: Ask for a stream; False to begin with the plain request.
            max_output_tokens: A ceiling on what the model says, sent as ``max_tokens``.
            sleeper: Replaces waiting; for tests.
        """
        self.base_url = base_url
        self.model = model
        self.streaming = streaming
        self._api_key = api_key
        self._allowed = allowed_hosts
        self._read_timeout = read_timeout
        self._total_timeout = total_timeout
        self._max_retries = max_retries
        self._max_wait = max_wait
        self._max_output_tokens = max_output_tokens
        self._sleeper = sleeper
        self._stream_options = True

    def __repr__(self) -> str:
        return f"ProviderClient(base_url={self.base_url!r}, model={self.model!r}, key=***)"

    # -- what the assistant asks of a provider ---------------------------------------------------

    def stream(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] = (),
        *,
        cancel: CancelToken | None = None,
    ) -> Iterator[Chunk]:
        """
        Send a conversation and read the answer as it comes.

        Args:
            messages: The conversation in the protocol's messages, already masked.
            tools: The tools on offer, as the protocol describes them.
            cancel: Set from another thread to stop the request.

        Yields:
            :class:`TextDelta` for each piece of text, a :class:`ToolCall` for each call once it is
            whole, and a :class:`Finish` last.

        Raises:
            ProviderError: Whatever kept the answer from being whole, with its code.
        """
        request = self._body(messages, tools)
        sent = estimate_tokens(request)
        while True:
            try:
                yield from self._answer(request, sent, cancel)
                return
            except _Retry:
                continue

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] = (),
        *,
        cancel: CancelToken | None = None,
    ) -> Completion:
        """
        Send a conversation and wait for the whole answer.

        Args:
            messages: The conversation, already masked.
            tools: The tools on offer.
            cancel: Set from another thread to stop the request.

        Returns:
            The answer.

        Raises:
            ProviderError: Whatever kept the answer from being whole.
        """
        text: list[str] = []
        calls: list[ToolCall] = []
        finish = Finish("stop", Usage(0, 0, estimated=True))
        for chunk in self.stream(messages, tools, cancel=cancel):
            if isinstance(chunk, TextDelta):
                text.append(chunk.text)
            elif isinstance(chunk, ToolCall):
                calls.append(chunk)
            else:
                finish = chunk
        return Completion("".join(text), tuple(calls), finish.reason, finish.usage)

    def list_models(self, *, cancel: CancelToken | None = None) -> list[str]:
        """
        Ask the provider what models it has.

        Args:
            cancel: Set from another thread to stop the request.

        Returns:
            Their names, as the provider lists them.

        Raises:
            ProviderError: The provider has no such list (``not_found``) or answered otherwise.
        """
        with self._exchange("GET", "/models", None, cancel, accept="application/json") as reply:
            raw = reply.response.read(MAX_REPLY_BYTES + 1)
        if len(raw) > MAX_REPLY_BYTES:
            raise ProviderError(
                say("assistant.provider.too_large", megabytes=MAX_REPLY_BYTES >> 20)
            )
        try:
            document = json.loads(raw)
        except ValueError as exc:
            raise ProviderError(say("assistant.provider.bad_reply")) from exc
        entries = (
            document.get("data", document.get("models")) if isinstance(document, dict) else document
        )
        if not isinstance(entries, list):
            raise ProviderError(say("assistant.provider.bad_reply"))
        names: list[str] = []
        for entry in entries:
            name = (
                entry.get("id") or entry.get("name") or entry.get("model")
                if isinstance(entry, dict)
                else entry
            )
            if isinstance(name, str) and name:
                names.append(name)
        return names

    # -- the request -----------------------------------------------------------------------------

    def _body(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """
        Args:
            messages: The conversation.
            tools: The tools on offer.

        Returns:
            What is sent, before it is told to stream or not.
        """
        body: dict[str, Any] = {"model": self.model, "messages": list(messages)}
        if tools:
            body["tools"] = list(tools)
        if self._max_output_tokens is not None:
            body["max_tokens"] = self._max_output_tokens
        return body

    def _answer(
        self, request: dict[str, Any], sent: int, cancel: CancelToken | None
    ) -> Iterator[Chunk]:
        """
        Make one attempt at the request in whichever way the client currently asks.

        Args:
            request: The body without ``stream``.
            sent: The estimate of what is sent, for when no usage comes back.
            cancel: The cancellation flag.

        Yields:
            The chunks of the answer.

        Raises:
            _Retry: The provider refused the way it was asked (a stream, the usage option): it
                is asked again the other way, before anything was handed over.
            ProviderError: As :meth:`stream`.
        """
        body = dict(request)
        if self.streaming:
            body["stream"] = True
            if self._stream_options:
                body["stream_options"] = {"include_usage": True}
        else:
            body["stream"] = False
        try:
            with self._exchange(
                "POST",
                "/chat/completions",
                body,
                cancel,
                accept="text/event-stream" if self.streaming else "application/json",
            ) as reply:
                kind = reply.response.getheader("Content-Type", "").split(";")[0].strip().lower()
                if kind == "text/event-stream":
                    answer = _Answer()
                    yield from self._read_stream(reply, answer)
                else:
                    answer = self._read_document(reply)
                    if answer.text:
                        yield TextDelta("".join(answer.text))
        except _Refused as refusal:
            if refusal.about == "stream_options" and self._stream_options:
                self._stream_options = False
            elif refusal.about == "stream" and self.streaming:
                self.streaming = False
            else:
                raise refusal.error from None
            raise _Retry from None
        calls = answer.calls()
        yield from calls
        used = answer.usage or Usage(
            sent,
            estimate_tokens("".join(answer.text))
            + sum(estimate_tokens(c.name) + estimate_tokens(c.arguments) for c in calls),
            estimated=True,
        )
        yield Finish(answer.finish or ("tool_calls" if calls else "stop"), used)

    def _read_stream(self, reply: _Reply, answer: _Answer) -> Iterator[Chunk]:
        """
        Read the events of a stream into an answer.

        Args:
            reply: The open answer.
            answer: Where the pieces are put together.

        Yields:
            The text of each event as it arrives.

        Raises:
            ProviderError: The stream was cut, or the provider reported an error in it.
        """
        seen_done = False
        try:
            for data in _events(reply.response):
                if data.strip() == "[DONE]":
                    seen_done = True
                    break
                try:
                    payload = json.loads(data)
                except ValueError as exc:
                    raise ProviderError(say("assistant.provider.bad_reply")) from exc
                if not isinstance(payload, dict):
                    raise ProviderError(say("assistant.provider.bad_reply"))
                text = answer.take(payload)
                if text:
                    yield TextDelta(text)
        except (http.client.HTTPException, OSError, ValueError) as exc:
            reply.fail(exc)
            raise ProviderError(say("assistant.provider.cut")) from exc
        if not seen_done and answer.finish is None:
            reply.fail(None)
            raise ProviderError(say("assistant.provider.cut"))

    def _read_document(self, reply: _Reply) -> _Answer:
        """
        Read an answer that came as one JSON document.

        Args:
            reply: The open answer.

        Returns:
            What it says, as a finished answer.

        Raises:
            ProviderError: It is cut, too large or not a completion.
        """
        try:
            raw = reply.response.read(MAX_REPLY_BYTES + 1)
        except (http.client.HTTPException, OSError) as exc:
            reply.fail(exc)
            raise ProviderError(say("assistant.provider.cut")) from exc
        if len(raw) > MAX_REPLY_BYTES:
            raise ProviderError(
                say("assistant.provider.too_large", megabytes=MAX_REPLY_BYTES >> 20)
            )
        try:
            document = json.loads(raw)
            message = document["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(say("assistant.provider.bad_reply")) from exc
        answer = _Answer()
        if isinstance(document, dict):
            if isinstance(document.get("usage"), Mapping):
                answer.usage = _usage_of(document["usage"])
            answer.finish = str(document["choices"][0].get("finish_reason") or "") or None
        content = message.get("content") if isinstance(message, Mapping) else None
        if isinstance(content, str) and content:
            answer.text.append(content)
        for number, call in enumerate(
            message.get("tool_calls") or [] if isinstance(message, Mapping) else []
        ):
            if isinstance(call, Mapping):
                answer.add_call({**call, "index": number})
        return answer

    # -- the exchange ----------------------------------------------------------------------------

    def _exchange(
        self,
        method: str,
        tail: str,
        body: dict[str, Any] | None,
        cancel: CancelToken | None,
        *,
        accept: str,
    ) -> _Exchange:
        """
        Args:
            method: ``GET`` or ``POST``.
            tail: What follows the base address.
            body: The JSON body, or None.
            cancel: The cancellation flag.
            accept: The ``Accept`` header.

        Returns:
            A context manager that makes the request, tries again where that is right, and gives
            the open answer.
        """
        return _Exchange(self, method, tail, body, cancel, accept)

    def _wait(self, seconds: float, cancel: CancelToken | None) -> None:
        """
        Wait between two tries, ending early when the request is cancelled.

        Args:
            seconds: How long.
            cancel: The cancellation flag.
        """
        if self._sleeper is not None:
            self._sleeper(seconds)
        elif cancel is not None:
            cancel.wait(seconds)
        else:
            time.sleep(seconds)


class _Retry(Exception):
    """Ask again the other way: the provider refused the way it was asked, nothing was handed over."""


class _Refused(Exception):
    """A 400 whose words say which part of the request it did not accept."""

    def __init__(self, about: str, error: ProviderError) -> None:
        super().__init__(about)
        self.about = about
        self.error = error


@dataclass
class _Reply:
    """An open answer: the response, and the watchdog that bounds it."""

    response: http.client.HTTPResponse
    watchdog: _Watchdog
    endpoint: Endpoint
    seconds: int

    def fail(self, cause: BaseException | None) -> None:
        """
        Turn what went wrong with a read into the error that says it.

        Args:
            cause: What was raised, or None when the watchdog ended the read quietly.

        Raises:
            ProviderError: ``cancelled`` or ``timeout`` when the watchdog ended it, ``timeout``
                when a read ran out; otherwise it returns and the caller says ``cut``.
        """
        if self.watchdog.reason == "cancelled":
            raise ProviderError(say("assistant.provider.cancelled")) from cause
        if self.watchdog.reason == "timeout" or isinstance(cause, TimeoutError):
            raise ProviderError(say("assistant.provider.timeout", seconds=self.seconds)) from cause


def _events(response: http.client.HTTPResponse) -> Iterator[str]:
    """
    Read the ``data:`` of each event of a server-sent stream.

    Args:
        response: The open answer.

    Yields:
        The data of each event, its lines joined, as the specification says.

    Raises:
        ProviderError: The answer is larger than is believed or a line is.
    """
    lines: list[str] = []
    total = 0
    while True:
        raw = response.readline(_MAX_LINE + 1)
        if not raw:
            if lines:
                yield "\n".join(lines)
            return
        total += len(raw)
        if total > MAX_REPLY_BYTES:
            raise ProviderError(
                say("assistant.provider.too_large", megabytes=MAX_REPLY_BYTES >> 20)
            )
        if len(raw) > _MAX_LINE:
            raise ProviderError(say("assistant.provider.bad_reply"))
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if lines:
                yield "\n".join(lines)
                lines = []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if name == "data":
            lines.append(value[1:] if value.startswith(" ") else value)


class _Exchange:
    """
    One request and the answer to it, as a context manager.

    Entering makes the request: the address is judged, the connection is made to the judged
    address, a 429 or a 5xx is waited out and asked again, and any other failure is the error that
    says it. What is returned is an open answer whose body has not been read; leaving closes it.
    """

    def __init__(
        self,
        client: ProviderClient,
        method: str,
        tail: str,
        body: dict[str, Any] | None,
        cancel: CancelToken | None,
        accept: str,
    ) -> None:
        self._client = client
        self._method = method
        self._tail = tail
        self._payload = json.dumps(body).encode("utf-8") if body is not None else None
        self._streamed = bool(body and body.get("stream"))
        self._options = bool(body and body.get("stream_options"))
        self._cancel = cancel
        self._accept = accept
        self._reply: _Reply | None = None
        self._connection: http.client.HTTPConnection | None = None

    def __enter__(self) -> _Reply:
        client = self._client
        started = time.monotonic()
        deadline = started + client._total_timeout
        attempt = 0
        while True:
            if self._cancel is not None and self._cancel.is_set():
                raise ProviderError(say("assistant.provider.cancelled"))
            reply = self._once(deadline)
            status = reply.response.status
            if 200 <= status < 300:
                self._reply = reply
                return reply
            problem = self._problem(reply, status, attempt)
            reply.watchdog.stop()
            self._close()
            if not isinstance(problem, _Wait):
                raise problem
            attempt += 1
            if problem.seconds > client._max_wait:
                raise ProviderError(
                    say("assistant.provider.rate_limited", status=status, tries=attempt),
                    status=status,
                    retry_after=problem.seconds,
                )
            if time.monotonic() + problem.seconds >= deadline:
                raise ProviderError(
                    say("assistant.provider.timeout", seconds=int(client._total_timeout))
                )
            client._wait(problem.seconds, self._cancel)

    def __exit__(self, *exc: object) -> None:
        if self._reply is not None:
            self._reply.watchdog.stop()
        self._close()

    def _close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except OSError:
                pass
            self._connection = None

    def _once(self, deadline: float) -> _Reply:
        """
        Make the request one time and read the status line and the headers.

        Args:
            deadline: When the whole request ends, on the monotonic clock.

        Returns:
            The open answer.

        Raises:
            ProviderError: It could not be made.
        """
        client = self._client
        endpoint = check_address(client.base_url, allowed_hosts=client._allowed)
        connection: http.client.HTTPConnection
        if endpoint.scheme == "https":
            connection = _SecureConnection(endpoint, client._read_timeout)
        else:
            connection = _PlainConnection(endpoint, client._read_timeout)
        self._connection = connection
        watchdog = _Watchdog(deadline, self._cancel)
        watchdog.start(connection)
        headers = {
            "Accept": self._accept,
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }
        if self._payload is not None:
            headers["Content-Type"] = "application/json"
        if client._api_key:
            headers["Authorization"] = f"Bearer {client._api_key}"
        try:
            connection.request(
                self._method, endpoint.path(self._tail), body=self._payload, headers=headers
            )
            response = connection.getresponse()
        except (http.client.HTTPException, OSError, ValueError) as exc:
            watchdog.stop()
            self._close()
            if watchdog.reason == "cancelled":
                raise ProviderError(say("assistant.provider.cancelled")) from exc
            if watchdog.reason == "timeout" or isinstance(exc, TimeoutError):
                raise ProviderError(
                    say("assistant.provider.timeout", seconds=int(client._read_timeout))
                ) from exc
            raise ProviderError(
                say(
                    "assistant.provider.unreachable",
                    host=endpoint.host,
                    reason=_words(str(exc), client._api_key) or type(exc).__name__,
                )
            ) from exc
        return _Reply(response, watchdog, endpoint, int(client._total_timeout))

    def _problem(
        self, reply: _Reply, status: int, attempt: int
    ) -> _Wait | _Refused | ProviderError:
        """
        Say what an answer that is not a success means.

        Args:
            reply: The answer.
            status: Its status.
            attempt: How many tries were already made.

        Returns:
            How long to wait before asking again (:class:`_Wait`), a refusal of the way the
            request was made (:class:`_Refused`) or the error to raise.
        """
        client = self._client
        try:
            raw = reply.response.read(64 * 1024)
        except (http.client.HTTPException, OSError):
            raw = b""
        try:
            words = _words(json.loads(raw), client._api_key) if raw else ""
        except ValueError:
            words = _words(raw.decode("utf-8", errors="replace"), client._api_key)
        if 300 <= status < 400:
            location = reply.response.getheader("Location", "")
            return ProviderError(
                say(
                    "assistant.provider.redirected",
                    location=_words(location, client._api_key)[:200] or "another address",
                ),
                status=status,
            )
        if status in _RETRYABLE:
            asked = _retry_after(reply.response)
            if attempt < client._max_retries:
                return _Wait(
                    asked if asked is not None else min(float(2**attempt), client._max_wait)
                )
            if status == 429:
                gave_up = say("assistant.provider.rate_limited", status=status, tries=attempt + 1)
            else:
                gave_up = say(
                    "assistant.provider.server_error",
                    status=status,
                    tries=attempt + 1,
                    message=words or "no details",
                )
            return ProviderError(gave_up, status=status, retry_after=asked)
        if status in (401, 403):
            return ProviderError(
                say(
                    "assistant.provider.unauthorized", status=status, message=words or "no details"
                ),
                status=status,
            )
        if status == 404:
            return ProviderError(
                say("assistant.provider.not_found", status=status, message=words or "no details"),
                status=status,
            )
        rejected = ProviderError(
            say("assistant.provider.rejected", status=status, message=words or "no details"),
            status=status,
        )
        lowered = words.lower()
        if status in (400, 422, 501) and self._streamed:
            if "stream_options" in lowered or "include_usage" in lowered:
                return _Refused("stream_options", rejected)
            if "stream" in lowered:
                return _Refused("stream", rejected)
        return rejected


@dataclass(frozen=True)
class _Wait:
    """How long to wait before the next try."""

    seconds: float


def _retry_after(response: http.client.HTTPResponse) -> float | None:
    """
    Read the ``Retry-After`` header, which is a number of seconds or a date.

    Args:
        response: The answer.

    Returns:
        Seconds to wait, never negative; None when the header is absent or unreadable.
    """
    raw = response.getheader("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(float(raw), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return max(when.timestamp() - time.time(), 0.0)
