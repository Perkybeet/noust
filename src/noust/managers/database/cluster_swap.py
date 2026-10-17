# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Replacing the data directory of the host's PostgreSQL, and putting it back when that fails.

Two features replace a whole cluster: a point-in-time recovery lays a base backup down and replays
the log over it (:mod:`~noust.managers.database.pitr`), and joining a replica lays down a copy of
another server's cluster (:mod:`~noust.managers.database.replication`). What they share is not
the content but the dangerous part, and it is written once (rule 3):

- stopping the engine and *waiting until its server process is gone*, because ``systemctl stop``
  returns before the shutdown checkpoint is written, and a server still writing it writes it to
  the data directory by name, so a directory renamed or replaced meanwhile gets that write;
- setting the data directory aside by renaming it, never deleting it;
- telling a cluster that is coming up from one that died, by the file the server keeps rather than
  by the unit (Debian's ``postgresql`` unit stays active whatever its clusters do);
- putting the previous directory back, keeping the failed attempt for inspection.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from noust.core.applock import AppBusyError, app_lock
from noust.core.exceptions import DatabaseBackupError, DatabaseEngineError
from noust.core.fs import FileSystem, get_fs
from noust.managers.database.postgres import PostgresManager

__all__ = ["LOCK_KEY", "ClusterSwap", "busy_error", "cluster_lock", "holder_text"]

#: The name the one-at-a-time lock of this area is kept under (``noust.core.applock``): a
#: recovery, a replica joining and the base backups all replace or copy the same cluster, and
#: each would be fine alone and wrong together.
LOCK_KEY = "database-postgresql-cluster"

#: Seconds the engine is given to answer after a restart.
READY_TIMEOUT = 180

#: Seconds between two looks at the engine while waiting for it.
POLL_SECONDS = 2.0

#: Seconds a stopping engine is given to finish its shutdown checkpoint and exit.
STOP_TIMEOUT_SECONDS = 180

#: Seconds a started engine is given to write its process file before a missing one means it
#: died. ``systemctl start postgresql`` succeeds on Debian whatever the cluster does (the unit
#: only wants the clusters'), so the engine's own process is the thing to look at.
STARTUP_GRACE_SECONDS = 20

#: Journal lines an error shows.
JOURNAL_LINES = 40


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def holder_text(busy: AppBusyError) -> str:
    """
    Say what holds the cluster lock.

    Args:
        busy: The refusal.

    Returns:
        The holder's operation, since when and its process, or a generic phrase.
    """
    holder = busy.holder
    return (
        f"{holder.operation} (since {holder.started_at}, pid {holder.pid})"
        if holder
        else "another operation"
    )


def busy_error(busy: AppBusyError, operation: str) -> DatabaseBackupError:
    """
    Turn a refused lock into the error every caller of the cluster lock raises.

    Args:
        busy: The refusal.
        operation: What the caller was about to do.

    Returns:
        The error, naming the holder.
    """
    return DatabaseBackupError(
        f"Cannot start the {operation}: {holder_text(busy)} is still running",
        details="Only one change to the archive, the base backups or the cluster runs at a time. "
        "Wait for it to finish, then retry. The lock is released the moment its process exits.",
    )


@contextmanager
def cluster_lock(operation: str, key: str = LOCK_KEY) -> Iterator[None]:
    """
    Hold the cluster lock for the length of a change to the cluster.

    Reentrant in one thread, like every :func:`~noust.core.applock.app_lock`.

    Args:
        operation: What is being done, as a refused caller will read it.
        key: The lock's name; the PostgreSQL cluster's unless an engine has one of its own.

    Yields:
        Nothing, while the lock is held.

    Raises:
        DatabaseBackupError: When something else holds it.
    """
    try:
        with app_lock(key, operation):
            yield
    except AppBusyError as busy:
        if busy.domain != key:
            raise
        raise busy_error(busy, operation) from busy


class ClusterSwap:
    """
    The engine-level steps of replacing a cluster, with the clock and the sleep injectable.
    """

    def __init__(
        self,
        manager: PostgresManager,
        *,
        fs: FileSystem | None = None,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """
        Args:
            manager: The PostgreSQL manager of the host.
            fs: The filesystem seam. The process-wide one when None.
            clock: The current instant, injected so a test controls it.
            sleep: Waits between two looks at the engine, injected for the same reason.
        """
        self.manager = manager
        self._fs = fs
        self._clock = clock
        self._sleep = sleep

    @property
    def fs(self) -> FileSystem:
        """The filesystem seam, resolved when used so a rehearsal started later is honoured."""
        return self._fs or get_fs()

    def alive(self) -> bool:
        """
        Returns:
            Whether the engine answers a query.
        """
        ok, _output = self.manager._execute_sql("SELECT 1;", timeout=15)
        return ok

    def journal(self) -> str:
        """
        Returns:
            The end of the engine's journal, verbatim, for an error to carry: its unit, and the
            per-cluster units Debian's packages start (``postgresql@16-main``), which are where
            a cluster that did not come up says why.
        """
        result = self.manager._exec(
            [
                "journalctl",
                "-u",
                self.manager.service_unit(),
                "-u",
                "postgresql@*",
                "-n",
                str(JOURNAL_LINES),
                "--no-pager",
            ],
            timeout=60,
        )
        return (result.stdout if result.success else result.stderr).strip()

    def wait_ready(self, timeout: int = READY_TIMEOUT) -> None:
        """
        Wait for the engine to answer after a restart.

        Args:
            timeout: Seconds to wait.

        Raises:
            DatabaseEngineError: When it does not, with the end of its journal.
        """
        deadline = self._clock() + timedelta(seconds=timeout)
        while True:
            if self.alive():
                return
            if self._clock() >= deadline:
                raise DatabaseEngineError(
                    "PostgreSQL did not answer after the restart",
                    details=self.journal(),
                )
            self._sleep(POLL_SECONDS)

    @staticmethod
    def postmaster_running(data: Path) -> bool:
        """
        Say whether the engine's server process is running, by the file it keeps.

        Args:
            data: The data directory.

        Returns:
            Whether ``postmaster.pid`` names a live process.
        """
        try:
            first = (data / "postmaster.pid").read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            return False
        return first.strip().isdigit() and Path(f"/proc/{first.strip()}").exists()

    def stop_engine(self, data: Path) -> None:
        """
        Stop the engine and wait until its server process is really gone.

        Args:
            data: The data directory the engine runs on.

        Raises:
            DatabaseBackupError: When the server process is still there after the wait. Nothing
                was changed.
        """
        self.manager.stop()
        deadline = self._clock() + timedelta(seconds=STOP_TIMEOUT_SECONDS)
        while self.postmaster_running(data):
            if self._clock() >= deadline:
                raise DatabaseBackupError(
                    "PostgreSQL did not stop",
                    details=f"Its server process (see {data / 'postmaster.pid'}) was still "
                    f"running {STOP_TIMEOUT_SECONDS} seconds after the stop. Nothing was changed.",
                )
            self._sleep(POLL_SECONDS / 4)

    def set_aside(self, data: Path, saved: Path) -> None:
        """
        Rename the data directory out of the way, and start the engine again if that fails.

        Args:
            data: The data directory, with the engine stopped.
            saved: Where it goes.

        Raises:
            DatabaseBackupError: When it cannot be renamed (a mount point cannot); nothing was
                changed and the engine runs again.
        """
        try:
            self.fs.rename(data, saved)
        except OSError as exc:
            self.manager.start()
            raise DatabaseBackupError(
                "The data directory could not be set aside",
                details=f"{exc}. A directory that is a mount point cannot be renamed; nothing "
                "was changed.",
            ) from exc

    def wait_until(
        self,
        data: Path,
        sql: str,
        expected: str,
        *,
        timeout: int,
        what: str,
        unfinished: str | None = None,
    ) -> str:
        """
        Wait for a query to give an answer, watching that the server process stays alive.

        Args:
            data: The data directory the engine was started on.
            sql: A query giving one value.
            expected: The value that means done.
            timeout: Seconds to wait.
            what: What is being waited for, for the messages ("the recovery").
            unfinished: What to say when the time runs out, instead of "<What> did not finish in
                N seconds".

        Returns:
            The value (``expected``).

        Raises:
            DatabaseBackupError: When the engine's process is gone or the time runs out, with its
                journal.
        """
        began = self._clock()
        deadline = began + timedelta(seconds=timeout)
        grace = began + timedelta(seconds=STARTUP_GRACE_SECONDS)
        while True:
            ok, output = self.manager._execute_sql(sql, timeout=15)
            if ok and output.strip() == expected:
                return expected
            if not self.postmaster_running(data) and self._clock() >= grace:
                raise DatabaseBackupError(
                    f"PostgreSQL stopped during {what}", details=self.journal()
                )
            if self._clock() >= deadline:
                raise DatabaseBackupError(
                    unfinished
                    or f"{what[:1].upper()}{what[1:]} did not finish in {timeout} seconds",
                    details=self.journal(),
                )
            self._sleep(POLL_SECONDS)

    def put_back(self, data: Path, saved: Path, failed_suffix: str) -> None:
        """
        Undo a failed replacement: keep the attempt, restore the previous directory, start it.

        Args:
            data: The data directory the attempt used.
            saved: Where the previous one was set aside.
            failed_suffix: What the failed attempt is renamed to, appended to ``data``'s name
                (``.noust-failed-recovery``).
        """
        failed = Path(f"{data}{failed_suffix}")
        try:
            self.stop_engine(data)
        except (DatabaseEngineError, DatabaseBackupError) as exc:
            self.manager.logger.warning(
                f"Could not stop the engine after the failure: {exc.message}"
            )
        try:
            if failed.exists():
                # An earlier failed attempt is replaced by this one: only the last is kept.
                self.fs.remove_tree(failed)
            self.fs.rename(data, failed)
            self.fs.rename(saved, data)
            self.manager.start()
        except (OSError, DatabaseEngineError) as exc:
            self.manager.logger.error(
                f"The previous data directory could not be put back: {exc}. It is at {saved}."
            )
