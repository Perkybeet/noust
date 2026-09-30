# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The managers of the server, wired once.

The API and the command line both build the same six managers from the same
inputs: a runner, a filesystem, where the system files are, what the machine is.
This is where that happens, so neither of them decides what a manager is given
and the two cannot drift. The pieces are built when they are first asked for:
``noust server logs`` should not detect the package manager.

``blockers`` is the one thing Noust's own state contributes: what it is running
that an update or a reboot would break. The console can see its job queue and
passes it; the command line cannot, and passes nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import NoustStore
from noust.managers.server.clock import ClockManager
from noust.managers.server.facts import FactCache
from noust.managers.server.host import HostPaths, Platform, detect_platform
from noust.managers.server.identity import IdentityManager
from noust.managers.server.journal import JournalReader
from noust.managers.server.power import PowerManager
from noust.managers.server.power_records import PowerRecords
from noust.managers.server.storage import StorageManager
from noust.managers.server.swap import SwapManager
from noust.managers.server.updates import RecordStore, UpdatesManager
from noust.managers.server.updates_unit import UpdateUnit


class ServerContext:
    """The managers of one server, sharing a runner, a filesystem and a platform."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        platform: Platform | None = None,
        blockers: Callable[[], list[str]] | None = None,
        records: RecordStore | None = None,
        cache: FactCache | None = None,
        store: NoustStore | None = None,
    ) -> None:
        """
        Args:
            runner: Command runner; the process-wide one when omitted, resolved
                at each use so ``--dry-run`` applies.
            fs: Filesystem seam; the process-wide one when omitted.
            host: Where the system files are.
            platform: What the machine is; detected on first use when omitted.
            blockers: Returns what Noust is running that an update or a reboot
                must not overlap.
            records: Where update runs are written down.
            cache: The fact cache; a background one when omitted.
            store: The store the power schedule lives in; the process-wide one
                when omitted.
        """
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()
        self._platform = platform
        self.blockers = blockers
        self._records = records
        self.cache = cache or FactCache()
        self._store = store
        self._built: dict[str, object] = {}

    @property
    def platform(self) -> Platform:
        """What the machine is, detected on first use."""
        if self._platform is None:
            self._platform = detect_platform(self._runner, self.host)
        return self._platform

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def records(self) -> RecordStore:
        """The update run records."""
        if self._records is None:
            self._records = RecordStore(fs=self._fs)
        return self._records

    def _once(self, name: str, build: Callable[[], object]) -> object:
        """
        Build a manager the first time it is asked for.

        Args:
            name: Its key.
            build: Builds it.

        Returns:
            The manager.
        """
        if name not in self._built:
            self._built[name] = build()
        return self._built[name]

    @property
    def updates(self) -> UpdatesManager:
        """The updates manager."""
        return self._once(  # type: ignore[return-value]
            "updates",
            lambda: UpdatesManager(
                platform=self.platform,
                runner=self._runner,
                fs=self._fs,
                host=self.host,
                records=self.records,
                blockers=self.blockers,
            ),
        )

    @property
    def unit(self) -> UpdateUnit:
        """The transient unit runner for updates."""
        return self._once(  # type: ignore[return-value]
            "unit",
            lambda: UpdateUnit(
                records=self.records, runner=self._runner, fs=self._fs, host=self.host
            ),
        )

    @property
    def power(self) -> PowerManager:
        """The power manager."""
        return self._once(  # type: ignore[return-value]
            "power",
            lambda: PowerManager(
                runner=self._runner,
                fs=self._fs,
                host=self.host,
                records=PowerRecords(self._store),
                blockers=self.blockers,
            ),
        )

    @property
    def storage(self) -> StorageManager:
        """The storage manager."""
        return self._once(  # type: ignore[return-value]
            "storage",
            lambda: StorageManager(
                runner=self._runner, fs=self._fs, host=self.host, platform=self.platform
            ),
        )

    @property
    def swap(self) -> SwapManager:
        """The swap manager."""
        return self._once(  # type: ignore[return-value]
            "swap",
            lambda: SwapManager(
                runner=self._runner, fs=self._fs, host=self.host, platform=self.platform
            ),
        )

    @property
    def clock(self) -> ClockManager:
        """The clock manager."""
        return self._once(  # type: ignore[return-value]
            "clock",
            lambda: ClockManager(
                runner=self._runner, fs=self._fs, host=self.host, platform=self.platform
            ),
        )

    @property
    def identity(self) -> IdentityManager:
        """The identity manager."""
        return self._once(  # type: ignore[return-value]
            "identity",
            lambda: IdentityManager(
                runner=self._runner, fs=self._fs, host=self.host, platform=self.platform
            ),
        )

    @property
    def journal(self) -> JournalReader:
        """The journal reader."""
        return self._once(  # type: ignore[return-value]
            "journal", lambda: JournalReader(runner=self._runner)
        )
