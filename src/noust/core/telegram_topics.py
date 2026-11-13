# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust creates the topics of a forum group, when somebody asks and the bot is allowed to.

A group that has topics turned on keeps conversations apart; a Noust channel can post each subject
(deploys, alerts, backups, security, system) in a topic of its own
(:mod:`noust.core.notifications.grouping`). Until now the operator had to create the topics by hand
and copy their numbers. :meth:`TelegramTopics.create` does it with the bot the channel already has:
it asks the group whether it has topics (``getChat``), whether the bot may manage them
(``getChatMember``), creates the ones that are missing (``createForumTopic``) and writes their
numbers into the channel. It is a button somebody pushes: nothing is asked of Telegram until then.

What it will not do, it says, and in Telegram's own words where Telegram said it:

- the group is **not a forum**: only its owner can turn topics on (Group settings, Topics), so it is
  said with the steps and not attempted;
- the bot is **not an administrator** or lacks the right to manage topics: said, with the right to
  give it;
- Telegram **refuses** a creation (a rate limit, a name): the topics made so far are kept, and
  asking again makes only the rest, never one twice.

Repeating is safe: a subject that already has a number is not created again, and a topic that
stopped taking messages (the channel marks it, :mod:`~noust.core.notifications.topic_notes`) is
made anew only when asked (``recreate``).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import NoustError, ValidationError
from noust.core.messages import message, normalize_locale
from noust.core.notification_channels import ChannelAdmin
from noust.core.notifications import grouping
from noust.core.notifier import Notifier
from noust.core.telegram_bot import TelegramRefused, TelegramUnreachable, topic_number

logger = logging.getLogger(__name__)

#: Why topics could not be created, as the codes the console words.
PROBLEMS: tuple[str, ...] = (
    "not_forum",
    "not_member",
    "no_right",
    "chat_unreachable",
    "unreachable",
    "refused",
)


@dataclass(frozen=True)
class TopicsResult:
    """
    What asking Noust to create the topics did.

    Attributes:
        created: The subjects that got a topic now, with its number.
        kept: The subjects that already had one, with its number.
        failed: The subjects that have none after this: the ones Telegram refused and the ones
            left when it did.
        problem: Why it stopped, one of :data:`PROBLEMS`, or None when every subject has a topic.
        detail: What Telegram said, in its own words, when it was Telegram who said it.
    """

    created: dict[str, int] = field(default_factory=dict)
    kept: dict[str, int] = field(default_factory=dict)
    failed: tuple[str, ...] = ()
    problem: str | None = None
    detail: str | None = None

    @property
    def topics(self) -> dict[str, int]:
        """The topic of each subject that has one after this, in the order of the subjects."""
        merged = {**self.kept, **self.created}
        return {subject: merged[subject] for subject in grouping.SUBJECTS if subject in merged}

    def to_dict(self) -> dict[str, Any]:
        """Returns: the result as the API and the command line show it."""
        return {
            "created": dict(self.created),
            "kept": dict(self.kept),
            "failed": list(self.failed),
            "problem": self.problem,
            "detail": self.detail,
            "topics": self.topics,
        }


class TelegramTopics:
    """The topics of a Telegram channel's group, created through the channel's own bot."""

    def __init__(self, notifier: Notifier) -> None:
        """
        Args:
            notifier: The notifier whose door every request to Telegram goes through (its
                deadline, its guard, its way of keeping a token out of an error).
        """
        self._notifier = notifier

    def create(self, name: str, admin: ChannelAdmin, *, recreate: bool = False) -> TopicsResult:
        """
        Create the topics a channel's subjects are missing, and save their numbers.

        The numbers are written into ``admin``'s configuration as they are made, so what was made
        before a refusal is kept; the caller writes the configuration to disk afterwards, whatever
        the answer.

        Args:
            name: The Telegram channel.
            admin: The channel admin whose configuration receives the numbers.
            recreate: Also make a new topic for a subject whose topic stopped taking messages (the
                channel marks those); without it they are left as they are.

        Returns:
            What was made, what was already there and why it stopped, if it did.

        Raises:
            ValidationError: When there is no such channel, it is not a Telegram one, or it has no
                bot token or chat yet.
        """
        instance = self._notifier.channel(name)
        if instance is None:
            raise ValidationError(
                f"There is no notification channel named {name!r}",
                details="'noust notify list' shows the channels.",
                field="name",
            )
        if instance.type != "telegram":
            raise ValidationError(
                f"{name!r} is not a Telegram channel",
                details="Only a Telegram group has topics.",
                field="name",
            )
        if not instance.configured:
            raise ValidationError(
                f"Channel {name!r} needs a bot token and a chat first",
                details="Save them, then create the topics.",
            )
        chat = str(instance.settings.get("chat_id") or "")
        problem = self._check(instance, chat)
        if problem is not None:
            return TopicsResult(problem=problem[0], detail=problem[1])

        current = instance.settings.get("topics")
        have = {
            subject: number
            for subject, written in (current if isinstance(current, dict) else {}).items()
            if subject in grouping.SUBJECTS
            and (number := grouping.as_topic_number(written)) is not None
        }
        lost = self._lost(name, instance.settings) if recreate else set()
        kept = {subject: number for subject, number in have.items() if number not in lost}
        missing = [subject for subject in grouping.SUBJECTS if subject not in kept]
        locale = normalize_locale(admin.config.get("notifications.language"))

        created: dict[str, int] = {}
        stopped: tuple[str, str | None] | None = None
        try:
            for subject in missing:
                made = self._create_one(instance, chat, message(f"topic.name.{subject}", locale))
                if isinstance(made, int):
                    created[subject] = made
                    continue
                stopped = made
                break
        finally:
            if created:
                merged = {**have, **created}
                admin.save(name, settings={"topics": merged})
        failed = tuple(subject for subject in missing if subject not in created)
        return TopicsResult(
            created=created,
            kept=kept,
            failed=failed,
            problem=stopped[0] if stopped else None,
            detail=stopped[1] if stopped else None,
        )

    def _check(self, instance: Any, chat: str) -> tuple[str, str | None] | None:
        """
        Ask the group whether it can take topics, and whether the bot may make them.

        Args:
            instance: The Telegram channel.
            chat: Its chat id.

        Returns:
            The problem and what Telegram said, or None when topics can be created.
        """
        try:
            group = self._notifier.telegram_call(instance, "getChat", {"chat_id": chat})
            if not isinstance(group, dict) or group.get("is_forum") is not True:
                return ("not_forum", None)
            me = self._notifier.telegram_call(instance, "getMe", {})
            bot = me.get("id") if isinstance(me, dict) else None
            member = self._notifier.telegram_call(
                instance, "getChatMember", {"chat_id": chat, "user_id": bot}
            )
        except TelegramRefused as exc:
            return ("chat_unreachable", exc.description)
        except TelegramUnreachable as exc:
            return ("unreachable", exc.reason)
        status = member.get("status") if isinstance(member, dict) else None
        if status in ("left", "kicked"):
            return ("not_member", None)
        if status == "creator":
            return None
        if status == "administrator" and member.get("can_manage_topics") is True:
            return None
        return ("no_right", None)

    def _create_one(self, instance: Any, chat: str, title: str) -> int | tuple[str, str | None]:
        """
        Make one topic.

        Args:
            instance: The Telegram channel.
            chat: Its chat id.
            title: The topic's name.

        Returns:
            The topic's number, or the problem and what Telegram said when it could not be made
            (including an answer that does not carry the number, which cannot be used).
        """
        try:
            found = self._notifier.telegram_call(
                instance, "createForumTopic", {"chat_id": chat, "name": title}
            )
        except TelegramRefused as exc:
            return ("refused", exc.description)
        except TelegramUnreachable as exc:
            return ("unreachable", exc.reason)
        number = topic_number(found)
        return number if number is not None else ("refused", None)

    def _lost(self, name: str, settings: Any) -> set[int]:
        """
        Args:
            name: The channel.
            settings: Its settings.

        Returns:
            The topics it still posts in that stopped taking messages.
        """
        try:
            notes = self._notifier.topic_notes()
            return {n.topic for n in notes.standing(name, settings)} if notes else set()
        except (sqlite3.Error, OSError, NoustError) as exc:
            logger.warning("The marks of channel %s could not be read: %s", name, exc)
            return set()
