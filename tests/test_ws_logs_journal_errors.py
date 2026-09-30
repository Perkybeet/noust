# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When ``/ws/logs`` fails, the error carries journalctl's own words (backlog 22).

A follow only ends on its own when journalctl failed - a journal it may not
read, an option this systemd does not know - and the console used to show "The
journal stream failed" with the reason thrown away. A system error is never
paraphrased: what journalctl wrote to stderr reaches the client verbatim, from
the first read before its first line and from what it wrote when it exited.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

#: The module, not the ``router`` object the package re-exports under the same name.
ws_router = importlib.import_module("noust.web.websockets.router")


class FakeSocket:
    """A WebSocket that keeps what it was sent."""

    def __init__(self, gone: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.gone = gone

    async def send_json(self, message: dict[str, Any]) -> None:
        """
        Args:
            message: What the console would receive.

        Raises:
            WebSocketDisconnect: When the client already left.
        """
        if self.gone:
            raise WebSocketDisconnect()
        self.sent.append(message)


class FakeProcess:
    """A journalctl that ended with a status."""

    def __init__(self, code: int | None) -> None:
        self.code = code

    async def wait(self) -> int:
        """
        Returns:
            The exit status, or never when the process is still running.
        """
        if self.code is None:
            await asyncio.sleep(3600)
        assert self.code is not None
        return self.code


def stream_of(text: str) -> asyncio.StreamReader:
    """
    Build a stderr that holds text and is closed.

    Args:
        text: What journalctl wrote.

    Returns:
        The reader.
    """
    reader = asyncio.StreamReader()
    reader.feed_data(text.encode())
    reader.feed_eof()
    return reader


def report(socket: FakeSocket, process: FakeProcess, stderr: str, already_read: str = "") -> None:
    """
    Run the report on a stream that ended.

    Args:
        socket: The client.
        process: The journalctl that stopped.
        stderr: What is left on its error stream.
        already_read: What was read before the first line.
    """

    async def go() -> None:
        await ws_router._report_journal_exit(
            socket,
            process,
            stream_of(stderr),
            already_read,  # type: ignore[arg-type]
        )

    asyncio.run(go())


def test_the_error_carries_journalctls_stderr_verbatim() -> None:
    socket = FakeSocket()

    report(socket, FakeProcess(1), "Failed to add match 'x.service': Invalid argument\n")

    (message,) = socket.sent
    assert message["type"] == "error"
    assert message["message"] == (
        "journalctl exited with status 1: Failed to add match 'x.service': Invalid argument"
    )


def test_what_was_read_before_the_first_line_is_kept_too() -> None:
    socket = FakeSocket()

    report(
        socket,
        FakeProcess(1),
        "second line\n",
        already_read="No journal files were opened due to insufficient permissions.",
    )

    (message,) = socket.sent
    assert "No journal files were opened due to insufficient permissions." in message["message"]
    assert "second line" in message["message"]
    assert message["message"].index("No journal") < message["message"].index("second line")


def test_a_clean_end_with_nothing_said_is_not_an_error() -> None:
    socket = FakeSocket()

    report(socket, FakeProcess(0), "")

    assert socket.sent == []


def test_a_process_that_is_still_running_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_router, "TERMINATE_GRACE_SECONDS", 0.01)
    socket = FakeSocket()

    report(socket, FakeProcess(None), "")

    assert socket.sent == []


def test_a_client_that_left_is_not_an_error() -> None:
    report(FakeSocket(gone=True), FakeProcess(1), "boom\n")
