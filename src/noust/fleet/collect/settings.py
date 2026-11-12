# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a central collects the copies of its servers: who is followed, how fast, how long they are kept.

Collecting is off until somebody turns it on: a central that quietly carried every server's data to
its own disk would be a promise the operator never made. It is turned on for the whole fleet or for a
server (``follow`` on a server is its own answer; none follows the fleet's), and "followed by N of M
servers" is one function (:meth:`Follows.counts`) so that every screen and every command says the
same.

The knobs are rows of ``collect_settings`` (schema v18), each judged on its way in:

- ``enabled``: whether the fleet is collected (off).
- ``kinds``: which kinds of copy are taken (all four).
- ``rate_limit_kbps``: the most a transfer may use, in kilobytes a second, so the office line is
  not the central's (none).
- ``poll_seconds``: how often each server is asked whether it has something new (60).
- ``pass_hours``: how long at most before a server's whole list is read again whether or not it said
  it changed (6), which is what picks up what an earlier pass left behind.
- ``retention_count``, ``retention_days``, ``retention_gb``: how many scheduled copies of each
  application, how old and how big they may be together before the central lets the oldest go (30,
  none, none). A copy by hand and a restore's safety copy are never let go by a retention.
- ``min_free_gb``: the room that is always left on the disk (1): below it nothing more is taken, a
  notice says so, and nothing is deleted to make room.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store
from noust.core.timeutil import utc_now_iso
from noust.managers.backup_catalogue import KINDS

__all__ = [
    "DEFAULTS",
    "KEYS",
    "CollectSettings",
    "Follows",
    "SettingsStore",
]

GIB = 1024**3


@dataclass(frozen=True)
class CollectSettings:
    """
    The knobs of collecting.

    Attributes:
        enabled: Whether the fleet is collected unless a server says otherwise.
        kinds: The kinds of copy that are taken.
        rate_limit_kbps: The most a transfer may use, in kilobytes a second; None for no limit.
        poll_seconds: Seconds between two looks at each server.
        pass_hours: Hours after which a server's whole list is read again.
        retention_count: Scheduled copies of each application kept; None for no limit by count.
        retention_days: Days a scheduled copy is kept; None for no limit by age.
        retention_gb: The most the scheduled copies of one application may weigh together, in
            gigabytes; None for no limit by size.
        min_free_gb: The room always left on the disk, in gigabytes.
    """

    enabled: bool = False
    kinds: tuple[str, ...] = KINDS
    rate_limit_kbps: int | None = None
    poll_seconds: int = 60
    pass_hours: int = 6
    retention_count: int | None = 30
    retention_days: int | None = None
    retention_gb: float | None = None
    min_free_gb: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every knob, JSON-ready.
        """
        data = asdict(self)
        data["kinds"] = list(self.kinds)
        return data

    @property
    def rate_bytes_per_second(self) -> float | None:
        """Returns: The speed limit in bytes a second, or None."""
        return None if self.rate_limit_kbps is None else self.rate_limit_kbps * 1024.0

    @property
    def min_free_bytes(self) -> int:
        """Returns: The room always left, in bytes."""
        return int(self.min_free_gb * GIB)


#: What a server that never said anything is collected with.
DEFAULTS = CollectSettings()

#: The names of the knobs, in the order they are shown.
KEYS: tuple[str, ...] = tuple(asdict(DEFAULTS))


def _whole(value: Any, field_name: str, low: int, high: int, *, none: bool = False) -> int | None:
    """
    Judge a whole number.

    Args:
        value: What the caller gave.
        field_name: The knob.
        low: The least it may be.
        high: The most it may be.
        none: Whether None means "no limit".

    Returns:
        The number, or None when ``none`` and the caller gave none.

    Raises:
        ValidationError: When it is not a whole number in range.
    """
    if value is None and none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValidationError(
            f"{field_name} must be a whole number from {low} to {high}",
            details="Leave it empty for no limit." if none else "",
            field=field_name,
        )
    return int(value)


def _gigabytes(
    value: Any, field_name: str, high: float, *, none: bool = False, zero: bool = False
) -> float | None:
    """
    Judge an amount of gigabytes.

    Args:
        value: What the caller gave.
        field_name: The knob.
        high: The most it may be.
        none: Whether None means "no limit".
        zero: Whether zero is allowed.

    Returns:
        The amount, or None.

    Raises:
        ValidationError: When it is not a number in range.
    """
    if value is None and none:
        return None
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not ok or not ((0 if zero else 1e-9) <= float(value) <= high):
        raise ValidationError(
            f"{field_name} must be an amount of gigabytes up to {high:g}"
            + ("" if zero else " and more than zero"),
            details="Leave it empty for no limit." if none else "",
            field=field_name,
        )
    return float(value)


def _checked(key: str, value: Any) -> Any:
    """
    Judge one knob.

    Args:
        key: The knob.
        value: What the caller gave.

    Returns:
        The value, as stored.

    Raises:
        ValidationError: When the knob is not one, or the value is not right for it.
    """
    if key == "enabled":
        if not isinstance(value, bool):
            raise ValidationError("enabled must be true or false", field=key)
        return value
    if key == "kinds":
        if (
            not isinstance(value, (list, tuple))
            or not value
            or any(kind not in KINDS for kind in value)
        ):
            raise ValidationError(
                "kinds must name at least one of the kinds of copy",
                details=f"The kinds are: {', '.join(KINDS)}.",
                field=key,
            )
        return [kind for kind in KINDS if kind in value]
    if key == "rate_limit_kbps":
        return _whole(value, key, 16, 10_000_000, none=True)
    if key == "poll_seconds":
        return _whole(value, key, 15, 3600)
    if key == "pass_hours":
        return _whole(value, key, 1, 168)
    if key == "retention_count":
        return _whole(value, key, 1, 3650, none=True)
    if key == "retention_days":
        return _whole(value, key, 1, 36500, none=True)
    if key == "retention_gb":
        return _gigabytes(value, key, 1_000_000, none=True)
    if key == "min_free_gb":
        return _gigabytes(value, key, 1_000_000, zero=True)
    raise ValidationError(
        f"No such setting: {key!r}", details=f"The settings are: {', '.join(KEYS)}.", field=key
    )


class SettingsStore:
    """
    Read and write the knobs of collecting.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def get(self) -> CollectSettings:
        """
        Returns:
            The knobs as they are now: what was set, and the defaults for the rest.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT key, value FROM collect_settings")
            .fetchall()
        )
        values: dict[str, Any] = {}
        for row in rows:
            key = str(row["key"])
            if key not in KEYS:
                continue
            try:
                values[key] = _checked(key, json.loads(row["value"]))
            except (ValueError, ValidationError):
                # A row nobody can read is the default, not a reason to stop collecting.
                continue
        if "kinds" in values:
            values["kinds"] = tuple(values["kinds"])
        return replace(DEFAULTS, **values)

    def set(self, **changes: Any) -> CollectSettings:
        """
        Change knobs, all or none.

        Args:
            **changes: Knob to value.

        Returns:
            The knobs as they are now.

        Raises:
            ValidationError: A knob is not one or its value is wrong; nothing is changed.
        """
        checked = {key: _checked(key, value) for key, value in changes.items()}
        now = utc_now_iso()
        with self.store._transaction() as cursor:
            for key, value in checked.items():
                cursor.execute(
                    "INSERT INTO collect_settings (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at",
                    (key, json.dumps(value), now),
                )
        return self.get()


@dataclass(frozen=True)
class FollowCount:
    """
    How many servers a central collects from.

    Attributes:
        following: The servers that are collected from.
        of: The servers the central manages.
        exceptions: The servers whose own answer differs from the fleet's.
    """

    following: int
    of: int
    exceptions: tuple[str, ...] = field(default_factory=tuple)


class Follows:
    """
    Which servers the central collects from.

    A server's own answer (on or off) wins over the fleet's; none follows the fleet.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def own(self) -> dict[str, bool]:
        """
        Returns:
            The servers that have an answer of their own, by name.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT node, follow FROM collect_nodes WHERE follow IS NOT NULL")
            .fetchall()
        )
        return {str(row["node"]): bool(row["follow"]) for row in rows}

    def follows(self, node: str, settings: CollectSettings | None = None) -> bool:
        """
        Args:
            node: A server.
            settings: The knobs; read now when None.

        Returns:
            Whether the central collects from it.
        """
        mine = self.own()
        if node in mine:
            return mine[node]
        return (settings or SettingsStore(self.store).get()).enabled

    def set_follow(self, node: str, value: bool | None) -> None:
        """
        Give a server its own answer, or take it back to the fleet's.

        Args:
            node: A server of this central.
            value: True or False for its own, None to follow the fleet.

        Raises:
            ValidationError: The server is not one this central manages.
        """
        if self.store.get_node(node) is None:
            raise ValidationError(
                f"No server named {node} is managed by this central",
                details="List them with 'noust node list'.",
                field="node",
            )
        flag = None if value is None else int(bool(value))
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO collect_nodes (node, follow) VALUES (?, ?) "
                "ON CONFLICT(node) DO UPDATE SET follow = excluded.follow",
                (node, flag),
            )

    def counts(
        self, nodes: Iterable[str] | None = None, settings: CollectSettings | None = None
    ) -> FollowCount:
        """
        Count how many servers are collected from: the one definition of "N of M".

        Args:
            nodes: The servers to count; every one this central manages when None. The central
                itself is never one.
            settings: The knobs; read now when None.

        Returns:
            The count and the servers that differ from the fleet's answer.
        """
        names = sorted(nodes) if nodes is not None else [n.name for n in self.store.list_nodes()]
        knobs = settings or SettingsStore(self.store).get()
        mine = self.own()
        following = [node for node in names if mine.get(node, knobs.enabled)]
        exceptions = tuple(node for node in names if node in mine and mine[node] != knobs.enabled)
        return FollowCount(len(following), len(names), exceptions)
