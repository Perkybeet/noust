# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Fedora, RHEL and its rebuilds: dnf (4 and 5), rpm and dnf-automatic.

dnf can already say what is a security update: ``check-update --security`` is
the same listing narrowed to the advisory subset, and ``upgrade --security``
applies exactly that. So unlike apt there is nothing to reconstruct; the
security flag of a package is "it is in the security listing".

Two dialects are read. dnf 4 prints ``name.arch  version  repo`` and, on a
terminal narrow enough, wraps a long line in two. dnf 5 prints a table. The
parser reads both, because RHEL 9 and Fedora 41 run on the same day and the
console has one listing.
"""

from __future__ import annotations

import configparser
import re
from collections.abc import Callable, Sequence

from noust.managers.server.errors import ServerError
from noust.managers.server.host import natural_key, newest_installed_kernel, running_kernel
from noust.managers.server.pkg.base import (
    COMMON_ENV,
    INSTALL_TIMEOUT,
    NEEDRESTART_TIMEOUT,
    REFRESH_TIMEOUT,
    AutoUpdates,
    PackageBackend,
    PackageUpdate,
    PendingUpdates,
    RebootStatus,
    RestartProbe,
    UpdateScope,
    file_age_seconds,
)

#: ``check-update`` exits 100 when there are updates, 0 when there are none.
EXIT_UPDATES_AVAILABLE = 100

#: Architectures that end a package name in the dnf 4 listing and fill the
#: second column of the dnf 5 table.
_ARCHES = frozenset(
    {"x86_64", "aarch64", "noarch", "i686", "ppc64le", "s390x", "armv7hl", "src", "i386"}
)

#: ``RHSA-2025:1234 Important/Sec. openssl-libs-1:3.2.2-6.el9_5.x86_64``.
_ADVISORY = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)$")

#: ``name-version-release.arch``, to recover the name from an advisory line.
_NVRA = re.compile(r"^(?P<name>.+)-[^-]+-[^-]+\.[^.]+$")

#: The units of dnf-automatic, from the one that installs to the generic dnf 5 one.
INSTALL_TIMER = "dnf-automatic-install.timer"
DNF5_TIMER = "dnf5-automatic.timer"
AUTOMATIC_TIMERS = (
    INSTALL_TIMER,
    "dnf-automatic-download.timer",
    "dnf-automatic.timer",
    DNF5_TIMER,
)


def _split_name_arch(token: str) -> tuple[str, str]:
    """
    Split ``bash.x86_64`` into its name and architecture.

    Args:
        token: A package as ``check-update`` prints it.

    Returns:
        Name and architecture; the architecture is empty when there is none.
    """
    name, _, arch = token.rpartition(".")
    if name and arch in _ARCHES:
        return name, arch
    return token, ""


def parse_check_update(text: str) -> list[tuple[str, str, str, str]]:
    """
    Read the listing of ``dnf check-update`` (dnf 4) or ``check-upgrade`` (dnf 5).

    Args:
        text: The command's output.

    Returns:
        ``(name, arch, version, repository)`` per package. The obsoleting
        section at the end is not an update and is left out.
    """
    rows: list[tuple[str, str, str, str]] = []
    pending: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Obsoleting Packages"):
            break
        own = line.split()
        # dnf 5 table: name, arch, version, repository, size. Read from the line
        # alone, so a leftover word of a heading cannot swallow the first row.
        if len(own) >= 4 and own[1] in _ARCHES:
            rows.append((own[0], own[1], own[2], own[3]))
            pending = []
            continue
        tokens = pending + own
        # dnf 4: name.arch, version, repository. A long line wraps after the name.
        if len(tokens) < 3:
            pending = tokens
            continue
        name, arch = _split_name_arch(tokens[0])
        if arch:
            rows.append((name, arch, tokens[1], tokens[2]))
        pending = []
    return rows


def parse_advisories(text: str) -> dict[str, tuple[str, str | None]]:
    """
    Read ``dnf updateinfo list --security``.

    Args:
        text: The command's output.

    Returns:
        For each package name, its advisory identifier and its severity
        (``None`` when the advisory states none).
    """
    found: dict[str, tuple[str, str | None]] = {}
    for line in text.splitlines():
        match = _ADVISORY.match(line.strip())
        if not match:
            continue
        advisory, kind, nvra = match.groups()
        name = _NVRA.match(nvra)
        if not name:
            continue
        severity = kind.split("/", 1)[0] if "/" in kind else None
        found.setdefault(name.group("name"), (advisory, severity))
    return found


def set_ini_value(text: str, section: str, key: str, value: str) -> str:
    """
    Set one key of an INI file, leaving comments, order and every other key alone.

    Args:
        text: The file as it is.
        section: The section, without brackets.
        key: The key.
        value: The value.

    Returns:
        The file with the key set; the section is added when it is missing.
    """
    lines = text.splitlines()
    header = f"[{section}]"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        prefix = "" if not lines or not lines[-1].strip() else "\n"
        body = "\n".join(lines)
        return (
            f"{body}{prefix}\n{header}\n{key} = {value}\n"
            if body
            else f"{header}\n{key} = {value}\n"
        )
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines)
    )
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i in range(start + 1, end):
        if pattern.match(lines[i]):
            lines[i] = f"{key} = {value}"
            break
    else:
        lines.insert(start + 1, f"{key} = {value}")
    return "\n".join(lines) + "\n"


class DnfBackend(PackageBackend):
    """Updates through dnf, and the state of dnf-automatic."""

    name = "dnf"
    LOCK_PROCESSES = ("dnf", "yum", "rpm", "packagekitd", "cloud-init")
    LOCK_FILES = ("/var/lib/rpm/.rpm.lock", "/usr/lib/sysimage/rpm/.rpm.lock")
    PID_FILES = (
        "/var/cache/dnf/metadata_lock.pid",
        "/var/lib/dnf/rpmdb_lock.pid",
        "/var/log/log_lock.pid",
    )

    def env(self) -> dict[str, str]:
        return dict(COMMON_ENV)

    # -- listing --------------------------------------------------------------

    def _listing(self, *extra: str) -> list[tuple[str, str, str, str]]:
        """
        Run ``check-update`` and read what it lists.

        Args:
            extra: Further options, such as ``--security``.

        Returns:
            The parsed rows; empty when nothing is pending.

        Raises:
            ServerError: dnf failed (exit 1), with its own output.
        """
        result = self.probe(["dnf", "-q", "-y", "check-update", *extra], timeout=REFRESH_TIMEOUT)
        if result.exit_code not in (0, EXIT_UPDATES_AVAILABLE):
            self.require(
                result,
                "Could not list the pending updates",
                "dnf failed. On RHEL, check that the system is registered and its "
                "repositories are enabled.",
            )
        return parse_check_update(result.stdout)

    def list_updates(self) -> PendingUpdates:
        from datetime import datetime, timezone

        every = self._listing()
        secure = {(name, arch) for name, arch, _v, _r in self._listing("--security")}

        advisories: dict[str, tuple[str, str | None]] = {}
        if secure:
            # The identifiers are a courtesy: a dnf that does not know the
            # subcommand must not turn the listing into an error.
            advisories = parse_advisories(
                self.probe(["dnf", "-q", "updateinfo", "list", "--security"]).stdout
            )

        packages = []
        for name, arch, version, repo in every:
            advisory, severity = advisories.get(name, (None, None))
            packages.append(
                PackageUpdate(
                    name=name,
                    installed=None,
                    candidate=version,
                    security=(name, arch) in secure,
                    kernel=name == "kernel" or name.startswith("kernel-core"),
                    origin=repo,
                    advisory=advisory,
                    severity=severity,
                )
            )
        now = datetime.now(timezone.utc)
        return PendingUpdates(
            packages=packages,
            checked_at=now.isoformat(),
            lists_age_seconds=self._cache_age(now.timestamp()),
            security_scope=True,
        )

    def _cache_age(self, now: float) -> int | None:
        """
        Age of the metadata dnf keeps.

        Args:
            now: The current time.

        Returns:
            Seconds since the newest of dnf's cache directories changed.
        """
        ages = [
            age
            for directory in ("/var/cache/dnf", "/var/cache/libdnf5")
            if (age := file_age_seconds(self.host.at(directory), now)) is not None
        ]
        return min(ages) if ages else None

    def removals(self, scope: UpdateScope, *, full: bool) -> list[str]:
        # dnf resolves a conflict by refusing, not by removing, and the packages
        # it does remove (old kernels, obsoleted ones) are what an update is.
        return []

    # -- acting ---------------------------------------------------------------

    def refresh_argv(self) -> list[str]:
        return ["dnf", "-q", "-y", "makecache", "--refresh"]

    def refresh_timeout(self) -> int:
        return REFRESH_TIMEOUT

    def upgrade_argv(self, scope: UpdateScope, packages: Sequence[str], *, full: bool) -> list[str]:
        argv = ["dnf", "-y", "upgrade", "--refresh"]
        if scope is UpdateScope.SECURITY:
            argv.append("--security")
        return argv

    def install_argv(self, packages: Sequence[str]) -> list[str]:
        return ["dnf", "-y", "install", *packages]

    def repair_commands(self) -> list[list[str]]:
        return []

    def clean_cache_argv(self) -> list[str] | None:
        # Only the downloaded packages: the metadata is small, and dropping it
        # would make the next listing slow for nothing.
        return ["dnf", "clean", "packages"]

    # -- reboot and services ----------------------------------------------------

    def restart_probe(self) -> RestartProbe:
        result = self.probe(["dnf", "needs-restarting", "-r"], timeout=NEEDRESTART_TIMEOUT)
        words = (result.stdout + result.stderr).lower()
        if "no such command" in words or "unknown argument" in words:
            return self._kernel_fallback()

        packages = [
            match.group(1).strip()
            for line in result.stdout.splitlines()
            if (match := re.match(r"^\s*\*\s+(.+)$", line))
        ]
        # 1 means "reboot needed"; 0 means not. Anything else is a failure to ask.
        required = result.exit_code == 1
        services = [
            line.strip()
            for line in self.probe(
                ["dnf", "needs-restarting", "-s"], timeout=NEEDRESTART_TIMEOUT
            ).stdout.splitlines()
            if line.strip().endswith(".service")
        ]
        return RestartProbe(
            reboot=RebootStatus(
                required=required,
                packages=tuple(packages),
                reasons=("Core libraries or services were updated since the last boot",)
                if required
                else (),
                source="dnf needs-restarting" if required else "",
            ),
            services=tuple(services),
        )

    def _kernel_fallback(self) -> RestartProbe:
        """
        Decide the reboot from the kernels alone, when the dnf plugin is missing.

        Returns:
            A probe that says the plugin is not there, with what the kernels alone say.
        """
        result = self.probe(
            ["rpm", "-q", "kernel-core", "--qf", "%{VERSION}-%{RELEASE}.%{ARCH}\\n"]
        )
        installed = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        newest = (
            max(installed, key=natural_key) if installed else newest_installed_kernel(self.host)
        )
        running = running_kernel()
        required = (
            bool(newest) and newest != running and natural_key(newest or "") > natural_key(running)
        )
        return RestartProbe(
            reboot=RebootStatus(
                required=required,
                reasons=(f"The running kernel is {running}; {newest} is installed",)
                if required
                else (),
                source="kernel" if required else "",
            ),
            available=False,
        )

    # -- automatic updates -------------------------------------------------------

    def _automatic_package(self) -> str:
        """
        Name the package that provides the automatic updates.

        Returns:
            ``dnf5-plugin-automatic`` on dnf 5, ``dnf-automatic`` before.
        """
        return "dnf5-plugin-automatic" if self.platform.dnf5 else "dnf-automatic"

    def _config(self) -> configparser.ConfigParser:
        """
        Read dnf-automatic's configuration.

        Returns:
            The parsed file; empty when it does not exist.
        """
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(self.host.dnf_automatic_conf.read_text(encoding="utf-8"))
        except (OSError, configparser.Error):
            return configparser.ConfigParser(interpolation=None)
        return parser

    def _timer_enabled(self, unit: str) -> bool:
        """
        Ask whether a timer is enabled.

        Args:
            unit: The timer.

        Returns:
            True when systemd says ``enabled``.
        """
        return self.probe(["systemctl", "is-enabled", unit]).stdout.strip() == "enabled"

    def auto_status(self) -> AutoUpdates:
        installed = self.probe(["rpm", "-q", self._automatic_package()]).success
        config = self._config()
        upgrade_type = config.get("commands", "upgrade_type", fallback="default")
        apply_updates = config.get("commands", "apply_updates", fallback="no").lower() in (
            "yes",
            "true",
            "1",
        )
        reboot = config.get("commands", "reboot", fallback="never")

        enabled = False
        if installed:
            enabled = self._timer_enabled(INSTALL_TIMER) or (
                self._timer_enabled(DNF5_TIMER) and apply_updates
            )
        return AutoUpdates(
            mechanism="dnf-automatic",
            supported=True,
            installed=installed,
            enabled=enabled,
            security_only=(upgrade_type == "security") if enabled else None,
            reboots=reboot != "never",
        )

    def set_auto(
        self, enabled: bool, security_only: bool, on_line: Callable[[str], None]
    ) -> list[str]:
        steps: list[str] = []
        status = self.auto_status()
        conf = self.host.dnf_automatic_conf
        timer = DNF5_TIMER if self.platform.dnf5 else INSTALL_TIMER

        if enabled:
            if not status.installed:
                self.stream(
                    self.install_argv([self._automatic_package()]),
                    on_line,
                    timeout=INSTALL_TIMEOUT,
                    message=f"Could not install {self._automatic_package()}",
                )
                steps.append(f"Installed {self._automatic_package()}")
            try:
                current = conf.read_text(encoding="utf-8")
            except OSError:
                current = ""
            text = set_ini_value(
                set_ini_value(
                    current, "commands", "upgrade_type", "security" if security_only else "default"
                ),
                "commands",
                "apply_updates",
                "yes",
            )
            if text != current:
                if current and not conf.with_name(conf.name + ".noust-bak").exists():
                    self.fs.write_text(conf.with_name(conf.name + ".noust-bak"), current)
                self.fs.write_text(conf, text)
                steps.append(
                    f"Set upgrade_type to {'security' if security_only else 'default'} in {conf}"
                )
            result = self.runner.run(["systemctl", "enable", "--now", timer], timeout=60)
            self.require(result, f"Could not enable {timer}")
            steps.append(f"Enabled {timer}")
        else:
            active = [unit for unit in AUTOMATIC_TIMERS if self._timer_enabled(unit)]
            if active:
                result = self.runner.run(["systemctl", "disable", "--now", *active], timeout=60)
                self.require(result, "Could not disable the automatic updates")
                steps.append(f"Disabled {', '.join(active)}")

        after = self.auto_status()
        if after.enabled != enabled:
            raise ServerError(
                "The setting was applied but is not in effect",
                "Check 'systemctl list-timers' and /etc/dnf/automatic.conf.",
                output="\n".join(steps),
            )
        return steps


__all__ = [
    "DnfBackend",
    "parse_advisories",
    "parse_check_update",
    "set_ini_value",
]
