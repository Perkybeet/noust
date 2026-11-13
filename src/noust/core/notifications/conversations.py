# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a channel remembers of a conversation: the first message about a thing, which every later one
answers.

A failure and its recovery are two messages about one thing (:func:`~noust.core.notifications.grouping.thing_key`),
and a chat that can answer a message reads better when the recovery is the reply to the failure,
and the end of a deploy the reply to its start. The rule is short, and it is the same for every
channel that can answer:

- the first message about a thing, since it was last fine, is its **root**;
- every later message about it answers the root;
- a message that says it is fine again answers the root and **ends** the conversation, so the next
  problem is a new one and not an answer to a recovery from last month.

A root is remembered by what the channel needs to answer it (a Telegram message id, the
``Message-ID`` of an email), where it was posted (the topic and a short fingerprint of the chat: an
answer is only made where the root is) and when. It is forgotten after a week, because a failure
nobody has closed in a week is not the thing a recovery now means to answer. Losing the memory (a
restart cannot, the store keeps it; a restored server can) only ever costs the grouping: a message
without a root is sent on its own: losing the grouping never loses the alert.

The memory is a value per thing in ``notification_state``, one scope per channel: a team's channel
never sees another's.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from noust.core.notification_state import NotificationState
from noust.core.notifications import grouping
from noust.core.notifications.model import Notification, State

#: How long a root is answered.
TTL_SECONDS = 7 * 86_400

#: What a message does to the conversation of its thing.
Settles = Literal["opens", "closes", "continues"]

_SCOPE = "threads:"


@dataclass(frozen=True)
class Root:
    """
    The first message about a thing, as a channel can answer it.

    Attributes:
        ref: What answers it: a Telegram message id, the local part of an email's ``Message-ID``.
        at: When it was sent, epoch seconds.
        thread: The topic it was posted in (Telegram), or None for the general thread.
        chat: A short fingerprint of the chat it was posted in, or empty: ids of a chat are
            personal and never kept, but an answer must not be made in another chat.
    """

    ref: str
    at: float
    thread: int | None
    chat: str


def settles(n: Notification) -> Settles | None:
    """
    Say what a notification does to the conversation of its thing.

    Args:
        n: The notification.

    Returns:
        ``opens`` for a problem, a warning or a start (the root when there is none, an answer when
        there is); ``closes`` for one that says it is fine again; ``continues`` for news that is
        neither (an alert somebody took); None for what is not about a thing that has a way back.
    """
    if grouping.family_of(n.kind) is None:
        return None
    if n.state is State.OK:
        return "closes"
    if n.state in (State.FAILED, State.WARNING, State.PROGRESS):
        return "opens"
    return "continues"


def chat_fingerprint(chat_id: str) -> str:
    """
    Args:
        chat_id: A chat's id.

    Returns:
        Twelve characters that say whether two ids are the same chat and nothing else.
    """
    return hashlib.sha256(f"noust-chat:{chat_id}".encode()).hexdigest()[:12]


class Conversations:
    """The roots of the conversations of the channels, in the store."""

    def __init__(self, state: NotificationState, *, clock: Callable[[], float] = time.time) -> None:
        """
        Args:
            state: The store's key-value memory.
            clock: Epoch seconds. Tests move it.
        """
        self._state = state
        self._clock = clock

    def root(
        self, channel: str, n: Notification, *, chat: str = "", thread: int | None = None
    ) -> Root | None:
        """
        Find the message a notification answers.

        Args:
            channel: The channel.
            n: What is about to be sent.
            chat: The fingerprint of the chat it will be posted in.
            thread: The topic it will be posted in.

        Returns:
            The root, or None when there is none, it is too old, or it was posted somewhere an
            answer would not reach.
        """
        if settles(n) is None:
            return None
        found = self._read(channel, grouping.thing_key(n))
        if found is None or self._clock() - found.at > TTL_SECONDS:
            return None
        if found.chat != chat or found.thread != thread:
            return None
        return found

    def sent(
        self,
        channel: str,
        n: Notification,
        ref: str | None,
        *,
        chat: str = "",
        thread: int | None = None,
    ) -> None:
        """
        Record that a notification was delivered.

        Args:
            channel: The channel.
            n: What was sent.
            ref: What answers it, when the channel says (a message that went out without an id
                cannot be a root).
            chat: The fingerprint of the chat it was posted in.
            thread: The topic it was posted in.
        """
        what = settles(n)
        if what is None:
            return
        key = grouping.thing_key(n)
        if what == "closes":
            self._state.delete(_SCOPE + channel, key)
            return
        if what == "opens" and ref and self.root(channel, n, chat=chat, thread=thread) is None:
            self._prune(channel)
            self._state.put_json(
                _SCOPE + channel,
                key,
                {"ref": ref, "at": self._clock(), "thread": thread, "chat": chat},
            )

    def forget(self, channel: str, n: Notification) -> None:
        """
        Forget the root of a thing: the message is not there to be answered any more.

        Args:
            channel: The channel.
            n: A notification about the thing.
        """
        if settles(n) is not None:
            self._state.delete(_SCOPE + channel, grouping.thing_key(n))

    def _read(self, channel: str, key: str) -> Root | None:
        """
        Args:
            channel: The channel.
            key: The thing's key.

        Returns:
            Its root, or None when there is none or what is stored cannot be read (a hand edit
            costs a grouping, never a delivery).
        """
        raw = self._state.get_json(_SCOPE + channel, key)
        return self._parse(raw)

    @staticmethod
    def _parse(raw: object) -> Root | None:
        """
        Args:
            raw: What was stored.

        Returns:
            The root it describes, or None.
        """
        if not isinstance(raw, dict):
            return None
        ref, at, thread, chat = raw.get("ref"), raw.get("at"), raw.get("thread"), raw.get("chat")
        if (
            not isinstance(ref, str)
            or not ref
            or isinstance(at, bool)
            or not isinstance(at, (int, float))
        ):
            return None
        if thread is not None and (isinstance(thread, bool) or not isinstance(thread, int)):
            return None
        return Root(
            ref=ref, at=float(at), thread=thread, chat=chat if isinstance(chat, str) else ""
        )

    def _prune(self, channel: str) -> None:
        """
        Forget what has expired, so the store does not keep what nobody will answer.

        Args:
            channel: The channel.
        """
        now = self._clock()
        for key, value in self._state.items(_SCOPE + channel).items():
            try:
                root = self._parse(json.loads(value))
            except ValueError:
                root = None
            if root is None or now - root.at > TTL_SECONDS:
                self._state.delete(_SCOPE + channel, key)
