# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a person said to the built-in assistant, kept in the store for them alone (item 171).

A conversation belongs to the account that began it. Every method takes the account and is held to
it here, in the store's own layer and not in the routes (rule 4): another account that guesses an
identifier gets the answer a conversation that does not exist gets, never "forbidden". Nobody reads
a transcript but its author; an auditor sees what the audit trail says (a summary of each turn),
not the conversation.

**What is written is already safe**, whoever writes it: the credentials that look like credentials
are masked (:func:`noust.core.scrubbing.scrub_text`, the one implementation), a text is cut at
:data:`MAX_CONTENT` characters and says so, and a conversation holds at most :data:`MAX_MESSAGES`
messages. The text kept is what the model was sent, with the markers of the disguise of personal
data (``<ip-1>``), and the key to them (:meth:`Conversations.markers`) is kept beside it, so that
what the person sees when they come back is what they saw.

**Retention** is ``retention.assistant_days`` (30 by default, with the other ``retention.*``), by the
last time the conversation was used, and a person keeps at most :data:`MAX_CONVERSATIONS`: the oldest
go first. Deleting a conversation does not delete what it cost (``assistant_usage``): a person who
deletes theirs does not give the month's spending back.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import ValidationError
from noust.core.scrubbing import scrub_text
from noust.core.teams.model import ResourceNotFoundError
from noust.core.timeutil import utc_now

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = [
    "MAX_CONTENT",
    "MAX_CONVERSATIONS",
    "MAX_MESSAGES",
    "Conversation",
    "ConversationNotFound",
    "Conversations",
    "Message",
]

#: The longest text one message keeps.
MAX_CONTENT = 60_000

#: The most messages a conversation holds.
MAX_MESSAGES = 500

#: The most conversations an account keeps.
MAX_CONVERSATIONS = 200

#: The roles stored. The instructions the model is given are built for every request, never kept.
ROLES = ("user", "assistant", "tool")

_MAX_TITLE = 120
_MAX_CALLS_JSON = 20_000


class ConversationNotFound(ResourceNotFoundError):
    """A conversation that does not exist, or is not the caller's: the same answer for both."""


@dataclass(frozen=True)
class Conversation:
    """
    One conversation, without its messages.

    Attributes:
        id: Its identifier.
        title: What it is called: the first question, or what the person named it.
        created_at: When it began (ISO 8601, UTC).
        updated_at: When it was last used.
    """

    id: str
    title: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Message:
    """
    One message of a conversation.

    Attributes:
        seq: Its place, from 1.
        role: ``user``, ``assistant`` or ``tool``.
        content: The text, masked and bounded.
        tool_calls: For an assistant message that called tools: ``{id, name, arguments}`` each.
        tool_call_id: For a tool message, the call it answers.
        created_at: When it was written.
    """

    seq: int
    role: str
    content: str
    tool_calls: list[dict[str, str]] | None
    tool_call_id: str | None
    created_at: str


def _safe(text: str, longest: int) -> str:
    """
    Args:
        text: What is about to be stored.
        longest: The most characters kept.

    Returns:
        The text with its credentials masked and, when too long, its end cut and said so.
    """
    cleaned = scrub_text(text)
    if len(cleaned) <= longest:
        return cleaned
    return f"{cleaned[:longest]}\n... (cut: {len(cleaned) - longest} more characters)"


def _title(text: str, longest: int = _MAX_TITLE) -> str:
    """
    Args:
        text: What a conversation is called, or the question that names it.
        longest: The most characters kept.

    Returns:
        One line with its credentials masked, cut without a note: a title is a label.
    """
    return scrub_text(" ".join(text.split()))[:longest]


class Conversations:
    """The conversations of every account, in the store's own connection."""

    def __init__(
        self, store: NoustStore | None = None, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
            clock: The moment now, in UTC; for tests.
        """
        self._store = store
        self._clock = clock

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    def _now(self) -> str:
        return self._clock().replace(microsecond=0).isoformat()

    # -- conversations ---------------------------------------------------------------------------

    def create(self, account_id: int, *, title: str = "") -> Conversation:
        """
        Begin a conversation. An account over :data:`MAX_CONVERSATIONS` loses its oldest.

        Args:
            account_id: Whose it is.
            title: What to call it; the first question names it when empty.

        Returns:
            The conversation.
        """
        now = self._now()
        identifier = uuid.uuid4().hex
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO assistant_conversations (id, account_id, title, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?)",
                (identifier, account_id, _title(title), now, now),
            )
            cursor.execute(
                "SELECT id FROM assistant_conversations WHERE account_id = ? "
                "ORDER BY updated_at DESC, rowid DESC LIMIT -1 OFFSET ?",
                (account_id, MAX_CONVERSATIONS),
            )
            for row in cursor.fetchall():
                self._remove(cursor, row["id"])
        return Conversation(identifier, _title(title), now, now)

    def get(self, conversation_id: str, account_id: int) -> Conversation | None:
        """
        Args:
            conversation_id: Its identifier.
            account_id: Who asks.

        Returns:
            The conversation, or None when there is none or it is not the asker's.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT id, title, created_at, updated_at FROM assistant_conversations "
                "WHERE id = ? AND account_id = ?",
                (conversation_id, account_id),
            )
            row = cursor.fetchone()
        return Conversation(**dict(row)) if row is not None else None

    def recent(self, account_id: int, *, limit: int = 50) -> list[Conversation]:
        """
        Args:
            account_id: Whose conversations.
            limit: The most to return.

        Returns:
            Their conversations, the most recently used first.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT id, title, created_at, updated_at FROM assistant_conversations "
                "WHERE account_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (account_id, max(limit, 1)),
            )
            return [Conversation(**dict(row)) for row in cursor.fetchall()]

    def rename(self, conversation_id: str, account_id: int, title: str) -> None:
        """
        Args:
            conversation_id: Its identifier.
            account_id: Who asks.
            title: The new name, masked and bounded.

        Raises:
            ConversationNotFound: There is none, or it is not the asker's.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE assistant_conversations SET title = ? WHERE id = ? AND account_id = ?",
                (_title(title), conversation_id, account_id),
            )
            if cursor.rowcount == 0:
                raise _not_found()

    def delete(self, conversation_id: str, account_id: int) -> bool:
        """
        Delete a conversation and its messages. What it cost stays counted.

        Args:
            conversation_id: Its identifier.
            account_id: Who asks.

        Returns:
            Whether one was deleted: False when there is none or it is not the asker's.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT 1 FROM assistant_conversations WHERE id = ? AND account_id = ?",
                (conversation_id, account_id),
            )
            if cursor.fetchone() is None:
                return False
            self._remove(cursor, conversation_id)
        return True

    @staticmethod
    def _remove(cursor: Any, conversation_id: str) -> None:
        """
        Args:
            cursor: A cursor inside a transaction.
            conversation_id: What to remove, with its messages.
        """
        cursor.execute(
            "DELETE FROM assistant_messages WHERE conversation_id = ?", (conversation_id,)
        )
        # What each turn kept and the calls prepared in them live as long as the conversation.
        cursor.execute("DELETE FROM assistant_turns WHERE conversation_id = ?", (conversation_id,))
        cursor.execute(
            "DELETE FROM assistant_proposals WHERE conversation_id = ?", (conversation_id,)
        )
        cursor.execute("DELETE FROM assistant_conversations WHERE id = ?", (conversation_id,))

    # -- messages --------------------------------------------------------------------------------

    def append(
        self,
        conversation_id: str,
        account_id: int,
        role: str,
        content: str,
        *,
        tool_calls: list[dict[str, str]] | None = None,
        tool_call_id: str | None = None,
    ) -> Message:
        """
        Add a message to a conversation.

        Args:
            conversation_id: Its identifier.
            account_id: Who asks.
            role: ``user``, ``assistant`` or ``tool``.
            content: The text. Credentials in it are masked here, whatever the caller did.
            tool_calls: For an assistant message, the calls it made.
            tool_call_id: For a tool message, the call it answers.

        Returns:
            The message as stored.

        Raises:
            ConversationNotFound: There is none, or it is not the asker's.
            ValidationError: The role is not one stored, or the conversation is full.
        """
        if role not in ROLES:
            raise ValidationError(
                f"A message's role is one of {', '.join(ROLES)}, not {role!r}", field="role"
            )
        safe = _safe(content, MAX_CONTENT)
        calls = (
            [
                {
                    "id": str(call.get("id", ""))[:100],
                    "name": str(call.get("name", ""))[:100],
                    "arguments": _safe(str(call.get("arguments", "")), _MAX_CALLS_JSON),
                }
                for call in tool_calls
            ]
            if tool_calls
            else None
        )
        now = self._now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT title FROM assistant_conversations WHERE id = ? AND account_id = ?",
                (conversation_id, account_id),
            )
            found = cursor.fetchone()
            if found is None:
                raise _not_found()
            cursor.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM assistant_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            seq = int(cursor.fetchone()[0]) + 1
            if seq > MAX_MESSAGES:
                raise ValidationError(
                    "This conversation is as long as one may be",
                    details="Start a new conversation: the assistant reads the one you are in "
                    "again at every step, and a long one costs more with each.",
                )
            cursor.execute(
                "INSERT INTO assistant_messages (conversation_id, seq, role, content, tool_calls, "
                "tool_call_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    seq,
                    role,
                    safe,
                    json.dumps(calls) if calls else None,
                    tool_call_id,
                    now,
                ),
            )
            title = found["title"] or (
                _safe(" ".join(content.split()), 60) if role == "user" else ""
            )
            cursor.execute(
                "UPDATE assistant_conversations SET updated_at = ?, title = ? WHERE id = ?",
                (now, title, conversation_id),
            )
        return Message(seq, role, safe, calls, tool_call_id, now)

    def messages(self, conversation_id: str, account_id: int) -> list[Message]:
        """
        Args:
            conversation_id: Its identifier.
            account_id: Who asks.

        Returns:
            The messages in order.

        Raises:
            ConversationNotFound: There is none, or it is not the asker's.
        """
        self._require(conversation_id, account_id)
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT seq, role, content, tool_calls, tool_call_id, created_at "
                "FROM assistant_messages WHERE conversation_id = ? ORDER BY seq",
                (conversation_id,),
            )
            return [
                Message(
                    row["seq"],
                    row["role"],
                    row["content"],
                    json.loads(row["tool_calls"]) if row["tool_calls"] else None,
                    row["tool_call_id"],
                    row["created_at"],
                )
                for row in cursor.fetchall()
            ]

    # -- the key to the disguise -----------------------------------------------------------------

    def markers(self, conversation_id: str, account_id: int) -> dict[str, str]:
        """
        Args:
            conversation_id: Its identifier.
            account_id: Who asks.

        Returns:
            The markers of the disguise of personal data in this conversation.

        Raises:
            ConversationNotFound: There is none, or it is not the asker's.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT markers FROM assistant_conversations WHERE id = ? AND account_id = ?",
                (conversation_id, account_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise _not_found()
        try:
            decoded = json.loads(row["markers"] or "{}")
        except ValueError:
            return {}
        return {str(k): str(v) for k, v in decoded.items()} if isinstance(decoded, dict) else {}

    def save_markers(
        self, conversation_id: str, account_id: int, markers: Mapping[str, str]
    ) -> None:
        """
        Args:
            conversation_id: Its identifier.
            account_id: Who asks.
            markers: The markers so far.

        Raises:
            ConversationNotFound: There is none, or it is not the asker's.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE assistant_conversations SET markers = ? WHERE id = ? AND account_id = ?",
                (json.dumps(dict(markers)), conversation_id, account_id),
            )
            if cursor.rowcount == 0:
                raise _not_found()

    # -- retention -------------------------------------------------------------------------------

    def prune(self, before: datetime) -> int:
        """
        Delete the conversations last used before a moment, with their messages.

        Args:
            before: The cutoff, in UTC.

        Returns:
            How many conversations were deleted.
        """
        cutoff = before.replace(microsecond=0).isoformat()
        with self.store._transaction() as cursor:
            cursor.execute("SELECT id FROM assistant_conversations WHERE updated_at < ?", (cutoff,))
            ids = [row["id"] for row in cursor.fetchall()]
            for identifier in ids:
                self._remove(cursor, identifier)
        return len(ids)

    def prune_usage(self, before: datetime) -> int:
        """
        Delete the ledger of what steps cost, and of the changes made, from before a moment.

        Args:
            before: The cutoff, in UTC. Far enough back that no month still counted is touched.

        Returns:
            How many rows were deleted.
        """
        cutoff = before.replace(microsecond=0).isoformat()
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM assistant_usage WHERE at < ?", (cutoff,))
            gone = cursor.rowcount
            cursor.execute("DELETE FROM assistant_actions WHERE at < ?", (cutoff,))
            return int(gone + cursor.rowcount)

    def _require(self, conversation_id: str, account_id: int) -> None:
        """
        Args:
            conversation_id: Its identifier.
            account_id: Who asks.

        Raises:
            ConversationNotFound: There is none, or it is not the asker's.
        """
        if self.get(conversation_id, account_id) is None:
            raise _not_found()


def _not_found() -> ConversationNotFound:
    return ConversationNotFound(
        "There is no such conversation",
        details="It may have been deleted, or it is another person's.",
    )
