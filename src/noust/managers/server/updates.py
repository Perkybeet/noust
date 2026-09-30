# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Operating system updates: what is pending, what applying would do, and doing it.

:class:`UpdatesManager` is the one implementation. The console's job, the CLI and
the transient systemd unit that both of them start (see
:mod:`noust.managers.server.updates_unit`) all end up calling :meth:`apply`; the
difference between them is only who is watching.

An update can replace Noust itself: the ``noust`` package is in the repository
being updated, and its post-install script restarts the console. That is why the
console never runs the package manager inside its own process. This module does
not know that; it is the same code either way. What it does know is what an
update needs before it starts (nothing else holding the package database, room
on the disk, no half-configured packages), what it would do (the removals a full
upgrade would make, the services it will disturb), and what to write down when
it is over, so the record survives whoever was watching.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from noust.core import paths
from noust.core.exceptions import ValidationError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.errors import (
    ConfirmationRequiredError,
    PreflightError,
    ServerError,
    UnsupportedHostError,
)
from noust.managers.server.host import HostPaths, Platform, detect_platform
from noust.managers.server.pkg import backend_for
from noust.managers.server.pkg.base import (
    UPGRADE_TIMEOUT,
    PackageBackend,
    PackageUpdate,
    PendingUpdates,
    RebootStatus,
    RestartProbe,
    UpdateScope,
    ensure_free,
)

#: Directory of the update records, below the state directory.
RECORDS_DIR_NAME = "os-updates"

#: Records kept; older ones are removed when a new one is written.
RECORDS_KEPT = 50

#: Free space below this on the package cache's filesystem stops an update.
MIN_FREE_BYTES = 1024**3

#: Last output lines a record keeps, for when the journal has been rotated away.
TAIL_LINES = 200

#: Package groups whose update disturbs something the operator runs, by glob.
IMPACT_GROUPS: dict[str, tuple[str, ...]] = {
    "docker": ("docker*", "containerd*", "moby*", "runc"),
    "nginx": ("nginx*",),
    "apache": ("apache2*", "httpd*"),
    "postgresql": ("postgresql*",),
    "mysql": ("mariadb*", "mysql*"),
    "ssh": ("openssh*",),
    "libc": ("libc6", "glibc*"),
    "kernel": ("linux-image*", "kernel", "kernel-core*", "kernel-default*"),
    "systemd": ("systemd*",),
    "noust": ("noust", "wasm", "wasm-cli"),
}

#: What dpkg prints about a configuration file it could not silently replace.
_CONFFILE = re.compile(r"^Configuration file '([^']+)'")

#: What rpm prints when it keeps the operator's file and writes the new one aside.
_RPMNEW = re.compile(r"^warning: (\S+) created as \S+\.rpmnew")


@dataclass(frozen=True)
class ApplyPlan:
    """
    What applying updates would do, worked out before anything is touched.

    Attributes:
        scope: Which updates.
        full: A full upgrade, the only kind allowed to remove packages.
        packages: What would be installed or upgraded.
        removals: What would be removed; the caller has to confirm a non-empty list.
        impact: Groups of software whose update restarts or replaces something
            running (``docker``, ``nginx``, ``kernel``...).
        restarts_console: Noust itself is among the updates, so the console
            restarts when its package is installed. Its session survives; the
            connection does not.
        argv: The exact command that would run, for the operator who wants to see it.
    """

    scope: UpdateScope
    full: bool
    packages: tuple[PackageUpdate, ...]
    removals: tuple[str, ...]
    impact: tuple[str, ...]
    restarts_console: bool
    argv: tuple[str, ...]


@dataclass
class UpdateRecord:
    """
    One run of an update, as written to disk.

    It exists because the update outlives the console that started it and the
    journal that followed it: the result has to be somewhere a restarted console
    can read.

    Attributes:
        id: The update's identifier, also the suffix of its transient unit.
        scope: ``security`` or ``all``.
        full: Whether it was a full upgrade.
        status: ``running``, ``completed`` or ``failed``.
        started_at: ISO 8601 UTC.
        finished_at: ISO 8601 UTC, once over.
        exit_code: The package manager's exit status.
        packages: Names of the packages the update covered.
        reboot_required: A reboot is due once it finished, None while running.
        stale_services: Services running old libraries once it finished.
        conffiles_kept: Configuration files the update left as the operator had them.
        error: What went wrong.
        unit: The transient unit that ran it, when there was one.
        job_id: The console job that started it, when there was one.
        actor: Who asked.
        tail: The last lines of output, verbatim.
    """

    id: str
    scope: str
    full: bool = False
    status: str = "running"
    started_at: str = ""
    finished_at: str | None = None
    exit_code: int | None = None
    packages: list[str] = field(default_factory=list)
    reboot_required: bool | None = None
    stale_services: list[str] = field(default_factory=list)
    conffiles_kept: list[str] = field(default_factory=list)
    error: str | None = None
    unit: str | None = None
    job_id: str | None = None
    actor: str | None = None
    tail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """
        Render the record as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> UpdateRecord:
        """
        Read a record back, ignoring fields a newer Noust added.

        Args:
            data: The parsed JSON.

        Returns:
            The record.
        """
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in data.items() if key in known})


class RecordStore:
    """The update records on disk: one small JSON file per run."""

    def __init__(self, directory: Path | None = None, *, fs: FileSystem | None = None) -> None:
        """
        Args:
            directory: Where the records live; ``os-updates`` under the state
                directory by default.
            fs: Filesystem seam; the process-wide one when omitted.
        """
        self.directory = directory or (paths.state_dir() / RECORDS_DIR_NAME)
        self._fs = fs

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    def path(self, update_id: str) -> Path:
        """
        Locate a record.

        Args:
            update_id: Its identifier.

        Returns:
            The file path.

        Raises:
            ValidationError: The identifier is not one Noust generates, so it
                could not name a file outside the directory.
        """
        if not re.fullmatch(r"[0-9a-f]{8}", update_id):
            raise ValidationError(
                f"Not an update identifier: {update_id!r}",
                "Use one from 'noust server updates history'.",
            )
        return self.directory / f"{update_id}.json"

    def write(self, record: UpdateRecord) -> None:
        """
        Write a record, replacing the previous state of the same run.

        Args:
            record: The record.
        """
        self.fs.make_dir(self.directory, mode=SECRET_DIR_MODE)
        self.fs.write_text(
            self.path(record.id), json.dumps(record.to_dict(), indent=2) + "\n", mode=SECRET_MODE
        )
        self._prune()

    def read(self, update_id: str) -> UpdateRecord | None:
        """
        Read one record.

        Args:
            update_id: Its identifier.

        Returns:
            The record, or None when there is none or it cannot be read.
        """
        try:
            data = json.loads(self.path(update_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return UpdateRecord.from_dict(data) if isinstance(data, dict) else None

    def recent(self, limit: int = 20) -> list[UpdateRecord]:
        """
        List the records, newest first.

        Args:
            limit: How many to return.

        Returns:
            The records that can be read.
        """
        try:
            files = sorted(
                self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
            )
        except OSError:
            return []
        records = []
        for file in files:
            record = self.read(file.stem)
            if record is not None:
                records.append(record)
            if len(records) >= limit:
                break
        return records

    def running(self) -> list[UpdateRecord]:
        """
        List the runs that say they are still going.

        Returns:
            Every record whose status is ``running``.
        """
        return [record for record in self.recent(RECORDS_KEPT) if record.status == "running"]

    def _prune(self) -> None:
        """Remove the oldest records beyond :data:`RECORDS_KEPT`."""
        try:
            files = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        for stale in files[: max(0, len(files) - RECORDS_KEPT)]:
            self.fs.remove(stale)


def conffiles_kept(output: str) -> list[str]:
    """
    Find the configuration files an update left as the operator had them.

    Args:
        output: What the package manager printed.

    Returns:
        The paths, without repeats: dpkg's "Configuration file" notices and
        rpm's ``.rpmnew`` warnings.
    """
    found: list[str] = []
    for line in output.splitlines():
        match = _CONFFILE.match(line) or _RPMNEW.match(line)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def impact_of(packages: list[str]) -> list[str]:
    """
    Name the groups of software an update would disturb.

    Args:
        packages: Package names.

    Returns:
        The keys of :data:`IMPACT_GROUPS` that match, in that table's order.
    """
    return [
        group
        for group, patterns in IMPACT_GROUPS.items()
        if any(fnmatch.fnmatch(name, pattern) for name in packages for pattern in patterns)
    ]


def new_update_id() -> str:
    """
    Generate an update identifier.

    Returns:
        Eight hex digits, safe in a unit name and a file name.
    """
    return uuid.uuid4().hex[:8]


class UpdatesManager:
    """Lists, plans and applies operating system updates through the right package manager."""

    def __init__(
        self,
        *,
        platform: Platform | None = None,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        records: RecordStore | None = None,
        blockers: Callable[[], list[str]] | None = None,
        backend: PackageBackend | None = None,
    ) -> None:
        """
        Args:
            platform: What the machine is; detected once when omitted.
            runner: Command runner; the process-wide one when omitted.
            fs: Filesystem seam; the process-wide one when omitted.
            host: Where the system files are.
            records: Where the runs are written down.
            blockers: Returns the names of operations Noust itself is running
                that an update must not overlap (a deploy, a backup). The
                manager cannot see the console's job queue, so the caller
                passes what it can see.
            backend: A backend to use instead of the one for the platform, for tests.
        """
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()
        self._platform = platform
        self._backend = backend
        self.records = records or RecordStore(fs=fs)
        self._blockers = blockers

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def platform(self) -> Platform:
        """What the machine is, detected on first use."""
        if self._platform is None:
            self._platform = detect_platform(self._runner, self.host)
        return self._platform

    @property
    def backend(self) -> PackageBackend:
        """The package manager backend for this machine."""
        if self._backend is None:
            self._backend = backend_for(
                self.platform, runner=self._runner, fs=self._fs, host=self.host
            )
        return self._backend

    # -- reading --------------------------------------------------------------

    def pending(self) -> PendingUpdates:
        """
        List what is waiting to be installed.

        Returns:
            The pending updates, security ones marked.
        """
        return self.backend.list_updates()

    def restart_probe(self) -> RestartProbe:
        """
        Ask whether a reboot is due and which services run old libraries.

        Returns:
            Both answers.
        """
        return self.backend.restart_probe()

    def reboot_status(self) -> RebootStatus:
        """
        Ask whether a reboot is due.

        Returns:
            The status and why.
        """
        return self.restart_probe().reboot

    # -- planning -------------------------------------------------------------

    def plan(
        self, scope: UpdateScope, *, full: bool = False, pending: PendingUpdates | None = None
    ) -> ApplyPlan:
        """
        Work out what applying updates would do, without touching anything.

        Args:
            scope: Which updates.
            full: A full upgrade, which may remove packages.
            pending: A listing computed a moment ago; computed when omitted.

        Returns:
            The plan.

        Raises:
            UnsupportedHostError: Updates are not managed on this system.
            PreflightError: The package database is half configured.
            ServerError: The listing or the simulation failed.
        """
        if not self.platform.updates_supported:
            raise UnsupportedHostError(
                "Updates cannot be managed on this system", self.platform.why_updates_unsupported()
            )
        listing = pending if pending is not None else self.pending()
        if listing.broken:
            raise PreflightError(
                "The package database is half configured",
                "A previous update was interrupted. Repair it first: "
                "'noust server updates repair'.",
                blockers=["dpkg reports packages that are unpacked but not configured"],
            )
        chosen = [
            package for package in listing.packages if scope is UpdateScope.ALL or package.security
        ]
        names = [package.name for package in chosen]
        # Nothing marked as security is a plan of nothing, not an error: the
        # caller says so in its own words. There is no command to build for it.
        argv = (
            self.backend.upgrade_argv(
                scope, [package.name for package in chosen if package.kind == "package"], full=full
            )
            if chosen
            else []
        )
        removals = self.backend.removals(scope, full=full)
        impact = impact_of(names)
        return ApplyPlan(
            scope=scope,
            full=full,
            packages=tuple(chosen),
            removals=tuple(removals),
            impact=tuple(impact),
            restarts_console="noust" in impact,
            argv=tuple(argv),
        )

    def preflight(self) -> None:
        """
        Refuse to start an update when something else needs the machine.

        Raises:
            UnsupportedHostError: Updates are not managed on this system.
            HostBusyError: Another package manager is running.
            PreflightError: A deploy or backup is running, or the disk is nearly full.
        """
        if not self.platform.updates_supported:
            raise UnsupportedHostError(
                "Updates cannot be managed on this system", self.platform.why_updates_unsupported()
            )
        ensure_free(self.backend)
        blockers: list[str] = []
        if self._blockers is not None:
            blockers += [f"{name} is running" for name in self._blockers()]
        free = shutil.disk_usage(self.host.at("/var")).free
        if free < MIN_FREE_BYTES:
            blockers.append(
                f"Only {free // 1024**2} MiB are free on the disk; an update needs at least "
                f"{MIN_FREE_BYTES // 1024**2} MiB"
            )
        if blockers:
            raise PreflightError(
                "An update cannot start now",
                "Wait for what is running to finish, or free some space.",
                blockers=blockers,
            )

    # -- acting ---------------------------------------------------------------

    def refresh(self, on_line: Callable[[str], None]) -> None:
        """
        Download fresh package metadata.

        Args:
            on_line: Receives what the package manager prints.

        Raises:
            UnsupportedHostError: Updates are not managed on this system.
            HostBusyError: Another package manager is running.
            ServerError: The refresh failed, carrying the tool's own output.
        """
        if not self.platform.updates_supported:
            raise UnsupportedHostError(
                "Updates cannot be managed on this system", self.platform.why_updates_unsupported()
            )
        ensure_free(self.backend)
        self.backend.stream(
            self.backend.refresh_argv(),
            on_line,
            timeout=self.backend.refresh_timeout(),
            message="Could not refresh the package lists",
            details="A repository could not be reached. The output below says which.",
        )
        # The Noust update check read the old index; its cached answer may say
        # the index has not seen a release it now lists.
        from noust.core.update_checker import UpdateChecker

        UpdateChecker.forget()

    def repair(self, on_line: Callable[[str], None]) -> list[str]:
        """
        Finish a half-applied update.

        Args:
            on_line: Receives what the tools print.

        Returns:
            The commands that ran, as text.

        Raises:
            UnsupportedHostError: There is nothing to repair with here.
            HostBusyError: Another package manager is running.
            ServerError: A repair command failed, carrying its output.
        """
        commands = self.backend.repair_commands()
        if not commands:
            raise UnsupportedHostError(
                "There is no repair step on this system",
                "Only dpkg leaves a database half configured.",
            )
        ensure_free(self.backend)
        ran = []
        for argv in commands:
            self.backend.stream(
                argv,
                on_line,
                timeout=900,
                message="A repair step failed",
                details="Fix what the output says and run the repair again.",
            )
            ran.append(" ".join(argv))
        return ran

    def apply(
        self,
        scope: UpdateScope,
        *,
        full: bool = False,
        allow_removals: bool = False,
        on_line: Callable[[str], None],
        update_id: str | None = None,
        job_id: str | None = None,
        unit: str | None = None,
        actor: str | None = None,
    ) -> UpdateRecord:
        """
        Apply updates in this process and write the result down.

        This is what runs inside the transient unit, and what the CLI runs when
        systemd is not there to give it one.

        Args:
            scope: Which updates.
            full: A full upgrade, which may remove packages.
            allow_removals: The caller has read the removal list and accepts it.
            on_line: Receives what the package manager prints, verbatim.
            update_id: The run's identifier; generated when omitted.
            job_id: The console job that asked, for the record.
            unit: The transient unit this runs in, for the record.
            actor: Who asked, for the record.

        Returns:
            The finished record.

        Raises:
            UnsupportedHostError: Updates are not managed on this system.
            HostBusyError: Another package manager is running.
            PreflightError: The disk is nearly full, or a deploy is running.
            ConfirmationRequiredError: The update would remove packages and the
                caller did not allow it; nothing was changed.
            ServerError: The package manager failed; the record says so and the
                error carries its output.
        """
        self.preflight()
        plan = self.plan(scope, full=full)
        if plan.removals and not allow_removals:
            raise ConfirmationRequiredError(
                f"This update would remove {len(plan.removals)} package(s)",
                "Read the list and repeat the request with the removals allowed.",
                required={"removals": list(plan.removals)},
            )
        if not plan.packages and scope is UpdateScope.SECURITY:
            raise ServerError(
                "There are no security updates to install",
                "Refresh the package lists first, or apply all updates.",
            )

        update_id = update_id or new_update_id()
        record = UpdateRecord(
            id=update_id,
            scope=scope.value,
            full=full,
            started_at=datetime.now(timezone.utc).isoformat(),
            packages=[package.name for package in plan.packages],
            unit=unit,
            job_id=job_id,
            actor=actor,
        )
        self.records.write(record)

        tail: list[str] = []

        def relay(line: str) -> None:
            tail.append(line)
            del tail[:-TAIL_LINES]
            on_line(line)

        started = time.monotonic()
        result = self.runner.stream(
            list(plan.argv),
            on_line=relay,
            env=self.backend.env(),
            timeout=UPGRADE_TIMEOUT,
        )
        record.exit_code = result.exit_code
        record.tail = list(tail)
        record.conffiles_kept = conffiles_kept("\n".join(tail))
        record.finished_at = datetime.now(timezone.utc).isoformat()
        if not result.success:
            record.status = "failed"
            record.error = (
                f"The update timed out after {int(time.monotonic() - started)}s"
                if result.timed_out
                else f"The package manager exited with code {result.exit_code}"
            )
            self.records.write(record)
            raise ServerError(
                record.error,
                self._failure_hint(),
                output="\n".join(tail[-40:]),
            )
        probe = self.restart_probe()
        record.status = "completed"
        record.reboot_required = probe.reboot.required
        record.stale_services = list(probe.services)
        self.records.write(record)
        return record

    def _failure_hint(self) -> str:
        """
        Say what to do about a failed update.

        Returns:
            A sentence; on Debian it points at the repair step.
        """
        if self.backend.repair_commands():
            return (
                "Read the output below. If dpkg was interrupted, 'noust server updates repair' "
                "finishes what it started."
            )
        return "Read the output below, fix what it names and apply again."
