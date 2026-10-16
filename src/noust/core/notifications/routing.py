# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who hears about what, and when: a channel's route and its quiet hours.

Every channel instance (see :mod:`noust.core.notifications.instances`) may carry
two optional blocks next to its own settings. They are read by one function,
:func:`decide`, so the answer to "does this channel get this notification
now?" lives in one place and a channel type cannot forget to ask:

``route``
    Narrows a channel to part of what the server does: ``apps`` (domains, with
    ``*`` globs), ``projects`` (slugs), ``environments`` (a slug, or
    ``project/environment``), ``tags`` (an application's tags) and
    ``min_severity`` (``warning`` or ``failed``). A route says "only", so a
    channel with one hears nothing about the server itself (a full disk, a
    node going down) unless ``server_events`` is on. Within one list any entry
    matches; across lists every non-empty one must.

``quiet``
    Hours in which a channel stays silent: ``start`` and ``end`` as ``HH:MM`` in
    ``timezone`` (an IANA name, ``UTC`` by default), a window that may cross
    midnight. A failure still gets through (``allow``: ``failed``, the
    default; ``warning`` lets warnings through as well; ``none`` holds
    everything). What is held is delivered when the window ends, as one digest
    when it is more than a few (``digest``, on by default).

``flood``
    Anti-flood: at most ``max`` messages in any ``minutes`` minutes. A server that
    reboots or an outage that touches thirty applications is thirty messages in a
    second; past the cap the rest are held and arrive once the window has passed,
    as one message that lists them.

``summary``
    A periodic digest: everything below a severity (``below``, ``warning`` by
    default: what is not a problem) waits and is sent once a day at ``at`` (on
    ``days``, every day when none) as one message, instead of one at a time all
    day. A warning or a failure still goes at once.

Pure: this module reads nothing from the machine. Where an application lives
(its project, environment and tags) is a :class:`Placement` the caller
resolves, only when some route needs it.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from noust.core.notifications.model import Notification, State

#: Severities a route can ask for, lowest first. ``info`` is what is not a
#: problem (a success, a recovery, a heartbeat), ``warning`` a thing to look at
#: and ``failed`` something that was meant to work and did not.
SEVERITIES: tuple[str, ...] = ("info", "warning", "failed")

#: How a notification's state ranks against :data:`SEVERITIES`.
_RANK = {
    State.OK: 0,
    State.PROGRESS: 0,
    State.INFO: 0,
    State.WARNING: 1,
    State.FAILED: 2,
}

#: What ``quiet.allow`` accepts: the lowest severity that still gets through
#: the quiet hours. ``none`` holds everything.
QUIET_ALLOW: tuple[str, ...] = ("failed", "warning", "none")

#: Entries a list of a route may have, and the longest one: a route is a
#: handful of names, not a database.
MAX_ENTRIES = 64
MAX_ENTRY_LENGTH = 253

#: A tag or a slug as an operator types it.
_ENTRY = re.compile(r"[A-Za-z0-9*?._:/@-]+")
_CLOCK = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")

_LISTS = ("apps", "projects", "environments", "tags")


def severity_of(notification: Notification) -> str:
    """
    Args:
        notification: Anything a channel may be sent.

    Returns:
        ``info``, ``warning`` or ``failed``, by its state.
    """
    return SEVERITIES[_RANK[notification.state]]


@dataclass(frozen=True)
class Placement:
    """
    Where an application lives, as a route asks about it.

    Attributes:
        project: Its project's slug.
        environment: Its environment's slug.
        tags: The tags it carries.
    """

    project: str | None = None
    environment: str | None = None
    tags: frozenset[str] = frozenset()


def _entries(value: Any, field_name: str) -> tuple[str, ...]:
    """
    Read one list of a route.

    Args:
        value: A list of strings, or one comma-separated string.
        field_name: Which list, for the error.

    Returns:
        The entries, trimmed, without repeats.

    Raises:
        ValueError: A list too long, an entry too long or with characters a
            domain, a slug or a tag never has.
    """
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        items: Iterable[Any] = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        raise ValueError(f"route.{field_name} must be a list of names")
    found: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > MAX_ENTRY_LENGTH or not _ENTRY.fullmatch(text):
            raise ValueError(
                f"route.{field_name} has an entry that is not a name: {text[:40]!r} "
                "(letters, digits and . _ - : / @, with * and ? for a pattern)"
            )
        if text not in found:
            found.append(text)
    if len(found) > MAX_ENTRIES:
        raise ValueError(f"route.{field_name} may name at most {MAX_ENTRIES} entries")
    return tuple(found)


@dataclass(frozen=True)
class Route:
    """
    The part of the server's events a channel is limited to.

    Attributes:
        apps: Application domains; ``*`` and ``?`` make a pattern.
        projects: Project slugs.
        environments: Environment slugs, or ``project/environment``.
        tags: Tags of the application.
        min_severity: The lowest severity delivered; None for every one.
        server_events: Also deliver what is about the server and no
            application, when the lists above would otherwise exclude it.
    """

    apps: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    min_severity: str | None = None
    server_events: bool = False

    @classmethod
    def from_mapping(cls, data: Any) -> Route:
        """
        Read a channel's ``route`` block.

        Args:
            data: The block; None or empty for no route.

        Returns:
            The route.

        Raises:
            ValueError: A shape or a value no route has.
        """
        if data in (None, ""):
            return cls()
        if not isinstance(data, Mapping):
            raise ValueError("route must be a mapping")
        unknown = set(data) - {*_LISTS, "min_severity", "server_events"}
        if unknown:
            raise ValueError(f"route has no setting {sorted(unknown)[0]!r}")
        minimum = data.get("min_severity")
        if minimum in ("", None):
            minimum = None
        elif minimum not in SEVERITIES[1:]:
            raise ValueError(f"route.min_severity must be one of: {', '.join(SEVERITIES[1:])}")
        server_events = data.get("server_events", False)
        if not isinstance(server_events, bool):
            raise ValueError("route.server_events must be true or false")
        return cls(
            **{name: _entries(data.get(name), name) for name in _LISTS},
            min_severity=minimum,
            server_events=server_events,
        )

    @property
    def narrows(self) -> bool:
        """Whether some list restricts which applications this route reaches."""
        return any(getattr(self, name) for name in _LISTS)

    @property
    def needs_placement(self) -> bool:
        """Whether answering needs the application's project, environment or tags."""
        return bool(self.projects or self.environments or self.tags)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The block as it is written in the configuration: only what is set.
        """
        data: dict[str, Any] = {name: list(getattr(self, name)) for name in _LISTS}
        data = {name: value for name, value in data.items() if value}
        if self.min_severity:
            data["min_severity"] = self.min_severity
        if self.server_events:
            data["server_events"] = True
        return data

    def allows(self, notification: Notification, placement: Placement | None) -> bool:
        """
        Whether the notification is inside this route.

        Args:
            notification: What is about to be delivered.
            placement: Where its application lives; None when the notification
                is about no application, or the application is unknown.

        Returns:
            True when a channel with this route should hear it.
        """
        if self.min_severity and _RANK[notification.state] < SEVERITIES.index(self.min_severity):
            return False
        if not self.narrows:
            return True
        domain = notification.domain
        if not domain:
            return self.server_events
        if self.apps and not any(fnmatch.fnmatchcase(domain, pattern) for pattern in self.apps):
            return False
        if not self.needs_placement:
            return True
        if placement is None:
            return False
        if self.projects and not _any_match(self.projects, placement.project):
            return False
        if self.environments and not self._environment_matches(placement):
            return False
        return not self.tags or any(
            fnmatch.fnmatchcase(tag, pattern) for tag in placement.tags for pattern in self.tags
        )

    def _environment_matches(self, placement: Placement) -> bool:
        """
        Args:
            placement: Where the application lives.

        Returns:
            Whether one entry names its environment, bare or with its project.
        """
        if placement.environment is None:
            return False
        qualified = f"{placement.project}/{placement.environment}"
        return any(
            fnmatch.fnmatchcase(placement.environment, entry)
            or fnmatch.fnmatchcase(qualified, entry)
            for entry in self.environments
        )


def _any_match(patterns: Iterable[str], value: str | None) -> bool:
    """
    Args:
        patterns: Names or patterns.
        value: What is being tested.

    Returns:
        Whether any pattern matches it.
    """
    return value is not None and any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def _zone(name: str) -> tzinfo:
    """
    Args:
        name: An IANA zone name.

    Returns:
        The zone. ``UTC`` needs no zone database, so a minimal image without
        ``tzdata`` still has quiet hours in the one zone it can always name.

    Raises:
        ZoneInfoNotFoundError: When no such zone is installed.
        ValueError: When the name is not a valid key.
    """
    return timezone.utc if name == "UTC" else ZoneInfo(name)


def _clock(value: Any, field_name: str, where: str = "quiet") -> time:
    """
    Args:
        value: ``HH:MM``.
        field_name: Which field, for the error.
        where: Which block, for the error.

    Returns:
        The time of day.

    Raises:
        ValueError: When it is not a time of day.
    """
    match = _CLOCK.fullmatch(str(value or "").strip())
    if match is None:
        raise ValueError(f"{where}.{field_name} must be a time like 22:00")
    return time(int(match.group(1)), int(match.group(2)))


@dataclass(frozen=True)
class QuietHours:
    """
    A window in which a channel is silent, except for what cannot wait.

    Attributes:
        start: When the quiet begins, in ``timezone``.
        end: When it ends. Earlier than ``start`` means across midnight.
        timezone: An IANA zone name.
        allow: The lowest severity that still gets through: ``failed``,
            ``warning`` or ``none``.
        digest: Deliver what was held as one message when it is more than a few.
    """

    start: time
    end: time
    timezone: str = "UTC"
    allow: str = "failed"
    digest: bool = True

    @classmethod
    def from_mapping(cls, data: Any) -> QuietHours | None:
        """
        Read a channel's ``quiet`` block.

        Args:
            data: The block; None or empty for none.

        Returns:
            The quiet hours, or None when the channel has none.

        Raises:
            ValueError: A time that is not one, a start equal to the end, a
                zone nobody knows, an unknown ``allow`` or setting.
        """
        if data in (None, "") or data == {}:
            return None
        if not isinstance(data, Mapping):
            raise ValueError("quiet must be a mapping")
        unknown = set(data) - {"start", "end", "timezone", "allow", "digest"}
        if unknown:
            raise ValueError(f"quiet has no setting {sorted(unknown)[0]!r}")
        start = _clock(data.get("start"), "start")
        end = _clock(data.get("end"), "end")
        if start == end:
            raise ValueError("quiet.start and quiet.end must differ")
        zone = str(data.get("timezone") or "UTC").strip()
        try:
            _zone(zone)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError(f"quiet.timezone {zone!r} is not a time zone name") from exc
        allow = str(data.get("allow") or "failed")
        if allow not in QUIET_ALLOW:
            raise ValueError(f"quiet.allow must be one of: {', '.join(QUIET_ALLOW)}")
        digest = data.get("digest", True)
        if not isinstance(digest, bool):
            raise ValueError("quiet.digest must be true or false")
        return cls(start, end, zone, allow, digest)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The block as it is written in the configuration.
        """
        return {
            "start": self.start.strftime("%H:%M"),
            "end": self.end.strftime("%H:%M"),
            "timezone": self.timezone,
            "allow": self.allow,
            "digest": self.digest,
        }

    def _local(self, moment: datetime) -> datetime:
        """
        Args:
            moment: A timezone-aware moment.

        Returns:
            It in this window's zone.
        """
        return moment.astimezone(_zone(self.timezone))

    def active(self, moment: datetime) -> bool:
        """
        Args:
            moment: A timezone-aware moment.

        Returns:
            Whether the window is open at that moment.
        """
        now = self._local(moment).time().replace(second=0, microsecond=0)
        if self.start < self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end

    def ends_at(self, moment: datetime) -> datetime:
        """
        Args:
            moment: A moment inside the window.

        Returns:
            When the window closes, in UTC; the next close after ``moment``.
        """
        local = self._local(moment)
        close = local.replace(hour=self.end.hour, minute=self.end.minute, second=0, microsecond=0)
        if close <= local:
            close += timedelta(days=1)
        return close.astimezone(timezone.utc)

    def lets_through(self, notification: Notification) -> bool:
        """
        Args:
            notification: What is about to be delivered inside the window.

        Returns:
            Whether its severity is high enough to be delivered anyway.
        """
        if self.allow == "none":
            return False
        return _RANK[notification.state] >= SEVERITIES.index(self.allow)


#: Why a notification is held.
HOLD_QUIET = "quiet"
HOLD_FLOOD = "flood"
HOLD_SUMMARY = "summary"

#: Weekdays by the name a configuration writes, Monday first.
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: Bounds of the anti-flood cap.
MAX_FLOOD_MESSAGES = 1_000
MAX_FLOOD_MINUTES = 1_440


@dataclass(frozen=True)
class Flood:
    """
    The most a channel says in a stretch of time.

    Attributes:
        max_messages: Messages allowed in the window.
        minutes: The window, in minutes.
    """

    max_messages: int
    minutes: int

    @classmethod
    def from_mapping(cls, data: Any) -> Flood | None:
        """
        Read a channel's ``flood`` block.

        Args:
            data: The block; None or empty for none.

        Returns:
            The cap, or None when the channel has none.

        Raises:
            ValueError: A number out of range, or a setting no cap has.
        """
        if data in (None, "") or data == {}:
            return None
        if not isinstance(data, Mapping):
            raise ValueError("flood must be a mapping")
        unknown = set(data) - {"max", "minutes"}
        if unknown:
            raise ValueError(f"flood has no setting {sorted(unknown)[0]!r}")
        values: dict[str, int] = {}
        for key, high in (("max", MAX_FLOOD_MESSAGES), ("minutes", MAX_FLOOD_MINUTES)):
            raw = data.get(key)
            if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= high:
                raise ValueError(f"flood.{key} must be a whole number from 1 to {high}")
            values[key] = raw
        return cls(values["max"], values["minutes"])

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The block as it is written in the configuration.
        """
        return {"max": self.max_messages, "minutes": self.minutes}


@dataclass(frozen=True)
class Summary:
    """
    A periodic summary that carries what is not worth a message of its own.

    Attributes:
        at: When the summary goes out, in ``timezone``.
        timezone: An IANA zone name.
        below: Severities below this wait for the summary: ``warning`` holds
            what is not a problem, ``failed`` also holds warnings.
        days: The days it goes out on, Monday 0; every day when empty.
    """

    at: time
    timezone: str = "UTC"
    below: str = "warning"
    days: tuple[int, ...] = ()

    @classmethod
    def from_mapping(cls, data: Any) -> Summary | None:
        """
        Read a channel's ``summary`` block.

        Args:
            data: The block; None or empty for none.

        Returns:
            The summary, or None when the channel has none.

        Raises:
            ValueError: A time that is not one, a zone nobody knows, a severity
                or a day that does not exist, or a setting no summary has.
        """
        if data in (None, "") or data == {}:
            return None
        if not isinstance(data, Mapping):
            raise ValueError("summary must be a mapping")
        unknown = set(data) - {"at", "timezone", "below", "days"}
        if unknown:
            raise ValueError(f"summary has no setting {sorted(unknown)[0]!r}")
        when = _clock(data.get("at"), "at", where="summary")
        zone = str(data.get("timezone") or "UTC").strip()
        try:
            _zone(zone)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError(f"summary.timezone {zone!r} is not a time zone name") from exc
        below = str(data.get("below") or "warning")
        if below not in SEVERITIES[1:]:
            raise ValueError(f"summary.below must be one of: {', '.join(SEVERITIES[1:])}")
        raw_days = data.get("days") or []
        if isinstance(raw_days, str):
            raw_days = [item for item in raw_days.replace(" ", "").split(",") if item]
        if not isinstance(raw_days, (list, tuple)):
            raise ValueError("summary.days must be a list of weekdays")
        days: list[int] = []
        for item in raw_days:
            name = str(item).strip().lower()[:3]
            if name not in WEEKDAYS:
                raise ValueError(
                    f"summary.days has no day {item!r}; the days are {', '.join(WEEKDAYS)}"
                )
            if WEEKDAYS.index(name) not in days:
                days.append(WEEKDAYS.index(name))
        return cls(when, zone, below, tuple(sorted(days)))

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The block as it is written in the configuration.
        """
        data: dict[str, Any] = {
            "at": self.at.strftime("%H:%M"),
            "timezone": self.timezone,
            "below": self.below,
        }
        if self.days:
            data["days"] = [WEEKDAYS[day] for day in self.days]
        return data

    def holds(self, notification: Notification) -> bool:
        """
        Args:
            notification: What is about to be delivered.

        Returns:
            Whether its severity is low enough to wait for the summary.
        """
        return _RANK[notification.state] < SEVERITIES.index(self.below)

    def next_at(self, moment: datetime) -> datetime:
        """
        Args:
            moment: A timezone-aware moment.

        Returns:
            The next time the summary goes out after ``moment``, in UTC.
        """
        zone = _zone(self.timezone)
        local = moment.astimezone(zone)
        candidate = local.replace(hour=self.at.hour, minute=self.at.minute, second=0, microsecond=0)
        for _ in range(8):
            if candidate > local and (not self.days or candidate.weekday() in self.days):
                return candidate.astimezone(timezone.utc)
            candidate += timedelta(days=1)
        return candidate.astimezone(
            timezone.utc
        )  # pragma: no cover - a day of the week is within 7


@dataclass(frozen=True)
class Decision:
    """
    What to do with one notification on one channel.

    Attributes:
        action: ``send`` now, ``hold`` until ``release_at`` or ``skip`` it.
        reason: Why, in a few words, for the log and the delivery ledger.
        release_at: When a held notification is delivered (UTC).
        cause: Why it is held: :data:`HOLD_QUIET`, :data:`HOLD_FLOOD` or
            :data:`HOLD_SUMMARY`.
    """

    action: str
    reason: str = ""
    release_at: datetime | None = None
    cause: str = ""


SEND = Decision("send")


@dataclass(frozen=True)
class Routing:
    """
    The two optional blocks of a channel, read.

    Attributes:
        route: Which events reach it.
        quiet: When it stays silent, if ever.
        events: This channel's own on/off switch per event kind; a kind it does
            not name falls back to the server-wide switch.
        flood: The most it says in a stretch of time, if capped.
        summary: What waits for the periodic summary, if it has one.
    """

    route: Route = field(default_factory=Route)
    quiet: QuietHours | None = None
    events: Mapping[str, bool] = field(default_factory=dict)
    flood: Flood | None = None
    summary: Summary | None = None


def decide(
    routing: Routing,
    notification: Notification,
    *,
    now: datetime,
    server_events: Mapping[str, bool],
    default_on: Callable[[str], bool],
    placement_of: Callable[[str], Placement | None],
    sent_recently: Callable[[int], int] | None = None,
) -> Decision:
    """
    Decide whether a channel receives a notification, now.

    Args:
        routing: The channel's own settings.
        notification: What happened.
        now: The current moment (timezone-aware), for the quiet hours.
        server_events: ``notifications.events``: the switches every channel
            falls back to.
        default_on: Whether a kind is on when no switch names it.
        placement_of: Where an application lives; only called when the
            channel's route asks about projects, environments or tags and the
            notification is about an application.
        sent_recently: How many messages the channel sent in the last N
            minutes; only called for a channel with a ``flood`` cap.

    Returns:
        ``send``, ``hold`` (with the moment to deliver and why) or ``skip``.
    """
    kind = notification.kind
    wanted = routing.events.get(kind)
    if wanted is None:
        wanted = server_events.get(kind, default_on(kind))
    if not wanted:
        return Decision("skip", "event switched off")

    route = routing.route
    placement = None
    if route.needs_placement and route.narrows and notification.domain:
        placement = placement_of(notification.domain)
    if not route.allows(notification, placement):
        return Decision("skip", "outside the channel's route")

    quiet = routing.quiet
    if quiet is not None and quiet.active(now) and not quiet.lets_through(notification):
        return Decision("hold", "quiet hours", quiet.ends_at(now), HOLD_QUIET)
    summary = routing.summary
    if summary is not None and summary.holds(notification):
        return Decision("hold", "waits for the summary", summary.next_at(now), HOLD_SUMMARY)
    flood = routing.flood
    if flood is not None and sent_recently is not None:
        if sent_recently(flood.minutes) >= flood.max_messages:
            return Decision(
                "hold",
                f"more than {flood.max_messages} messages in {flood.minutes} minutes",
                now + timedelta(minutes=flood.minutes),
                HOLD_FLOOD,
            )
    return SEND
