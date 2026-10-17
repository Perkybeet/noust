# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/teams/{team}/notifications``: the channels a team keeps for its own applications.

A thin client of :class:`~noust.core.notifications.team_channels.TeamChannels`,
which is a view over the same channels the server's own use (rule 3). The team in
the path is held to the principal's teams before a handler runs
(:mod:`noust.web.api.scope`), so another team's channels do not exist for a
caller; reading is ``apps.read`` and changing ``apps.manage``, in that team.

Changing a channel needs sudo mode, as it does for the server's own, because it
decides where notices go and holds the keys to those places. A credential is
never in a response (``***`` when one is stored), and a ``***`` in a body keeps
the stored one.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.config import Config
from noust.core.notification_channels import ChannelView
from noust.core.notifications.instances import TYPES
from noust.core.notifications.team_channels import TEAM_TYPES, TeamChannels
from noust.core.notifier import Notifier, fresh_config
from noust.web.api.auth import get_current_session
from noust.web.api.config import persist
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute, require_elevated

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(get_current_session)]
Elevated = Annotated[dict[str, Any], Depends(require_elevated)]


class TeamChannelModel(BaseModel):
    """
    One of a team's channels, without its credentials.

    Attributes:
        name: What the team calls it.
        type: One of the types a team may use (everything but email).
        enabled: Whether it is on.
        configured: Whether it has what its type needs to deliver.
        settings: The type's settings; a credential reads ``***`` when stored.
        events: Its own switch per event.
        route: Which of the team's applications it hears (projects, environments,
            tags, a minimum severity); empty is all of them.
        quiet: When it stays silent, or null.
        flood: The most it says in a stretch of time, or null.
        summary: What waits for a periodic summary, or null.
    """

    name: str
    type: str
    enabled: bool
    configured: bool
    settings: dict[str, Any] = Field(default_factory=dict)
    events: dict[str, bool] = Field(default_factory=dict)
    route: dict[str, Any] = Field(default_factory=dict)
    quiet: dict[str, Any] | None = None
    flood: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None


class TeamChannelListResponse(BaseModel):
    """
    A team's channels and the types it may add.

    Attributes:
        team: The team's slug.
        items: Its channels, by name.
        types: The types a team may use, with their settings.
    """

    team: str
    items: list[TeamChannelModel]
    types: list[dict[str, Any]]


class TeamChannelBody(BaseModel):
    """
    What to set on a team's channel; whatever is left out stays as it is.

    Attributes:
        type: The type; needed for a new channel.
        enabled: On or off.
        settings: The type's settings to set (``***`` keeps a stored credential,
            an empty string clears a setting).
        events: Replaces the channel's own event switches (empty clears them).
        route: Replaces which of the team's applications it hears (empty clears it).
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


class TeamChannelTestResult(BaseModel):
    """
    The outcome of a test message.

    Attributes:
        ok: Whether it was delivered.
        detail: A confirmation, or the failure in the receiving side's own words.
    """

    ok: bool
    detail: str


def _model(view: ChannelView) -> TeamChannelModel:
    """
    Args:
        view: A channel as the admin shows it.

    Returns:
        The response model; the team is the path's, so it is not repeated.
    """
    shown = view.to_dict()
    shown.pop("default", None)
    shown.pop("team", None)
    return TeamChannelModel(**shown)


def _types() -> list[dict[str, Any]]:
    """
    Returns:
        The types a team may use, each with its settings.
    """
    return [
        {
            "name": name,
            "keys": [
                {"key": f.key, "secret": f.secret, "required": f.required}
                for f in TYPES[name].fields
            ],
        }
        for name in TEAM_TYPES
    ]


def _channels(team: str) -> TeamChannels:
    """
    Args:
        team: The team's slug.

    Returns:
        Its channels, over the configuration as it stands on disk.
    """
    return TeamChannels(team, _admin())


def _admin() -> Any:
    """
    Returns:
        A channel admin over a fresh read of the configuration. Module level so a
        test can stand in another.
    """
    from noust.core.notification_channels import ChannelAdmin

    config = Config()
    config.reload()
    return ChannelAdmin(config)


@router.get("/{team}/notifications/channels", response_model=TeamChannelListResponse)
def list_team_channels(team: str, session: Session) -> TeamChannelListResponse:
    """
    List a team's notification channels.

    Args:
        team: The team's slug.
        session: The authenticated session.

    Returns:
        Its channels without credentials, and the types it may add.

    Raises:
        TeamNotFoundError: 404 when there is no such team.
    """
    channels = _channels(team)
    return TeamChannelListResponse(
        team=channels.team.slug, items=[_model(view) for view in channels.list()], types=_types()
    )


@router.put("/{team}/notifications/channels/{name}", response_model=TeamChannelModel)
def save_team_channel(
    team: str, name: str, body: TeamChannelBody, session: Elevated
) -> TeamChannelModel:
    """
    Create one of a team's channels or change one.

    A team's channel hears about the applications of that team and nothing else.

    Args:
        team: The team's slug.
        name: What the team calls the channel.
        body: What to set; what is left out stays.
        session: The elevated session.

    Returns:
        The channel as it now stands.

    Raises:
        ValidationError: 400 for a name or a type that is not acceptable, an email
            channel, a team with too many channels, or a setting the type refuses.
    """
    channels = _channels(team)
    view = channels.save(
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
    persist(channels.admin.config)
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
        target=f"channel:{channels.key_of(name)}",
        details={"type": view.type, "changed": changed, "team": channels.team.slug},
    )
    return _model(view)


@router.delete("/{team}/notifications/channels/{name}", status_code=204)
def remove_team_channel(team: str, name: str, session: Elevated) -> None:
    """
    Remove one of a team's channels.

    Args:
        team: The team's slug.
        name: What the team calls the channel.
        session: The elevated session.

    Raises:
        HTTPException: 404 when the team has no such channel.
    """
    channels = _channels(team)
    key = channels.key_of(name)
    if not channels.remove(name):
        raise HTTPException(status_code=404, detail=f"No notification channel named {name!r}")
    persist(channels.admin.config)
    audit.record(
        "notify.channel.remove",
        actor=session_actor(session),
        target=f"channel:{key}",
        details={"team": channels.team.slug},
    )


@router.post("/{team}/notifications/channels/{name}/test", response_model=TeamChannelTestResult)
def test_team_channel(team: str, name: str, session: Session) -> TeamChannelTestResult:
    """
    Send a test message through one of a team's channels.

    Ignores the master switch and the event filters, as the server's own test does,
    and goes through the notifier's own guard against private destinations.

    Args:
        team: The team's slug.
        name: What the team calls the channel.
        session: The authenticated session.

    Returns:
        ``ok`` with a confirmation, or the failure in the receiving side's own words.

    Raises:
        HTTPException: 404 when the team has no such channel.
    """
    channels = _channels(team)
    if channels.get(name) is None:
        raise HTTPException(status_code=404, detail=f"No notification channel named {name!r}")
    error = Notifier(fresh_config()).test_channel(channels.key_of(name))
    if error is None:
        return TeamChannelTestResult(ok=True, detail=f"Test message sent through {name}.")
    return TeamChannelTestResult(ok=False, detail=error)
