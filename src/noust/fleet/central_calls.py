# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When a central last spoke to this server, and whether it said it keeps watch.

A server cannot call its central: nothing it knows would be a credential for one. So the only way
it can tell that the central is gone is that the central stops calling, and for that it has to
remember when each central last did. It notes, for every central that presents a fleet token, the
time of its last call and how often the central said it would call.

A central says that (``X-Noust-Heartbeat: 30``, seconds) only when it can promise it: the monitor
of a central probes every server every half minute for as long as it runs, while the console
probes only while somebody has it open. A server therefore expects to hear again only from a
central that said so, and only that one is said to have gone quiet
(:mod:`noust.monitor.watch_central`). A central that never said it keeps watch is not missed when
nobody has its console open.

The note is made at most once a minute for each central: the probe calls every thirty seconds and
a console many times a second, and neither is a reason to write to the store at every request. The
time is the store's, so the monitor, which is another process than the console that receives the
calls, reads what the console noted.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from noust.core.store import NoustStore, get_store

__all__ = [
    "MAX_HEARTBEAT",
    "WRITE_EVERY",
    "CentralCall",
    "CentralCalls",
    "central_of_token",
    "heartbeat_of",
]

#: Seconds between two notes of the same central, whatever the number of calls in between.
WRITE_EVERY = 60.0

#: The longest interval a central may say it calls at, in seconds: an hour. A longer one is not a
#: watch.
MAX_HEARTBEAT = 3600

#: A central's fleet token: ``fleet-<central>`` or ``fleet-<central>.<n>``. The one definition of
#: what a token's name says of its central.
_FLEET_TOKEN = re.compile(r"fleet-([a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?)(?:\.[0-9]+)?")

#: What a heartbeat claim looks like: whole seconds, in ASCII.
_HEARTBEAT = re.compile(r"[0-9]{1,4}")


def central_of_token(token_name: str | None) -> str | None:
    """
    Args:
        token_name: The name of the token a request presented.

    Returns:
        The central it belongs to (``fleet-nas.2`` is ``nas``); None when it is not a fleet token.
    """
    if not token_name:
        return None
    match = _FLEET_TOKEN.fullmatch(token_name)
    return match.group(1) if match else None


def heartbeat_of(header: str | None) -> int | None:
    """
    Read what a central says of how often it calls.

    Args:
        header: The value of ``X-Noust-Heartbeat``.

    Returns:
        The seconds, from 1 to :data:`MAX_HEARTBEAT`; None for anything else, which is no claim
        and never an error: the call is still a call.
    """
    if header is None or not _HEARTBEAT.fullmatch(header):
        return None
    seconds = int(header)
    return seconds if 1 <= seconds <= MAX_HEARTBEAT else None


@dataclass(frozen=True)
class CentralCall:
    """
    What a server knows of one central's calls.

    Attributes:
        central: The central, as its fleet token names it.
        first_call_at: Epoch seconds of the first call noted.
        last_call_at: Epoch seconds of the last call noted (to the minute).
        heartbeat_s: How often it said it calls, when it said so; None if it never did.
    """

    central: str
    first_call_at: float
    last_call_at: float
    heartbeat_s: int | None


class CentralCalls:
    """
    The note of when each central last called.

    Args:
        store: This server's store; the process-wide one by default.
        clock: Epoch seconds (tests move it).
    """

    def __init__(
        self, store: NoustStore | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._noted: dict[str, tuple[float, int | None]] = {}

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def note(self, central: str, heartbeat_s: int | None = None) -> bool:
        """
        Note that a central called now.

        Args:
            central: The central.
            heartbeat_s: How often it said it calls, when this call says so. What an earlier call
                said is kept when this one says nothing.

        Returns:
            Whether it was written: not when this central was noted less than
            :data:`WRITE_EVERY` seconds ago and has nothing new to say.
        """
        now = self._clock()
        with self._lock:
            last, said = self._noted.get(central, (None, None))
            news = heartbeat_s is not None and heartbeat_s != said
            if last is not None and now - last < WRITE_EVERY and not news:
                return False
            with self.store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO fleet_central_calls (central, first_call_at, last_call_at, "
                    "heartbeat_s) VALUES (?, ?, ?, ?) ON CONFLICT(central) DO UPDATE SET "
                    "last_call_at = excluded.last_call_at, "
                    "heartbeat_s = COALESCE(excluded.heartbeat_s, fleet_central_calls.heartbeat_s)",
                    (central, now, now, heartbeat_s),
                )
            self._noted[central] = (now, heartbeat_s if heartbeat_s is not None else said)
        return True

    def list(self) -> list[CentralCall]:
        """Returns: Every central that has called, by name."""
        rows = self.store._get_connection().execute(
            "SELECT central, first_call_at, last_call_at, heartbeat_s FROM fleet_central_calls "
            "ORDER BY central"
        )
        return [
            CentralCall(
                central=str(row["central"]),
                first_call_at=float(row["first_call_at"]),
                last_call_at=float(row["last_call_at"]),
                heartbeat_s=int(row["heartbeat_s"]) if row["heartbeat_s"] is not None else None,
            )
            for row in rows.fetchall()
        ]

    def forget(self, central: str) -> bool:
        """
        Drop what is noted of a central: it left, or this server no longer lets it in.

        Args:
            central: The central.

        Returns:
            Whether there was a note.
        """
        with self._lock:
            self._noted.pop(central, None)
            with self.store._transaction() as cursor:
                cursor.execute("DELETE FROM fleet_central_calls WHERE central = ?", (central,))
                return cursor.rowcount > 0
