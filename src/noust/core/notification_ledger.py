# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The record of every notification delivery, and the queue of those that must be tried again.

A notification that goes to a channel that is down used to be a line in a log
and nothing more: no second attempt, no way to see afterwards that an alert
never arrived. Every delivery is now a row of ``notification_deliveries`` (the
store's v14 schema), written once it has been tried and updated by each try
after it:

``retrying``
    Failed for a reason that may pass (the destination was unreachable, answered
    5xx or 429): tried again at ``next_attempt_at``, after a wait that grows.
``held``
    Not for now: the channel's quiet hours are on, and it is delivered when
    they end (as one digest when there are more than a few).
``sent``
    Delivered. The notification itself is dropped from the row.
``failed``
    Given up on, with the destination's own words in ``last_error``. The
    notification is kept so an operator can send it again by hand.

This is the whole of K-30's promise: reliable delivery with a history, and a
failure to notify that stays a row and an audit event, never an effect on the
deploy, the backup or the check that raised it. The ledger raises nothing the
notifier does not handle: every method that can fail on the store says so, and
the notifier treats the ledger as optional.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from noust.core.notifications.model import Notification
from noust.core.store import NoustStore, get_store

#: The states a delivery can be in.
STATES: tuple[str, ...] = ("retrying", "held", "sent", "failed")

#: Seconds to wait after each failed attempt before the next: a minute, five,
#: half an hour, two hours, six. After the last wait there is one more attempt;
#: if that fails too the delivery is ``failed``.
RETRY_DELAYS: tuple[int, ...] = (60, 300, 1_800, 7_200, 21_600)

#: Attempts in all: the first, and one after each wait.
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1

#: How long a settled delivery is kept: delivered ones are only history, failed
#: ones are what an operator may still want to send again.
KEEP_SENT_SECONDS = 3 * 86_400
KEEP_FAILED_SECONDS = 30 * 86_400

#: Rows kept at most, oldest delivered first when there are more.
MAX_ROWS = 5_000

#: The longest ``last_error`` stored; the destination's words, not a page.
MAX_ERROR_LENGTH = 1_000


@dataclass(frozen=True)
class Delivery:
    """
    One notification on its way to one channel.

    Attributes:
        id: The row.
        notification_id: The notification's own id (what the webhook calls
            ``id``); several deliveries share it, one per channel.
        kind: The event kind (the switch).
        code: What happened, fine-grained.
        subject: What it is about.
        channel: The channel instance's name.
        channel_type: Its type.
        state: One of :data:`STATES`.
        attempts: Tries so far.
        created_at: Epoch seconds.
        updated_at: Epoch seconds.
        next_attempt_at: When it is tried (or released) next, or None.
        last_error: The failure, in the destination's words.
        payload: The notification, while it may still be delivered or sent by
            hand; None once it was.
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
    next_attempt_at: float | None
    last_error: str | None
    payload: dict[str, Any] | None

    def notification(self) -> Notification:
        """
        Returns:
            The notification to deliver.

        Raises:
            ValueError: When no payload was kept or it is not a notification.
        """
        if self.payload is None:
            raise ValueError(f"Delivery {self.id} kept no notification to send")
        return Notification.from_dict(self.payload)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The delivery as the API shows it: everything but the payload,
            which can hold a verbatim excerpt of a log.
        """
        return {
            "id": self.id,
            "notification_id": self.notification_id,
            "kind": self.kind,
            "code": self.code,
            "subject": self.subject,
            "channel": self.channel,
            "channel_type": self.channel_type,
            "state": self.state,
            "attempts": self.attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "next_attempt_at": self.next_attempt_at,
            "last_error": self.last_error,
            "can_retry": self.state in ("failed", "retrying", "held") and self.payload is not None,
        }


def _delivery(row: sqlite3.Row) -> Delivery:
    """
    Args:
        row: A row of ``notification_deliveries``.

    Returns:
        The delivery; a payload that cannot be read is dropped, not fatal.
    """
    payload: dict[str, Any] | None = None
    if row["payload"]:
        try:
            decoded = json.loads(row["payload"])
        except ValueError:
            decoded = None
        payload = decoded if isinstance(decoded, dict) else None
    return Delivery(
        id=int(row["id"]),
        notification_id=str(row["notification_id"]),
        kind=str(row["kind"]),
        code=str(row["code"]),
        subject=str(row["subject"]),
        channel=str(row["channel"]),
        channel_type=str(row["channel_type"]),
        state=str(row["state"]),
        attempts=int(row["attempts"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        next_attempt_at=float(row["next_attempt_at"])
        if row["next_attempt_at"] is not None
        else None,
        last_error=row["last_error"],
        payload=payload,
    )


def backoff(attempts: int) -> int | None:
    """
    Say how long to wait after an attempt failed.

    Args:
        attempts: How many attempts have been made, this failed one included.

    Returns:
        Seconds until the next, or None when there are no attempts left.
    """
    if attempts < 1 or attempts > len(RETRY_DELAYS):
        return None
    return RETRY_DELAYS[attempts - 1]


class DeliveryLedger:
    """
    The store's record of deliveries.

    Every method can raise :class:`sqlite3.Error` (and :class:`OSError` when
    the store cannot be opened): the notifier catches both and delivers
    without a record rather than not at all.
    """

    def __init__(
        self, store: NoustStore | None = None, *, clock: Callable[[], float] = time.time
    ) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
            clock: Epoch seconds. Tests move it.
        """
        self._store = store
        self._clock = clock

    @property
    def store(self) -> NoustStore:
        """The store the deliveries live in."""
        return self._store if self._store is not None else get_store()

    def record_sent(
        self, notification: Notification, channel: str, channel_type: str, attempts: int = 1
    ) -> int:
        """
        Write down a delivery that went out at the first try.

        Args:
            notification: What was delivered.
            channel: The channel instance.
            channel_type: Its type.
            attempts: Tries it took.

        Returns:
            The row's id.
        """
        return self._insert(
            notification, channel, channel_type, "sent", None, attempts=attempts, keep_payload=False
        )

    def record_failure(
        self,
        notification: Notification,
        channel: str,
        channel_type: str,
        error: str,
        *,
        retry_in: int | None,
    ) -> int:
        """
        Write down a first attempt that failed.

        Args:
            notification: What could not be delivered.
            channel: The channel instance.
            channel_type: Its type.
            error: What went wrong, scrubbed of credentials.
            retry_in: Seconds until it is tried again, or None for never.

        Returns:
            The row's id.
        """
        now = self._clock()
        return self._insert(
            notification,
            channel,
            channel_type,
            "retrying" if retry_in is not None else "failed",
            now + retry_in if retry_in is not None else None,
            attempts=1,
            error=error,
        )

    def hold(
        self, notification: Notification, channel: str, channel_type: str, release_at: float
    ) -> int:
        """
        Write down a delivery that waits for the quiet hours to end.

        Args:
            notification: What is held.
            channel: The channel instance.
            channel_type: Its type.
            release_at: Epoch seconds when it may go.

        Returns:
            The row's id.
        """
        return self._insert(notification, channel, channel_type, "held", release_at)

    def _insert(
        self,
        notification: Notification,
        channel: str,
        channel_type: str,
        state: str,
        next_attempt_at: float | None,
        *,
        attempts: int = 0,
        error: str | None = None,
        keep_payload: bool = True,
    ) -> int:
        """
        Args:
            notification: What is delivered.
            channel: The channel instance.
            channel_type: Its type.
            state: The first state.
            next_attempt_at: When it is next tried, if it waits.
            attempts: Tries so far.
            error: What went wrong, if something did.
            keep_payload: Whether the notification stays on the row.

        Returns:
            The row's id.
        """
        now = self._clock()
        payload = json.dumps(notification.to_dict(), ensure_ascii=False) if keep_payload else None
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO notification_deliveries (notification_id, kind, code, subject, "
                "channel, channel_type, state, attempts, created_at, updated_at, "
                "next_attempt_at, last_error, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    notification.id,
                    notification.kind,
                    notification.code,
                    notification.subject,
                    channel,
                    channel_type,
                    state,
                    attempts,
                    now,
                    now,
                    next_attempt_at,
                    error[:MAX_ERROR_LENGTH] if error else None,
                    payload,
                ),
            )
            return int(cursor.lastrowid or 0)

    def sent(self, delivery_id: int) -> None:
        """
        Settle a delivery that went out.

        Args:
            delivery_id: The row.
        """
        self._settle(delivery_id, "sent", None, None, keep_payload=False)

    def retry_later(self, delivery_id: int, error: str, attempts: int, wait_s: int) -> None:
        """
        Settle an attempt that failed for a reason that may pass.

        Args:
            delivery_id: The row.
            error: What went wrong, scrubbed of credentials.
            attempts: Attempts made, this one included.
            wait_s: Seconds until the next.
        """
        self._settle(delivery_id, "retrying", error, self._clock() + wait_s, attempts=attempts)

    def failed(self, delivery_id: int, error: str, attempts: int) -> None:
        """
        Settle a delivery that will not be tried again.

        Args:
            delivery_id: The row.
            error: What went wrong, scrubbed of credentials.
            attempts: Attempts made, this one included.
        """
        self._settle(delivery_id, "failed", error, None, attempts=attempts)

    def _settle(
        self,
        delivery_id: int,
        state: str,
        error: str | None,
        next_attempt_at: float | None,
        *,
        attempts: int | None = None,
        keep_payload: bool = True,
    ) -> None:
        """
        Args:
            delivery_id: The row.
            state: The state it moves to.
            error: The failure, or None.
            next_attempt_at: When it is next tried.
            attempts: Attempts made, when this was one.
            keep_payload: Whether the notification stays on the row.
        """
        text = error[:MAX_ERROR_LENGTH] if error else None
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE notification_deliveries SET state = ?, last_error = ?, "
                "next_attempt_at = ?, updated_at = ?, "
                "attempts = COALESCE(?, attempts + 1), "
                "payload = CASE WHEN ? THEN payload ELSE NULL END WHERE id = ?",
                (
                    state,
                    text,
                    next_attempt_at,
                    self._clock(),
                    attempts,
                    1 if keep_payload else 0,
                    delivery_id,
                ),
            )

    def reschedule(self, delivery_id: int, when: float) -> None:
        """
        Move a held delivery to a later moment (its quiet hours were changed).

        Args:
            delivery_id: The row.
            when: Epoch seconds it is due.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE notification_deliveries SET next_attempt_at = ?, updated_at = ? "
                "WHERE id = ? AND state = 'held'",
                (when, self._clock(), delivery_id),
            )

    def get(self, delivery_id: int) -> Delivery | None:
        """
        Args:
            delivery_id: The row.

        Returns:
            The delivery, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT * FROM notification_deliveries WHERE id = ?", (delivery_id,))
            row = cursor.fetchone()
        return _delivery(row) if row is not None else None

    def due(self, limit: int = 100) -> list[Delivery]:
        """
        List what is ready to be tried: retries whose wait is over and held
        notifications whose quiet hours ended.

        Args:
            limit: Most rows.

        Returns:
            The deliveries, oldest first.
        """
        now = self._clock()
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM notification_deliveries WHERE payload IS NOT NULL "
                "AND state IN ('retrying', 'held') AND next_attempt_at <= ? ORDER BY id LIMIT ?",
                (now, limit),
            )
            rows = cursor.fetchall()
        return [_delivery(row) for row in rows]

    def retry_now(self, delivery_id: int) -> Delivery | None:
        """
        Make a failed, retrying or held delivery ready for the next sweep, and
        forget the attempts it used.

        Args:
            delivery_id: The row.

        Returns:
            The delivery as it now is, or None when it does not exist or kept
            no notification to send.
        """
        now = self._clock()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE notification_deliveries SET state = 'retrying', attempts = 0, "
                "next_attempt_at = ?, updated_at = ? WHERE id = ? AND payload IS NOT NULL "
                "AND state IN ('failed', 'retrying', 'held')",
                (now, now, delivery_id),
            )
            changed = cursor.rowcount
        return self.get(delivery_id) if changed else None

    def recent(
        self, *, limit: int = 100, state: str | None = None, channel: str | None = None
    ) -> list[Delivery]:
        """
        Args:
            limit: Most rows.
            state: Only this state.
            channel: Only this channel instance.

        Returns:
            The deliveries, newest first.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if channel is not None:
            clauses.append("channel = ?")
            params.append(channel)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store._transaction() as cursor:
            cursor.execute(
                f"SELECT * FROM notification_deliveries{where} ORDER BY id DESC LIMIT ?",  # noqa: S608
                (*params, limit),
            )
            rows = cursor.fetchall()
        return [_delivery(row) for row in rows]

    def counts(self) -> dict[str, int]:
        """
        Returns:
            How many deliveries are in each state; every state is present.
        """
        found = dict.fromkeys(STATES, 0)
        with self.store._transaction() as cursor:
            cursor.execute("SELECT state, COUNT(*) FROM notification_deliveries GROUP BY state")
            for state, count in cursor.fetchall():
                found[str(state)] = int(count)
        return found

    def sent_since(self, channel: str, since: float) -> int:
        """
        Args:
            channel: A channel instance.
            since: Epoch seconds.

        Returns:
            How many deliveries to it went out since then.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM notification_deliveries "
                "WHERE channel = ? AND state = 'sent' AND updated_at >= ?",
                (channel, since),
            )
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def purge(self) -> int:
        """
        Forget what is old: delivered rows after a few days, failed ones after
        a month, and the oldest delivered ones beyond :data:`MAX_ROWS`.

        Returns:
            How many rows were deleted.
        """
        now = self._clock()
        deleted = 0
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM notification_deliveries WHERE state = 'sent' AND updated_at < ?",
                (now - KEEP_SENT_SECONDS,),
            )
            deleted += cursor.rowcount
            cursor.execute(
                "DELETE FROM notification_deliveries WHERE state = 'failed' AND updated_at < ?",
                (now - KEEP_FAILED_SECONDS,),
            )
            deleted += cursor.rowcount
            cursor.execute(
                "DELETE FROM notification_deliveries WHERE state = 'sent' AND id NOT IN "
                "(SELECT id FROM notification_deliveries ORDER BY id DESC LIMIT ?)",
                (MAX_ROWS,),
            )
            deleted += cursor.rowcount
        return deleted
