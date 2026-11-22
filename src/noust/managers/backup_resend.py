# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sending again what was made and not sent (item 201).

:class:`~noust.managers.backup_sending.Sender` keeps a copy that did not reach a destination as owed.
This is what sends it again, from three doors that run the same pass:

* ``noust backup resend``, which a timer runs every few minutes on a server (``noust-backup-resend``) and
  the central's own scheduler runs on a machine that has none (:mod:`noust.central.scheduler`): it tries
  what is **due** (a quarter of an hour, an hour or four hours after the last try, then every day);
* the **next scheduled run** of the same thing, which sends its owed copies first (:meth:`Resender.run`
  with ``only``), without waiting for the clock;
* ``noust backup resend --now``, for a person who has just mended the network.

A pass sends a copy at a time, the oldest first, through the same ``push`` its run used (the kind's own
manager, with the retention its schedule says: nothing is re-implemented, rule 3). A destination that
does not answer ends the tries to it for the pass, since the next copy would only wait as long; the
others go on. What is not owed to anybody any more (the copy was deleted, the destination left the
schedule) is forgotten, not tried for ever.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from noust.core import timeutil
from noust.core.exceptions import NoustError
from noust.core.pending_sends import KINDS, PendingSend, PendingSends
from noust.core.runner import CommandRunner
from noust.core.send_claims import claim, claim_directory

__all__ = [
    "ApplicationPusher",
    "DatabasePusher",
    "NoustPusher",
    "Pusher",
    "ResendReport",
    "Resender",
    "VolumePusher",
    "default_pushers",
]

_log = logging.getLogger(__name__)


class Pusher(Protocol):
    """What knows how to send one kind of copy again."""

    def prepare(self, row: PendingSend) -> Callable[[], dict[str, Any]] | None:
        """
        Args:
            row: A copy that is owed to a destination.

        Returns:
            The send, or None when it is not owed to anybody any more (the copy is gone, or the
            destination is no longer one its schedule sends to).
        """
        ...


@dataclass
class ResendReport:
    """
    What a pass did.

    Attributes:
        sent: Copies that arrived.
        failed: Copies that were tried and did not arrive.
        skipped: Copies not tried because the destination had just not answered.
        dropped: Copies forgotten because nothing owes them any more.
        owed: Copies still owed to a destination when it ended.
        busy: Copies left alone because another pass had them in hand, or had just sent or failed them.
    """

    sent: int = 0
    failed: int = 0
    skipped: int = 0
    dropped: int = 0
    owed: int = 0
    busy: int = 0

    def to_dict(self) -> dict[str, int]:
        """
        Returns:
            The report as plain data.
        """
        return {
            "sent": self.sent,
            "failed": self.failed,
            "skipped": self.skipped,
            "dropped": self.dropped,
            "owed": self.owed,
            "busy": self.busy,
        }


def _entry(destinations: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    """
    Args:
        destinations: The destinations a schedule or a policy sends to.
        name: A destination.

    Returns:
        Its entry (with the retention it asks for), or None when the schedule does not send to it.
    """
    return next((entry for entry in destinations if entry.get("name") == name), None)


def _retention(entry: Mapping[str, Any]) -> dict[str, Any]:
    """
    Args:
        entry: A destination's entry in a schedule.

    Returns:
        The retention arguments every kind's ``push`` takes.
    """
    return {
        "retention_count": entry.get("retention_count"),
        "retention_days": entry.get("retention_days"),
        "retention_gb": entry.get("retention_gb"),
    }


class ApplicationPusher:
    """Sends an application's backup again, as ``run_schedule`` sent it."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner

    def prepare(self, row: PendingSend) -> Callable[[], dict[str, Any]] | None:
        from noust.core.store import get_store
        from noust.managers.backup_destinations import BackupDestinationManager
        from noust.managers.backup_manager import BackupManager

        schedule = get_store().get_backup_schedule(row.owner)
        entry = _entry(schedule.destinations, row.destination) if schedule else None
        if entry is None:
            return None
        backups = BackupManager(verbose=False, runner=self._runner)
        metadata = backups.get_backup(row.item_id)
        if metadata is None:
            return None
        manager = BackupDestinationManager(runner=self._runner)
        return lambda: manager.push(
            metadata, row.destination, backup_manager=backups, **_retention(entry)
        )


class VolumePusher:
    """
    Sends the data of an application again, as its policy sent it.

    Args:
        manager: The manager of the data's backups to send through; a new one by default.
        runner: The runner a new manager uses.
    """

    def __init__(self, manager: Any = None, *, runner: CommandRunner | None = None) -> None:
        self._manager = manager
        self._runner = runner

    def prepare(self, row: PendingSend) -> Callable[[], dict[str, Any]] | None:
        from noust.managers.volume_backups import VolumeBackupManager

        manager = self._manager or VolumeBackupManager(runner=self._runner)
        policy = manager.records.policy(row.owner)
        entry = _entry(policy.destinations, row.destination) if policy else None
        if entry is None or manager.get_backup(row.item_id) is None:
            return None
        return lambda: manager.push(
            row.item_id, row.destination, scheduled=True, **_retention(entry)
        )


class DatabasePusher:
    """
    Sends a database's dump again, as its policy sent it.

    Args:
        backups: The manager of the dumps to send through; a new one by default.
    """

    def __init__(self, backups: Any = None) -> None:
        self._backups = backups

    def prepare(self, row: PendingSend) -> Callable[[], dict[str, Any]] | None:
        from noust.managers.database.backups import DatabaseBackups
        from noust.managers.database.scope import ALL_DATABASES
        from noust.managers.database.service import DatabaseService

        engine, _, file_name = row.item_id.partition(":")
        _, _, database = row.owner.partition("/")
        backups = self._backups or DatabaseBackups(DatabaseService())
        policy = backups.records.policy(engine, database) or backups.records.policy(
            engine, ALL_DATABASES
        )
        entry = _entry(policy.destinations, row.destination) if policy else None
        if entry is None or not any(
            view.name == file_name and str(view.info.engine) == engine
            for view in backups.list_dumps()
        ):
            return None
        return lambda: backups.push(
            engine, file_name, row.destination, scheduled=True, **_retention(entry)
        )


class NoustPusher:
    """
    Sends a bundle of Noust's own state again, as its schedule sent it.

    Args:
        manager: The manager of the bundles to send through; a new one by default.
        runner: The runner a new manager uses.
    """

    def __init__(self, manager: Any = None, *, runner: CommandRunner | None = None) -> None:
        self._manager = manager
        self._runner = runner

    def prepare(self, row: PendingSend) -> Callable[[], dict[str, Any]] | None:
        from noust.managers.self_backup import SelfBackupManager

        manager = self._manager or SelfBackupManager(runner=self._runner)
        schedule = manager.records.get()
        entry = _entry(schedule.destinations, row.destination) if schedule else None
        if entry is None or all(item.name != row.item_id for item in manager.entries()):
            return None
        return lambda: manager.push(row.item_id, row.destination, **_retention(entry))


def default_pushers(runner: CommandRunner | None = None) -> dict[str, Pusher]:
    """
    Args:
        runner: The one way a process is run; the process-wide one when None.

    Returns:
        The pusher of each kind of copy, each asking its own manager.
    """
    return {
        "app": ApplicationPusher(runner),
        "volume": VolumePusher(runner=runner),
        "database": DatabasePusher(),
        "noust": NoustPusher(runner=runner),
    }


class Resender:
    """
    Make the pass that sends what is owed.

    Args:
        sender: Decides what a send that fails becomes; the real one by default.
        pending: What is owed; the store's by default.
        pushers: The pusher of each kind of copy; the managers' by default.
        runner: The runner the default pushers use.
    """

    def __init__(
        self,
        *,
        sender: Any = None,
        pending: PendingSends | None = None,
        pushers: Mapping[str, Pusher] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._sender = sender
        self._pending = pending
        self._pushers = dict(pushers) if pushers is not None else None
        self._runner = runner

    @property
    def sender(self) -> Any:
        """Decides what a send that fails becomes."""
        if self._sender is None:
            from noust.managers.backup_sending import Sender

            self._sender = Sender(pending=self.pending)
        return self._sender

    @property
    def pending(self) -> PendingSends:
        """What is owed."""
        if self._pending is None:
            self._pending = PendingSends()
        return self._pending

    @property
    def pushers(self) -> dict[str, Pusher]:
        """The pusher of each kind of copy."""
        if self._pushers is None:
            self._pushers = default_pushers(self._runner)
        return self._pushers

    def run(self, *, force: bool = False, only: tuple[str, str] | None = None) -> ResendReport:
        """
        Send what is owed.

        Args:
            force: Try everything that is owed, not only what is due.
            only: ``(kind, owner)``: try what is owed for that one thing, due or not. This is how a
                scheduled run sends its own owed copies before it takes a new one.

        Returns:
            What the pass did. Nothing it tries can raise: a send that fails stays owed.
        """
        report = ResendReport()
        dropped_from: set[str] = set()
        now: datetime = timeutil.utc_now()
        if only is not None:
            rows = self.pending.for_owner(*only)
        elif force:
            rows = self.pending.all()
        else:
            rows = self.pending.due(now)
        silent: set[str] = set()
        directory = claim_directory(self.pending.store)
        for row in rows:
            if row.destination in silent:
                report.skipped += 1
                continue
            with ExitStack() as held:
                try:
                    ours = held.enter_context(
                        claim(directory, row.kind, row.item_id, row.destination)
                    )
                except OSError as exc:
                    # A claim that cannot be made (a directory that cannot be written) is a copy that
                    # cannot be looked at: it stays owed, and the others go.
                    _log.warning(
                        "Could not take %s %s in hand to send it again: %s",
                        row.kind,
                        row.item_id,
                        exc,
                    )
                    report.failed += 1
                    continue
                if not ours:
                    report.busy += 1
                    continue
                # Read again now that it is in hand: another pass may have delivered it, or failed it
                # and put the next try ahead, since this one listed it.
                current = self.pending.get(row.kind, row.item_id, row.destination)
                if current is None or not (force or only is not None or current.is_due(now)):
                    report.busy += 1
                    continue
                self._send_again(current, report, silent, dropped_from)
        for destination in sorted(dropped_from):
            self.sender.tidy(destination)
        left = self.pending.all()
        report.owed = len(left)
        for destination in sorted({row.destination for row in left}):
            self.sender.remind(destination)
        return report

    def _send_again(
        self, row: PendingSend, report: ResendReport, silent: set[str], dropped_from: set[str]
    ) -> None:
        """
        Try one owed copy that this pass has in hand.

        Args:
            row: What is owed, as it is now.
            report: What the pass has done, which this adds to.
            silent: The destinations that did not answer in this pass, which this adds to.
            dropped_from: The destinations a copy was forgotten for, which this adds to.
        """
        try:
            send = self._prepare(row)
        except (NoustError, OSError, sqlite3.Error) as exc:
            # One copy that cannot be looked at (a sidecar that cannot be read) is not the end of
            # the pass: it stays owed, and the others go.
            _log.warning("Could not prepare %s %s to be sent again: %s", row.kind, row.item_id, exc)
            report.failed += 1
            return
        if send is None:
            self.pending.delivered(row.kind, row.item_id, row.destination)
            report.dropped += 1
            dropped_from.add(row.destination)
            return
        outcome = self.sender.retry(row, send)
        if outcome.ok:
            report.sent += 1
            return
        report.failed += 1
        current = self.pending.get(row.kind, row.item_id, row.destination)
        if current is None or not current.permanent:
            # The next copy would wait as long as this one did.
            silent.add(row.destination)

    def _prepare(self, row: PendingSend) -> Callable[[], dict[str, Any]] | None:
        """
        Args:
            row: What is owed.

        Returns:
            The send, or None when nothing owes it any more or its kind has no pusher.
        """
        if row.kind not in KINDS or row.kind not in self.pushers:
            return None
        return self.pushers[row.kind].prepare(row)
