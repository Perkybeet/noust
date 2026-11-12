# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/alerts/watching``: what Noust watches by itself and by which channels it says so (item 159).

A thin client of :func:`noust.monitor.watching.report`, which holds the table of what is watched and
the rule of who hears it. The console's Alerts page opens with this answer, and keeps no list of its
own. Nothing secret is in it: a channel is its name and its type.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.notifier import fresh_config
from noust.monitor.watching import report
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(get_current_session)]


class WatcherModel(BaseModel):
    """
    Whether the monitor, which does the watching, runs.

    Attributes:
        state: ``running``, ``not_running`` (enabled, and not running), ``disabled`` (installed and
            not enabled), ``not_installed`` or ``unknown`` (systemd cannot be asked).
        fix: The command that deals with it, or null when there is nothing to do.
    """

    state: str
    fix: str | None = None


class ChannelRef(BaseModel):
    """
    A channel that delivers, without any of its settings.

    Attributes:
        name: What it is called.
        type: Its type (``slack``, ``telegram``...).
    """

    name: str
    type: str


class NotificationsModel(BaseModel):
    """
    Whether what is found leaves the server at all.

    Attributes:
        enabled: The master switch (``notifications.enabled``).
        paused_until: When a pause of every notification ends, or null for none.
        channels: The server's channels that are on and have what their type needs.
    """

    enabled: bool
    paused_until: str | None = None
    channels: list[ChannelRef] = Field(default_factory=list)


class WatchEvent(BaseModel):
    """
    A message a watch can say.

    Attributes:
        kind: The notification kind, as ``/api/notifications/events`` lists it.
        on: Whether the server-wide switch has it on.
    """

    kind: str
    on: bool


class Reach(BaseModel):
    """
    A channel that would take what a watch says.

    Attributes:
        name: The channel.
        type: Its type.
        limited: It does not say everything at once: a route that narrows it, a floor of
            severity, quiet hours or a summary.
    """

    name: str
    type: str
    limited: bool = False


class Offer(BaseModel):
    """
    What turning on a watch that ships off writes.

    Attributes:
        key: The setting, through ``PATCH /api/config``.
        value: The value that turns it on.
    """

    key: str
    value: Any


class WatchModel(BaseModel):
    """
    One thing Noust looks at without being asked.

    Attributes:
        id: Stable; the console words it.
        group: ``applications``, ``domains``, ``server``, ``backups`` or ``noust``.
        state: ``on``, ``off`` (its check is turned off) or ``muted`` (none of its messages is
            switched on anywhere).
        events: The messages it can say.
        params: The numbers its sentence quotes (``percent``, ``days``, ``hours``).
        reach: The channels that would take what it says, whether or not it is on now.
        offer: What to write to turn it on, for a watch that ships off and is worth offering.
        asks_outside: It sends a request to somebody else, which the offer says first.
    """

    id: str
    group: str
    state: str
    events: list[WatchEvent] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    reach: list[Reach] = Field(default_factory=list)
    offer: Offer | None = None
    asks_outside: bool = False


class WatchingResponse(BaseModel):
    """
    What Noust watches by itself, and where what it finds goes.

    Attributes:
        watcher: Whether the monitor that does the watching runs.
        notifications: Whether it can be heard, and by which channels.
        watches: Each thing watched, in the order the console draws them.
    """

    watcher: WatcherModel
    notifications: NotificationsModel
    watches: list[WatchModel] = Field(default_factory=list)


@router.get("/watching", response_model=WatchingResponse)
def watching(session: Session) -> Any:
    """
    Say what Noust watches without being asked and who hears what it finds.

    Args:
        session: Authenticated session, injected.

    Returns:
        The watches, the channels, and whether the monitor that watches is running.
    """
    return report(fresh_config())
