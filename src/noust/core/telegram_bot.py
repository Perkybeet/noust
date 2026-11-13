# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Telegram Bot API as Noust speaks it: where a method lives, what a refusal means and what an
answer carries.

Pure: it builds an address and reads bytes, and never sends. Everything that talks to Telegram goes
through :func:`noust.core.notifier.send_guarded` (the one door, with its deadline and the guard
against private destinations), and the callers are
:class:`~noust.core.notifier.Notifier` (messages) and
:class:`~noust.core.telegram_topics.TelegramTopics` (the topics of a group).

A refusal is read from the Bot API's own ``description``, which is not documented on a page and
has been spelled several ways (``message thread not found``, ``TOPIC_CLOSED``...), so each meaning
is a list of spellings and the test of it runs every one. What matters is what the sender can do
about it, never the exact words: a topic that cannot take a message means "send it without the
topic"; a message to answer that is gone means "send it without the reply".
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

#: The Bot API's one host. Fixed and public, never taken from a setting.
API_ROOT = "https://api.telegram.org"

#: ``<bot id>:<secret>``, the only shape the Bot API issues. The token becomes part of the request
#: path, so anything else is refused before it can reshape the URL. Always applied with
#: ``fullmatch``: ``$`` also matches just before a trailing newline, which let a token ending in a
#: line break through.
_TOKEN_RE = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")

_METHOD_RE = re.compile(r"[A-Za-z]{1,40}")


def validate_bot_token(value: str) -> str:
    """
    Check that a value is, in full, the shape of a Bot API token.

    The one rule for a token's shape: the settings endpoint applies it on save, and the notifier
    again before the token becomes part of a URL, for a value written some other way.

    Args:
        value: The bot token.

    Returns:
        The value, unchanged.

    Raises:
        ValueError: When it is not ``<digits>:<secret>`` from its first character to its last. The
            value itself is never quoted: it is a credential.
    """
    if not _TOKEN_RE.fullmatch(value):
        raise ValueError(
            "notifications.channels.telegram.bot_token does not look like a "
            "Telegram bot token (expected <digits>:<secret>)"
        )
    return value


def method_url(token: str, method: str) -> str:
    """
    Args:
        token: The bot's token.
        method: A Bot API method (``sendMessage``, ``createForumTopic``).

    Returns:
        Its address.

    Raises:
        ValueError: When the token does not have the Bot API's shape, or the method is not a word.
    """
    validate_bot_token(token)
    if not _METHOD_RE.fullmatch(method):
        raise ValueError("A Bot API method is a word of letters")
    return f"{API_ROOT}/bot{token}/{method}"


class Refusal(str, Enum):
    """What the sender can do about a request Telegram answered with ``400``."""

    #: The text's entities cannot be read: send it again as plain text.
    MARKUP = "markup"
    #: The topic does not take messages (deleted, closed, the group is not a forum): send it
    #: without a topic.
    TOPIC = "topic"
    #: The message to answer is not there: send it without the reply.
    REPLY = "reply"


#: What the Bot API has said for a topic that cannot take a message, spelled with spaces: the
#: underscores of an error code (``TOPIC_CLOSED``) are read as spaces.
_TOPIC_MARKERS = (
    "thread not found",
    "topic closed",
    "topic is closed",
    "topic deleted",
    "topic not found",
    "topic id invalid",
    "not a forum",
)


def refusal_of(description: str | None) -> Refusal | None:
    """
    Say what a ``400`` is about, from the Bot API's own words.

    Args:
        description: The ``description`` of the refusal.

    Returns:
        What it means, or None for a refusal that is no business of the sender's workarounds (the
        chat is not found, the token is wrong, the message is too long).
    """
    text = (description or "").lower().replace("_", " ")
    if any(marker in text for marker in _TOPIC_MARKERS):
        return Refusal.TOPIC
    if "repl" in text and ("not found" in text or "invalid" in text):
        return Refusal.REPLY
    if "can't parse entities" in text:
        return Refusal.MARKUP
    return None


def _decode(body: bytes) -> Any:
    """
    Args:
        body: An answer.

    Returns:
        Its JSON, or None for what is not JSON (a test double, a proxy's page).
    """
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None


def result(body: bytes) -> Any:
    """
    Args:
        body: The answer to any call.

    Returns:
        Its ``result``, or None when the body is not the Bot API's success shape.
    """
    data = _decode(body)
    if not isinstance(data, dict) or data.get("ok") is not True:
        return None
    return data.get("result")


def _positive(value: Any) -> int | None:
    """
    Args:
        value: Something an answer carried as an id.

    Returns:
        It as a positive whole number, or None.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return int(value)


def message_id(body: bytes) -> int | None:
    """
    Args:
        body: The answer to ``sendMessage``.

    Returns:
        The id of the message that was sent, or None when the answer does not say: a delivery is
        never judged by whether its answer could be read, only by whether it was accepted.
    """
    found = result(body)
    return _positive(found.get("message_id")) if isinstance(found, dict) else None


def topic_number(found: Any) -> int | None:
    """
    Args:
        found: The ``result`` of ``createForumTopic``.

    Returns:
        The number of the topic that was created (``message_thread_id``), or None.
    """
    return _positive(found.get("message_thread_id")) if isinstance(found, dict) else None


def created_topic(body: bytes) -> int | None:
    """
    Args:
        body: The answer to ``createForumTopic``.

    Returns:
        The number of the topic that was created, or None.
    """
    return topic_number(result(body))


class TelegramRefused(Exception):
    """
    The Bot API answered a call with an HTTP error.

    Attributes:
        code: The HTTP status.
        description: What Telegram said, in its own words (never paraphrased: a system error is
            shown verbatim). Free of credentials: the notifier scrubs it.
    """

    def __init__(self, code: int, description: str) -> None:
        """
        Args:
            code: The HTTP status.
            description: Telegram's ``description``.
        """
        super().__init__(f"HTTP {code}: {description}")
        self.code = code
        self.description = description


class TelegramUnreachable(Exception):
    """
    The Bot API could not be reached, or the destination was refused before it was dialled.

    Attributes:
        reason: Why, with the credentials taken out.
    """

    def __init__(self, reason: str) -> None:
        """
        Args:
            reason: Why, already scrubbed of the token.
        """
        super().__init__(reason)
        self.reason = reason
