# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One place that attaches a UTC offset to a timestamp the store wrote naively.

:class:`~noust.core.store.NoustStore` and the job manager both write
``datetime.now().isoformat()`` for most of their timestamp columns - naive,
in whatever zone the machine that wrote it happens to be set to. That was
never a problem for the CLI: it reads the string back on the same machine, in
the same zone, a moment later. A browser reading the same string over the API
has no such guarantee, and cannot know which zone "2026-09-25T10:00:00" was
written in.

This module is the one place that gap is closed, so every response model
applies the same conversion instead of each endpoint growing its own slightly
different guess. Nothing here changes what the store writes: other code
compares those naive datetimes directly, and rewriting the column would break
that comparison for a UI convenience.

It is also the one clock. Forty-five modules each had a private ``_now`` and
they wrote three different things: a UTC timestamp to the second, a UTC
timestamp with its fraction, and the store's own naive local one. What a column
holds is data that a later reader parses and compares, so each spelling is a
function here and a module calls the one it always wrote; none of them may be
swapped for another without a migration.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso(timespec: str = "auto") -> str:
    """
    Read the clock as ISO 8601 in UTC, with its offset.

    Args:
        timespec: What :meth:`datetime.isoformat` takes. ``"seconds"`` for a
            record that never needs finer than that; the default writes the
            fraction whenever there is one.

    Returns:
        For example ``2026-10-27T18:00:00+00:00``.
    """
    return datetime.now(timezone.utc).isoformat(timespec=timespec)


def local_now_iso() -> str:
    """
    Read the clock the way the store writes its own timestamps.

    Returns:
        ISO 8601 to the second, in this machine's local time and with no offset
        (``to_iso_offset`` attaches the machine's when a browser needs one).
    """
    return datetime.now().isoformat(timespec="seconds")


def local_now() -> datetime:
    """
    Read the clock as an aware datetime in this machine's zone.

    Returns:
        The current time with the machine's UTC offset: the zone a systemd calendar expression
        is read in, so what a person is asked for and shown is this one.
    """
    return datetime.now().astimezone()


def utc_now() -> datetime:
    """
    Read the clock as an aware datetime in UTC.

    Returns:
        The current time. A function, so a test can move it where it is read.
    """
    return datetime.now(timezone.utc)


def to_iso_offset(value: str | datetime | None) -> str | None:
    """
    Render a stored timestamp as ISO 8601 with an explicit UTC offset.

    Args:
        value: A timestamp as the store or an in-memory record holds it: a
            naive or aware ``datetime``, an ISO 8601 string in either form,
            or None.

    Returns:
        None when ``value`` is None. A naive value - no ``tzinfo``, the shape
        the store writes - is presumed to be this machine's own local time
        and gets this machine's current UTC offset attached, without moving
        the wall clock. An aware value keeps the offset it already carries:
        it was not written by the naive path this module exists for, and
        re-stamping it with this machine's offset would silently change what
        moment it names whenever the two zones differ. A string that is not
        a valid ISO 8601 datetime - systemd's or certbot's own formatting,
        which some fields carry verbatim - is returned unchanged rather than
        failing the whole response over a field the operator needs to see.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value)
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return text

    if moment.tzinfo is None:
        # Naive datetimes are presumed to be this machine's local time, and
        # astimezone() with no argument attaches that machine's own offset
        # for the timestamp's own date - which matters wherever the zone
        # observes daylight saving - without changing the wall clock.
        moment = moment.astimezone()

    return moment.isoformat()
