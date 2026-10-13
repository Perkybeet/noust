# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The event stream is limited like the WebSockets (checklist L-37).

``GET /events`` holds a coroutine, a queue, a subscription and, every few
seconds, a render of the machine in a worker thread, for as long as the client
stays. WebSockets were capped per credential and closed after twelve hours; the
event stream was neither, so one low-privilege token could open streams until the
console that runs as root had nothing left for anyone else. It is now held to the
same discipline: a number of streams per credential, a maximum lifetime, and the
place given back however the stream ends.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from noust.web import events as events_module
from noust.web.server import SecurityMiddleware, get_security_config, get_token_manager
from tests.test_web_auth import build_client, issue_token, login
from tests.test_web_stream_guards import ConnectedRequest


class Harness:
    """Drives the security middleware over an application that holds each request open."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.entered = 0
        self.fail = False

        async def application(scope: Any, receive: Any, send: Any) -> None:
            self.entered += 1
            if self.fail:
                raise RuntimeError("the stream broke")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {"type": "http.response.body", "body": b": connected\n\n", "more_body": True}
            )
            await self.release.wait()
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        self.middleware = SecurityMiddleware(application, get_security_config())

    async def open(self, token: str, path: str = "/events") -> dict[str, Any]:
        """Start a request; return what the server answered first and the task serving it."""
        started: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        body = bytearray()

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and not started.done():
                started.set_result({"status": message["status"]})
            if message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [
                (b"authorization", f"Bearer {token}".encode()),
                (b"host", b"testserver"),
            ],
            "client": ("203.0.113.9", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
            "http_version": "1.1",
            "root_path": "",
        }
        task = asyncio.create_task(self.middleware(scope, receive, send))
        first = await asyncio.wait_for(started, timeout=5)
        await asyncio.sleep(0)
        return {**first, "task": task, "body": body}


@pytest.fixture
def credentials(sandbox: Path) -> tuple[str, str]:
    """Two API tokens, so one can be at its limit while the other is not."""
    client = build_client(sandbox, event_streams_max_per_credential=3)
    master = get_token_manager().generate_master_token()
    csrf = login(client, master)["csrf_token"]
    first = issue_token(client, csrf, master, name="first", scope="read")["token"]
    second = issue_token(client, csrf, master, name="second", scope="read")["token"]
    return first, second


def test_a_credential_holds_a_few_streams_and_the_next_is_refused(
    credentials: tuple[str, str],
) -> None:
    first, second = credentials

    async def exercise() -> tuple[list[int], dict[str, Any], int]:
        harness = Harness()
        held = [await harness.open(first) for _ in range(3)]
        refused = await harness.open(first)
        refusal = json.loads(bytes(refused["body"]).decode())
        other = await harness.open(second)
        harness.release.set()
        await asyncio.gather(*(h["task"] for h in [*held, refused, other]))
        return (
            [h["status"] for h in held],
            {**refusal, "status": refused["status"]},
            other["status"],
        )

    statuses, refusal, other_status = asyncio.run(exercise())

    assert statuses == [200, 200, 200]
    assert refusal["status"] == 429 and refusal["error"] == "rate_limited"
    assert "event streams" in refusal["detail"]
    assert other_status == 200, "another credential has its own streams"


def test_a_centrals_relay_of_a_nodes_feed_is_counted_with_the_rest(
    credentials: tuple[str, str],
) -> None:
    first, _ = credentials

    async def exercise() -> list[int]:
        harness = Harness()
        opened = [await harness.open(first, "/events")]
        opened += [await harness.open(first, "/api/nodes/alpha/events") for _ in range(2)]
        opened.append(await harness.open(first, "/api/nodes/beta/events"))
        harness.release.set()
        await asyncio.gather(*(h["task"] for h in opened))
        return [h["status"] for h in opened]

    assert asyncio.run(exercise()) == [200, 200, 200, 429]


def test_is_event_stream_names_the_feed_and_its_relay_and_nothing_else() -> None:
    from noust.web.server import is_event_stream

    assert is_event_stream("/events") and is_event_stream("/api/nodes/alpha/events")
    for other in (
        "/events/",
        "/api/events",
        "/api/nodes/alpha/events/x",
        "/api/nodes//events",
        "/ws/events",
    ):
        assert not is_event_stream(other), other


def test_a_stream_that_ends_gives_its_place_back(credentials: tuple[str, str]) -> None:
    first, _ = credentials

    async def exercise() -> int:
        harness = Harness()
        held = [await harness.open(first) for _ in range(3)]
        harness.release.set()
        await asyncio.gather(*(h["task"] for h in held))
        harness.release.clear()
        again = await harness.open(first)
        harness.release.set()
        await again["task"]
        return again["status"]

    assert asyncio.run(exercise()) == 200


def test_a_stream_that_breaks_gives_its_place_back_too(credentials: tuple[str, str]) -> None:
    first, _ = credentials

    async def exercise() -> int:
        harness = Harness()
        harness.fail = True
        for _ in range(5):  # more than the limit: each one fails and must not keep a place
            with pytest.raises(RuntimeError):
                task = asyncio.create_task(harness.middleware(*_failing_call(first)))
                await task
        harness.fail = False
        opened = await harness.open(first)
        harness.release.set()
        await opened["task"]
        return opened["status"]

    assert asyncio.run(exercise()) == 200


def _failing_call(token: str) -> tuple[dict[str, Any], Any, Any]:
    async def send(message: dict[str, Any]) -> None:
        return None

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/events",
        "raw_path": b"/events",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode()), (b"host", b"testserver")],
        "client": ("203.0.113.9", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
    }
    return scope, receive, send


def test_only_the_event_stream_is_counted(credentials: tuple[str, str]) -> None:
    first, _ = credentials

    async def exercise() -> list[int]:
        harness = Harness()
        opened = [await harness.open(first, "/api/apps") for _ in range(6)]
        harness.release.set()
        await asyncio.gather(*(h["task"] for h in opened))
        return [h["status"] for h in opened]

    assert set(asyncio.run(exercise())) == {200}


def test_a_stream_ends_after_its_longest_life_and_the_client_comes_back(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_client(sandbox)
    master = get_token_manager().generate_master_token()
    from noust.web.auth import check_credential

    payload = check_credential(master, "testclient")
    assert payload is not None
    monkeypatch.setattr(events_module, "STREAM_MAX_SECONDS", 0.05)

    async def exercise() -> bool:
        stream = events_module._stream(ConnectedRequest(), payload)
        await stream.__anext__()
        try:
            while True:
                await asyncio.wait_for(stream.__anext__(), timeout=5)
        except StopAsyncIteration:
            return True
        except asyncio.TimeoutError:
            return False

    assert asyncio.run(exercise()) is True


def test_the_default_is_more_than_a_tab_each_and_less_than_unbounded() -> None:
    from noust.web.auth import EVENT_STREAMS_MAX_PER_CREDENTIAL, SecurityConfig

    assert 8 <= EVENT_STREAMS_MAX_PER_CREDENTIAL <= 64
    assert SecurityConfig().event_streams_max_per_credential == EVENT_STREAMS_MAX_PER_CREDENTIAL
