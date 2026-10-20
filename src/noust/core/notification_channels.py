# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading and changing the notification channels, for every front end.

The console, ``noust notify`` and the API all list, add, change and remove
channel instances through :class:`ChannelAdmin`, so what a channel accepts is
decided once (:mod:`noust.core.notifications.instances`, applied by the
configuration chokepoint) and what is *shown* of it is decided once too: a
credential is never read back, only whether one is stored.

The admin works on a :class:`~noust.core.config.Config` it is given and does
not write the file: the caller persists it the way its own front end does
(the API maps a permission error to a 403, the CLI prints it). Changing a
channel is a whole-block write through :meth:`Config.set`, which is where the
validation happens and where a ``***`` sent back by a form takes the stored
secret instead of replacing it.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from noust.core.config import DEFAULT_CONFIG, REDACTED, Config
from noust.core.exceptions import ValidationError
from noust.core.notifications.instances import (
    NAME_PATTERN,
    SHARED_KEYS,
    TYPES,
    ChannelInstance,
    resolve_instances,
)
from noust.core.notifications.model import EVENT_KINDS, OFF_BY_DEFAULT


@dataclass(frozen=True)
class ChannelView:
    """
    A channel as it is shown: nothing secret in it.

    Attributes:
        name: The instance's name.
        type: Its type.
        enabled: Whether it is on.
        configured: Whether it has what its type needs to deliver.
        default: It is the default instance of its type (named after it): it
            cannot be removed, only emptied.
        settings: The type's own settings. A credential reads ``***`` when one
            is stored and ``""`` when none is, never its value.
        events: This channel's own event switches.
        route: Its route, as it is written.
        quiet: Its quiet hours, as they are written; None when it has none.
        flood: Its anti-flood cap, as it is written; None when it has none.
        summary: Its periodic summary, as it is written; None when it has none.
        team: The slug of the team it belongs to; None for the server's own.
    """

    name: str
    type: str
    enabled: bool
    configured: bool
    default: bool
    settings: dict[str, Any] = field(default_factory=dict)
    events: dict[str, bool] = field(default_factory=dict)
    route: dict[str, Any] = field(default_factory=dict)
    quiet: dict[str, Any] | None = None
    flood: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    team: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The channel as the API and the CLI show it.
        """
        return {
            "name": self.name,
            "type": self.type,
            "enabled": self.enabled,
            "configured": self.configured,
            "default": self.default,
            "settings": dict(self.settings),
            "events": dict(self.events),
            "route": dict(self.route),
            "quiet": dict(self.quiet) if self.quiet is not None else None,
            "flood": dict(self.flood) if self.flood is not None else None,
            "summary": dict(self.summary) if self.summary is not None else None,
            "team": self.team,
        }


def event_catalog(config: Config | None = None) -> list[dict[str, Any]]:
    """
    List the events a channel may be switched for.

    Args:
        config: The configuration, to read the server-wide switches from.

    Returns:
        One entry per event kind: its name, whether it is on by default and
        whether the server-wide switch has it on now.
    """
    switches = (config.get("notifications.events") if config is not None else None) or {}
    return [
        {
            "kind": kind,
            "default_on": kind not in OFF_BY_DEFAULT,
            "enabled": bool(switches.get(kind, kind not in OFF_BY_DEFAULT)),
        }
        for kind in EVENT_KINDS
    ]


def _view(instance: ChannelInstance) -> ChannelView:
    """
    Args:
        instance: A resolved channel.

    Returns:
        How it is shown.
    """
    secrets = set(instance.spec.secret_keys)
    settings: dict[str, Any] = {}
    for spec in instance.spec.fields:
        value = instance.settings.get(spec.key)
        if spec.key in secrets:
            settings[spec.key] = REDACTED if value else ""
        elif value is not None:
            settings[spec.key] = copy.deepcopy(value)
        else:
            settings[spec.key] = ""
    quiet = instance.routing.quiet
    flood = instance.routing.flood
    summary = instance.routing.summary
    return ChannelView(
        name=instance.name,
        type=instance.type,
        enabled=instance.enabled,
        configured=instance.configured,
        default=instance.name in TYPES,
        settings=settings,
        events=dict(instance.routing.events),
        route=instance.routing.route.to_dict(),
        quiet=quiet.to_dict() if quiet is not None else None,
        flood=flood.to_dict() if flood is not None else None,
        summary=summary.to_dict() if summary is not None else None,
        team=instance.routing.team,
    )


class ChannelAdmin:
    """List, change and remove the channels of a configuration."""

    def __init__(self, config: Config | None = None) -> None:
        """
        Args:
            config: The configuration to work on; the shared one, refreshed
                from disk, when None.
        """
        if config is None:
            config = Config()
            config.reload()
        self.config = config

    def _block(self, name: str) -> dict[str, Any]:
        """
        Args:
            name: A channel's name.

        Returns:
            A copy of its block as stored; empty when there is none.
        """
        found = self.config.get(f"notifications.channels.{name}")
        return dict(found) if isinstance(found, Mapping) else {}

    def channels(self) -> list[ChannelView]:
        """
        Returns:
            Every readable channel: the defaults of each type (configured or
            not) and the operator's own.
        """
        channels = self.config.get("notifications.channels") or {}
        return [_view(instance) for instance in resolve_instances(channels)]

    def get(self, name: str) -> ChannelView | None:
        """
        Args:
            name: A channel's name.

        Returns:
            The channel, or None when there is none of that name.
        """
        return next((view for view in self.channels() if view.name == name), None)

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
        team: str | None = None,
    ) -> ChannelView:
        """
        Create a channel or change one, keeping what is not mentioned.

        Args:
            name: The channel's name.
            type: Its type; required for a new channel that is not the default
                of a type, ignored (and checked) for one that is.
            enabled: On or off; unchanged when None.
            settings: The type's own settings to set. A value of ``***`` keeps
                the stored credential; ``""`` clears a setting.
            events: Replaces this channel's own event switches when given (an
                empty mapping clears them all).
            route: Replaces its route when given (empty clears it).
            quiet: Replaces its quiet hours when given (empty clears them).
            flood: Replaces its anti-flood cap when given (empty clears it).
            summary: Replaces its periodic summary when given (empty clears it).
            team: The team the channel belongs to, set when it is made by a team
                (:class:`noust.core.notifications.team_channels.TeamChannels`, which
                checks the team exists); unchanged when None. A channel's team is
                never cleared here: a team's channel is removed, not handed over.

        Returns:
            The channel as it now stands.

        Raises:
            ValidationError: The name or the type is not acceptable.
            ConfigError: A setting the configuration refuses (the message
                names the channel and the setting).
        """
        if not NAME_PATTERN.fullmatch(name):
            raise ValidationError(
                f"Invalid channel name: {name!r}",
                details="Use lower-case letters, digits, '-' and '_', starting with a letter.",
                field="name",
            )
        block = self._block(name)
        stored_type = block.get("type") or (name if name in TYPES else None)
        chosen = type or stored_type
        if chosen is None:
            raise ValidationError(
                f"Channel {name!r} needs a type",
                details=f"The types are: {', '.join(TYPES)}.",
                field="type",
            )
        if chosen not in TYPES:
            raise ValidationError(
                f"Unknown channel type: {chosen!r}",
                details=f"The types are: {', '.join(TYPES)}.",
                field="type",
            )
        if name in TYPES and chosen != name:
            raise ValidationError(
                f"{name!r} is the default {name} channel and cannot be a {chosen} one",
                details="Give the channel another name.",
                field="type",
            )

        if name not in TYPES:
            block["type"] = chosen
        if team is not None:
            block["team"] = team
        if enabled is not None:
            block["enabled"] = enabled
        allowed = TYPES[chosen].keys
        for key, value in (settings or {}).items():
            if key in SHARED_KEYS or key not in allowed:
                raise ValidationError(
                    f"A {chosen} channel has no setting {key!r}",
                    details=f"Its settings are: {', '.join(sorted(allowed))}.",
                    field=key,
                )
            block[key] = value
        shared = (
            ("events", events),
            ("route", route),
            ("quiet", quiet),
            ("flood", flood),
            ("summary", summary),
        )
        for key, value in shared:
            if value is None:
                continue
            if value:
                block[key] = copy.deepcopy(dict(value))
            else:
                block.pop(key, None)
        # One whole-block write: validated as a channel, and a "***" in it takes
        # the credential already stored (Config.set).
        self.config.set(f"notifications.channels.{name}", block)
        saved = self.get(name)
        if saved is None:  # pragma: no cover - the block was just written
            raise ValidationError(f"The channel {name!r} could not be stored")
        return saved

    def remove(self, name: str) -> bool:
        """
        Remove a channel. A default instance cannot disappear (its type is
        always there to be configured): it is emptied to its defaults.

        Args:
            name: The channel's name.

        Returns:
            Whether there was something to remove.
        """
        if name in TYPES:
            existing = self.get(name)
            if existing is None or not (existing.configured or existing.events or existing.route):
                return False
            defaults = copy.deepcopy(DEFAULT_CONFIG["notifications"]["channels"].get(name, {}))
            self.config.set(f"notifications.channels.{name}", defaults)
            return True
        tree = self.config.to_dict()
        channels = tree.get("notifications", {}).get("channels", {})
        if name not in channels:
            return False
        del channels[name]
        self.config.replace(tree)
        return True
