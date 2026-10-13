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

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.core.notification_channels import ChannelAdmin, ChannelView, event_catalog
from noust.core.notification_ledger import STATES, Delivery, DeliveryLedger
from noust.core.notifications.instances import TYPES
from noust.core.notifier import Notifier, fresh_config
from noust.web.api.auth import get_current_session
from noust.web.api.config import persist
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute, require_elevated

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(get_current_session)]
Elevated = Annotated[dict[str, Any], Depends(require_elevated)]


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


class ChannelTypeModel(BaseModel):
    """
    One kind of channel Noust can deliver through.

    Attributes:
        name: The type's name.
        keys: Its settings.
    """

    name: str
    keys: list[ChannelFieldModel]


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
    """

    type: str | None = None
    enabled: bool | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    events: dict[str, bool] | None = None
    route: dict[str, Any] | None = None
    quiet: dict[str, Any] | None = None


class EventModel(BaseModel):
    """
    One event a channel may be switched for.

    Attributes:
        kind: Its name.
        default_on: Whether it is on when nothing says otherwise.
        enabled: Whether the server-wide switch has it on now.
    """

    kind: str
    default_on: bool
    enabled: bool


class EventListResponse(BaseModel):
    """
    The events.

    Attributes:
        items: Every event kind.
    """

    items: list[EventModel]


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
        digests: Digest messages sent for what the quiet hours held.
    """

    delivered: int
    retried: int
    failed: int
    digests: int


def _model(view: ChannelView) -> ChannelModel:
    """
    Args:
        view: A channel as the admin shows it.

    Returns:
        The response model.
    """
    return ChannelModel(**view.to_dict())


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
    )
    persist(admin.config)
    # What changed, never a value: the fields hold credentials.
    changed = sorted(
        {*body.settings, *(["events"] if body.events is not None else [])}
        | {key for key in ("enabled", "route", "quiet") if getattr(body, key) is not None}
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
    return EventListResponse(items=[EventModel(**item) for item in event_catalog(config)])


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
