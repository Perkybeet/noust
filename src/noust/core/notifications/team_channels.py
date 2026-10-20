# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The notification channels a team keeps for itself.

A shared server's team can bring its own Slack, ntfy or webhook and decide where
the notices about its applications go. A team's channel is an ordinary channel of
``notifications.channels`` (one implementation of what a channel is, rule 3) that
carries ``team: <slug>``: it is delivered through the same notifier, retried and
recorded in the same ledger, and :func:`~noust.core.notifications.routing.decide`
gives it only the applications of its team.

The team names its channels as it likes, and two teams may both have an
``alerts``. In the configuration the key is ``t<team id>-<name>``: the id never
changes, the prefix cannot be mistaken for the server's own channels (which are
named after a type or by the operator), and a name cannot be read as another team's.

Email is not offered: it goes out through the server's own mail account, which
is the administrator's to give.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import ValidationError
from noust.core.notification_channels import ChannelAdmin, ChannelView
from noust.core.notifications.instances import TYPES
from noust.core.teams import Scope, TeamStore

if TYPE_CHECKING:
    from noust.core.teams.model import Team

#: Most channels one team may keep: a handful of destinations, and the server's
#: own list must not be crowded out by one team.
MAX_CHANNELS = 8

#: The channel types a team may use. Email is the server's.
TEAM_TYPES: tuple[str, ...] = tuple(name for name in TYPES if name != "email")

#: A name as a team writes it; the key adds a prefix and the whole must stay a channel name (32).
_NAME = re.compile(r"[a-z][a-z0-9_-]*")
_KEY = re.compile(r"t(\d+)-(.+)")
_KEY_LIMIT = 32


def channel_key(team_id: int, name: str) -> str:
    """
    Args:
        team_id: A team's id.
        name: What the team calls the channel.

    Returns:
        The key it has in ``notifications.channels``.
    """
    return f"t{team_id}-{name}"


class TeamChannels:
    """
    A team's channels, over the configuration the server's own live in.

    Args:
        team: The team's slug.
        admin: The channel admin to work through; one over the shared configuration
            by default. The caller writes the configuration, as with the admin itself.
        teams: The team store; the process-wide one by default.
    """

    def __init__(
        self, team: str, admin: ChannelAdmin | None = None, *, teams: TeamStore | None = None
    ) -> None:
        self._slug = team
        self.admin = admin if admin is not None else ChannelAdmin()
        self._teams = teams or TeamStore()

    @property
    def team(self) -> Team:
        """
        Returns:
            The team.

        Raises:
            TeamNotFoundError: When there is no such team.
        """
        return self._teams.require_team(self._slug)

    def _own(self, view: ChannelView) -> bool:
        return view.team == self.team.slug

    def _shown(self, view: ChannelView) -> ChannelView:
        """
        Args:
            view: A channel of this team, as the admin has it.

        Returns:
            The channel under the name the team gave it.
        """
        match = _KEY.fullmatch(view.name)
        if match is not None and int(match.group(1)) == self.team.id:
            return replace(view, name=match.group(2))
        return view

    def list(self) -> list[ChannelView]:
        """
        Returns:
            The team's channels, by name, without any credential.
        """
        return sorted(
            (self._shown(view) for view in self.admin.channels() if self._own(view)),
            key=lambda view: view.name,
        )

    def get(self, name: str) -> ChannelView | None:
        """
        Args:
            name: What the team calls the channel.

        Returns:
            The channel, or None when the team has none by that name (a channel
            of the server's, or of another team, is never found this way).
        """
        found = self.admin.get(channel_key(self.team.id, name))
        return self._shown(found) if found is not None and self._own(found) else None

    def save(
        self,
        name: str,
        *,
        type: str | None = None,
        enabled: bool | None = None,
        settings: Mapping[str, Any] | None = None,
        events: Mapping[str, bool] | None = None,
        route: Mapping[str, Any] | None = None,
        quiet: Mapping[str, Any] | None = None,
        flood: Mapping[str, Any] | None = None,
        summary: Mapping[str, Any] | None = None,
    ) -> ChannelView:
        """
        Create one of the team's channels or change one, keeping what is not mentioned.

        Args:
            name: What the team calls the channel.
            type: Its type; needed for a new one.
            enabled: On or off; unchanged when None.
            settings: The type's own settings (``***`` keeps a stored credential).
            events: Its own event switches, replacing the ones it has.
            route: Which of the team's applications it hears, replacing the route.
            quiet: Quiet hours, replacing them.
            flood: An anti-flood cap, replacing it.
            summary: A periodic summary, replacing it.

        Returns:
            The channel as it now stands.

        Raises:
            TeamNotFoundError: When there is no such team.
            ValidationError: For a name or type that is not acceptable, a team
                with too many channels, or a setting the type refuses.
        """
        team = self.team
        key = channel_key(team.id, name)
        if not _NAME.fullmatch(name) or len(key) > _KEY_LIMIT:
            raise ValidationError(
                f"Invalid channel name: {name!r}",
                details=(
                    "Use lower-case letters, digits, '-' and '_', starting with a letter, "
                    f"at most {_KEY_LIMIT - len(channel_key(team.id, ''))} characters."
                ),
                field="name",
            )
        existing = self.admin.get(key)
        if existing is not None and existing.team != team.slug:
            raise ValidationError(f"Invalid channel name: {name!r}", field="name")
        chosen = type or (existing.type if existing is not None else None)
        if chosen == "email":
            raise ValidationError(
                "A team's channel cannot be email",
                details=(
                    "Email goes out through the server's own mail account, which its "
                    "administrator gives. Use a chat or push channel, or ask for one."
                ),
                field="type",
            )
        if chosen is not None and chosen not in TEAM_TYPES:
            raise ValidationError(
                f"Unknown channel type: {chosen!r}",
                details=f"The types are: {', '.join(TEAM_TYPES)}.",
                field="type",
            )
        if existing is None and len(self.list()) >= MAX_CHANNELS:
            raise ValidationError(
                f"A team has at most {MAX_CHANNELS} channels",
                details="Remove one that is no longer used first.",
                field="name",
            )
        view = self.admin.save(
            key,
            type=type,
            enabled=enabled,
            settings=settings,
            events=events,
            route=route,
            quiet=quiet,
            flood=flood,
            summary=summary,
            team=team.slug,
        )
        return self._shown(view)

    def remove(self, name: str) -> bool:
        """
        Args:
            name: What the team calls the channel.

        Returns:
            Whether the team had such a channel. Only the team's own can be removed.
        """
        if self.get(name) is None:
            return False
        return self.admin.remove(channel_key(self.team.id, name))

    def key_of(self, name: str) -> str:
        """
        Args:
            name: What the team calls the channel.

        Returns:
            The key to send a test through (``Notifier.test_channel``).
        """
        return channel_key(self.team.id, name)


def visible_to(scope: Scope, config: Mapping[str, Any]) -> dict[str, Any]:
    """
    Take the channels of the teams a principal does not see out of a configuration.

    The console shows the configuration to anyone who may read settings (secrets
    redacted), and a team's channel names its destination (a chat id, a topic).
    A principal that sees every team gets it whole.

    Args:
        scope: Whom the configuration is for.
        config: The (already redacted) configuration tree.

    Returns:
        A copy without the channels that belong to other teams.
    """
    result = dict(config)
    if scope.sees_all:
        return result
    notifications = result.get("notifications")
    if not isinstance(notifications, Mapping):
        return result
    channels = notifications.get("channels")
    if not isinstance(channels, Mapping):
        return result
    teams = TeamStore()
    kept: dict[str, Any] = {}
    for key, block in channels.items():
        slug = block.get("team") if isinstance(block, Mapping) else None
        owner = teams.get_team(str(slug)) if slug else None
        if slug and (owner is None or not scope.sees_team(owner.id)):
            continue
        kept[key] = block
    result["notifications"] = {**notifications, "channels": kept}
    return result


def may_use(scope: Scope, channel: str, config: Mapping[str, Any]) -> bool:
    """
    Whether a principal may act on a channel by its key (a test message).

    Args:
        scope: Whom it is for.
        channel: The channel's key in ``notifications.channels``.
        config: The configuration tree.

    Returns:
        True for a channel of the server's, one of a team the principal sees, and
        for a principal that sees every team.
    """
    if scope.sees_all:
        return True
    notifications = config.get("notifications")
    channels = notifications.get("channels") if isinstance(notifications, Mapping) else None
    block = channels.get(channel) if isinstance(channels, Mapping) else None
    slug = block.get("team") if isinstance(block, Mapping) else None
    if not slug:
        return True
    owner = TeamStore().get_team(str(slug))
    return owner is not None and scope.sees_team(owner.id)
