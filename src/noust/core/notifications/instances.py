# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The channels an operator configured, as instances of the types Noust speaks.

``notifications.channels`` used to be a fixed tuple of five blocks, one per
type, so there could be one Slack and one webhook. It is now a mapping of
*instances*: a block is named by whatever the operator calls it and has a
``type``. The five old names keep working exactly as they were, because a block
named like a type is an instance of that type:

.. code-block:: yaml

    notifications:
      channels:
        slack:                         # an instance of type slack, named "slack"
          webhook_url: https://hooks.slack.com/services/...
        slack-staging:                 # a second one
          type: slack
          webhook_url: https://hooks.slack.com/services/...
          events: {deploy_started: true, deploy_failed: true}
          route: {environments: [staging]}
        oncall:
          type: pushover
          user_key: ...
          app_token: ...
          route: {min_severity: failed}
          quiet: {start: "22:00", end: "07:00", timezone: Europe/Madrid}

Besides its type's own settings, an instance may carry the settings every
type shares: ``enabled`` (on unless the type is email, which has no destination
to say it is used), ``events`` (this channel's own switches, falling back to
``notifications.events``), ``route``, ``quiet``, ``flood`` and ``summary``
(:mod:`noust.core.notifications.routing`).

Pure: nothing here reads the machine or the network. :func:`validate_channels`
is the one rule for what may be written (the configuration chokepoint calls it),
and :func:`resolve_instances` the one reader every delivery goes through.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from noust.core.notifications.model import EVENT_KINDS, Notification
from noust.core.notifications.render import pagerduty
from noust.core.notifications.routing import Flood, QuietHours, Route, Routing, Summary
from noust.core.notifier_requests import MATRIX_ROOM_ID
from noust.validators.discord import validate_discord_id
from noust.validators.telegram import validate_telegram_chat_id

logger = logging.getLogger(__name__)

#: What a form sends back for a credential it was not asked to change
#: (:data:`noust.core.config.REDACTED`; this package cannot import the
#: configuration, which reads its rules from here).
REDACTED = "***"

#: How an instance may be named: the name is a key of the configuration, a
#: column of the delivery ledger and a path segment of the API.
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,31}")

#: Most instances a server may have: a handful of destinations of its own, and room for the
#: few each team keeps (:data:`noust.core.notifications.team_channels.MAX_CHANNELS`).
MAX_INSTANCES = 128

#: The settings every instance has whatever its type.
SHARED_KEYS: frozenset[str] = frozenset(
    {"type", "enabled", "events", "route", "quiet", "flood", "summary", "team"}
)

#: A team's slug, as the teams write it (:data:`noust.core.teams.model.SLUG_PATTERN`; this
#: module is pure and does not import the teams).
_TEAM_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")


@dataclass(frozen=True)
class ChannelField:
    """
    One setting of a channel type.

    Attributes:
        key: Its name under the instance.
        secret: A credential: redacted when read back and scrubbed from logs.
        required: The channel cannot deliver without it.
    """

    key: str
    secret: bool = False
    required: bool = False


@dataclass(frozen=True)
class ChannelType:
    """
    One way of reaching somebody.

    Attributes:
        name: The type's name, also the name of its default instance.
        fields: Its own settings.
        default_enabled: Whether an instance that does not say is on.
        wants: Whether a notification is something this type can carry at all;
            None for a type that carries every one. A pager is told of
            problems and recoveries, not of a deploy that started.
        closes_incidents: The type keeps state on the other side (an incident
            that opens and closes): a recovery reaches it whatever the
            channel's severity floor and quiet hours say, and follows the
            switches of the problem it closes, so what was opened is closed.
    """

    name: str
    fields: tuple[ChannelField, ...]
    default_enabled: bool = True
    wants: Callable[[Notification], bool] | None = None
    closes_incidents: bool = False

    @property
    def secret_keys(self) -> tuple[str, ...]:
        """The settings that are credentials."""
        return tuple(f.key for f in self.fields if f.secret)

    @property
    def keys(self) -> frozenset[str]:
        """Every setting name this type accepts besides the shared ones."""
        return frozenset(f.key for f in self.fields)

    def configured(self, settings: Mapping[str, Any]) -> bool:
        """
        Args:
            settings: An instance's block.

        Returns:
            Whether every required setting has a value. Email has none: it
            needs the monitor's SMTP account, which the transport checks.
        """
        return all(str(settings.get(f.key) or "").strip() for f in self.fields if f.required)


#: The types, in the order a default install delivers them.
TYPES: dict[str, ChannelType] = {
    spec.name: spec
    for spec in (
        ChannelType(
            "webhook",
            (ChannelField("webhook_url", True, True), ChannelField("secret", True)),
        ),
        ChannelType("slack", (ChannelField("webhook_url", True, True),)),
        ChannelType(
            "discord",
            (
                ChannelField("webhook_url", True, True),
                ChannelField("mention_role_id"),
                ChannelField("mention_user_id"),
            ),
        ),
        ChannelType(
            "telegram",
            (
                ChannelField("bot_token", True, True),
                ChannelField("chat_id", False, True),
                ChannelField("thread_id"),
                ChannelField("threads"),
            ),
        ),
        ChannelType("email", (ChannelField("recipients"),), default_enabled=False),
        ChannelType(
            "pushover",
            (
                ChannelField("user_key", True, True),
                ChannelField("app_token", True, True),
                ChannelField("device"),
            ),
        ),
        ChannelType("ntfy", (ChannelField("webhook_url", True, True), ChannelField("token", True))),
        ChannelType(
            "gotify", (ChannelField("webhook_url", True, True), ChannelField("token", True, True))
        ),
        ChannelType("teams", (ChannelField("webhook_url", True, True),)),
        ChannelType("mattermost", (ChannelField("webhook_url", True, True),)),
        ChannelType(
            "matrix",
            (
                ChannelField("homeserver", False, True),
                ChannelField("room_id", False, True),
                ChannelField("access_token", True, True),
            ),
        ),
        ChannelType(
            "lark", (ChannelField("webhook_url", True, True), ChannelField("secret", True))
        ),
        ChannelType(
            "pagerduty",
            (ChannelField("routing_key", True, True), ChannelField("region")),
            wants=lambda n: pagerduty.plan(n) is not None,
            closes_incidents=True,
        ),
    )
}

#: The type names in delivery order; the names of the default instances.
TYPE_NAMES: tuple[str, ...] = tuple(TYPES)


@dataclass(frozen=True)
class ChannelInstance:
    """
    A configured channel: a type, a name and its settings.

    Attributes:
        name: What the operator calls it.
        type: One of :data:`TYPE_NAMES`.
        settings: The whole block, shared settings included.
        enabled: Whether it is on.
        routing: Its own event switches, route and quiet hours.
    """

    name: str
    type: str
    settings: Mapping[str, Any]
    enabled: bool
    routing: Routing = field(default_factory=Routing)

    @property
    def spec(self) -> ChannelType:
        """The type's description."""
        return TYPES[self.type]

    @property
    def configured(self) -> bool:
        """
        Whether the instance has what its type needs to deliver.

        Email has no destination of its own (it uses the monitor's SMTP
        account and recipients), so it counts as configured when it is on.
        """
        if self.type == "email":
            return self.enabled
        return self.spec.configured(self.settings)

    @property
    def secrets(self) -> tuple[str, ...]:
        """The credentials this instance holds, for scrubbing a log."""
        values = (str(self.settings.get(key) or "") for key in self.spec.secret_keys)
        return tuple(value for value in values if value)


def _events(value: Any, where: str) -> dict[str, bool]:
    """
    Args:
        value: A block's ``events``.
        where: The instance, for the error.

    Returns:
        The switches.

    Raises:
        ValueError: A kind that does not exist or a switch that is not a flag.
    """
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{where}.events must be a mapping of event to true or false")
    found: dict[str, bool] = {}
    for kind, flag in value.items():
        if kind not in EVENT_KINDS:
            raise ValueError(
                f"{where}.events has no event {kind!r}; the events are: {', '.join(EVENT_KINDS)}"
            )
        if not isinstance(flag, bool):
            raise ValueError(f"{where}.events.{kind} must be true or false")
        found[str(kind)] = flag
    return found


def _team(value: Any) -> str | None:
    """
    Args:
        value: A block's ``team``.

    Returns:
        The team's slug, or None for a channel that is the server's own.

    Raises:
        ValueError: When it is not a slug.
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not _TEAM_SLUG.fullmatch(text):
        raise ValueError(
            f"team must be a team's short name (lower-case letters, digits and '-'), not {value!r}"
        )
    return text


def _thread(value: Any, where: str) -> int:
    """
    Args:
        value: A Telegram topic id.
        where: Which setting, for the error.

    Returns:
        The id.

    Raises:
        ValueError: When it is not a positive integer.
    """
    if isinstance(value, bool):
        raise ValueError(f"{where} must be a topic number")
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{where} must be a topic number, not {value!r}") from exc
    if number < 1:
        raise ValueError(f"{where} must be a positive topic number")
    return number


def _check_type_settings(kind: str, block: Mapping[str, Any], where: str) -> None:
    """
    Check the settings that only mean something to one type.

    Args:
        kind: The instance's type.
        block: Its block.
        where: The instance, for the error.

    Raises:
        ValueError: A setting that fails its type's rule.
    """
    unknown = set(block) - SHARED_KEYS - TYPES[kind].keys
    if unknown:
        raise ValueError(f"{where} has no setting {sorted(unknown)[0]!r} for a {kind} channel")
    for key in ("webhook_url",):
        url = str(block.get(key) or "").strip()
        # "***" is a form sending back the credential it was shown redacted:
        # the stored one takes its place when the block is written.
        if url and url != REDACTED and not re.match(r"https?://[^\s/]+", url, re.IGNORECASE):
            raise ValueError(f"{where}.{key} must be an http:// or https:// URL")
    if kind == "telegram":
        chat = str(block.get("chat_id") or "").strip()
        if chat:
            try:
                validate_telegram_chat_id(chat)
            except ValueError as exc:
                raise ValueError(f"{where}.chat_id: {exc}") from exc
        if block.get("thread_id") not in (None, ""):
            _thread(block["thread_id"], f"{where}.thread_id")
        threads = block.get("threads")
        if threads not in (None, ""):
            if not isinstance(threads, Mapping):
                raise ValueError(f"{where}.threads must map an event to a topic number")
            for event, number in threads.items():
                if event not in EVENT_KINDS:
                    raise ValueError(f"{where}.threads has no event {event!r}")
                _thread(number, f"{where}.threads.{event}")
    elif kind == "discord":
        for key, what in (
            ("mention_role_id", "a Discord role id"),
            ("mention_user_id", "a Discord user id"),
        ):
            value = str(block.get(key) or "").strip()
            if value:
                try:
                    validate_discord_id(value, what)
                except ValueError as exc:
                    raise ValueError(f"{where}.{key}: {exc}") from exc
    elif kind == "matrix":
        homeserver = str(block.get("homeserver") or "").strip()
        if homeserver and not re.match(r"https?://[^\s/]+", homeserver, re.IGNORECASE):
            raise ValueError(f"{where}.homeserver must be an http:// or https:// URL")
        room = str(block.get("room_id") or "").strip()
        if room and not MATRIX_ROOM_ID.fullmatch(room):
            raise ValueError(
                f"{where}.room_id must be a room id such as !abcdefg:example.org "
                "(Room settings, Advanced), not an alias"
            )
    elif kind == "pagerduty":
        region = str(block.get("region") or "").strip()
        if region and region not in pagerduty.ENDPOINTS:
            raise ValueError(f"{where}.region must be us or eu")
    elif kind == "email":
        recipients = block.get("recipients")
        if recipients not in (None, "") and not isinstance(recipients, (list, str)):
            raise ValueError(f"{where}.recipients must be a list of addresses")


def _instance(name: str, block: Mapping[str, Any], *, strict: bool = True) -> ChannelInstance:
    """
    Read one block.

    Writing is strict (a setting that means nothing is refused with the place
    and the reason). Reading is not: a file somebody edited by hand must not
    cost an alert. A block that cannot be read at all (no such type, not a
    mapping) is refused either way, but a route, quiet hours, event switch or
    type-specific value that is wrong is ignored with a warning when reading,
    and the channel delivers as if it were not there.

    Args:
        name: Its key.
        block: Its value.
        strict: Refuse anything wrong, as the configuration chokepoint does.

    Returns:
        The instance.

    Raises:
        ValueError: The name, the type or (when strict) any setting is not
            acceptable; the message names the instance and says what to change.
    """
    where = f"notifications.channels.{name}"
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"{where}: a channel is named with lower-case letters, digits, '-' and '_', "
            "starting with a letter, at most 32 characters"
        )
    if not isinstance(block, Mapping):
        raise ValueError(f"{where} must be a mapping of settings")
    declared = block.get("type")
    kind = str(declared) if declared not in (None, "") else name
    if kind not in TYPES:
        hint = (
            f"set {where}.type to one of: {', '.join(TYPE_NAMES)}"
            if declared in (None, "")
            else f"the types are: {', '.join(TYPE_NAMES)}"
        )
        raise ValueError(f"{where}: {kind!r} is not a channel type; {hint}")
    if name in TYPES and kind != name:
        raise ValueError(
            f"{where}: this name is the default {name} channel and cannot be a {kind} one; "
            "give it another name"
        )
    if strict:
        _check_type_settings(kind, block, where)

    def read(parse: Callable[[], Any], what: str, default: Any) -> Any:
        try:
            return parse()
        except ValueError as exc:
            if strict:
                text = str(exc)
                raise ValueError(
                    text if text.startswith("notifications.") else f"{where}.{text}"
                ) from exc
            logger.warning("Ignoring %s.%s, which cannot be read: %s", where, what, exc)
            return default

    enabled = block.get("enabled", TYPES[kind].default_enabled)
    if not isinstance(enabled, bool):
        if strict:
            raise ValueError(f"{where}.enabled must be true or false")
        enabled = TYPES[kind].default_enabled
    routing = Routing(
        route=read(lambda: Route.from_mapping(block.get("route")), "route", Route()),
        quiet=read(lambda: QuietHours.from_mapping(block.get("quiet")), "quiet", None),
        events=read(lambda: _events(block.get("events"), where), "events", {}),
        flood=read(lambda: Flood.from_mapping(block.get("flood")), "flood", None),
        summary=read(lambda: Summary.from_mapping(block.get("summary")), "summary", None),
        team=read(lambda: _team(block.get("team")), "team", None),
    )
    return ChannelInstance(name, kind, dict(block), enabled, routing)


def validate_channels(channels: Any) -> None:
    """
    Refuse a ``notifications.channels`` block the notifier could not read.

    The one rule for the configuration chokepoint, so ``noust config set``, the
    console and a whole-file ``PUT`` refuse the same things for the same
    reason.

    Args:
        channels: The mapping of instances.

    Raises:
        ValueError: With the instance and the setting named.
    """
    if channels in (None, ""):
        return
    if not isinstance(channels, Mapping):
        raise ValueError("notifications.channels must be a mapping of channel names to settings")
    if len(channels) > MAX_INSTANCES:
        raise ValueError(f"notifications.channels may have at most {MAX_INSTANCES} channels")
    for name, block in channels.items():
        _instance(str(name), block, strict=True)


def resolve_instances(channels: Any) -> list[ChannelInstance]:
    """
    Read every instance that can be read.

    A block that cannot be (a hand edit gone wrong) is logged and left out,
    never allowed to stop the others from delivering.

    Args:
        channels: The ``notifications.channels`` mapping.

    Returns:
        The default instances in :data:`TYPE_NAMES` order, then the operator's
        own by name.
    """
    if not isinstance(channels, Mapping):
        return []
    found: dict[str, ChannelInstance] = {}
    for name, block in channels.items():
        try:
            found[str(name)] = _instance(str(name), block, strict=False)
        except ValueError as exc:
            logger.warning("Ignoring a notification channel: %s", exc)
    builtin = [found.pop(name) for name in TYPE_NAMES if name in found]
    return [*builtin, *(found[name] for name in sorted(found))]


def instance_names(channels: Any) -> tuple[str, ...]:
    """
    Args:
        channels: The ``notifications.channels`` mapping.

    Returns:
        The names of the readable instances, in delivery order.
    """
    return tuple(instance.name for instance in resolve_instances(channels))
