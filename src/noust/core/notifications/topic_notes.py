# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a Telegram channel remembers of a topic that stopped taking messages.

A topic that was deleted, closed, or belongs to a group that is no longer a forum is refused with
``400``, and the alert goes to the general thread instead (the alert matters more than its place).
Two things have to outlive that one delivery:

- the **mark**, so the console can say which topic failed, since when and in Telegram's own
  words, until it works again or somebody sets the topics again;
- the **hour**: the message that fell back says so once, not in every message that follows.

Both are one value per channel in ``notification_state`` (``telegram-topics``): a few topic
numbers, a subject and a sentence from Telegram. Nothing in it names the chat or the bot.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.notification_state import NotificationState
from noust.core.notifications import grouping
from noust.core.notifications.model import clean_inline

#: Where the marks are kept.
SCOPE = "telegram-topics"

#: A message that fell back says so at most this often, per topic.
WARN_EVERY_SECONDS = 3_600

#: The most of Telegram's words that are kept.
_REASON_LIMIT = 300


@dataclass(frozen=True)
class TopicNote:
    """
    One topic of a channel that did not take a message.

    Attributes:
        topic: Its number in the chat.
        subject: The subject it was the topic of, or None for an event's or the channel's own.
        reason: What Telegram answered, in its own words.
        at: When it was first refused, epoch seconds.
        warned_at: When a message last said so, epoch seconds.
    """

    topic: int
    subject: str | None
    reason: str
    at: float
    warned_at: float


class TopicNotes:
    """The marks of the channels, in the store."""

    def __init__(self, state: NotificationState, *, clock: Callable[[], float] = time.time) -> None:
        """
        Args:
            state: The store's key-value memory.
            clock: Epoch seconds. Tests move it.
        """
        self._state = state
        self._clock = clock

    def _read(self, channel: str) -> dict[str, dict[str, Any]]:
        """
        Args:
            channel: A channel's name.

        Returns:
            Its marks by topic number; empty for none, and for a value that cannot be read (a hand
            edit costs a mark, never a delivery).
        """
        found = self._state.get_json(SCOPE, channel)
        if not isinstance(found, dict):
            return {}
        return {key: value for key, value in found.items() if isinstance(value, dict)}

    def of(self, channel: str) -> list[TopicNote]:
        """
        Args:
            channel: A channel's name.

        Returns:
            Its marks, the oldest first.
        """
        notes: list[TopicNote] = []
        for key, value in self._read(channel).items():
            try:
                topic = int(key)
                at = float(value["at"])
                notes.append(
                    TopicNote(
                        topic=topic,
                        subject=str(value["subject"]) if value.get("subject") else None,
                        reason=str(value.get("reason") or ""),
                        at=at,
                        warned_at=float(value.get("warned_at", at)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(notes, key=lambda note: (note.at, note.topic))

    def standing(self, channel: str, settings: Mapping[str, Any]) -> list[TopicNote]:
        """
        Args:
            channel: A channel's name.
            settings: Its settings now.

        Returns:
            The marks of the topics it still posts in: a topic the operator has since taken out
            is no trouble to report, so the mark is left to be forgotten.
        """
        in_use = grouping.topics_in_use(settings)
        return [note for note in self.of(channel) if note.topic in in_use]

    def lost(self, channel: str, topic: int, subject: str | None, reason: str) -> bool:
        """
        Mark a topic that did not take a message.

        Args:
            channel: The channel.
            topic: The topic's number.
            subject: The subject it is the topic of, when it is one.
            reason: What Telegram answered.

        Returns:
            Whether the message that fell back should say so: the first time, and then once an
            hour.
        """
        now = self._clock()
        marks = self._read(channel)
        earlier = marks.get(str(topic))
        first = float(earlier["at"]) if earlier and "at" in earlier else now
        warned = float(earlier.get("warned_at", first)) if earlier else None
        say = warned is None or now - warned >= WARN_EVERY_SECONDS
        marks[str(topic)] = {
            "subject": subject,
            "reason": clean_inline(reason)[:_REASON_LIMIT],
            "at": first,
            "warned_at": now if say else warned,
        }
        self._state.put_json(SCOPE, channel, marks)
        return say

    def worked(self, channel: str, topic: int) -> bool:
        """
        Forget a topic that took a message after all.

        Args:
            channel: The channel.
            topic: The topic's number.

        Returns:
            Whether it was marked.
        """
        marks = self._read(channel)
        if str(topic) not in marks:
            return False
        del marks[str(topic)]
        if marks:
            self._state.put_json(SCOPE, channel, marks)
        else:
            self._state.delete(SCOPE, channel)
        return True

    def clear(self, channel: str) -> None:
        """
        Forget every mark of a channel: its topics were set again.

        Args:
            channel: The channel.
        """
        self._state.delete(SCOPE, channel)
