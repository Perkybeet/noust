# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What machine this is: where its system files are, which distribution and which
package manager it runs.

Every manager of the server side asks here instead of spelling ``/etc/os-release``
or guessing the package manager (the family itself, shared with the messages that
say how to install something, is :mod:`noust.core.package_family`). Two reasons, both learnt the hard way in this
codebase: the package manager was detected in four places with four opinions,
and a path spelled in the middle of a parser cannot be pointed at a temporary
directory by a test, which is how parsers end up tested against the developer's
own machine.

:class:`HostPaths` is the answer to the second: every system path the server
managers read lives on it, relative to a root that a test replaces.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from noust.core.package_family import PackageFamily, detect_family
from noust.core.runner import CommandRunner, get_runner

#: Deadline of a probe that only asks the machine a question.
PROBE_TIMEOUT = 20


@dataclass(frozen=True)
class HostPaths:
    """
    Every system path the server managers read or write, under one root.

    Attributes:
        root: ``/`` on a real machine; a temporary directory in a test.
    """

    root: Path = Path("/")

    def at(self, relative: str) -> Path:
        """
        Resolve a path below the root.

        Args:
            relative: Absolute path as it is on a real machine (``/etc/fstab``).

        Returns:
            The same path below :attr:`root`.
        """
        return self.root / relative.lstrip("/")

    @property
    def os_release(self) -> Path:
        """``/etc/os-release``."""
        return self.at("/etc/os-release")

    @property
    def usr_os_release(self) -> Path:
        """``/usr/lib/os-release``, where ``/etc/os-release`` is missing."""
        return self.at("/usr/lib/os-release")

    @property
    def systemd_marker(self) -> Path:
        """A directory that only exists on a machine booted with systemd."""
        return self.at("/run/systemd/system")

    @property
    def ostree_marker(self) -> Path:
        """Present on an image-based system, where packages are not changed in place."""
        return self.at("/run/ostree-booted")

    @property
    def proc_uptime(self) -> Path:
        """``/proc/uptime``."""
        return self.at("/proc/uptime")

    @property
    def boot_id(self) -> Path:
        """The identifier of the current boot."""
        return self.at("/proc/sys/kernel/random/boot_id")

    @property
    def swappiness(self) -> Path:
        """``vm.swappiness``."""
        return self.at("/proc/sys/vm/swappiness")

    @property
    def shutdown_scheduled(self) -> Path:
        """What ``shutdown +N`` leaves for systemd-logind."""
        return self.at("/run/systemd/shutdown/scheduled")

    @property
    def fstab(self) -> Path:
        """``/etc/fstab``."""
        return self.at("/etc/fstab")

    @property
    def etc_hostname(self) -> Path:
        """``/etc/hostname``."""
        return self.at("/etc/hostname")

    @property
    def etc_hosts(self) -> Path:
        """``/etc/hosts``."""
        return self.at("/etc/hosts")

    @property
    def boot(self) -> Path:
        """``/boot``, where the installed kernels live."""
        return self.at("/boot")

    @property
    def reboot_required(self) -> Path:
        """The flag Debian and Ubuntu leave when an update needs a reboot."""
        return self.at("/var/run/reboot-required")

    @property
    def reboot_required_pkgs(self) -> Path:
        """The packages behind :attr:`reboot_required`, one per line."""
        return self.at("/var/run/reboot-required.pkgs")

    @property
    def zypper_reboot_needed(self) -> Path:
        """The flag openSUSE leaves when an update needs a reboot."""
        return self.at("/run/reboot-needed")

    @property
    def apt_periodic(self) -> Path:
        """The directory apt keeps its timestamps in."""
        return self.at("/var/lib/apt/periodic")

    @property
    def apt_lists(self) -> Path:
        """Where apt keeps the package lists it downloaded."""
        return self.at("/var/lib/apt/lists")

    @property
    def apt_conf_d(self) -> Path:
        """``/etc/apt/apt.conf.d``."""
        return self.at("/etc/apt/apt.conf.d")

    @property
    def unattended_log(self) -> Path:
        """What unattended-upgrades wrote the last time it ran."""
        return self.at("/var/log/unattended-upgrades/unattended-upgrades.log")

    @property
    def dnf_automatic_conf(self) -> Path:
        """``/etc/dnf/automatic.conf``."""
        return self.at("/etc/dnf/automatic.conf")

    @property
    def os_update_conf(self) -> Path:
        """``/etc/os-update.conf``, openSUSE's own automatic update."""
        return self.at("/etc/os-update.conf")

    @property
    def sysctl_d(self) -> Path:
        """``/etc/sysctl.d``."""
        return self.at("/etc/sysctl.d")

    @property
    def cloud_cfg_d(self) -> Path:
        """``/etc/cloud/cloud.cfg.d``, where cloud-init reads its overrides."""
        return self.at("/etc/cloud/cloud.cfg.d")

    @property
    def journal_dir(self) -> Path:
        """``/var/log/journal``, present when the journal is persistent."""
        return self.at("/var/log/journal")

    @property
    def var_crash(self) -> Path:
        """``/var/crash``."""
        return self.at("/var/crash")


@dataclass(frozen=True)
class OsRelease:
    """
    The fields of ``/etc/os-release`` Noust looks at.

    Attributes:
        id: ``ID``, such as ``ubuntu``.
        id_like: ``ID_LIKE`` split into words.
        version_id: ``VERSION_ID``, such as ``24.04``.
        codename: ``VERSION_CODENAME``.
        pretty_name: ``PRETTY_NAME``, what to show a person.
        variant_id: ``VARIANT_ID``; ``transactional`` marks openSUSE MicroOS.
        support_end: ``SUPPORT_END`` (ISO date) when the distribution states it.
    """

    id: str = "linux"
    id_like: tuple[str, ...] = ()
    version_id: str = ""
    codename: str = ""
    pretty_name: str = "Linux"
    variant_id: str = ""
    support_end: str = ""


def read_os_release(host: HostPaths | None = None) -> OsRelease:
    """
    Read the distribution's identity.

    Args:
        host: Where the files are; the real machine by default.

    Returns:
        What ``/etc/os-release`` (or ``/usr/lib/os-release``) says, or the
        generic ``Linux`` when neither can be read.
    """
    paths = host or HostPaths()
    for candidate in (paths.os_release, paths.usr_os_release):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        fields: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            try:
                words = shlex.split(raw)
            except ValueError:
                words = [raw.strip("\"'")]
            fields[key] = words[0] if words else ""
        return OsRelease(
            id=fields.get("ID", "linux"),
            id_like=tuple(fields.get("ID_LIKE", "").split()),
            version_id=fields.get("VERSION_ID", ""),
            codename=fields.get("VERSION_CODENAME", ""),
            pretty_name=fields.get("PRETTY_NAME") or fields.get("NAME") or "Linux",
            variant_id=fields.get("VARIANT_ID", ""),
            support_end=fields.get("SUPPORT_END", ""),
        )
    return OsRelease()


@dataclass(frozen=True)
class Platform:
    """
    What this machine can do about its own packages, decided once.

    Attributes:
        family: Which package manager drives updates.
        program: Its executable, for messages.
        dnf5: dnf is dnf5 (Fedora 41 and later, RHEL 10), whose output differs.
        rolling: A rolling release (openSUSE Tumbleweed): every update is "the
            security update", so there is no security-only subset.
        transactional: The root is read-only or image-based; packages change
            through a transaction and a reboot, not in place.
        container: What kind of container this is, or None on a machine.
        systemd: The machine was booted with systemd, so a transient unit can run.
        os: The distribution's identity.
    """

    family: PackageFamily
    program: str = ""
    dnf5: bool = False
    rolling: bool = False
    transactional: bool = False
    container: str | None = None
    systemd: bool = True
    os: OsRelease = OsRelease()

    @property
    def updates_supported(self) -> bool:
        """Whether Noust lists and applies updates here."""
        return self.family is not PackageFamily.NONE and not self.transactional

    @property
    def security_scope_supported(self) -> bool:
        """Whether "security updates only" means something on this system."""
        return self.updates_supported and not self.rolling

    def why_updates_unsupported(self) -> str:
        """
        Say why updates cannot be managed here.

        Returns:
            One sentence for the operator, empty when updates are supported.
        """
        if self.transactional:
            return (
                "This system installs packages through transactions and a reboot "
                "(transactional-update or an image-based system), so Noust only "
                "reports its state. Update it with its own tool."
            )
        if self.family is PackageFamily.NONE:
            return (
                "Noust drives apt, dnf and zypper, and none of them is installed here. "
                "Update this system with its own package manager."
            )
        return ""


_cached: Platform | None = None


def reset_platform_cache() -> None:
    """Forget the detected platform, so the next call detects it again."""
    global _cached
    _cached = None


def detect_platform(
    runner: CommandRunner | None = None,
    host: HostPaths | None = None,
    *,
    refresh: bool = False,
) -> Platform:
    """
    Detect the platform once and remember it.

    Args:
        runner: The command runner; the process-wide one by default.
        host: Where the system files are.
        refresh: Detect again instead of answering from memory.

    Returns:
        The platform. Cached because none of it changes while the console runs,
        and because asking dnf which version it is costs a process.
    """
    global _cached
    if _cached is not None and not refresh:
        return _cached
    run = runner or get_runner()
    paths = host or HostPaths()
    os_release = read_os_release(paths)
    family, program = detect_family(run)

    dnf5 = False
    if family is PackageFamily.DNF:
        version = run.run(["dnf", "--version"], timeout=PROBE_TIMEOUT)
        dnf5 = "dnf5" in version.stdout.lower() or run.exists("dnf5")

    transactional = (
        run.exists("transactional-update")
        or os_release.variant_id == "transactional"
        or paths.ostree_marker.exists()
    )
    rolling = (
        os_release.id == "opensuse-tumbleweed" or "tumbleweed" in os_release.pretty_name.lower()
    )

    container = None
    virt = run.run(["systemd-detect-virt", "-c"], timeout=PROBE_TIMEOUT)
    kind = virt.stdout.strip()
    if virt.success and kind and kind != "none":
        container = kind

    platform = Platform(
        family=family,
        program=program,
        dnf5=dnf5,
        rolling=rolling,
        transactional=transactional,
        container=container,
        systemd=paths.systemd_marker.is_dir(),
        os=os_release,
    )
    _cached = platform
    return platform


def read_text(path: Path) -> str | None:
    """
    Read a small system file, treating "not there" and "not readable" as absence.

    Args:
        path: The file.

    Returns:
        Its text, or None when it cannot be read. Callers that need to tell the
        two apart do not use this.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def running_kernel() -> str:
    """
    Name the kernel that is running.

    Returns:
        ``uname -r``, such as ``6.8.0-45-generic``.
    """
    return os.uname().release


def natural_key(text: str) -> list[tuple[int, int, str]]:
    """
    Sort key that orders version strings the way a person reads them.

    Args:
        text: A version such as ``6.8.0-100-generic``.

    Returns:
        A key in which ``100`` sorts after ``45``, which plain string order gets
        wrong.
    """
    return [
        (1, int(part), "") if part.isdigit() else (0, 0, part)
        for part in re.split(r"(\d+)", text)
        if part != ""
    ]


def newest_installed_kernel(host: HostPaths | None = None) -> str | None:
    """
    Find the newest kernel installed in ``/boot``.

    Args:
        host: Where the system files are.

    Returns:
        Its release string (``6.8.0-100-generic``), or None when ``/boot`` holds
        no kernel image, as in a container or on a machine that boots from
        outside its own disk.
    """
    paths = host or HostPaths()
    try:
        images = [entry.name for entry in paths.boot.iterdir() if entry.name.startswith("vmlinuz-")]
    except OSError:
        return None
    releases = [name.removeprefix("vmlinuz-") for name in images]
    return max(releases, key=natural_key) if releases else None
