# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Rebooting and shutting the server down, now or later, and knowing it came back.

A reboot is not a job. A job lives in the process the reboot kills, so it could
never report its own end; what can is a row written before and read after, which
is :class:`~noust.managers.server.power_records.PowerRecords`. ``shutdown -r
+N`` does the waiting (systemd keeps the schedule, survives the console
restarting and can be cancelled by anyone with a root shell), and this module
keeps the record of who asked and from which boot.

Nothing here reboots the machine because an update said so. That is a decision
of the operator, taken with the preflight checks in front of them: a reboot in
the middle of a deploy, of a server whose console is not a systemd unit, or with
a broken ``fstab`` is not an inconvenience but a call to the provider's console.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from noust.core import paths
from noust.core.exceptions import ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.errors import PreflightError, ServerError
from noust.managers.server.host import HostPaths, read_text
from noust.managers.server.power_records import PowerRecords, ScheduledPower

#: The longest delay a reboot can be scheduled with: a week. Further ahead is a
#: cron job's business, and a schedule nobody remembers is a surprise.
MAX_DELAY_MINUTES = 7 * 24 * 60

#: The longest wall message; it goes to every logged-in terminal.
MAX_MESSAGE_LENGTH = 200

#: Deadline of the commands here; ``shutdown`` returns at once.
COMMAND_TIMEOUT = 30

#: Where the last boot the console saw is remembered, so a reboot nobody
#: scheduled through Noust is still noticed.
LAST_BOOT_FILE = "last-boot-id"

_UNIT_ENABLED = ("enabled", "static", "alias", "enabled-runtime")

_MESSAGE_CLEANER = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Check:
    """
    One thing looked at before a reboot.

    Attributes:
        id: Stable identifier the console translates.
        status: ``ok`` or ``warn``; a warning needs ``force`` to be overridden.
        message: What was found, one sentence, with the system's own words
            where there are any.
    """

    id: str
    status: str
    message: str


@dataclass(frozen=True)
class PowerStatus:
    """
    The power state of the machine.

    Attributes:
        scheduled: The pending reboot or shutdown, if any.
        boot_id: The identifier of the current boot.
        uptime_seconds: Seconds since boot, None when unreadable.
        mode: What systemd says is scheduled (``reboot``, ``poweroff``), or None.
        due_at: When systemd will act, ISO 8601 UTC, or None.
    """

    scheduled: ScheduledPower | None
    boot_id: str
    uptime_seconds: float | None
    mode: str | None = None
    due_at: str | None = None

    def to_dict(self) -> dict:
        """
        Render the status as JSON-serialisable data.

        Returns:
            Every field, by name; the schedule as a mapping.
        """
        data = asdict(self)
        data["scheduled"] = self.scheduled.to_dict() if self.scheduled else None
        return data


@dataclass(frozen=True)
class Returned:
    """
    The machine is back after a restart.

    Attributes:
        action: ``reboot`` or ``poweroff`` for a scheduled one; ``reboot`` when
            nobody scheduled it.
        planned: An operator scheduled it through Noust.
        requested_by: Who, when planned.
        scheduled_for: When it was due, when planned.
        returned_at: When the console saw the machine back, ISO 8601 UTC.
        took_seconds: From the moment it was due to the console answering; None
            when nobody scheduled it.
    """

    action: str
    planned: bool
    requested_by: str | None
    scheduled_for: str | None
    returned_at: str
    took_seconds: int | None


def parse_scheduled_file(text: str) -> tuple[str | None, str | None]:
    """
    Read what ``shutdown`` leaves for systemd-logind.

    Args:
        text: The contents of ``/run/systemd/shutdown/scheduled``.

    Returns:
        The mode (``reboot``, ``poweroff``, ``halt``) and the due time as ISO
        8601 UTC; None for what the file does not say.
    """
    fields = dict(line.partition("=")[::2] for line in text.splitlines() if "=" in line)
    usec = fields.get("USEC", "")
    due = (
        datetime.fromtimestamp(int(usec) / 1_000_000, tz=timezone.utc).isoformat()
        if usec.isdigit()
        else None
    )
    return fields.get("MODE") or None, due


def resolve_delay(
    *, in_minutes: int | None = None, at: datetime | None = None, now: datetime | None = None
) -> int:
    """
    Turn "in N minutes" or "at this time" into the minutes ``shutdown`` takes.

    Args:
        in_minutes: A delay, 0 for now.
        at: A moment. A naive datetime is the server's local time.
        now: The current time; injectable for tests.

    Returns:
        Whole minutes from now, rounded up so it is never earlier than asked.

    Raises:
        ValidationError: Neither or both were given, the moment is in the past
            or further away than :data:`MAX_DELAY_MINUTES`, or the delay is
            negative.
    """
    if (in_minutes is None) == (at is None):
        raise ValidationError(
            "Say when: give either a delay in minutes or a time, not both",
            "For example 'in 5 minutes' or 'at 04:00'.",
        )
    if in_minutes is not None:
        minutes = in_minutes
    else:
        assert at is not None  # noqa: S101 - narrowed by the check above
        current = now or datetime.now(timezone.utc)
        moment = at if at.tzinfo else at.astimezone()
        minutes = math.ceil((moment - current).total_seconds() / 60)
    if minutes < 0:
        raise ValidationError("That time has already passed", "Pick a moment in the future.")
    if minutes > MAX_DELAY_MINUTES:
        raise ValidationError(
            "That is more than a week away",
            "Use a cron job for anything further out; a schedule nobody remembers is a surprise.",
        )
    return minutes


def clean_message(text: str | None, default: str) -> str:
    """
    Make a wall message safe to send to terminals.

    Args:
        text: What the caller wants said.
        default: What to say when it is empty.

    Returns:
        One line, without control characters (an escape sequence in a wall
        message rewrites the operator's terminal), at most
        :data:`MAX_MESSAGE_LENGTH` characters.
    """
    cleaned = _MESSAGE_CLEANER.sub(" ", text or "").strip()
    return (cleaned or default)[:MAX_MESSAGE_LENGTH]


def unenabled_app_units(runner: CommandRunner) -> list[str]:
    """
    Name the units of deployed applications that would not start after a reboot.

    Args:
        runner: Used to ask systemd.

    Each application is asked about through the units it runs as
    (:meth:`~noust.managers.service_manager.ServiceManager.serving_units`),
    never through the name derived from its domain: a legacy ``wasm-`` unit, a
    monorepo's workspaces and a Compose project each have their own.

    Returns:
        Units that are not enabled. Of a blue/green application only the
        serving instance counts (``shop@green``): the idle one is disabled by
        design, and with no serving colour recorded there is none to ask about.
    """
    from noust.core.store import get_store
    from noust.managers.service_manager import ServiceManager

    manager = ServiceManager(runner=runner)
    missing: list[str] = []
    for app in get_store().list_apps():
        for unit in manager.serving_units(app):
            if "@" in unit and not getattr(app, "active_color", None):
                continue
            state = runner.run(
                ["systemctl", "is-enabled", f"{unit}.service"], timeout=COMMAND_TIMEOUT
            ).stdout.strip()
            if state not in _UNIT_ENABLED:
                missing.append(unit)
    return missing


class PowerManager:
    """Schedules, cancels and follows reboots and shutdowns."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        records: PowerRecords | None = None,
        blockers: Callable[[], list[str]] | None = None,
        apps_check: Callable[[CommandRunner], list[str]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """
        Args:
            runner: Command runner; the process-wide one when omitted.
            fs: Filesystem seam; the process-wide one when omitted.
            host: Where the system files are.
            records: The schedule in the store.
            blockers: Returns what Noust is running that a reboot would break (a
                deploy, a backup, an update). The manager cannot see the
                console's job queue, so the caller passes what it can see.
            apps_check: Returns the units of applications that would not come
                back; :func:`unenabled_app_units` by default.
            clock: The current time; injectable for tests.
        """
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()
        self.records = records or PowerRecords()
        self._blockers = blockers
        self._apps_check = apps_check or unenabled_app_units
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    # -- reading --------------------------------------------------------------

    def boot_id(self) -> str:
        """
        Read the identifier of the current boot.

        Returns:
            The kernel's boot id, without dashes, or an empty string when unreadable.
        """
        return (read_text(self.host.boot_id) or "").strip().replace("-", "")

    def _uptime(self) -> float | None:
        """
        Read the seconds since boot.

        Returns:
            The uptime, or None when ``/proc/uptime`` cannot be read.
        """
        text = read_text(self.host.proc_uptime)
        try:
            return float(text.split()[0]) if text else None
        except (ValueError, IndexError):
            return None

    def status(self) -> PowerStatus:
        """
        Say what is scheduled.

        The truth is systemd's file; the row says who asked. When they
        disagree the row is closed: a schedule that systemd no longer has was
        cancelled by hand (``shutdown -c``) or already happened.

        Returns:
            The status.
        """
        scheduled = self.records.active()
        mode: str | None = None
        due: str | None = None
        file_text = read_text(self.host.shutdown_scheduled)
        if file_text:
            mode, due = parse_scheduled_file(file_text)
        if scheduled is not None and not file_text:
            if scheduled.boot_id != self.boot_id():
                self.records.finish(scheduled.id, "completed")
            else:
                self.records.finish(scheduled.id, "lost")
            scheduled = None
        return PowerStatus(
            scheduled=scheduled,
            boot_id=self.boot_id(),
            uptime_seconds=self._uptime(),
            mode=mode,
            due_at=due,
        )

    def checks(self) -> list[Check]:
        """
        Look at what a reboot would break.

        Returns:
            One check per thing: jobs running, the console coming back, the
            applications coming back, ``fstab``, the newest kernel's ramdisk.
        """
        checks: list[Check] = []

        running = self._blockers() if self._blockers else []
        checks.append(
            Check(
                "jobs",
                "warn" if running else "ok",
                f"Running now: {', '.join(running)}" if running else "Nothing is running",
            )
        )

        console = [
            self.runner.run(
                ["systemctl", "is-enabled", f"{unit}.service"], timeout=COMMAND_TIMEOUT
            ).stdout.strip()
            for unit in (paths.WEB_UNIT, paths.LEGACY_WEB_UNIT)
        ]
        returns = any(state in _UNIT_ENABLED for state in console)
        checks.append(
            Check(
                "console",
                "ok" if returns else "warn",
                "The console starts on boot"
                if returns
                else "The console is not an enabled systemd unit: it will not come back after the "
                "reboot. Run 'noust web enable' first.",
            )
        )

        stay_down = self._apps_check(self.runner)
        checks.append(
            Check(
                "apps",
                "warn" if stay_down else "ok",
                f"These applications are not enabled and will not start again: {', '.join(stay_down)}"
                if stay_down
                else "Every application starts on boot",
            )
        )

        verify = self.runner.run(["findmnt", "--verify"], timeout=COMMAND_TIMEOUT)
        fstab_words = "\n".join(p for p in (verify.stdout.strip(), verify.stderr.strip()) if p)
        checks.append(
            Check(
                "fstab",
                "ok" if verify.success else "warn",
                "fstab is correct"
                if verify.success
                else f"findmnt --verify found a problem, and a bad fstab can leave the machine "
                f"in emergency mode: {fstab_words}",
            )
        )

        ramdisk = self._missing_ramdisk()
        checks.append(
            Check(
                "ramdisk",
                "warn" if ramdisk else "ok",
                f"The newest kernel, {ramdisk}, has no initial ramdisk in /boot"
                if ramdisk
                else "The newest kernel is complete",
            )
        )
        return checks

    def _missing_ramdisk(self) -> str | None:
        """
        Find a newest kernel that would not boot for want of its ramdisk.

        Returns:
            The kernel's release, or None when it is complete or none is installed.
        """
        from noust.managers.server.host import newest_installed_kernel

        release = newest_installed_kernel(self.host)
        if release is None:
            return None
        names = (f"initrd.img-{release}", f"initramfs-{release}.img", f"initrd-{release}")
        if any((self.host.boot / name).exists() for name in names):
            return None
        return release

    # -- acting ---------------------------------------------------------------

    def schedule(
        self,
        action: str,
        *,
        minutes: int,
        actor: str | None,
        message: str | None = None,
        force: bool = False,
    ) -> ScheduledPower:
        """
        Schedule a reboot or a shutdown.

        Args:
            action: ``reboot`` or ``poweroff``.
            minutes: Minutes from now, 0 for now (see :func:`resolve_delay`).
            actor: Who asked, for the record and the wall message.
            message: What to tell logged-in users.
            force: Go ahead although a check warned.

        Returns:
            The schedule that is now pending.

        Raises:
            ValidationError: The action is not one of the two.
            PreflightError: A check warned and ``force`` was not given; nothing
                was scheduled.
            ServerError: ``shutdown`` refused, carrying its output.
        """
        if action not in ("reboot", "poweroff"):
            raise ValidationError(f"Cannot schedule {action!r}", "Use 'reboot' or 'poweroff'.")
        warnings = [check for check in self.checks() if check.status != "ok"]
        if warnings and not force:
            raise PreflightError(
                f"The {action} needs a second look",
                "Settle what is listed, or repeat the request confirming that you have read it.",
                blockers=[check.message for check in warnings],
            )

        default = f"{'Reboot' if action == 'reboot' else 'Shutdown'} requested from Noust" + (
            f" by {actor}" if actor else ""
        )
        text = clean_message(message, default)
        flag = "-r" if action == "reboot" else "-P"
        result = self.runner.run(["shutdown", flag, f"+{minutes}", text], timeout=COMMAND_TIMEOUT)
        if not result.success:
            raise ServerError(
                f"Could not schedule the {action}",
                "shutdown refused. Nothing was scheduled.",
                output="\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p),
            )
        due = (self._clock() + timedelta(minutes=minutes)).isoformat()
        return self.records.schedule(
            action, due, requested_by=actor, message=text, boot_id=self.boot_id()
        )

    def cancel(self) -> bool:
        """
        Cancel the pending reboot or shutdown.

        Returns:
            True when there was one to cancel.

        Raises:
            ServerError: ``shutdown -c`` failed for a reason other than there
                being nothing to cancel.
        """
        pending = self.records.active()
        result = self.runner.run(["shutdown", "-c"], timeout=COMMAND_TIMEOUT)
        had_file = self.host.shutdown_scheduled.exists()
        if not result.success and had_file:
            raise ServerError(
                "Could not cancel the scheduled shutdown",
                "shutdown -c failed. Run it yourself as root.",
                output="\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p),
            )
        if pending is not None:
            self.records.finish(pending.id, "cancelled")
        return pending is not None or had_file

    # -- coming back ------------------------------------------------------------

    def detect_return(self) -> Returned | None:
        """
        Notice that the machine restarted since the console last looked.

        Called once when the console starts. A restart of the console alone
        leaves the boot the same and says nothing; a restart of the machine
        changes it, whether the operator scheduled it here, ran ``reboot`` by
        hand or the provider did it.

        Returns:
            What happened, or None when the boot is the same one (or this is
            the first time anyone looked).
        """
        current = self.boot_id()
        if not current:
            return None
        marker = paths.state_dir() / LAST_BOOT_FILE
        previous = (read_text(marker) or "").strip()
        if previous == current:
            return None
        self.fs.write_text(marker, current + "\n")
        if not previous:
            return None

        now = self._clock()
        pending = self.records.active()
        if pending is not None and pending.boot_id != current:
            self.records.finish(pending.id, "completed")
            try:
                due = datetime.fromisoformat(pending.scheduled_for)
                took = max(0, int((now - due).total_seconds()))
            except ValueError:
                took = None
            return Returned(
                action=pending.action,
                planned=True,
                requested_by=pending.requested_by,
                scheduled_for=pending.scheduled_for,
                returned_at=now.isoformat(),
                took_seconds=took,
            )
        return Returned(
            action="reboot",
            planned=False,
            requested_by=None,
            scheduled_for=None,
            returned_at=now.isoformat(),
            took_seconds=None,
        )
