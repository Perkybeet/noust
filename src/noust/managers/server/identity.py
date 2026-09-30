# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What this machine is called and what it runs.

The name of a server is read in one place and written in three: ``hostnamectl``,
the ``127.0.1.1`` line of ``/etc/hosts`` that Debian and Ubuntu resolve their own
name through (without it every ``sudo`` prints "unable to resolve host"), and,
on a cloud image, cloud-init, which sets the name back on every boot unless it is
told not to. Changing only the first is how a rename lasts until the next reboot.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server import eol
from noust.managers.server.errors import ServerError
from noust.managers.server.host import (
    HostPaths,
    PackageFamily,
    Platform,
    detect_platform,
    read_os_release,
    read_text,
)

#: Deadline of the quick commands.
COMMAND_TIMEOUT = 30

#: One DNS label: lower-case letters, digits and hyphens, not starting or ending
#: with a hyphen, at most 63 characters.
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

#: The cloud-init file Noust writes to stop it renaming the host.
CLOUD_INIT_FILE = "99-noust-hostname.cfg"


@dataclass(frozen=True)
class HostnameInfo:
    """
    The names of the machine.

    Attributes:
        hostname: The transient name in use now.
        static: The name set in ``/etc/hostname``.
        pretty: The free-form name, when one is set.
        machine_id: The machine's stable identifier.
        boot_id: The current boot's identifier.
        chassis: ``server``, ``vm``, ``container``... as systemd guesses.
        cloud_init: cloud-init manages this machine.
        cloud_init_resets: cloud-init is set to rename it on every boot.
    """

    hostname: str
    static: str
    pretty: str | None
    machine_id: str
    boot_id: str
    chassis: str | None
    cloud_init: bool = False
    cloud_init_resets: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Render the names as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass(frozen=True)
class Identity:
    """
    Everything the system tab says about the machine.

    Attributes:
        hostname: Its names.
        os_id: ``ID`` from ``/etc/os-release``.
        os_version: ``VERSION_ID``.
        os_name: The name to show a person.
        codename: The release's codename.
        kernel: The running kernel.
        architecture: ``x86_64``, ``aarch64``...
        container: The kind of container this is, when it is one.
        eol: Where the operating system is in its support.
        uptime_seconds: Seconds since boot.
        booted_at: When it booted, ISO 8601 UTC.
        load: The 1, 5 and 15 minute load averages.
        cpu_count: Logical CPUs.
    """

    hostname: HostnameInfo
    os_id: str
    os_version: str
    os_name: str
    codename: str
    kernel: str
    architecture: str
    container: str | None
    eol: eol.EolStatus
    uptime_seconds: float | None
    booted_at: str | None
    load: tuple[float, float, float]
    cpu_count: int

    def to_dict(self) -> dict[str, Any]:
        """
        Render the identity as JSON-serialisable data.

        Returns:
            Every field, by name; the nested records as mappings.
        """
        data = asdict(self)
        data["load"] = list(self.load)
        return data


def valid_hostname(name: str) -> bool:
    """
    Tell whether a name can be a host name.

    Args:
        name: The candidate.

    Returns:
        True for lower-case DNS labels of 63 characters at most, joined by dots,
        253 characters in all.
    """
    return (
        isinstance(name, str)
        and 0 < len(name) <= 253
        and all(_LABEL.match(label) for label in name.split("."))
    )


def parse_hostnamectl_status(text: str) -> dict[str, str]:
    """
    Read ``hostnamectl status``, for systemd before 250 which has no ``--json``.

    Args:
        text: The human-readable status.

    Returns:
        ``Hostname``, ``StaticHostname``, ``PrettyHostname``, ``Chassis``,
        ``MachineID`` and ``BootID`` where the output has them.
    """
    keys = {
        "Static hostname": "StaticHostname",
        "Transient hostname": "Hostname",
        "Pretty hostname": "PrettyHostname",
        "Chassis": "Chassis",
        "Machine ID": "MachineID",
        "Boot ID": "BootID",
    }
    found: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        name = keys.get(key.strip())
        if name:
            found[name] = value.strip()
    found.setdefault("Hostname", found.get("StaticHostname", ""))
    return found


def rewrite_hosts(text: str, old: str, new: str) -> tuple[str, bool]:
    """
    Point the ``127.0.1.1`` line of ``/etc/hosts`` at the new name.

    Args:
        text: The file.
        old: The name it had.
        new: The name it has.

    Returns:
        The new text and whether a line changed. Only a ``127.0.1.1`` line is
        touched, which is the one Debian and Ubuntu use; the rest of the file
        is never rewritten.
    """
    lines = text.splitlines(keepends=True)
    changed = False
    short = new.split(".", 1)[0]
    for index, line in enumerate(lines):
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 2 and fields[0] == "127.0.1.1":
            names = [new] if short == new else [new, short]
            lines[index] = f"127.0.1.1 {' '.join(names)}\n"
            changed = changed or lines[index] != line
    return "".join(lines), changed


class IdentityManager:
    """Reads what the machine is and renames it."""

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
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    @property
    def platform(self) -> Platform:
        """What the machine is, detected on first use."""
        if self._platform is None:
            self._platform = detect_platform(self._runner, self.host)
        return self._platform

    # -- reading --------------------------------------------------------------

    def _cloud_init(self) -> tuple[bool, bool]:
        """
        Look at cloud-init's configuration.

        Returns:
            Whether cloud-init manages this machine, and whether it is set to
            rename it on every boot (it is, unless ``preserve_hostname`` is true).
        """
        directory = self.host.at("/etc/cloud")
        if not directory.is_dir():
            return False, False
        texts = [read_text(directory / "cloud.cfg") or ""]
        try:
            texts += [read_text(path) or "" for path in sorted(self.host.cloud_cfg_d.glob("*.cfg"))]
        except OSError:
            pass
        preserved = any(
            re.search(r"^preserve_hostname:\s*true\b", text, re.MULTILINE) for text in texts
        )
        return True, not preserved

    def hostname(self) -> HostnameInfo:
        """
        Read the machine's names.

        Returns:
            The names.
        """
        result = self.runner.run(["hostnamectl", "--json=short"], timeout=COMMAND_TIMEOUT)
        data: dict[str, Any] = {}
        if result.success:
            try:
                parsed = json.loads(result.stdout)
                data = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                data = {}
        if not data:
            status = self.runner.run(["hostnamectl", "status"], timeout=COMMAND_TIMEOUT)
            data = parse_hostnamectl_status(status.stdout) if status.success else {}
        static = read_text(self.host.etc_hostname)
        managed, resets = self._cloud_init()
        return HostnameInfo(
            hostname=str(data.get("Hostname") or os.uname().nodename),
            static=str(data.get("StaticHostname") or (static or "").strip()),
            pretty=data.get("PrettyHostname") or None,
            machine_id=str(data.get("MachineID") or ""),
            boot_id=str(data.get("BootID") or ""),
            chassis=data.get("Chassis") or None,
            cloud_init=managed,
            cloud_init_resets=resets,
        )

    def identity(self, *, today: date | None = None) -> Identity:
        """
        Read everything the system tab shows.

        Args:
            today: The current date, for the end-of-life status; injectable.

        Returns:
            The identity.
        """
        os_release = read_os_release(self.host)
        uname = os.uname()
        text = read_text(self.host.proc_uptime)
        try:
            uptime = float(text.split()[0]) if text else None
        except (ValueError, IndexError):
            uptime = None
        booted = (
            datetime.fromtimestamp(time.time() - uptime, tz=timezone.utc).isoformat()
            if uptime is not None
            else None
        )
        return Identity(
            hostname=self.hostname(),
            os_id=os_release.id,
            os_version=os_release.version_id,
            os_name=os_release.pretty_name,
            codename=os_release.codename,
            kernel=uname.release,
            architecture=uname.machine,
            container=self.platform.container,
            eol=eol.status_of(os_release, today=today),
            uptime_seconds=uptime,
            booted_at=booted,
            load=os.getloadavg(),
            cpu_count=os.cpu_count() or 1,
        )

    # -- renaming -------------------------------------------------------------

    def set_hostname(self, name: str, *, keep_against_cloud_init: bool = False) -> list[str]:
        """
        Rename the machine.

        Args:
            name: The new name.
            keep_against_cloud_init: Also tell cloud-init not to set the name
                back at the next boot.

        Returns:
            One sentence per thing done, and any warning worth reading.

        Raises:
            ValidationError: The name is not a valid host name.
            ServerError: ``hostnamectl`` refused, carrying its output.
        """
        if not valid_hostname(name):
            raise ValidationError(
                f"Not a valid host name: {name!r}",
                "Use lower-case letters, digits and hyphens, at most 63 characters per part, "
                "with parts joined by dots.",
            )
        before = self.hostname()
        result = self.runner.run(["hostnamectl", "set-hostname", name], timeout=COMMAND_TIMEOUT)
        if not result.success:
            raise ServerError(
                "Could not change the host name",
                "hostnamectl refused. Nothing was changed.",
                output="\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p),
            )
        steps = [f"Set the host name to {name}"]

        if self.platform.family is PackageFamily.APT:
            hosts = self.host.etc_hosts
            text = read_text(hosts)
            if text is not None:
                updated, changed = rewrite_hosts(text, before.static or before.hostname, name)
                if changed:
                    backup = hosts.with_name(hosts.name + ".noust-bak")
                    if not backup.exists():
                        self.fs.write_text(backup, text)
                    self.fs.write_text(hosts, updated)
                    steps.append(
                        "Updated the 127.0.1.1 line of /etc/hosts, so sudo can still resolve it"
                    )

        if before.cloud_init and before.cloud_init_resets:
            if keep_against_cloud_init:
                self.fs.write_text(
                    self.host.cloud_cfg_d / CLOUD_INIT_FILE,
                    "# Generated by Noust\npreserve_hostname: true\n",
                )
                steps.append("Told cloud-init to keep the name (preserve_hostname: true)")
            else:
                steps.append(
                    "Warning: cloud-init sets the host name back on every boot unless it is told "
                    "not to. Repeat with cloud-init kept out of it, or set preserve_hostname: true."
                )
        return steps
