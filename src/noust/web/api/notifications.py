# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/notifications``: the channels, the events and the record of deliveries.

A thin client of :class:`~noust.core.notification_channels.ChannelAdmin` (which
``noust notify`` uses too), the delivery ledger
(:mod:`noust.core.notification_ledger`) and the notifier. What a channel accepts
is decided by the configuration chokepoint, so an endpoint here only translates:
a body into a call and a refusal into the one error body every router has.

A credential is never in a response: a channel's secrets read ``***`` when one
is stored and ``""`` when none is, and a ``***`` in a body keeps the stored one.
Changing a channel needs sudo mode, because it decides where the alerts of the
server go and holds the keys to those places.

Sending a test through a channel is ``POST /api/config/notifications/{channel}/test``,
which takes any channel's name.

Literal paths come before the parametrised ones.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.exceptions import NoustError, ValidationError
from noust.core.notification_channels import ChannelAdmin, ChannelView, event_catalog
from noust.core.notification_ledger import STATES, Delivery, DeliveryLedger
from noust.core.notification_state import NotificationState
from noust.core.notifications import grouping
from noust.core.notifications.instances import TYPES
from noust.core.notifications.topic_notes import TopicNotes
from noust.core.notifier import Notifier, fresh_config
from noust.core.telegram_topics import TelegramTopics
from noust.web.api.auth import get_current_session
from noust.web.api.config import persist
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute, require_elevated

logger = logging.getLogger(__name__)

router = APIRouter(route_class=NoustErrorRoute)

#: The setting the address of the console is kept in.
CONSOLE_URL_KEY = "notifications.console_url"

Session = Annotated[dict[str, Any], Depends(get_current_session)]
Elevated = Annotated[dict[str, Any], Depends(require_elevated)]


class NoticeModel(BaseModel):
    """
    Something a channel has to say about how it is working.

    Attributes:
        code: What it is: ``topic_lost`` (a topic of a Telegram group stopped taking messages, and
            what was meant for it went to the general thread).
        topic: The topic's number in the group.
        subject: The subject it was the topic of, when it was one.
        reason: What the destination answered, in its own words.
        at: When it was first refused, epoch seconds.
    """

    code: str
    topic: int
    subject: str | None = None
    reason: str = ""
    at: float = 0.0


class ChannelModel(BaseModel):
    """
    One notification channel, without its credentials.

    Attributes:
        name: What the channel is called.
        type: One of the types (``slack``, ``pushover``...).
        enabled: Whether it is on.
        configured: Whether it has what its type needs to deliver.
        default: It is the default channel of its type: it cannot be removed,
            only emptied.
        settings: The type's own settings; a credential reads ``***`` when one
            is stored and an empty string when none is.
        events: This channel's own switch per event (those it does not name
            follow ``notifications.events``).
        route: What it is limited to (applications, projects, environments,
            tags, a minimum severity).
        quiet: When it stays silent, or null for never.
        flood: The most it says in a stretch of time (``max`` messages in
            ``minutes``), or null for no cap; what passes it arrives later as
            one message.
        summary: What waits for a periodic summary (``at``, ``timezone``,
            ``below``, ``days``), or null for none.
        team: The slug of the team that keeps it (``/api/teams/{team}/notifications``),
            or null for the server's own.
        notices: What it has to say about how it is working (a topic that stopped taking
            messages); empty when there is nothing.
    """

    name: str
    type: str
    enabled: bool
    configured: bool
    default: bool
    settings: dict[str, Any] = Field(default_factory=dict)
    events: dict[str, bool] = Field(default_factory=dict)
    route: dict[str, Any] = Field(default_factory=dict)
    quiet: dict[str, Any] | None = None
    flood: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    team: str | None = None
    notices: list[NoticeModel] = Field(default_factory=list)


class ChannelFieldModel(BaseModel):
    """
    A setting of a channel type.

    Attributes:
        key: Its name.
        secret: It is a credential.
        required: The channel cannot deliver without it.
    """

    key: str
    secret: bool
    required: bool


class GroupingModel(BaseModel):
    """
    What a channel type can do to keep messages together.

    Attributes:
        topics: A chat that gives each subject a topic of its own (a forum group).
        replies: A recovery can be put as the answer to its failure.
        reason: When it can do neither, why, as a code the console words (``incoming_webhook``:
            the webhook never says the id of the message it posts, so it cannot be answered).
    """

    topics: bool
    replies: bool
    reason: str | None = None


class ChannelTypeModel(BaseModel):
    """
    One kind of channel Noust can deliver through.

    Attributes:
        name: The type's name.
        keys: Its settings.
        grouping: What it can do to keep messages together.
    """

    name: str
    keys: list[ChannelFieldModel]
    grouping: GroupingModel


class ChannelListResponse(BaseModel):
    """
    Every channel, and the types a new one may be.

    Attributes:
        items: The channels: the default of each type, then the operator's own.
        types: The types, with their settings.
        enabled: The master switch (``notifications.enabled``).
    """

    items: list[ChannelModel]
    types: list[ChannelTypeModel]
    enabled: bool


class ChannelBody(BaseModel):
    """
    What to set on a channel; whatever is left out stays as it is.

    Attributes:
        type: The type; needed for a new channel that is not the default of a
            type.
        enabled: On or off.
        settings: The type's settings to set. ``***`` keeps a stored credential,
            an empty string clears a setting.
        events: Replaces the channel's own event switches (empty clears them).
        route: Replaces its route (empty clears it).
        quiet: Replaces its quiet hours (empty clears them).
        flood: Replaces its anti-flood cap (empty clears it).
        summary: Replaces its periodic summary (empty clears it).
    """

    type: str | None = None
    enabled: bool | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    events: dict[str, bool] | None = None
    route: dict[str, Any] | None = None
    quiet: dict[str, Any] | None = None
    flood: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None


class TopicsBody(BaseModel):
    """
    What to ask when creating the topics of a Telegram group.

    Attributes:
        recreate: Also make a new topic for a subject whose topic stopped taking messages.
    """

    recreate: bool = False


class TopicsResultModel(BaseModel):
    """
    What creating the topics did.

    Attributes:
        created: The subjects that got a topic now, with its number.
        kept: The subjects that already had one, with its number.
        failed: The subjects that have none after this.
        problem: Why it stopped (``not_forum``, ``not_member``, ``no_right``, ``chat_unreachable``,
            ``unreachable``, ``refused``), or null when every subject has a topic.
        detail: What Telegram said, in its own words, when it was Telegram who said it.
        topics: The topic of every subject that has one now.
    """

    created: dict[str, int] = Field(default_factory=dict)
    kept: dict[str, int] = Field(default_factory=dict)
    failed: list[str] = Field(default_factory=list)
    problem: str | None = None
    detail: str | None = None
    topics: dict[str, int] = Field(default_factory=dict)


class EventModel(BaseModel):
    """
    One event a channel may be switched for.

    Attributes:
        kind: Its name.
        default_on: Whether it is on when nothing says otherwise.
        enabled: Whether the server-wide switch has it on now.
        subject: What it is about (``deploys``, ``alerts``, ``backups``, ``security``,
            ``system``): the topic a chat gives it.
    """

    kind: str
    default_on: bool
    enabled: bool
    subject: str | None = None


class EventListResponse(BaseModel):
    """
    The events.

    Attributes:
        items: Every event kind.
        subjects: What the events are about, in the order the console offers them: each is the
            ``subject`` of some of the ``items``, and a chat can give each a topic.
    """

    items: list[EventModel]
    subjects: list[str] = Field(default_factory=list)


class DeliveryModel(BaseModel):
    """
    One notification on its way to one channel.

    Attributes:
        id: The row.
        notification_id: The notification's own id.
        kind: The event kind.
        code: What happened, fine-grained.
        subject: What it is about.
        channel: The channel's name.
        channel_type: Its type.
        state: ``retrying``, ``held``, ``sent`` or ``failed``.
        attempts: Tries so far.
        created_at: Epoch seconds.
        updated_at: Epoch seconds.
        next_attempt_at: When it is tried (or released) next.
        last_error: The failure, in the destination's own words.
        can_retry: It can be sent again by hand.
        held_for: Why a held one waits: ``quiet``, ``flood`` or ``summary``.
    """

    id: int
    notification_id: str
    kind: str
    code: str
    subject: str
    channel: str
    channel_type: str
    state: str
    attempts: int
    created_at: float
    updated_at: float
    next_attempt_at: float | None = None
    last_error: str | None = None
    can_retry: bool = False
    held_for: str | None = None


class DeliveryListResponse(BaseModel):
    """
    The record of deliveries.

    Attributes:
        items: The newest first.
        counts: How many are in each state.
    """

    items: list[DeliveryModel]
    counts: dict[str, int]


class SweepResponse(BaseModel):
    """
    What a pass over the due deliveries did.

    Attributes:
        delivered: Deliveries that went out.
        retried: Deliveries that failed again and wait.
        failed: Deliveries given up on.
        digests: Digest messages sent for what the channels held.
    """

    delivered: int
    retried: int
    failed: int
    digests: int


def _notices(view: ChannelView) -> list[NoticeModel]:
    """
    Args:
        view: A channel as the admin shows it.

    Returns:
        What it has to say about how it is working: the topics of a Telegram group that stopped
        taking messages and that it still posts in. Empty for another type, and when the marks
        cannot be read: they help to diagnose, they never get in the way of the list.
    """
    if view.type != "telegram":
        return []
    try:
        found = TopicNotes(NotificationState()).standing(view.name, view.settings)
    except (sqlite3.Error, OSError, NoustError) as exc:
        logger.warning("The marks of channel %s could not be read: %s", view.name, exc)
        return []
    return [
        NoticeModel(code="topic_lost", topic=n.topic, subject=n.subject, reason=n.reason, at=n.at)
        for n in found
    ]


def _model(view: ChannelView) -> ChannelModel:
    """
    Args:
        view: A channel as the admin shows it.

    Returns:
        The response model.
    """
    return ChannelModel(**view.to_dict(), notices=_notices(view))


def _types() -> list[ChannelTypeModel]:
    """
    Returns:
        The channel types with their settings.
    """
    return [
        ChannelTypeModel(
            name=spec.name,
            keys=[
                ChannelFieldModel(key=f.key, secret=f.secret, required=f.required)
                for f in spec.fields
            ],
            grouping=GroupingModel(
                topics=grouping.GROUPING[spec.name].topics,
                replies=grouping.GROUPING[spec.name].replies,
                reason=grouping.GROUPING[spec.name].reason,
            ),
        )
        for spec in TYPES.values()
    ]


def _ledger() -> DeliveryLedger:
    """
    Returns:
        The ledger over the process-wide store. Module level so a test can
        stand in another.
    """
    return DeliveryLedger()


def _notifier() -> Notifier:
    """
    Returns:
        A notifier over the configuration as it stands on disk.
    """
    return Notifier(fresh_config())


@router.get("/channels", response_model=ChannelListResponse)
def list_channels(session: Session) -> ChannelListResponse:
    """
    List every notification channel.

    Args:
        session: The authenticated session.

    Returns:
        The channels, without credentials, and the types a new one may be.
    """
    admin = ChannelAdmin()
    return ChannelListResponse(
        items=[_model(view) for view in admin.channels()],
        types=_types(),
        enabled=bool(admin.config.get("notifications.enabled", False)),
    )


@router.put("/channels/{name}", response_model=ChannelModel)
def save_channel(name: str, body: ChannelBody, request: Request, session: Elevated) -> ChannelModel:
    """
    Create a channel or change one.

    Args:
        name: The channel's name.
        body: What to set; what is left out stays.
        request: The incoming request.
        session: The elevated session.

    Returns:
        The channel as it now stands.

    Raises:
        ValidationError: 400 for a name or a type that is not acceptable.
        ConfigError: 400 for a setting the configuration refuses, naming the
            channel and the setting.
    """
    admin = ChannelAdmin()
    view = admin.save(
        name,
        type=body.type,
        enabled=body.enabled,
        settings=body.settings,
        events=body.events,
        route=body.route,
        quiet=body.quiet,
        flood=body.flood,
        summary=body.summary,
    )
    persist(admin.config)
    # What changed, never a value: the fields hold credentials.
    changed = sorted(
        {*body.settings, *(["events"] if body.events is not None else [])}
        | {
            key
            for key in ("enabled", "route", "quiet", "flood", "summary")
            if getattr(body, key) is not None
        }
    )
    audit.record(
        "notify.channel.set",
        actor=session_actor(session),
        target=f"channel:{name}",
        details={"type": view.type, "changed": changed},
    )
    return _model(view)


@router.delete("/channels/{name}", status_code=204)
def remove_channel(name: str, request: Request, session: Elevated) -> None:
    """
    Remove a channel (a default one is emptied instead).

    Args:
        name: The channel's name.
        request: The incoming request.
        session: The elevated session.

    Raises:
        HTTPException: 404 when there is no such channel or nothing to remove.
    """
    admin = ChannelAdmin()
    if not admin.remove(name):
        raise HTTPException(status_code=404, detail=f"No notification channel named {name!r}")
    persist(admin.config)
    audit.record("notify.channel.remove", actor=session_actor(session), target=f"channel:{name}")


@router.post("/channels/{name}/topics", response_model=TopicsResultModel)
def create_topics(
    name: str, body: TopicsBody, request: Request, session: Elevated
) -> TopicsResultModel:
    """
    Create the topics of a Telegram channel's group that its subjects are missing.

    The one request here that asks something of Telegram besides a message, and it is a button
    somebody pushes: nothing is asked until then. The bot the channel already has checks the
    group (does it have topics, may the bot manage them), makes the topics that are missing and
    their numbers are saved into the channel. A group that cannot take them is an answer, not an
    error: it says why, in Telegram's own words where Telegram said it. Asking again makes only
    what is missing.

    Args:
        name: The Telegram channel.
        body: Whether to also make anew the topics that stopped taking messages.
        request: The incoming request.
        session: The elevated session.

    Returns:
        What was made, what was there and why it stopped, if it did.

    Raises:
        ValidationError: 400 when there is no such channel, it is not a Telegram one, or it has
            no bot token or chat yet.
    """
    admin = ChannelAdmin()
    result = TelegramTopics(_notifier()).create(name, admin, recreate=body.recreate)
    if result.created:
        persist(admin.config)
    # Which subjects, never the numbers, the chat or the bot: the trail is kept for years.
    audit.record(
        "notify.topics.create",
        actor=session_actor(session),
        target=f"channel:{name}",
        outcome="failure" if result.problem else "success",
        details={"created": sorted(result.created), "problem": result.problem},
    )
    return TopicsResultModel(**result.to_dict())


class ConsoleUrlModel(BaseModel):
    """
    Where the links of this server's notifications open.

    Attributes:
        url: This server's address inside the console of the central that manages it,
            ``https://<central>/n/<server>``; empty when none is set, and the links are built
            from ``web.public_url``.
    """

    url: str = ""


@router.get("/console-url", response_model=ConsoleUrlModel)
def get_console_url(session: Session) -> ConsoleUrlModel:
    """
    Read where the links of this server's notifications open.

    Args:
        session: The authenticated session.

    Returns:
        The address, empty when none is set.
    """
    admin = ChannelAdmin()
    return ConsoleUrlModel(url=str(admin.config.get(CONSOLE_URL_KEY) or ""))


@router.put("/console-url", response_model=ConsoleUrlModel)
def set_console_url(body: ConsoleUrlModel, session: Elevated) -> ConsoleUrlModel:
    """
    Set where the links of this server's notifications open.

    A central that manages this server sets it (``notifications.console_url``), through the
    settings of the fleet; it can also be set by hand, and an empty address clears it.

    Args:
        body: The address.
        session: The elevated session.

    Returns:
        The address as it is stored.

    Raises:
        ConfigError: 400 for an address that is not an absolute https one, naming the setting.
    """
    admin = ChannelAdmin()
    admin.config.set(CONSOLE_URL_KEY, body.url)
    persist(admin.config)
    stored = str(admin.config.get(CONSOLE_URL_KEY) or "")
    audit.record(
        "notify.console_url.set",
        actor=session_actor(session),
        target="notifications:console_url",
        details={"url": stored},
    )
    return ConsoleUrlModel(url=stored)


@router.get("/events", response_model=EventListResponse)
def list_events(session: Session) -> EventListResponse:
    """
    List the events a channel may be switched for.

    Args:
        session: The authenticated session.

    Returns:
        Every kind, whether it is on by default and whether it is on now.
    """
    config = ChannelAdmin().config
    return EventListResponse(
        items=[EventModel(**item) for item in event_catalog(config)],
        subjects=list(grouping.SUBJECTS),
    )


@router.get("/deliveries", response_model=DeliveryListResponse)
def list_deliveries(
    session: Session,
    state: Annotated[str | None, Query(description="Only deliveries in this state.")] = None,
    channel: Annotated[str | None, Query(description="Only deliveries to this channel.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DeliveryListResponse:
    """
    Read the record of deliveries, newest first.

    Args:
        session: The authenticated session.
        state: Only this state.
        channel: Only this channel.
        limit: Most rows.

    Returns:
        The deliveries and how many there are in each state.

    Raises:
        ValidationError: 400 for a state that does not exist.
    """
    if state is not None and state not in STATES:
        raise ValidationError(
            f"Unknown delivery state: {state!r}",
            details=f"The states are: {', '.join(STATES)}.",
            field="state",
        )
    ledger = _ledger()
    return DeliveryListResponse(
        items=[
            DeliveryModel(**delivery.to_dict())
            for delivery in ledger.recent(limit=limit, state=state, channel=channel)
        ],
        counts=ledger.counts(),
    )


@router.post("/deliveries/sweep", response_model=SweepResponse)
def sweep_deliveries(session: Session) -> SweepResponse:
    """
    Try now what is due: retries whose wait is over, and held notifications.

    The monitor does this on every scan; this is for whoever does not want to
    wait for the next.

    Args:
        session: The authenticated session.

    Returns:
        What was done.
    """
    return SweepResponse(**_notifier().deliver_due().to_dict())


@router.post("/deliveries/{delivery_id}/retry", response_model=DeliveryModel)
def retry_delivery(delivery_id: int, session: Session) -> DeliveryModel:
    """
    Send a failed, waiting or held delivery again now.

    Args:
        delivery_id: The row.
        session: The authenticated session.

    Returns:
        The delivery as it is after the attempt.

    Raises:
        HTTPException: 404 when there is no such delivery or it kept nothing to
            send (one that went out cannot be sent again).
    """
    ledger = _ledger()
    if ledger.retry_now(delivery_id) is None:
        raise HTTPException(status_code=404, detail="That delivery cannot be sent again")
    _notifier().deliver_due()
    delivery: Delivery | None = ledger.get(delivery_id)
    if delivery is None:  # pragma: no cover - purged between the two calls
        raise HTTPException(status_code=404, detail="That delivery is gone")
    return DeliveryModel(**delivery.to_dict())
