# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The turns that are being answered, and how what they do reaches the person (item 171).

A turn runs in a thread of its own inside the console, as a background piece of work of it. It is not
a job of the queue: the queue is the line deployments wait in, a turn spends minutes waiting for a model
and must neither hold a place in that line nor wait in it, and the work a job leaves behind (its log
file, its row) is the conversation's own record here. What a turn announces goes out on the console's
event stream as the ``assistant`` event, **to the streams of the account that asked and to no other**
(:mod:`noust.web.events`), so it counts in the limit of streams of each credential like everything the
stream carries.

At most :data:`MAX_RUNNING` turns run at once, in the whole server: the assistant is a way of spending
somebody's money and the server's threads, and what it is asked in a crowd waits for its turn to be
asked again. A turn can be cancelled by its person, and turning the assistant off cancels every turn.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from noust.core.exceptions import AssistantBusyError
from noust.core.server_text import say
from noust.web.assistant.loop import TokenCache, TurnRunner

__all__ = ["MAX_RUNNING", "TurnRegistry", "publisher", "registry", "tokens"]

logger = logging.getLogger(__name__)

#: The most turns answered at once in the whole server.
MAX_RUNNING = 4


class _Running:
    """A turn that runs."""

    def __init__(self, runner: TurnRunner, thread: threading.Thread) -> None:
        self.runner = runner
        self.thread = thread


class TurnRegistry:
    """The turns running in this process."""

    def __init__(self, limit: int = MAX_RUNNING) -> None:
        """
        Args:
            limit: The most turns that run at once.
        """
        self._limit = limit
        self._running: dict[str, _Running] = {}
        self._lock = threading.Lock()

    def start(self, runner: TurnRunner) -> None:
        """
        Run a turn.

        Args:
            runner: The turn.

        Raises:
            AssistantBusyError: The server is already answering as many as it may.
        """
        turn_id = runner.turn.id
        with self._lock:
            if len(self._running) >= self._limit:
                raise AssistantBusyError(say("assistant.turn.busy"), limit=self._limit)
            thread = threading.Thread(
                target=self._run,
                args=(runner,),
                name=f"noust-assistant-{turn_id[:8]}",
                daemon=True,
            )
            self._running[turn_id] = _Running(runner, thread)
        thread.start()

    def _run(self, runner: TurnRunner) -> None:
        """
        Args:
            runner: The turn, in its thread.
        """
        try:
            runner.run()
        finally:
            with self._lock:
                self._running.pop(runner.turn.id, None)

    def alive(self) -> set[str]:
        """
        Returns:
            The turns that run now.
        """
        with self._lock:
            return set(self._running)

    def running_in(self, conversation_id: str) -> str | None:
        """
        Args:
            conversation_id: A conversation.

        Returns:
            Its turn that runs now, if one.
        """
        with self._lock:
            for turn_id, held in self._running.items():
                if held.runner.ctx.conversation_id == conversation_id:
                    return turn_id
        return None

    def cancel(self, turn_id: str) -> bool:
        """
        Args:
            turn_id: A turn.

        Returns:
            Whether it was running, and is asked to stop.
        """
        with self._lock:
            held = self._running.get(turn_id)
        if held is None:
            return False
        held.runner.cancel.set()
        return True

    def cancel_all(self) -> int:
        """
        Ask every turn to stop: the assistant was turned off.

        Returns:
            How many were running.
        """
        with self._lock:
            held = list(self._running.values())
        for item in held:
            item.runner.cancel.set()
        return len(held)

    def wait(self, turn_id: str, timeout: float = 30.0) -> bool:
        """
        Wait for a turn to end.

        Args:
            turn_id: A turn.
            timeout: Seconds to wait at most.

        Returns:
            Whether it has ended.
        """
        with self._lock:
            held = self._running.get(turn_id)
        if held is None:
            return True
        held.thread.join(timeout)
        return not held.thread.is_alive()


_REGISTRY = TurnRegistry()
_TOKENS = TokenCache()


def registry() -> TurnRegistry:
    """
    Returns:
        The process-wide registry of turns.
    """
    return _REGISTRY


def tokens() -> TokenCache:
    """
    Returns:
        The tokens of the conversations being answered, kept in memory.
    """
    return _TOKENS


def publisher(account_id: int) -> Callable[[dict[str, Any]], None]:
    """
    Args:
        account_id: Whose turn it is.

    Returns:
        What announces what a turn does on the event stream, to that account's streams alone.
    """
    from noust.web.events import hub

    def publish(payload: dict[str, Any]) -> None:
        hub.publish("assistant", {"for": account_id, **payload})

    return publish
