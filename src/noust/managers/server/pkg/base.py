# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The interface every package manager backend implements, and the shapes it returns.

One adapter per family (apt, dnf, zypper) behind the same methods, so the
manager, the API, the CLI and the console never branch on the distribution: they
ask for pending updates, for whether a reboot is due, for the state of the
automatic updates, and each backend answers in its own dialect.

What is common lives here rather than being copied into three backends: the
environment every probe and action runs in (a translated ``apt`` breaks every
parser), the timeouts, turning a failed command into an error that carries the
tool's own output, and the search for a package manager already running.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.managers.server.errors import HostBusyError, ServerError
from noust.managers.server.host import HostPaths, Platform

#: Deadline of a probe that only asks the machine a question.
PROBE_TIMEOUT = 20

#: Deadline of a simulation or a listing (apt-get -s, check-update, list-updates).
LIST_TIMEOUT = 120

#: Deadline of refreshing the package metadata from the mirrors.
REFRESH_TIMEOUT = 300

#: Deadline of installing one package, such as the unattended-upgrades package.
INSTALL_TIMEOUT = 600

#: Deadline of a full upgrade. An hour is longer than any real one; a hung
#: mirror must not hold the package manager lock for a day.
UPGRADE_TIMEOUT = 3600

#: Deadline of ``needrestart -b``, which walks every process's maps.
NEEDRESTART_TIMEOUT = 60

#: How long apt waits for another package manager to release its lock, in seconds.
LOCK_WAIT_SECONDS = 300


class UpdateScope(str, Enum):
    """Which updates an apply covers."""

    SECURITY = "security"
    ALL = "all"


@dataclass(frozen=True)
class PackageUpdate:
    """
    One pending update.

    Attributes:
        name: Package (or patch) name.
        installed: The version installed now, None for a new dependency.
        candidate: The version that would be installed.
        security: Whether the update comes from a security source.
        kernel: Whether it replaces the kernel image, which needs a reboot.
        origin: Where it comes from, as the package manager states it.
        kind: ``package``, or ``patch`` for an openSUSE patch.
        advisory: The advisory identifier (``USN-...``, ``RHSA-...``) when the
            distribution says one.
        severity: The advisory's severity when it says one.
    """

    name: str
    installed: str | None
    candidate: str
    security: bool = False
    kernel: bool = False
    origin: str = ""
    kind: str = "package"
    advisory: str | None = None
    severity: str | None = None


@dataclass
class PendingUpdates:
    """
    What is waiting to be installed.

    Attributes:
        packages: Every pending update, security ones included.
        kept_back: Packages the resolver holds back (a new dependency it would
            have to install without being allowed to).
        holds: Packages the operator pinned.
        checked_at: When this list was computed, ISO 8601 UTC.
        lists_age_seconds: Age of the package metadata, None when unknown. A
            list computed from month-old metadata answers "nothing pending"
            about a mirror that has moved on.
        security_scope: Whether "security only" is meaningful here.
        broken: The package database is half configured; repair before updating.
        notes: Explanations the operator should read (why security is not
            distinguished, why the metadata is stale).
    """

    packages: list[PackageUpdate] = field(default_factory=list)
    kept_back: list[str] = field(default_factory=list)
    holds: list[str] = field(default_factory=list)
    checked_at: str | None = None
    lists_age_seconds: int | None = None
    security_scope: bool = True
    broken: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def pending(self) -> int:
        """
        How many updates are waiting.

        Returns:
            The number of packages; on openSUSE, where the list also carries the
            patches those packages belong to, patches are not counted twice.
        """
        packages = sum(1 for package in self.packages if package.kind == "package")
        return packages or len(self.packages)

    @property
    def security(self) -> int:
        """How many of them are security updates."""
        return sum(1 for package in self.packages if package.security)


@dataclass(frozen=True)
class RebootStatus:
    """
    Whether the machine has to be restarted for what was installed to take effect.

    Attributes:
        required: A reboot is due.
        packages: The packages whose update asks for it, without repeats.
        reasons: Why, one sentence each (the kernel that runs against the one
            installed, the microcode, the flag a package left).
        since: When the requirement appeared (ISO 8601 UTC), when the system
            says; None otherwise. How long a kernel fix has been installed but
            inactive is what turns a warning into an incident.
        source: Which signal said it, for the operator who wants to check.
    """

    required: bool = False
    packages: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    since: str | None = None
    source: str = ""


@dataclass(frozen=True)
class RestartProbe:
    """
    The reboot requirement and the services running old libraries, from one probe.

    Attributes:
        reboot: Whether a reboot is due, and why.
        services: Units still running code that was replaced on disk.
        available: The tool that answers this exists; False means the answer is
            partial (only the reboot flag), and the console offers to install it.
    """

    reboot: RebootStatus = RebootStatus()
    services: tuple[str, ...] = ()
    available: bool = True


@dataclass(frozen=True)
class AutoUpdates:
    """
    The state of the distribution's own automatic updates.

    Attributes:
        mechanism: ``unattended-upgrades``, ``dnf-automatic``, ``os-update`` or
            ``none``.
        supported: Noust can turn it on and off here.
        installed: The mechanism's package is installed.
        enabled: It is set to run.
        security_only: It applies only security updates; None when not enabled.
        reboots: It is set to reboot the machine by itself. Noust never turns
            this on: a server with clients' applications does not restart on
            its own.
        last_run: When it last ran (ISO 8601 UTC), when it says.
        detail: Why it cannot be changed, or what it is set to, in a sentence.
    """

    mechanism: str = "none"
    supported: bool = False
    installed: bool = False
    enabled: bool = False
    security_only: bool | None = None
    reboots: bool = False
    last_run: str | None = None
    detail: str = ""


class PackageBackend(ABC):
    """
    One family's way of listing, refreshing and applying updates.

    Every method that touches the machine goes through the injected runner or
    filesystem, resolved when it is used rather than when the backend is built:
    ``--dry-run`` swaps them process-wide, and a backend built before the swap
    must still obey it.
    """

    #: Family name for messages and the ``mechanism`` of the API.
    name: str = "none"

    #: Processes that hold or compete for this family's lock.
    LOCK_PROCESSES: tuple[str, ...] = ()
    #: Files the package manager locks with fcntl while it works; a resident
    #: daemon (:data:`RESIDENT_DAEMONS`) is busy only while it holds one.
    LOCK_FILES: tuple[str, ...] = ()
    #: Files a transaction writes its process id into while it runs.
    PID_FILES: tuple[str, ...] = ()

    def __init__(
        self,
        platform: Platform,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
    ) -> None:
        """
        Args:
            platform: What this machine is.
            runner: Command runner; the process-wide one when omitted.
            fs: Filesystem seam; the process-wide one when omitted.
            host: Where the system files are; the real machine when omitted.
        """
        self.platform = platform
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    # -- environment ----------------------------------------------------------

    @abstractmethod
    def env(self) -> dict[str, str]:
        """
        Environment every command of this family runs in.

        Returns:
            Variables merged over the process environment. ``LC_ALL=C`` at the
            least: apt, dnf and zypper translate their output, and every parser
            here reads English.
        """

    # -- listing --------------------------------------------------------------

    @abstractmethod
    def list_updates(self) -> PendingUpdates:
        """
        Compute what is waiting, without changing anything.

        Returns:
            The pending updates, security ones marked.

        Raises:
            ServerError: The listing command failed; the message carries its output.
        """

    @abstractmethod
    def removals(self, scope: UpdateScope, *, full: bool) -> list[str]:
        """
        Say which packages an upgrade would remove, without doing it.

        Args:
            scope: Which updates would be applied.
            full: A full upgrade (``full-upgrade``, ``dist-upgrade``), the only
                kind that may remove a package to resolve a conflict.

        Returns:
            Package names that would be removed; empty for the common case.
        """

    # -- acting ---------------------------------------------------------------

    @abstractmethod
    def refresh_argv(self) -> list[str]:
        """
        Build the command that downloads fresh package metadata.

        Returns:
            The argv.
        """

    @abstractmethod
    def refresh_timeout(self) -> int:
        """
        Deadline for :meth:`refresh_argv`.

        Returns:
            Seconds.
        """

    @abstractmethod
    def upgrade_argv(self, scope: UpdateScope, packages: Sequence[str], *, full: bool) -> list[str]:
        """
        Build the command that applies updates.

        Args:
            scope: Which updates to apply.
            packages: The packages of the security subset, for a family whose
                package manager has no "security only" switch.
            full: Allow the upgrade to remove packages.

        Returns:
            The argv.

        Raises:
            ServerError: The scope does not exist on this system.
        """

    @abstractmethod
    def install_argv(self, packages: Sequence[str]) -> list[str]:
        """
        Build the command that installs packages.

        The one place that knows how each family installs, so the things that
        install a helper (chrony, unattended-upgrades, fail2ban) do not each
        spell ``apt-get install -y``.

        Args:
            packages: Package names, already validated by the caller.

        Returns:
            The argv.

        Raises:
            UnsupportedHostError: Packages are not installed by Noust here.
        """

    @abstractmethod
    def repair_commands(self) -> list[list[str]]:
        """
        Build the commands that finish a half-applied update.

        Returns:
            The argvs, in the order to run them; empty where there is no such thing.
        """

    @abstractmethod
    def clean_cache_argv(self) -> list[str] | None:
        """
        Build the command that empties the downloaded packages.

        Returns:
            The argv, or None where there is nothing to clean.
        """

    # -- reboot, services, automatic updates ------------------------------------

    @abstractmethod
    def restart_probe(self) -> RestartProbe:
        """
        Ask whether a reboot is due and which services run old libraries.

        Returns:
            Both answers from one probe.
        """

    @abstractmethod
    def auto_status(self) -> AutoUpdates:
        """
        Read the state of the automatic updates.

        Returns:
            The state.
        """

    @abstractmethod
    def set_auto(
        self, enabled: bool, security_only: bool, on_line: Callable[[str], None]
    ) -> list[str]:
        """
        Turn the automatic updates on or off.

        Args:
            enabled: The wanted state.
            security_only: Apply only security updates.
            on_line: Receives what the tools print, verbatim.

        Returns:
            One sentence per thing that was done, for the record.

        Raises:
            ServerError: It cannot be changed here.
        """

    # -- shared machinery -------------------------------------------------------

    def stream(
        self,
        argv: Sequence[str],
        on_line: Callable[[str], None],
        *,
        timeout: int,
        message: str,
        details: str = "",
    ) -> CommandResult:
        """
        Run a command, delivering its output line by line, and fail with its words.

        Args:
            argv: The command.
            on_line: Receives each output line.
            timeout: Deadline in seconds.
            message: The sentence for the error when it fails.
            details: What to do about it.

        Returns:
            The result of a command that succeeded.

        Raises:
            ServerError: It failed or timed out; the error's output is the
                command's own, verbatim.
        """
        result = self.runner.stream(argv, on_line=on_line, env=self.env(), timeout=timeout)
        return self.require(result, message, details)

    def probe(self, argv: Sequence[str], *, timeout: int = PROBE_TIMEOUT) -> CommandResult:
        """
        Run a read-only probe in this family's environment.

        Args:
            argv: The command.
            timeout: Deadline in seconds.

        Returns:
            The result, whatever its exit code.
        """
        return self.runner.run(argv, env=self.env(), timeout=timeout)

    @staticmethod
    def require(result: CommandResult, message: str, details: str = "") -> CommandResult:
        """
        Turn a failed result into an error that carries the tool's own words.

        Args:
            result: What the command returned.
            message: The sentence for the error.
            details: What to do about it.

        Returns:
            The result, when it succeeded.

        Raises:
            ServerError: It did not.
        """
        if result.success:
            return result
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        if result.timed_out:
            details = details or "The command did not finish in time; nothing else was changed."
        raise ServerError(f"{message} (exit code {result.exit_code})", details, output=output)

    def lock_holders(self) -> list[str]:
        """
        Find package manager processes already running.

        Returns:
            Their names; empty when the way is clear. psutil is optional: when it
            is missing the answer is "none found", and the package manager's own
            lock is the guard that still holds.
        """
        return running_processes(
            self.LOCK_PROCESSES,
            holding=lock_owner_pids(self.LOCK_FILES, self.PID_FILES),
        )


#: Resident helpers that share a package manager's truncated process name but
#: hold no lock: Ubuntu keeps ``unattended-upgrade-shutdown --wait-for-signal``
#: running at all times, so matching it by name alone refused every update.
IDLE_HELPERS = ("unattended-upgrade-shutdown",)

#: Package daemons that stay running between transactions (PackageKit, and
#: aptdaemon), started over D-Bus and by apt's own hook after every run: one
#: is busy only while it holds the package manager's lock. Matched by name
#: alone, a packagekitd an apt run had just woken refused a fleet update.
RESIDENT_DAEMONS = ("packagekitd", "aptd")

#: The kernel's table of file locks.
PROC_LOCKS = Path("/proc/locks")


def lock_owner_pids(
    lock_files: Sequence[str], pid_files: Sequence[str] = (), *, locks: Path = PROC_LOCKS
) -> set[int] | None:
    """
    Find the processes holding a package manager's locks, by reading, never by locking.

    Args:
        lock_files: Files locked with fcntl while it works (``/proc/locks``
            names their holders).
        pid_files: Files holding the process id of the transaction running.
        locks: The kernel's lock table.

    Returns:
        The holders' process ids; None when a lock is held by a process the
        table does not name (an open file description lock), or the table
        cannot be read, so nothing can be ruled out.
    """
    wanted: set[tuple[int, int, int]] = set()
    for name in lock_files:
        try:
            status = os.stat(name)
        except OSError:
            continue
        wanted.add((os.major(status.st_dev), os.minor(status.st_dev), status.st_ino))
    owners: set[int] = set()
    for name in pid_files:
        try:
            text = Path(name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text.isdigit() and Path(f"/proc/{text}").exists():
            owners.add(int(text))
    if not wanted:
        return owners
    try:
        table = locks.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in table.splitlines():
        # "1: POSIX  ADVISORY  WRITE 1234 08:01:131090 0 EOF"; a blocked
        # waiter is shown as "1: -> POSIX ...".
        fields = line.split()
        if "->" in fields:
            continue
        try:
            pid, device = int(fields[4]), fields[5]
            major, minor, inode = device.split(":")
            key = (int(major, 16), int(minor, 16), int(inode))
        except (IndexError, ValueError):
            continue
        if key in wanted:
            if pid <= 0:
                return None
            owners.add(pid)
    return owners


def running_processes(names: Sequence[str], *, holding: set[int] | None = None) -> list[str]:
    """
    List the running processes whose name is one of ``names``.

    Args:
        names: Process names to look for (prefix match, since the kernel
            truncates them to 15 characters).
        holding: The processes holding the package manager's lock
            (:func:`lock_owner_pids`): each counts, whatever its name, and a
            resident daemon counts only when it is one of them. None counts
            every resident daemon, as when the lock cannot be read.

    Returns:
        The distinct names found, excluding this process itself.
    """
    try:
        import psutil
    except ImportError:
        return []
    found: set[str] = set()
    own = os.getpid()
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = process.info
            name = info.get("name") or ""
            if info.get("pid") == own:
                continue
            if holding and info.get("pid") in holding:
                # Whatever it is called, it holds the lock.
                found.add(name)
                continue
            if not any(name.startswith(prefix) for prefix in names):
                continue
            command = " ".join(info.get("cmdline") or [])
            if any(helper in command for helper in IDLE_HELPERS):
                continue
            if (
                holding is not None
                and name.startswith(RESIDENT_DAEMONS)
                and info.get("pid") not in holding
            ):
                continue
            found.add(name)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return sorted(found)


def ensure_free(backend: PackageBackend) -> None:
    """
    Refuse to start when another package manager is running.

    Args:
        backend: The backend whose lock matters.

    Raises:
        HostBusyError: Another package manager process is running.
    """
    holders = backend.lock_holders()
    if holders:
        raise HostBusyError(
            f"Another package manager is running: {', '.join(holders)}",
            "Wait for it to finish, then try again. Starting a second one would "
            "fail on its lock or leave the package database half configured.",
            holders=holders,
        )


def file_age_seconds(path: Path, now: float) -> int | None:
    """
    Age of a file in seconds.

    Args:
        path: The file or directory.
        now: The current time, ``time.time()``.

    Returns:
        Whole seconds since it was modified, or None when it cannot be read.
    """
    try:
        return max(0, int(now - path.stat().st_mtime))
    except OSError:
        return None


def iso_from_mtime(path: Path) -> str | None:
    """
    Modification time of a file as ISO 8601 UTC.

    Args:
        path: The file.

    Returns:
        The timestamp, or None when the file cannot be read.
    """
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def env_with(base: Mapping[str, str], extra: Mapping[str, str]) -> dict[str, str]:
    """
    Merge two environments, the second winning.

    Args:
        base: The common variables.
        extra: The family's own.

    Returns:
        A new dict.
    """
    merged = dict(base)
    merged.update(extra)
    return merged


#: What every family's commands run with: English output, no terminal.
COMMON_ENV: dict[str, str] = {"LC_ALL": "C", "LANG": "C", "TERM": "dumb"}
