# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Telegram Bot API that answers the way the documented one does, for the tests of the topics.

``FakeTelegram`` stands in for ``urlopen``: it is the opener a ``Notifier`` (or the topic
creation) is given, so no socket is ever opened. It keeps the state a group has - which topics
exist, which are closed, which messages are still there, whether the group is a forum, what the
bot may do - and answers ``sendMessage``, ``getMe``, ``getChat``, ``getChatMember`` and
``createForumTopic`` from it, with the HTTP errors the Bot API gives (``400 Bad Request: ...``)
carrying Telegram's own ``description``.

The refusal texts are the ones seen in practice for a topic that is gone; what Telegram says for
each case is not documented on one page, which is why the tests also run every spelling in
:data:`TOPIC_REFUSALS`.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import Message
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request

from noust.core.notifications.model import Notification, State

BOT_TOKEN = "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
CHAT_ID = "-1001234567890"
START = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)

#: What the Bot API has answered for a topic that cannot take a message.
TOPIC_REFUSALS = (
    "Bad Request: message thread not found",
    "Bad Request: TOPIC_CLOSED",
    "Bad Request: TOPIC_DELETED",
    "Bad Request: the chat is not a forum",
    "Bad Request: topic closed",
)

#: What it has answered for a reply to a message that is no longer there.
REPLY_REFUSALS = (
    "Bad Request: message to be replied not found",
    "Bad Request: reply message not found",
)


#: The Bot API method each handler answers.
_HANDLERS = {
    "sendMessage": "_send_message",
    "getMe": "_get_me",
    "getChat": "_get_chat",
    "getChatMember": "_get_chat_member",
    "createForumTopic": "_create_forum_topic",
}


class Clock:
    """One moment, read as a datetime by the notifier and as epoch seconds by the store."""

    def __init__(self, moment: datetime = START) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def epoch(self) -> float:
        return self.moment.timestamp()

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


def refusal(url: str, code: int, description: str) -> HTTPError:
    """
    Args:
        url: The request that was refused.
        code: The HTTP status.
        description: Telegram's own words.

    Returns:
        The error ``urlopen`` raises, whose body is the Bot API's JSON.
    """
    body = json.dumps({"ok": False, "error_code": code, "description": description})
    return HTTPError(url, code, "Bad Request", Message(), io.BytesIO(body.encode("utf-8")))


@dataclass
class FakeTelegram:
    """
    One bot in one group.

    Attributes:
        topics: The topics that exist and accept messages, by number.
        closed: Topics that exist and are closed.
        is_forum: Whether the group has topics turned on.
        can_manage_topics: Whether the bot may create topics.
        is_member: Whether the bot can see the group at all.
        status: What the bot is in the group: ``administrator``, ``creator``, ``member``, ``left``.
        refusal_text: What a message to a topic that cannot take it is answered with.
        gone_messages: Messages that were deleted: a reply to one is refused.
        sent: Every ``sendMessage`` body, in order.
        calls: Every call as ``(method, body)``.
        creation_failure: What ``createForumTopic`` answers instead of a topic, as ``(code, text)``.
        rate_limit_after: ``createForumTopic`` is answered ``429`` after this many topics.
    """

    topics: set[int] = field(default_factory=set)
    closed: set[int] = field(default_factory=set)
    is_forum: bool = True
    can_manage_topics: bool = True
    is_member: bool = True
    status: str = "administrator"
    refusal_text: str = TOPIC_REFUSALS[0]
    gone_messages: set[int] = field(default_factory=set)
    sent: list[dict[str, Any]] = field(default_factory=list)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    creation_failure: tuple[int, str] | None = None
    rate_limit_after: int | None = None
    bot_id: int = 7_000_000_001
    next_message: int = 100
    next_topic: int = 2
    requests: list[Request] = field(default_factory=list)

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        self.requests.append(request)
        url = str(request.full_url)
        method = url.rsplit("/", 1)[-1]
        data = request.data
        body: dict[str, Any] = json.loads(data) if isinstance(data, bytes) and data else {}
        self.calls.append((method, body))
        handler = getattr(self, _HANDLERS.get(method, "_unknown"), None)
        if handler is None:
            raise refusal(url, 404, "Not Found")
        return self._ok(handler(url, body))

    @staticmethod
    def _ok(result: Any) -> io.BytesIO:
        return io.BytesIO(json.dumps({"ok": True, "result": result}).encode("utf-8"))

    def _send_message(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        thread = body.get("message_thread_id")
        if thread is not None and (thread not in self.topics or thread in self.closed):
            raise refusal(url, 400, self.refusal_text)
        reply = (body.get("reply_parameters") or {}).get("message_id")
        if reply is not None and reply in self.gone_messages:
            raise refusal(url, 400, REPLY_REFUSALS[0])
        self.sent.append(body)
        self.next_message += 1
        return {"message_id": self.next_message, "chat": {"id": int(CHAT_ID)}}

    def _get_me(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        return {"id": self.bot_id, "is_bot": True, "username": "noust_test_bot"}

    def _get_chat(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.is_member:
            raise refusal(url, 400, "Bad Request: chat not found")
        chat: dict[str, Any] = {"id": int(CHAT_ID), "type": "supergroup", "title": "Ops"}
        if self.is_forum:
            chat["is_forum"] = True
        return chat

    def _get_chat_member(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        member: dict[str, Any] = {"status": self.status, "user": {"id": self.bot_id}}
        if self.status == "administrator":
            member["can_manage_topics"] = self.can_manage_topics
        return member

    def _create_forum_topic(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.creation_failure is not None:
            raise refusal(url, *self.creation_failure)
        created = sum(1 for method, _ in self.calls if method == "createForumTopic") - 1
        if self.rate_limit_after is not None and created >= self.rate_limit_after:
            raise refusal(url, 429, "Too Many Requests: retry after 3")
        if self.status != "creator" and not self.can_manage_topics:
            raise refusal(url, 400, "Bad Request: not enough rights to create a topic")
        number = self.next_topic
        self.next_topic += 1
        self.topics.add(number)
        return {"message_thread_id": number, "name": body.get("name"), "icon_color": 7322096}

    def of(self, method: str) -> list[dict[str, Any]]:
        """
        Args:
            method: A Bot API method.

        Returns:
            The bodies of every call to it.
        """
        return [body for name, body in self.calls if name == method]


def note(
    kind: str = "app_unreachable",
    state: State = State.FAILED,
    domain: str | None = "shop.example.com",
    *,
    title: str | None = None,
    server: str = "web-1",
    when: datetime = START,
) -> Notification:
    """
    Args:
        kind: The event kind.
        state: How it went.
        domain: The application, or None.
        title: The state as a phrase; a generic one by default.
        server: The server's name.
        when: When it happened.

    Returns:
        A notification with nothing fancy in it.
    """
    return Notification(
        kind=kind,
        code=f"{kind}.x",
        state=state,
        locale="en",
        title=title or ("Problem" if state is not State.OK else "Fine again"),
        subject=domain or "",
        summary="Something to read.",
        server=server,
        domain=domain,
        ts=when,
    )
