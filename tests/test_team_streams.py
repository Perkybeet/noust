# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a team member's live feeds carry: its own applications, and nothing about anyone else's.

The event stream and the WebSockets are routes like any other and were not
filtered by what a route is about, so each is held to the scope where it opens
(I-04): the stream tells a member what happens to the applications of its teams
and says nothing of the machine; a journal, a job and the event feed of another
team read as absent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from noust.core.store import NoustStore
from noust.web import events
from noust.web.api.node_proxy import WS_CLOSE_NODE_NOT_FOUND
from noust.web.events import _frame_domain, _stream, hub
from noust.web.jobs import Job, JobType, get_job_manager
from noust.web.websockets.router import WS_SUBPROTOCOL, WS_TOKEN_PREFIX
from tests.teams_support import World, build_world

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    """Two teams, their resources and their people, served by the console."""
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def subprotocols(world: World, who: str) -> list[str]:
    """
    Args:
        world: The world.
        who: One of its people.

    Returns:
        The subprotocols that carry that person's credential.
    """
    return [WS_SUBPROTOCOL, f"{WS_TOKEN_PREFIX}{world.people[who].token}"]


def payload_of(world: World, who: str) -> dict[str, Any]:
    """
    Args:
        world: The world.
        who: One of its people.

    Returns:
        The payload the console admits that person's token with.
    """
    from noust.web.server import get_token_manager

    payload = get_token_manager().verify_api_token(world.people[who].token, "127.0.0.1")
    assert payload is not None
    return payload


class Request:
    """The part of a request the stream asks about: whether the client is still there."""

    def __init__(self) -> None:
        self.gone = False

    async def is_disconnected(self) -> bool:
        """
        Returns:
            Whether the test has finished listening.
        """
        return self.gone


async def listen(session: dict[str, Any], publish: Any, *, wait: float = 1.5) -> list[str]:
    """
    Open the event stream as a principal, let something happen, and read what it carried.

    Args:
        session: The principal's payload.
        publish: What happens once the stream is open (a coroutine function taking nothing).
        wait: How long to keep listening.

    Returns:
        The frames received, the greeting and the machine's included.
    """
    request = Request()
    stream = _stream(request, None if session is None else session)  # type: ignore[arg-type]
    frames: list[str] = []
    frames.append(await asyncio.wait_for(stream.__anext__(), 2))  # ": connected"
    publish()
    deadline = asyncio.get_running_loop().time() + wait
    while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
        try:
            frames.append(await asyncio.wait_for(stream.__anext__(), remaining))
        except (asyncio.TimeoutError, StopAsyncIteration):
            break
    request.gone = True
    await stream.aclose()
    return frames


def domains_in(frames: list[str]) -> set[str]:
    """
    Args:
        frames: Frames of the stream.

    Returns:
        The domains the ``app`` and ``job`` frames are about.
    """
    found: set[str] = set()
    for frame in frames:
        name, _, rest = frame.partition("\n")
        if name in ("event: app", "event: job") and rest.startswith("data: "):
            body = json.loads(rest.removeprefix("data: ").strip())
            if body.get("domain"):
                found.add(body["domain"])
    return found


class TestTheEventStream:
    def test_an_application_event_reaches_the_team_that_owns_it_and_not_the_other(
        self, world: World
    ) -> None:
        def happen() -> None:
            hub.publish("app", {"domain": ACME, "status": "running"})
            hub.publish("app", {"domain": GLOBEX, "status": "running"})

        alice = asyncio.run(listen(payload_of(world, "alice"), happen))
        bob = asyncio.run(listen(payload_of(world, "bob"), happen))
        admin = asyncio.run(listen(payload_of(world, "admin"), happen))

        assert domains_in(alice) == {ACME}
        assert domains_in(bob) == {GLOBEX}
        assert domains_in(admin) == {ACME, GLOBEX}

    def test_a_job_of_another_team_is_not_announced(self, world: World) -> None:
        manager = get_job_manager()

        def happen() -> None:
            for domain in (ACME, GLOBEX, None):
                job = Job(
                    id=f"j-{domain}",
                    type=JobType.DEPLOY,
                    name="Deploy",
                    description="",
                    metadata={"domain": domain} if domain else {},
                )
                manager._notify_subscribers(job)

        alice = asyncio.run(listen(payload_of(world, "alice"), happen))

        assert domains_in(alice) == {ACME}
        # The job of the server itself belongs to nobody, and only the instance hears of it.
        assert not any("j-None" in frame for frame in alice)

    def test_a_member_is_not_sent_the_machine_or_its_metrics(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The frames exist (a collector is running, the machine can be read) and a member's
        # stream still does not carry them; the administrator's does.
        monkeypatch.setattr(events, "metrics_frame", lambda: "event: metrics\ndata: {}\n\n")
        monkeypatch.setattr(events, "MACHINE_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(events, "machine_frame", lambda: "event: machine\ndata: {}\n\n")

        alice = asyncio.run(listen(payload_of(world, "alice"), lambda: None, wait=0.5))
        admin = asyncio.run(listen(payload_of(world, "admin"), lambda: None, wait=0.5))

        assert not any(frame.startswith(("event: machine", "event: metrics")) for frame in alice)
        assert any(frame.startswith("event: metrics") for frame in admin)

    def test_the_domain_of_a_frame(self) -> None:
        assert _frame_domain('event: app\ndata: {"domain":"a.example.com"}\n\n') == "a.example.com"
        assert _frame_domain('event: job\ndata: {"domain":"a.example.com"}\n\n') is None
        assert _frame_domain("event: app\ndata: not json\n\n") is None
        assert _frame_domain("event: app\ndata: [1]\n\n") is None


class TestTheWebSockets:
    def refused(self, world: World, who: str, url: str) -> dict[str, Any]:
        """
        Args:
            world: The world.
            who: One of its people.
            url: A WebSocket path.

        Returns:
            The first frame the server sent.
        """
        with world.client.websocket_connect(url, subprotocols=subprotocols(world, who)) as ws:
            frame = ws.receive_json()
            try:
                ws.receive_json()
            except WebSocketDisconnect:
                pass
        return frame

    def test_the_journal_of_another_teams_application_is_absent(self, world: World) -> None:
        hidden = self.refused(world, "alice", f"/ws/logs/{GLOBEX}")
        missing = self.refused(world, "alice", "/ws/logs/ghost.example.com")

        assert hidden["type"] == missing["type"] == "error"
        assert hidden["message"].replace(GLOBEX, "X") == missing["message"].replace(
            "ghost.example.com", "X"
        )

    def test_a_unit_is_not_followed_by_name(self, world: World) -> None:
        frame = self.refused(world, "alice", "/ws/logs/ssh")

        assert frame["type"] == "error"
        assert "not found" in frame["message"]

    def test_the_journal_of_its_own_application_gets_past_the_scope(self, world: World) -> None:
        frame = self.refused(world, "alice", f"/ws/logs/{ACME}")

        # What stops it next is the machine (no unit here), not the scope.
        assert "Application not found" not in frame.get("message", "")

    def test_the_server_event_feed_is_not_for_a_team_member(self, world: World) -> None:
        frame = self.refused(world, "alice", "/ws/events")

        assert frame["type"] == "error"
        assert "whole server" in frame["message"]

    def test_a_job_of_another_team_is_absent(self, world: World) -> None:
        manager = get_job_manager()
        for domain in (ACME, GLOBEX):
            manager._jobs[f"live-{domain[:4]}"] = Job(
                id=f"live-{domain[:4]}",
                type=JobType.DEPLOY,
                name="Deploy",
                description="",
                metadata={"domain": domain},
            )

        hidden = self.refused(world, "alice", "/ws/jobs/live-blog")
        missing = self.refused(world, "alice", "/ws/jobs/live-none")
        own = self.refused(world, "alice", "/ws/jobs/live-shop")

        assert hidden["type"] == missing["type"] == "error"
        assert hidden["message"].replace("live-blog", "X") == missing["message"].replace(
            "live-none", "X"
        )
        assert own["type"] == "connected"

    def test_the_list_of_all_jobs_holds_only_the_teams(self, world: World) -> None:
        manager = get_job_manager()
        for domain in (ACME, GLOBEX):
            manager._jobs[f"all-{domain[:4]}"] = Job(
                id=f"all-{domain[:4]}",
                type=JobType.DEPLOY,
                name="Deploy",
                description="",
                metadata={"domain": domain},
            )

        frame = self.refused(world, "alice", "/ws/jobs")

        names = {j["id"] for j in frame["jobs"]}
        assert "all-shop" in names
        assert "all-blog" not in names

    def test_a_server_nobody_gave_the_team_has_no_stream_for_it(self, world: World) -> None:
        # A server is the instance's until it is assigned to a team: to a member it is not there.
        with world.client.websocket_connect(
            "/ws/nodes/lisbon/events", subprotocols=subprotocols(world, "alice")
        ) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()

        assert closed.value.code == WS_CLOSE_NODE_NOT_FOUND
