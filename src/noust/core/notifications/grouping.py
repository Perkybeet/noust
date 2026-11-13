# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How notifications group, in one place: by subject, by thing, and what each channel can do with it.

Two different ideas of "these belong together" live here, and every channel that can group uses
one or both:

- **A subject** is what an event is about at the level of an operator's chat: deploys, alerts,
  backups, security, system. A chat that has topics gives each subject a topic of its own, so the
  main line is not every message of every kind. :func:`subject_of` is the one answer, derived from
  the families below and completed by a short table for the events that have no way back.
- **A thing** is one application, one unit, one disk, one deploy. A failure and its recovery are
  two messages about one thing, and a channel that can answer a message puts the recovery as the
  reply to the failure. :data:`FAMILY` says which kinds are two sides of one thing and
  :func:`thing_key` names the thing; the pager (:mod:`~noust.core.notifications.render.pagerduty`)
  has always used the same key as its incident, which is why it lives here and not there (rule 3).

A failure and its recovery have to be in the same subject: a reply across topics is refused by
Telegram, so a test fails on a family whose kinds are split, and on an event nobody ruled on.

What a channel can do is :data:`GROUPING`, and what it cannot is said with a code
(:data:`REASONS`) that the console words; a channel never offers what it cannot do.

Pure: nothing here reads the machine or the network.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.notifications.model import EVENT_KINDS, Notification

#: What a chat's topics are made for, in the order the console lists them.
SUBJECTS: tuple[str, ...] = ("deploys", "alerts", "backups", "security", "system")

#: The kinds that have a way back, and what they are one thing with. Two kinds of one family
#: (``app_unreachable`` and ``app_recovered``) are the two sides of the same thing. The start of
#: a deploy is one of them: the deploy it starts is the thing its end answers.
FAMILY: dict[str, str] = {
    "app_unreachable": "app",
    "app_recovered": "app",
    "unit_failed": "unit",
    "disk_threshold": "disk",
    "destination_unreachable": "destination",
    "node_unreachable": "node",
    "node_recovered": "node",
    "server_rebooted": "server",
    "server_back": "server",
    "alert_firing": "alert",
    "alert_resolved": "alert",
    "alert_acknowledged": "alert",
    "deploy_started": "deploy",
    "deploy_failed": "deploy",
    "deploy_rolled_back": "deploy",
    "deploy_hook_failed": "deploy",
    "deploy_success": "deploy",
    "backup_failed": "backup",
    "backup_success": "backup",
    "restore_failed": "restore",
    "restore_success": "restore",
    "cron_failed": "cron",
    "cron_success": "cron",
    "cron_missed": "cron-timer",
    "cleanup_failed": "cleanup",
    "cleanup_success": "cleanup",
    "probe_failed": "probe",
    "probe_recovered": "probe",
}

#: The kinds that open what a family's recovery closes.
OPENING: dict[str, tuple[str, ...]] = {
    "app": ("app_unreachable",),
    "unit": ("unit_failed",),
    "disk": ("disk_threshold",),
    "destination": ("destination_unreachable",),
    "node": ("node_unreachable",),
    "server": ("server_rebooted",),
    "alert": ("alert_firing",),
    "deploy": ("deploy_failed", "deploy_rolled_back", "deploy_hook_failed"),
    "backup": ("backup_failed",),
    "restore": ("restore_failed",),
    "cron": ("cron_failed",),
    "cron-timer": ("cron_missed",),
    "cleanup": ("cleanup_failed",),
    "probe": ("probe_failed",),
}

#: The subject of each family: a thing's two sides are always in the same one.
_FAMILY_SUBJECT: dict[str, str] = {
    "app": "alerts",
    "unit": "alerts",
    "disk": "system",
    "destination": "backups",
    "node": "alerts",
    "server": "system",
    "alert": "alerts",
    "deploy": "deploys",
    "backup": "backups",
    "restore": "backups",
    "cron": "alerts",
    "cron-timer": "alerts",
    "cleanup": "system",
    "probe": "alerts",
}

#: The events that have no way back, and so no family, ruled on one by one.
_KIND_SUBJECT: dict[str, str] = {
    "backup_missing": "backups",
    "container_restart_loop": "alerts",
    "app_oom_killed": "alerts",
    "central_silent": "alerts",
    "node_host_key_changed": "security",
    "security_release": "security",
    "token_expiring": "security",
    "approval_requested": "security",
    "approval_decided": "security",
    "access_change_reverted": "security",
    "cert_expiring": "system",
    "cert_renewal_failed": "system",
    "domain_expiring": "system",
    "domain_expired": "system",
    "os_patches": "system",
    "recipe_outdated": "system",
}


def family_of(kind: str) -> str | None:
    """
    Args:
        kind: A notification kind.

    Returns:
        The family it is one side of, or None for a kind with no way back.
    """
    return FAMILY.get(kind)


def subject_of(kind: str) -> str | None:
    """
    Args:
        kind: A notification kind.

    Returns:
        What it is about (one of :data:`SUBJECTS`), or None for what is not an event of the
        catalogue (the test message, a digest, a report): that has no topic of its own.
    """
    family = FAMILY.get(kind)
    if family is not None:
        return _FAMILY_SUBJECT[family]
    return _KIND_SUBJECT.get(kind)


def kinds_of(subject: str) -> tuple[str, ...]:
    """
    Args:
        subject: One of :data:`SUBJECTS`.

    Returns:
        The events it covers, in the order of the catalogue; empty for a name that is not a subject.
    """
    return tuple(kind for kind in EVENT_KINDS if subject_of(kind) == subject)


def opening_kinds(n: Notification) -> tuple[str, ...]:
    """
    Args:
        n: A notification that closes something.

    Returns:
        The kinds whose problem it closes: the switches a channel must have on for the recovery
        to be worth sending. Empty for a kind that closes nothing.
    """
    return OPENING.get(FAMILY.get(n.kind, ""), ())


def thing_key(n: Notification) -> str:
    """
    Name the thing a notification is about.

    Args:
        n: The notification.

    Returns:
        A stable key of at most 255 characters: the same for the two sides of a kind that has a
        way back, so the recovery is found by what the failure opened. It is the pager's
        ``dedup_key`` and the key a chat remembers a conversation by.
    """
    family = FAMILY.get(n.kind, n.kind)
    rule = next((fact.value for fact in n.facts if fact.key == "rule"), "")
    parts = ["noust", n.server, family, n.domain or n.subject]
    if family == "alert":
        # Two rules on one application are two things.
        parts = ["noust", n.server, family, rule, n.domain or ""]
    key = "/".join(parts)
    if len(key) <= 255:
        return key
    return "noust/" + hashlib.sha256(key.encode("utf-8")).hexdigest()


# -- the topic of a Telegram chat ------------------------------------------------------------


@dataclass(frozen=True)
class Topic:
    """
    The topic of a forum chat a message goes to.

    Attributes:
        number: Its number in the chat.
        subject: The subject it is the topic of, when it was chosen as one.
        via: Why this one: ``event`` (the event has a topic of its own), ``subject`` (the topic of
            its subject) or ``channel`` (the one topic the channel posts in).
    """

    number: int
    subject: str | None
    via: str


def as_topic_number(value: Any) -> int | None:
    """
    Args:
        value: A topic number as it was written: an integer, or text.

    Returns:
        The number, or None for anything that is not a positive whole one (a hand edit): the
        alert matters more than its place, so it is ignored and never refused here.
    """
    if value is None or isinstance(value, (bool, float)):
        return None
    try:
        number = int(str(value).strip())
    except ValueError:
        return None
    return number if number > 0 else None


def topic_for(settings: Mapping[str, Any], kind: str) -> Topic | None:
    """
    Pick the forum topic a Telegram message is posted in.

    Args:
        settings: The Telegram channel's settings.
        kind: The event kind being sent.

    Returns:
        The topic of that event (``threads``), else the topic of its subject (``topics``), else
        the channel's one topic (``thread_id``), else None: the chat's general thread.
    """
    threads = settings.get("threads")
    if isinstance(threads, Mapping) and (own := as_topic_number(threads.get(kind))) is not None:
        return Topic(own, None, "event")
    subject = subject_of(kind)
    topics = settings.get("topics")
    if subject is not None and isinstance(topics, Mapping):
        number = as_topic_number(topics.get(subject))
        if number is not None:
            return Topic(number, subject, "subject")
    one = as_topic_number(settings.get("thread_id"))
    return Topic(one, None, "channel") if one is not None else None


def topics_in_use(settings: Mapping[str, Any]) -> frozenset[int]:
    """
    Args:
        settings: The Telegram channel's settings.

    Returns:
        Every topic number the channel can post in: the one of the channel, those of its
        subjects and those of its events.
    """
    one = as_topic_number(settings.get("thread_id"))
    found: set[int] = {one} if one is not None else set()
    for key in ("threads", "topics"):
        block = settings.get(key)
        if isinstance(block, Mapping):
            found.update(
                n for n in (as_topic_number(value) for value in block.values()) if n is not None
            )
    return frozenset(found)


# -- what each channel can do ----------------------------------------------------------------


@dataclass(frozen=True)
class Grouping:
    """
    What a channel type can do to keep messages together.

    Attributes:
        topics: It has places a chat can give each subject (a forum's topics).
        replies: A recovery can be put as the answer to its failure (a reply, a mail in the same
            conversation).
        reason: When it can do neither, why, as a code of :data:`REASONS`.
    """

    topics: bool = False
    replies: bool = False
    reason: str | None = None


#: Why a channel cannot group: the codes the console words.
REASONS: tuple[str, ...] = (
    "incoming_webhook",
    "workflow_webhook",
    "custom_bot",
    "no_threads",
    "incidents",
    "receiver",
    "not_yet",
)

#: Every channel type. A webhook of Slack or Mattermost never says the id of the message it posts
#: and so cannot be answered; a pager keeps its own incidents; the rest are not done yet.
GROUPING: dict[str, Grouping] = {
    "telegram": Grouping(topics=True, replies=True),
    "email": Grouping(replies=True),
    "slack": Grouping(reason="incoming_webhook"),
    "mattermost": Grouping(reason="incoming_webhook"),
    "teams": Grouping(reason="workflow_webhook"),
    "lark": Grouping(reason="custom_bot"),
    "pushover": Grouping(reason="no_threads"),
    "gotify": Grouping(reason="no_threads"),
    "pagerduty": Grouping(reason="incidents"),
    "webhook": Grouping(reason="receiver"),
    "discord": Grouping(reason="not_yet"),
    "matrix": Grouping(reason="not_yet"),
    "ntfy": Grouping(reason="not_yet"),
}
