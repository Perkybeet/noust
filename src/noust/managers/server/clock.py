# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The server's clock: its time zone, whether it keeps itself right, and how far off it is.

A clock that is wrong is not a cosmetic problem here. It breaks TLS (a certificate
is "not yet valid"), certbot, and the six-digit codes of the console's two-factor
authentication, which are a function of the time. And the time zone is not
cosmetic either: the timers Noust writes for cron jobs and backups are calendar
expressions in local time, so changing the zone moves every one of them. The
change lists the ones it moves.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.errors import ServerError
from noust.managers.server.host import HostPaths, Platform, detect_platform
from noust.managers.server.pkg import backend_for

#: Deadline of the quick commands.
COMMAND_TIMEOUT = 30

#: Deadline of installing a time daemon.
INSTALL_TIMEOUT = 600

#: What a time zone name may look like: ``Region/City``, ``Etc/GMT+1``, ``UTC``.
_ZONE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+\-]*(/[A-Za-z0-9][A-Za-z0-9_+\-]*){0,2}$")

#: The package that keeps a clock right, on every family Noust manages.
NTP_PACKAGE = "chrony"

_STATUS_KEYS = {
    "Time zone": "Timezone",
    "System clock synchronized": "NTPSynchronized",
    "NTP service": "NTP",
    "RTC in local TZ": "LocalRTC",
}


@dataclass(frozen=True)
class TimeStatus:
    """
    The state of the clock.

    Attributes:
        timezone: The zone name, such as ``Europe/Madrid``.
        local_time: The local time as ``timedatectl`` prints it.
        utc: The current UTC time, ISO 8601.
        ntp_supported: A time daemon is installed that ``timedatectl`` can drive.
        ntp_enabled: It is set to run.
        synchronized: The clock is in step with a source.
        local_rtc: The hardware clock keeps local time, which breaks across
            daylight saving changes.
        offset_seconds: How far off the clock is from its source, when chrony says.
    """

    timezone: str
    local_time: str
    utc: str
    ntp_supported: bool
    ntp_enabled: bool
    synchronized: bool
    local_rtc: bool
    offset_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the status as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass
class TimezoneChange:
    """
    What changing the time zone did.

    Attributes:
        previous: The zone before.
        timezone: The zone now.
        moved_timers: Timers of Noust's cron jobs and backups, which fire at
            local times and so fire at different moments now.
        output: What the tool printed.
    """

    previous: str
    timezone: str
    moved_timers: list[str] = field(default_factory=list)
    output: str = ""


def _yes(value: str) -> bool:
    """
    Read a systemd boolean.

    Args:
        value: ``yes``, ``no``, ``true``...

    Returns:
        True for the affirmative spellings.
    """
    return value.strip().lower() in ("yes", "true", "1", "active")


def parse_show(text: str) -> dict[str, str]:
    """
    Read ``timedatectl show``.

    Args:
        text: ``Key=Value`` lines.

    Returns:
        The properties.
    """
    return dict(line.partition("=")[::2] for line in text.splitlines() if "=" in line)


def parse_status(text: str) -> dict[str, str]:
    """
    Read ``timedatectl status`` into the keys ``show`` uses.

    For systemd before 239, which has no ``show``.

    Args:
        text: The human-readable status.

    Returns:
        The properties that could be read; the zone is its bare name.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        name = _STATUS_KEYS.get(key.strip())
        if name:
            found[name] = value.strip().split(" ", 1)[0] if name == "Timezone" else value.strip()
        elif key.strip() == "Local time":
            found["TimeUSec"] = value.strip()
    found.setdefault("CanNTP", "yes" if "NTP" in found else "no")
    return found


def parse_chrony_offset(text: str) -> float | None:
    """
    Read the offset from ``chronyc -c tracking``.

    Args:
        text: The comma-separated line.

    Returns:
        The system time offset in seconds, or None.
    """
    fields = text.strip().split(",")
    if len(fields) < 5:
        return None
    try:
        return float(fields[4])
    except ValueError:
        return None


class ClockManager:
    """Reads and changes the time zone and the time synchronisation."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        platform: Platform | None = None,
    ) -> None:
        """
        Args:
            runner: Command runner; the process-wide one when omitted.
            fs: Filesystem seam; the process-wide one when omitted.
            host: Where the system files are.
            platform: What the machine is; detected once when omitted.
        """
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()
        self._platform = platform

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

    def status(self) -> TimeStatus:
        """
        Read the clock's state.

        Returns:
            The status.

        Raises:
            ServerError: ``timedatectl`` cannot be run at all, carrying its output.
        """
        result = self.runner.run(["timedatectl", "show"], timeout=COMMAND_TIMEOUT)
        properties = parse_show(result.stdout) if result.success else {}
        if not properties:
            fallback = self.runner.run(["timedatectl", "status"], timeout=COMMAND_TIMEOUT)
            if not fallback.success:
                raise ServerError(
                    "Could not read the clock",
                    "timedatectl did not answer: systemd-timedated may not be running.",
                    output="\n".join(
                        p for p in (fallback.stdout.strip(), fallback.stderr.strip()) if p
                    ),
                )
            properties = parse_status(fallback.stdout)

        offset = None
        if self.runner.exists("chronyc"):
            tracking = self.runner.run(["chronyc", "-c", "tracking"], timeout=COMMAND_TIMEOUT)
            offset = parse_chrony_offset(tracking.stdout) if tracking.success else None
        return TimeStatus(
            timezone=properties.get("Timezone", ""),
            local_time=properties.get("TimeUSec", ""),
            utc=datetime.now(timezone.utc).isoformat(),
            ntp_supported=_yes(properties.get("CanNTP", "no")),
            ntp_enabled=_yes(properties.get("NTP", "no")),
            synchronized=_yes(properties.get("NTPSynchronized", "no")),
            local_rtc=_yes(properties.get("LocalRTC", "no")),
            offset_seconds=offset,
        )

    def valid_timezone(self, name: str) -> bool:
        """
        Tell whether a zone name is one this machine knows.

        Args:
            name: The zone.

        Returns:
            True when the tz database has it. The shape is checked first, so a
            name cannot walk out of the zoneinfo directory.
        """
        if not isinstance(name, str) or not _ZONE.match(name) or ".." in name:
            return False
        try:
            from zoneinfo import available_timezones

            if name in available_timezones():
                return True
        except ImportError:  # pragma: no cover - zoneinfo is standard since 3.9
            pass
        # A Debian without tzdata Python packages still has the files.
        return self.host.at(f"/usr/share/zoneinfo/{name}").is_file()

    def affected_timers(self) -> list[str]:
        """
        List Noust's timers, which fire at local times.

        Returns:
            The cron and backup timers that exist.
        """
        result = self.runner.run(
            [
                "systemctl",
                "list-timers",
                "noust-cron-*",
                "noust-backup-*",
                "wasm-cron-*",
                "wasm-backup-*",
                "--all",
                "--no-legend",
                "--plain",
                "--no-pager",
            ],
            timeout=COMMAND_TIMEOUT,
        )
        names = []
        for line in result.stdout.splitlines():
            for token in line.split():
                if token.endswith(".timer"):
                    names.append(token)
        return sorted(set(names))

    def set_timezone(self, zone: str) -> TimezoneChange:
        """
        Change the time zone.

        Args:
            zone: The zone name.

        Returns:
            The change, with the timers it moves.

        Raises:
            ValidationError: The zone is not one this machine knows.
            ServerError: ``timedatectl`` refused, carrying its output.
        """
        if not self.valid_timezone(zone):
            raise ValidationError(
                f"Unknown time zone: {zone!r}",
                "Use a name from the tz database, such as Europe/Madrid or UTC "
                "('timedatectl list-timezones' lists them).",
            )
        previous = self.status().timezone
        result = self.runner.run(["timedatectl", "set-timezone", zone], timeout=COMMAND_TIMEOUT)
        words = "\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p)
        if not result.success:
            raise ServerError(
                "Could not change the time zone",
                "timedatectl refused. Nothing was changed.",
                output=words,
            )
        return TimezoneChange(
            previous=previous,
            timezone=zone,
            moved_timers=self.affected_timers() if previous != zone else [],
            output=words,
        )

    def set_ntp(self, enabled: bool, *, install: bool = False) -> str:
        """
        Turn time synchronisation on or off.

        Args:
            enabled: The wanted state.
            install: Install chrony when there is no daemon to turn on.

        Returns:
            What ``timedatectl`` printed, verbatim.

        Raises:
            ServerError: It refused; without a time daemon and without ``install``
                the message says to install one.
        """
        argv = ["timedatectl", "set-ntp", "true" if enabled else "false"]
        result = self.runner.run(argv, timeout=COMMAND_TIMEOUT)
        words = "\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p)
        if result.success:
            return words
        if enabled and "not supported" in words.lower():
            if not install:
                raise ServerError(
                    "There is no time daemon to turn on",
                    f"Neither systemd-timesyncd nor chrony is installed. Repeat the request "
                    f"with the installation allowed, and Noust installs {NTP_PACKAGE}.",
                    output=words,
                )
            backend = backend_for(self.platform, runner=self._runner, fs=self._fs, host=self.host)
            install_result = self.runner.run(
                backend.install_argv([NTP_PACKAGE]), env=backend.env(), timeout=INSTALL_TIMEOUT
            )
            if not install_result.success:
                raise ServerError(
                    f"Could not install {NTP_PACKAGE}",
                    "The package manager failed; its output is below.",
                    output="\n".join(
                        p
                        for p in (install_result.stdout.strip(), install_result.stderr.strip())
                        if p
                    ),
                )
            again = self.runner.run(argv, timeout=COMMAND_TIMEOUT)
            if again.success:
                return f"Installed {NTP_PACKAGE}; " + (
                    again.stdout.strip() or "synchronisation is on"
                )
            words = "\n".join(p for p in (again.stdout.strip(), again.stderr.strip()) if p)
        raise ServerError(
            "Could not change time synchronisation",
            "timedatectl refused. The output below is its own.",
            output=words,
        )
