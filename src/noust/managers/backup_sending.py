# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What becomes of a send that did not arrive: the one place that decides it (item 201).

Every scheduled run of every kind of backup (an application's, its data's, a database's and Noust's own)
sends its copy to the destinations of its schedule, and used to treat a destination that did not take
it as a failure of the run: one notice for each, the run's unit failed (so the monitor said it again),
and nothing ever sent the copy again, which was left on this server alone. A night of bad network
turned nine such sends into nineteen messages and nine copies that were nowhere else.

A send that fails here is **owed**, not failed:

* The copy stays where it is and the destination it did not reach is written down
  (:class:`~noust.core.pending_sends.PendingSends`), with what it said, verbatim.
* It is tried again after a quarter of an hour, an hour and four hours, and then every day
  (:data:`DEFERRED_DELAYS`, :data:`LATER_DELAY`), by :mod:`noust.managers.backup_resend`, and by the next
  scheduled run of the same thing before it takes its new copy. Each of those tries is itself patient
  (:mod:`noust.managers.destination_patience`).
* It is **said when it matters**: when a deferred try fails as well, or at once when what the destination
  refused is of the kind waiting does not mend (a credential, a quota, a bucket that is not there). And it
  is said **once for a destination**, naming the copies that wait, not once for each application:
  another failure to the same destination within the window (``monitor.backup_missing_renotify_hours``, the
  setting that already says how often an outage is repeated) joins the message that went out.
* When everything owed to a destination has arrived, one message says so and answers the first.
* The destination watch (:mod:`noust.monitor.watch_destinations`) and this say the same trouble once
  between them: each is silent while the other has spoken within the window.

If the store cannot be written the send is said as it used to be, at once: an owing nobody can remember is
not one to wait on.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from noust.core import timeutil
from noust.core.config import DEFAULT_BACKUP_DIR, Config, resolve_backup_directory
from noust.core.exceptions import ConfigError, DependencyError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.notification_state import NotificationState
from noust.core.notifications.composers import compose_backup_upload_failed
from noust.core.notifications.composers_sends import (
    compose_backup_sends_failed,
    compose_backup_sends_recovered,
)
from noust.core.notifications.context import NotificationContext
from noust.core.pending_sends import PendingSend, PendingSends
from noust.core.redact import scrubber_for
from noust.managers import destination_patience as patience
from noust.managers.backup_failures import record_failure

if TYPE_CHECKING:
    from noust.core.notifications.model import Notification

__all__ = [
    "DEFERRED_DELAYS",
    "LATER_DELAY",
    "SENDS_SCOPE",
    "WATCH_SCOPE",
    "SendOutcome",
    "Sender",
    "as_moment",
    "install_resend_timer",
    "is_permanent_failure",
    "pending_line",
]

_log = logging.getLogger(__name__)

#: How long after a failed send each deferred try is, in seconds: a quarter of an hour rides out a router
#: that restarts or a provider's blip, an hour a longer one, four hours the maintenance window of a night.
DEFERRED_DELAYS = (15 * 60, 60 * 60, 4 * 60 * 60)

#: After those, a copy that is still owed is tried every day, until it is sent or it is old enough that a
#: retention may let it go (:mod:`noust.core.backup_hold`): the next scheduled run tries it first as well.
LATER_DELAY = 24 * 60 * 60

#: Where this remembers what it has said about each destination, in ``notification_state``.
SENDS_SCOPE = "sends"

#: Where the destination watch remembers what it has said, which this reads and never writes.
WATCH_SCOPE = "destinations"

#: How often an outage is said again when the configuration does not say: one a day, like the watch.
DEFAULT_WINDOW_HOURS = 24.0

#: The refusals that are made before any byte moves: a destination that does not exist and one that
#: is not encrypted when backups must leave encrypted. Waiting does not mend them.
_REFUSALS = frozenset(
    {
        "backup_destinations.such_backup_destination",
        "backup_destinations.encrypted_backups_leave_server",
    }
)


def install_resend_timer() -> None:
    """
    Make the timer that sends again what is owed, when the first copy is.

    Best effort: a timer that cannot be made costs the retry that waits a quarter of an hour, never the
    run that found a copy could not be sent; the next scheduled run sends what is owed first all the same.
    """
    from noust.managers.backup_scheduler import BackupScheduler

    try:
        BackupScheduler().ensure_resend_timer()
    except (NoustError, OSError) as exc:
        _log.warning("The timer that sends again what is owed could not be made: %s", exc)


def is_permanent_failure(error: Exception) -> bool:
    """
    Say whether waiting would mend a failed send.

    Args:
        error: What the send raised.

    Returns:
        True for a refusal that is made before anything is sent, a configuration that is wrong, and
        what the destination said that is of the kind waiting does not mend
        (:func:`~noust.managers.destination_patience.is_permanent`).
    """
    if isinstance(error, (DependencyError, ConfigError, ValidationError)):
        return True
    coded = getattr(error, "coded", None)
    if getattr(coded, "code", None) in _REFUSALS:
        return True
    return patience.is_permanent(str(error))


def pending_line(name: str, outcome: Mapping[str, Any]) -> str:
    """
    Say in words that a copy was not sent and is owed, for the commands that run a schedule.

    Args:
        name: The destination.
        outcome: What the run reported for it (:meth:`SendOutcome.entry`), with ``pending`` set.

    Returns:
        ``Not sent to nas yet: it is kept on this server and is sent again at 2026-11-17 02:15 UTC``.
    """
    when = as_moment(outcome.get("next_attempt_at"))
    again = f" and is sent again at {when:%Y-%m-%d %H:%M} UTC" if when is not None else ""
    return f"Not sent to {name} yet: it is kept on this server{again}"


def delay_after(attempts: int) -> timedelta:
    """
    Args:
        attempts: How many deferred tries a copy has had.

    Returns:
        How long until the next.
    """
    seconds = DEFERRED_DELAYS[attempts] if attempts < len(DEFERRED_DELAYS) else LATER_DELAY
    return timedelta(seconds=seconds)


@dataclass(frozen=True)
class SendOutcome:
    """
    How one send ended.

    Attributes:
        ok: Whether the copy reached the destination.
        deferred: Whether it did not and is owed (it will be tried again). False with ``ok`` False means
            the owing could not be kept and the failure was said as it used to be.
        summary: What the destination manager reported, when it went through.
        error: What went wrong, ``message`` then ``Details``, when it did not.
        next_attempt_at: When it is tried next, when it is owed.
        permanent: Whether what the destination said is of the kind waiting does not mend: the
            run's own status says somebody has to look at it, as it did when a send failed.
    """

    ok: bool
    deferred: bool = False
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    next_attempt_at: datetime | None = None
    permanent: bool = False

    def entry(self) -> dict[str, Any]:
        """
        Returns:
            The outcome as a run reports it for its destination: ``{"ok": True, ...}`` or the error
            and, when it is owed, that it is ``pending`` and when it is tried next.
        """
        if self.ok:
            return {"ok": True, **self.summary}
        entry: dict[str, Any] = {"ok": False, "error": self.error}
        if self.deferred:
            entry["pending"] = True
            entry["permanent"] = self.permanent
            if self.next_attempt_at is not None:
                entry["next_attempt_at"] = self.next_attempt_at.astimezone(timezone.utc).isoformat(
                    timespec="seconds"
                )
        return entry


class Sender:
    """
    Decide what a send that did not arrive becomes, and say what is worth saying.

    Args:
        pending: Where the owed copies are kept; the store's by default.
        config: The configuration the notices are composed and delivered with.
        notify: Delivers a notification; the notifier's by default.
        state: The memory of what has been said; the store's ``notification_state`` by default.
        kept_in: The directory the copies are in meanwhile, for the notice; the configured backup
            directory by default.
    """

    def __init__(
        self,
        *,
        pending: PendingSends | None = None,
        config: Config | None = None,
        notify: Callable[[Notification], None] | None = None,
        state: NotificationState | None = None,
        kept_in: str | None = None,
    ) -> None:
        self._pending = pending
        self._config = config
        self._notify = notify
        self._state = state
        self._kept_in = kept_in

    # ------------------------------------------------------------ collaborators

    @property
    def pending(self) -> PendingSends:
        """Where the owed copies are kept."""
        if self._pending is None:
            self._pending = PendingSends()
        return self._pending

    @property
    def config(self) -> Config:
        """The configuration."""
        if self._config is None:
            self._config = Config()
        return self._config

    @property
    def state(self) -> NotificationState:
        """What has been said, which survives a restart."""
        if self._state is None:
            self._state = NotificationState(clock=lambda: self._now().timestamp())
        return self._state

    @staticmethod
    def _now() -> datetime:
        """
        Returns:
            The moment, from the one clock (:func:`noust.core.timeutil.utc_now`), which a test moves.
        """
        return timeutil.utc_now()

    def _deliver(self, notification: Notification) -> None:
        """
        Args:
            notification: What to say. A channel that cannot be reached is the notifier's to skip.
        """
        if self._notify is not None:
            self._notify(notification)
            return
        from noust.core.notifier import Notifier

        Notifier(self.config).notify(notification)

    def _window(self) -> float:
        """
        Returns:
            Seconds within which what was said is not said again.
        """
        hours = float(
            self.config.get("monitor.backup_missing_renotify_hours", DEFAULT_WINDOW_HOURS)
        )
        return max(hours, 0.0) * 3600

    # ------------------------------------------------------------------ sending

    def send(
        self,
        kind: str,
        item_id: str,
        owner: str,
        destination: str,
        *,
        copy_created_at: datetime | str,
        push: Callable[[], dict[str, Any]],
        defer: bool = True,
    ) -> SendOutcome:
        """
        Send a copy to a destination, and keep it as owed when it does not arrive.

        Args:
            kind: ``app``, ``database``, ``volume`` or ``noust``.
            item_id: The copy's identifier as its manager gives it.
            owner: What it is a copy of.
            destination: Where it goes.
            copy_created_at: When the copy was made, which says how long it may be kept for: a
                moment, or the text its manager records (a time with no zone is this server's own).
            push: Sends it; raises a :class:`~noust.core.exceptions.NoustError` when it does not go.
            defer: Keep a copy that does not arrive as owed (a scheduled run). False for a run somebody
                asked for by hand, which reports the failure to whoever asked and owes nothing.

        Returns:
            How it ended; a send that failed is ``deferred`` and no error leaves this.
        """
        created = as_moment(copy_created_at) or self._now()
        return self._attempt(kind, item_id, owner, destination, created, push, None, defer)

    def retry(self, row: PendingSend, push: Callable[[], dict[str, Any]]) -> SendOutcome:
        """
        Try an owed copy again.

        Args:
            row: What is owed.
            push: Sends it.

        Returns:
            How it ended; a copy that fails again stays owed.
        """
        created = _moment(row.copy_created_at) or self._now()
        return self._attempt(
            row.kind, row.item_id, row.owner, row.destination, created, push, row, True
        )

    def _attempt(
        self,
        kind: str,
        item_id: str,
        owner: str,
        destination: str,
        created: datetime,
        push: Callable[[], dict[str, Any]],
        owed: PendingSend | None,
        defer: bool,
    ) -> SendOutcome:
        """
        One try, from either door.

        Args:
            kind: The kind of copy.
            item_id: Its identifier.
            owner: What it is a copy of.
            destination: Where it goes.
            created: When it was made.
            push: Sends it.
            owed: The row, when it was already owed.
            defer: Whether a failure is kept as owed.

        Returns:
            How it ended.
        """
        try:
            summary = push()
        except NoustError as exc:
            if not defer:
                record_failure("upload", owner, str(exc), destination=destination)
                return SendOutcome(ok=False, deferred=False, error=str(exc))
            return self._failed(kind, item_id, owner, destination, created, owed, exc)
        self._arrived(kind, item_id, destination)
        return SendOutcome(ok=True, summary=summary)

    def _arrived(self, kind: str, item_id: str, destination: str) -> None:
        """
        End what was owed for a copy that reached a destination, and say so when it settles it.

        Args:
            kind: The kind of copy.
            item_id: Its identifier.
            destination: Where it arrived.
        """
        try:
            row = self.pending.delivered(kind, item_id, destination)
            if row is None:
                return
            told = self._told(destination)
            if told is not None:
                told["delivered"] = int(told.get("delivered", 0)) + 1
                self._remember(destination, told)
            self._settled(destination)
        except (sqlite3.Error, OSError) as exc:
            # The copy is where it was meant to be; what could not be written is only the memory of it.
            _log.warning(
                "Could not write down that %s %s reached %s: %s", kind, item_id, destination, exc
            )

    def _failed(
        self,
        kind: str,
        item_id: str,
        owner: str,
        destination: str,
        created: datetime,
        owed: PendingSend | None,
        error: NoustError,
    ) -> SendOutcome:
        """
        Keep a copy that did not arrive as owed, and say it when it is time to.

        Args:
            kind: The kind of copy.
            item_id: Its identifier.
            owner: What it is a copy of.
            destination: Where it did not go.
            created: When the copy was made.
            owed: The row, when it was already owed.
            error: What went wrong.

        Returns:
            The outcome: deferred, or not when the owing could not be kept.
        """
        # What is kept and said is scrubbed once more, whatever the destination manager did: a row
        # and a notice outlive the call.
        text = scrubber_for(owner).scrub(str(error))
        permanent = is_permanent_failure(error)
        failure_id = record_failure("upload", owner, text, destination=destination)
        now = self._now()
        attempts_after = 0 if owed is None else owed.attempts + 1
        next_at = now + delay_after(attempts_after)
        row: PendingSend | None
        try:
            if owed is None:
                row = self.pending.defer(
                    kind,
                    item_id,
                    destination,
                    owner=owner,
                    copy_created_at=created,
                    reason=text,
                    permanent=permanent,
                    failure_id=failure_id,
                    now=now,
                    next_attempt_at=next_at,
                )
            else:
                row = self.pending.failed_again(
                    kind,
                    item_id,
                    destination,
                    reason=text,
                    permanent=permanent,
                    now=now,
                    next_attempt_at=next_at,
                    failure_id=failure_id,
                )
        except (sqlite3.Error, OSError) as exc:
            _log.warning("Could not keep %s %s as owed to %s: %s", kind, item_id, destination, exc)
            return self._unkept(owner, destination, text, failure_id)
        Logger(verbose=False).warning(
            f"{owner}: {item_id} could not be sent to {destination}; it stays on this server and "
            f"is sent again at {next_at:%Y-%m-%d %H:%M} UTC. {_first_line(text)}"
        )
        if owed is None:
            install_resend_timer()
        if row is None or row.permanent or row.attempts >= 1:
            self._notice(destination)
        return SendOutcome(
            ok=False, deferred=True, error=text, next_attempt_at=next_at, permanent=permanent
        )

    def _unkept(
        self, owner: str, destination: str, text: str, failure_id: int | None
    ) -> SendOutcome:
        """
        Say a failure at once, as it was said before copies could be owed.

        Args:
            owner: What the copy is a copy of.
            destination: Where it did not go.
            text: What went wrong.
            failure_id: The record of the whole output, if one was kept.

        Returns:
            The outcome, not deferred: the run treats it as the failure it is.
        """
        ctx = NotificationContext.from_config(self.config)
        self._deliver(
            compose_backup_upload_failed(owner, destination, text, ctx, failure_id=failure_id)
        )
        return SendOutcome(ok=False, deferred=False, error=text)

    # ------------------------------------------------------------------- saying

    def remind(self, destination: str) -> None:
        """
        Say again what is still owed to a destination, when the window since it was said has passed.

        Args:
            destination: The destination.
        """
        self._notice(destination)

    def _told(self, destination: str) -> dict[str, Any] | None:
        """
        Args:
            destination: A destination.

        Returns:
            What was said about it and not closed (``at`` and ``delivered``), or None.
        """
        raw = self.state.get_json(SENDS_SCOPE, destination)
        if not isinstance(raw, dict) or not isinstance(raw.get("at"), (int, float)):
            return None
        return dict(raw)

    def _remember(self, destination: str, told: dict[str, Any]) -> None:
        """
        Args:
            destination: A destination.
            told: What to remember of what was said.
        """
        self.state.put_json(SENDS_SCOPE, destination, told)

    def _watch_said(self, destination: str) -> float | None:
        """
        Args:
            destination: A destination.

        Returns:
            When the destination watch said it did not answer and has not said it does, or None.
        """
        said = self.state.get(WATCH_SCOPE, destination)
        try:
            return float(said) if said is not None else None
        except ValueError:
            return None

    def _notice(self, destination: str) -> None:
        """
        Say what is owed to a destination, once within the window.

        A message that has been said covers what fails to that destination meanwhile, and so does the
        watch's own: the rows are marked as covered and nothing more is sent.

        Args:
            destination: The destination.
        """
        try:
            owed = self.pending.for_destination(destination)
            worth = [row for row in owed if row.permanent or row.attempts >= 1]
            if not worth:
                return
            now = self._now()
            stamp = now.timestamp()
            window = self._window()
            told = self._told(destination)
            watched = self._watch_said(destination)
            covered = (told is not None and stamp - float(told["at"]) < window) or (
                watched is not None and stamp - watched < window
            )
            if not covered:
                self._deliver(self._failed_notice(destination, owed))
                self._remember(
                    destination,
                    {"at": stamp, "delivered": int(told.get("delivered", 0)) if told else 0},
                )
            self.pending.mark_noticed([row.key for row in owed], now)
        except (sqlite3.Error, OSError) as exc:
            _log.warning("Could not tell whether %s has been said: %s", destination, exc)

    def _failed_notice(self, destination: str, owed: list[PendingSend]) -> Notification:
        """
        Args:
            destination: The destination.
            owed: Everything that is owed to it, the oldest first.

        Returns:
            The notice that names them.
        """
        latest = max(owed, key=lambda row: row.last_attempt_at)
        owners = list(dict.fromkeys(row.owner for row in owed))
        rounds = max(
            (1 + row.attempts) * (1 if row.permanent else patience.WORK.attempts) for row in owed
        )
        next_try = min(_moment(row.next_attempt_at) or self._now() for row in owed)
        ctx = NotificationContext.from_config(self.config)
        return compose_backup_sends_failed(
            destination,
            len(owed),
            owners,
            latest.reason,
            ctx,
            tried=rounds,
            next_try=next_try,
            kept_in=self._kept_in
            or str(
                resolve_backup_directory(
                    self.config.get("backup.directory"), default=DEFAULT_BACKUP_DIR
                )
            ),
            failure_id=latest.failure_id,
        )

    def _settled(self, destination: str) -> None:
        """
        Say that what was owed to a destination has arrived, when it was said that it was owed.

        Args:
            destination: The destination.
        """
        if self.pending.for_destination(destination):
            return
        told = self._told(destination)
        if told is None:
            return
        ctx = NotificationContext.from_config(self.config)
        self._deliver(
            compose_backup_sends_recovered(destination, int(told.get("delivered", 0)), ctx)
        )
        self.state.delete(SENDS_SCOPE, destination)

    # ----------------------------------------------------------------- forgetting

    def tidy(self, destination: str) -> None:
        """
        Forget what was said about a destination when nothing is owed to it any more.

        What was owed went because nobody owes it (the copy was deleted, the schedule stopped sending
        there), not because it arrived, so there is nothing to say: only the memory goes, so that
        the next trouble is a new message and not one the old window covers.

        Args:
            destination: The destination.
        """
        try:
            if not self.pending.for_destination(destination):
                self.state.delete(SENDS_SCOPE, destination)
        except (sqlite3.Error, OSError) as exc:
            _log.warning("Could not tidy what was said about %s: %s", destination, exc)

    def forget_destination(self, destination: str) -> None:
        """
        Forget what was owed to a destination that was removed, and what was said about it.

        Args:
            destination: Its name.
        """
        self.pending.forget_destination(destination)
        self.state.delete(SENDS_SCOPE, destination)


def as_moment(value: datetime | str | None) -> datetime | None:
    """
    Read the moment a copy was made as its manager records it.

    Args:
        value: A datetime, or ISO 8601 text; text with no zone was written by this server in its own.

    Returns:
        It as an aware datetime, or None when there is none or it cannot be read.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value.astimezone() if value.tzinfo is None else value


def _moment(text: str | None) -> datetime | None:
    """
    Args:
        text: A time of ``pending_sends``, always UTC.

    Returns:
        It as an aware datetime, or None when there is none or it cannot be read.
    """
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _first_line(text: str) -> str:
    """
    Args:
        text: What a destination said.

    Returns:
        The last line of it that says anything, bounded: the one a journal line is made of.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:200]
