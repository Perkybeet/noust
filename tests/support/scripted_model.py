# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A model that answers what the test dictates, speaking the protocol of the built-in assistant.

The assistant's client talks to a provider with Chat Completions (``POST <base>/chat/completions``
answered as a stream of ``data:`` events that ends in ``[DONE]``, or as one JSON document) and
asks it what models it has (``GET <base>/models``). This is a server of that protocol inside the
test process: no real provider is ever asked, and the cases that matter are written as data. One
:class:`Reply` is what the next request will be answered with, and a request with no reply
waiting is answered with an error that says so, so a test that makes a call it did not script
fails loudly instead of waiting.

What a :class:`Reply` can be, from the cases a real provider produces:

- **Text or tool calls**, streamed or not, with or without ``usage``, in pieces of a size the
  test chooses (a JSON document of arguments split in the middle of a string).
- **Broken arguments**: not JSON, or JSON cut off, sent as the model's last word.
- **A tool call that repeats its ``index``**, as some servers do for every call.
- **An error status** with ``Retry-After``, which the client must wait out and try again.
- **A cut**: the connection closes after some events, with no ``[DONE]``, in the middle of a
  call or of a sentence.
- **A server that does not stream**: it answers a streamed request with one JSON document, or
  refuses the streamed request and takes the same one without ``stream``.
- **A slow one**: it waits before answering, or between events, for the timeouts.

Every request is recorded (method, path, headers, the body as JSON and as the bytes that
arrived), so a test can prove what was sent and, above all, what was not: a secret planted in a
variable or a log must never be in any of them.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

__all__ = ["Recorded", "Reply", "ScriptedCall", "ScriptedModel"]


def body_wants_stream(body: Any) -> bool:
    """
    Args:
        body: A decoded request body.

    Returns:
        Whether it asks for a stream.
    """
    return isinstance(body, dict) and bool(body.get("stream"))


@dataclass(frozen=True)
class ScriptedCall:
    """
    One tool call the model makes.

    Attributes:
        name: The tool.
        arguments: What it is called with: a dictionary (sent as JSON) or text (sent as it is, to
            make broken arguments).
        id: The call's identifier; ``call_<n>`` when empty.
    """

    name: str
    arguments: dict[str, Any] | str
    id: str = ""

    def text(self) -> str:
        """The arguments as the model would send them."""
        if isinstance(self.arguments, str):
            return self.arguments
        return json.dumps(self.arguments)


@dataclass
class Reply:
    """
    What the next request is answered with. Build one with the class's constructors.

    Attributes:
        status: The HTTP status; anything but 200 is an error answer with ``error_body``.
        headers: Extra headers of the answer (``Retry-After``).
        text: What the model says.
        calls: The tool calls it makes.
        stream: True to stream, False to answer with one document, None to do what the request
            asked.
        usage: ``(prompt_tokens, completion_tokens)`` to report; None to report nothing.
        piece: The size of the pieces text and arguments are streamed in.
        same_index: Every call carries ``index`` 0, as some servers send it.
        cut_after: Close the connection after this many events, with no ``[DONE]``.
        delay: Seconds to wait before answering.
        gap: Seconds to wait between events.
        finish_reason: Overrides what the last event says (``length``).
        error_body: The body of an error answer.
        refuse_stream: Answer a streamed request with a 400 that says streaming is not supported.
        repeat_name: Send the name of a call in every piece, as some servers do.
    """

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    calls: Sequence[ScriptedCall] = ()
    stream: bool | None = None
    usage: tuple[int, int] | None = (11, 7)
    piece: int = 5
    same_index: bool = False
    cut_after: int | None = None
    delay: float = 0.0
    gap: float = 0.0
    finish_reason: str | None = None
    error_body: str = ""
    refuse_stream: bool = False
    repeat_name: bool = False

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Reply:
        """
        Args:
            document: A reply as JSON, the way a browser test sends it to ``/__script``: any field of
                the class, with ``calls`` as a list of ``{"name", "arguments", "id"?}`` and ``usage`` as
                a list of two numbers or ``null``.

        Returns:
            The reply.
        """
        known = {name for name in cls.__dataclass_fields__ if name not in {"calls", "usage"}}
        options: dict[str, Any] = {k: v for k, v in document.items() if k in known}
        options["calls"] = tuple(
            ScriptedCall(str(c["name"]), c.get("arguments", {}), str(c.get("id", "")))
            for c in document.get("calls", ())
        )
        if "usage" in document:
            usage = document["usage"]
            options["usage"] = None if usage is None else (int(usage[0]), int(usage[1]))
        return cls(**options)

    @classmethod
    def say(cls, text: str, **options: Any) -> Reply:
        """
        Args:
            text: What the model says.
            **options: Any other field of the reply.

        Returns:
            A reply that only talks.
        """
        return cls(text=text, **options)

    @classmethod
    def call(cls, name: str, arguments: dict[str, Any] | str, **options: Any) -> Reply:
        """
        Args:
            name: The tool.
            arguments: Its arguments, as a dictionary or as raw text.
            **options: Any other field of the reply.

        Returns:
            A reply that makes one call.
        """
        return cls(calls=(ScriptedCall(name, arguments),), **options)

    @classmethod
    def calls_of(cls, *calls: ScriptedCall, **options: Any) -> Reply:
        """
        Args:
            *calls: The calls, in order.
            **options: Any other field of the reply.

        Returns:
            A reply that makes several calls.
        """
        return cls(calls=calls, **options)

    @classmethod
    def error(
        cls, status: int, *, retry_after: str | None = None, body: str = "", **options: Any
    ) -> Reply:
        """
        Args:
            status: The HTTP status.
            retry_after: The ``Retry-After`` header, in seconds or as a date.
            body: The body; a provider-style error document when empty.
            **options: Any other field of the reply.

        Returns:
            An error answer.
        """
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        return cls(status=status, headers=headers, error_body=body, **options)

    @classmethod
    def cut(cls, after: int, **options: Any) -> Reply:
        """
        Args:
            after: How many events are sent before the connection is closed.
            **options: Any other field of the reply (the text or calls that were being sent).

        Returns:
            A reply the provider never finishes.
        """
        return cls(cut_after=after, **options)


@dataclass(frozen=True)
class Recorded:
    """
    One request the model received.

    Attributes:
        method: ``GET`` or ``POST``.
        path: The path, with its query.
        headers: The headers, lower-cased names.
        raw: The body as it arrived.
        body: The body as JSON, ``None`` when there is none or it is not JSON.
    """

    method: str
    path: str
    headers: dict[str, str]
    raw: bytes
    body: Any


class ScriptedModel:
    """A provider of the protocol, listening on a port of this machine for as long as it is open."""

    def __init__(self, *, models: Iterable[str] = ("scripted-model",), api_key: str = "") -> None:
        """
        Args:
            models: What ``GET /models`` lists.
            api_key: When not empty, a request without ``Authorization: Bearer <api_key>`` is
                answered 401.
        """
        self.models = list(models)
        self.api_key = api_key
        self.models_status = 200
        self.requests: list[Recorded] = []
        self._replies: deque[Reply] = deque()
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- the script ----------------------------------------------------------------------------

    def script(self, *replies: Reply) -> ScriptedModel:
        """
        Queue the answers of the next requests, in order.

        Args:
            *replies: What each of them is answered with.

        Returns:
            This model, to chain.
        """
        with self._lock:
            self._replies.extend(replies)
        return self

    def unscript(self) -> None:
        """Forget the answers nobody asked for (what a scenario that ended early left unsaid)."""
        with self._lock:
            self._replies.clear()

    def pending(self) -> int:
        """Returns how many replies nobody asked for yet."""
        with self._lock:
            return len(self._replies)

    def chat_requests(self) -> list[Recorded]:
        """Returns the requests to ``chat/completions``, in order."""
        return [r for r in self.requests if r.path.split("?", 1)[0].endswith("/chat/completions")]

    def everything_received(self) -> str:
        """
        Returns:
            Every byte of every request, headers and bodies, as text: what a test searches for a
            secret that must never have left.
        """
        parts: list[str] = []
        for request in self.requests:
            parts.append(request.path)
            parts.extend(f"{k}: {v}" for k, v in request.headers.items() if k != "authorization")
            parts.append(request.raw.decode("utf-8", errors="replace"))
        return "\n".join(parts)

    # -- the server ------------------------------------------------------------------------------

    @property
    def port(self) -> int:
        """The port it listens on."""
        assert self._server is not None, "the scripted model is not open"
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        """The address a client is given: ``http://127.0.0.1:<port>/v1``."""
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> ScriptedModel:
        """Opens it."""
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Closes it."""
        self.stop()

    def start(self) -> None:
        """Start listening on a free port of this machine."""
        model = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                model._answer(self)

            def do_POST(self) -> None:
                model._answer(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop listening."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # -- answering -------------------------------------------------------------------------------

    def _answer(self, handler: BaseHTTPRequestHandler) -> None:
        """
        Args:
            handler: The request being served.
        """
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        headers = {k.lower(): v for k, v in handler.headers.items()}
        with self._lock:
            self.requests.append(Recorded(handler.command, handler.path, headers, raw, body))
        path = handler.path.split("?", 1)[0]
        if path == "/__script" and handler.command == "POST":
            self._control(handler, body)
            return
        if self.api_key and headers.get("authorization") != f"Bearer {self.api_key}":
            self._json(handler, 401, {"error": {"message": "Incorrect API key provided"}})
            return
        if handler.command == "GET" and path.endswith("/models"):
            if self.models_status != 200:
                self._json(handler, self.models_status, {"error": {"message": "no such route"}})
                return
            data = [{"id": name, "object": "model"} for name in self.models]
            self._json(handler, 200, {"object": "list", "data": data})
            return
        if handler.command == "POST" and path.endswith("/chat/completions"):
            with self._lock:
                # A reply that refuses streaming is not used up by the refusal: it is what the
                # same request, asked again without ``stream``, is answered with.
                refusing = bool(
                    self._replies and self._replies[0].refuse_stream and body_wants_stream(body)
                )
                reply = self._replies[0] if refusing else None
                if reply is None and self._replies:
                    reply = self._replies.popleft()
            if reply is None:
                self._json(
                    handler, 500, {"error": {"message": "the scripted model has no reply left"}}
                )
                return
            try:
                self._chat(handler, reply, body if isinstance(body, dict) else {})
            except (BrokenPipeError, ConnectionResetError):
                # The client gave up (a timeout or a cancellation under test): nothing to say.
                handler.close_connection = True
            return
        self._json(handler, 404, {"error": {"message": f"nothing at {path}"}})

    def _control(self, handler: BaseHTTPRequestHandler, body: Any) -> None:
        """
        The script by HTTP, for a browser test that drives a console whose assistant talks to this
        model in another process: ``{"replies": [...]}`` queues replies (see :meth:`Reply.from_dict`),
        ``{"reset": true}`` forgets the ones nobody asked for. It answers how many are waiting.

        Args:
            handler: The request being served.
            body: The decoded body.
        """
        document = body if isinstance(body, dict) else {}
        with self._lock:
            if document.get("reset"):
                self._replies.clear()
            self._replies.extend(Reply.from_dict(item) for item in document.get("replies", ()))
            waiting = len(self._replies)
        self._json(handler, 200, {"waiting": waiting})

    @staticmethod
    def _json(handler: BaseHTTPRequestHandler, status: int, document: Any) -> None:
        """
        Args:
            handler: The request being served.
            status: The status.
            document: The JSON body.
        """
        data = json.dumps(document).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _chat(self, handler: BaseHTTPRequestHandler, reply: Reply, request: dict[str, Any]) -> None:
        """
        Args:
            handler: The request being served.
            reply: What it is answered with.
            request: The decoded body of the request.
        """
        if reply.delay:
            time.sleep(reply.delay)
        wants_stream = bool(request.get("stream"))
        if reply.refuse_stream and wants_stream:
            self._json(
                handler,
                400,
                {"error": {"message": "streaming is not supported together with tools"}},
            )
            return
        if reply.status != 200:
            data = (
                reply.error_body
                or json.dumps({"error": {"message": f"scripted error {reply.status}"}})
            ).encode()
            handler.send_response(reply.status)
            handler.send_header("Content-Type", "application/json")
            for name, value in reply.headers.items():
                handler.send_header(name, value)
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            return
        streaming = wants_stream if reply.stream is None else (reply.stream and wants_stream)
        include_usage = bool((request.get("stream_options") or {}).get("include_usage"))
        if not streaming:
            self._json(handler, 200, self._document(reply))
            return
        self._stream(handler, reply, include_usage)

    @staticmethod
    def _usage(reply: Reply) -> dict[str, int] | None:
        """
        Args:
            reply: The reply.

        Returns:
            The ``usage`` object it reports, or None.
        """
        if reply.usage is None:
            return None
        prompt, completion = reply.usage
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }

    def _document(self, reply: Reply) -> dict[str, Any]:
        """
        Args:
            reply: The reply.

        Returns:
            The one JSON document a server that does not stream answers with.
        """
        message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
        if reply.calls:
            message["tool_calls"] = [
                {
                    "id": call.id or f"call_{number}",
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.text()},
                }
                for number, call in enumerate(reply.calls, start=1)
            ]
        finish = reply.finish_reason or ("tool_calls" if reply.calls else "stop")
        document: dict[str, Any] = {
            "id": "chatcmpl-scripted",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        }
        usage = self._usage(reply)
        if usage is not None:
            document["usage"] = usage
        return document

    def _events(self, reply: Reply, include_usage: bool) -> list[str]:
        """
        Args:
            reply: The reply.
            include_usage: Whether the request asked for the usage event.

        Returns:
            The ``data:`` payloads in the order a provider sends them, ``[DONE]`` last.
        """

        def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
            return json.dumps(
                {
                    "id": "chatcmpl-scripted",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
            )

        step = max(1, reply.piece)
        events = [chunk({"role": "assistant", "content": ""})]
        for start in range(0, len(reply.text), step):
            events.append(chunk({"content": reply.text[start : start + step]}))
        for number, call in enumerate(reply.calls):
            index = 0 if reply.same_index else number
            identifier = call.id or f"call_{number + 1}"
            events.append(
                chunk(
                    {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": identifier,
                                "type": "function",
                                "function": {"name": call.name, "arguments": ""},
                            }
                        ]
                    }
                )
            )
            arguments = call.text()
            for start in range(0, len(arguments), step):
                function: dict[str, Any] = {"arguments": arguments[start : start + step]}
                if reply.repeat_name:
                    function["name"] = call.name
                events.append(chunk({"tool_calls": [{"index": index, "function": function}]}))
        finish = reply.finish_reason or ("tool_calls" if reply.calls else "stop")
        events.append(chunk({}, finish))
        usage = self._usage(reply)
        if usage is not None and include_usage:
            events.append(json.dumps({"id": "chatcmpl-scripted", "choices": [], "usage": usage}))
        events.append("[DONE]")
        return events

    def _stream(self, handler: BaseHTTPRequestHandler, reply: Reply, include_usage: bool) -> None:
        """
        Args:
            handler: The request being served.
            reply: What it is answered with.
            include_usage: Whether the request asked for the usage event.
        """
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        events = self._events(reply, include_usage)
        for sent, payload in enumerate(events):
            if reply.cut_after is not None and sent >= reply.cut_after:
                handler.close_connection = True
                handler.wfile.flush()
                try:
                    handler.connection.shutdown(2)
                except OSError:
                    pass
                return
            data = f"data: {payload}\n\n".encode()
            handler.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            handler.wfile.flush()
            if reply.gap:
                time.sleep(reply.gap)
        handler.wfile.write(b"0\r\n\r\n")
        handler.wfile.flush()
