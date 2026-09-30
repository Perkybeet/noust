# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
openSUSE: zypper and os-update.

zypper keeps "security" on patches, not on packages: a patch is a named bundle
that says which packages it replaces and whether it is a security fix. So the
listing has two kinds of entry, and the security count is the number of security
patches; applying only those is ``zypper patch --category security``, which
needs no package list.

Tumbleweed is the exception that proves it: a rolling release has no patches,
every update is the current state of the distribution, and "security only" does
not exist there. It is refused with that explanation rather than approximated.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from noust.managers.server.errors import ServerError, UnsupportedHostError
from noust.managers.server.host import read_text
from noust.managers.server.pkg.base import (
    COMMON_ENV,
    LIST_TIMEOUT,
    NEEDRESTART_TIMEOUT,
    REFRESH_TIMEOUT,
    AutoUpdates,
    PackageBackend,
    PackageUpdate,
    PendingUpdates,
    RebootStatus,
    RestartProbe,
    UpdateScope,
    env_with,
    file_age_seconds,
    iso_from_mtime,
)

#: ``zypper needs-rebooting`` exits with this when a reboot is due.
EXIT_REBOOT_NEEDED = 102

#: How long zypper waits for another zypper (or PackageKit) to let go, in seconds.
#: Its default is not to wait: it exits 7 at once.
ZYPP_LOCK_TIMEOUT = 120

#: The header of a block of package names in a zypper transaction summary.
_REMOVED_BLOCK = re.compile(r"^The following (?:\d+ )?packages? (?:are|is) going to be REMOVED:")

_UNIT_NAME = re.compile(r"^[A-Za-z0-9:_.@\\-]+$")

OS_UPDATE_TIMER = "os-update.timer"


def parse_updates_xml(text: str) -> list[PackageUpdate]:
    """
    Read ``zypper --xmlout list-updates``.

    Args:
        text: The XML zypper printed.

    Returns:
        One entry per ``<update>``: a package, or a patch with its category.

    Raises:
        ServerError: The output is not the XML it should be.
    """
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ServerError(
            "zypper printed a document type declaration, which it never does",
            "Refusing to parse it. Run 'zypper --xmlout list-updates' by hand and look at it.",
            output=text[:2000],
        )
    start = text.find("<?xml")
    try:
        root = ET.fromstring(text[start:] if start >= 0 else text)  # noqa: S314
    except ET.ParseError as exc:
        raise ServerError(
            "Could not read the list of updates zypper printed",
            f"The XML is not well formed: {exc}",
            output=text[:2000],
        ) from exc

    updates: list[PackageUpdate] = []
    for element in root.iter("update"):
        kind = element.get("kind", "package")
        category = element.get("category", "")
        updates.append(
            PackageUpdate(
                name=element.get("name", ""),
                installed=element.get("edition-old") or None,
                candidate=element.get("edition", ""),
                security=kind == "patch" and category == "security",
                origin=element.get("category", "") if kind == "patch" else "",
                kind=kind,
                severity=element.get("severity") or None,
            )
        )
    return updates


def parse_removed(text: str) -> list[str]:
    """
    Read which packages a dry run of ``zypper dup`` would remove.

    Args:
        text: The transaction summary.

    Returns:
        The package names of the "going to be REMOVED" block.
    """
    names: list[str] = []
    in_block = False
    for line in text.splitlines():
        if _REMOVED_BLOCK.match(line.strip()):
            in_block = True
            continue
        if in_block:
            if not line.startswith(" ") or not line.strip():
                in_block = False
                continue
            names.extend(line.split())
    return names


def set_shell_var(text: str, key: str, value: str) -> str:
    """
    Set ``KEY=value`` in a shell-style configuration file, keeping the rest.

    Args:
        text: The file as it is.
        key: The variable.
        value: Its value.

    Returns:
        The file with the variable set, replaced in place or appended.
    """
    lines = text.splitlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def read_shell_var(text: str, key: str) -> str | None:
    """
    Read ``KEY=value`` from a shell-style configuration file.

    Args:
        text: The file.
        key: The variable.

    Returns:
        The value without its quotes, or None when it is not set.
    """
    value: str | None = None
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*?)\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match and not line.lstrip().startswith("#"):
            value = match.group(1).strip("\"'")
    return value


class ZypperBackend(PackageBackend):
    """Updates through zypper, and the state of os-update."""

    name = "zypper"
    LOCK_PROCESSES = ("zypper", "rpm", "packagekitd", "cloud-init")

    def env(self) -> dict[str, str]:
        return env_with(COMMON_ENV, {"ZYPP_LOCK_TIMEOUT": str(ZYPP_LOCK_TIMEOUT)})

    # -- listing --------------------------------------------------------------

    def _list(self, kind: str) -> list[PackageUpdate]:
        """
        Ask zypper for one kind of update.

        Args:
            kind: ``package`` or ``patch``.

        Returns:
            The parsed updates.

        Raises:
            ServerError: zypper failed.
        """
        result = self.probe(
            [
                "zypper",
                "--non-interactive",
                "--xmlout",
                "--no-refresh",
                "list-updates",
                "-t",
                kind,
            ],
            timeout=LIST_TIMEOUT,
        )
        self.require(
            result,
            "Could not list the pending updates",
            "zypper failed. Run 'zypper refresh' and check that its repositories are reachable.",
        )
        return parse_updates_xml(result.stdout)

    def list_updates(self) -> PendingUpdates:
        updates = self._list("package")
        notes: list[str] = []
        if self.platform.rolling:
            notes.append(
                "openSUSE Tumbleweed is a rolling release: every update is the current "
                "state of the distribution, so there is no security-only subset."
            )
        else:
            updates += self._list("patch")
            notes.append(
                "openSUSE marks security on patches, not on packages: the security "
                "count is the number of security patches."
            )
        now = datetime.now(timezone.utc)
        return PendingUpdates(
            packages=updates,
            checked_at=now.isoformat(),
            lists_age_seconds=file_age_seconds(
                self.host.at("/var/cache/zypp/raw"), now.timestamp()
            ),
            security_scope=not self.platform.rolling,
            notes=notes,
        )

    def removals(self, scope: UpdateScope, *, full: bool) -> list[str]:
        if not self.platform.rolling or scope is not UpdateScope.ALL:
            return []
        result = self.probe(
            ["zypper", "--non-interactive", "dist-upgrade", "--dry-run"], timeout=LIST_TIMEOUT
        )
        self.require(result, "Could not simulate the distribution upgrade")
        return parse_removed(result.stdout)

    # -- acting ---------------------------------------------------------------

    def refresh_argv(self) -> list[str]:
        return ["zypper", "--non-interactive", "--quiet", "refresh"]

    def refresh_timeout(self) -> int:
        return REFRESH_TIMEOUT

    def upgrade_argv(self, scope: UpdateScope, packages: Sequence[str], *, full: bool) -> list[str]:
        if scope is UpdateScope.SECURITY:
            if self.platform.rolling:
                raise UnsupportedHostError(
                    "Tumbleweed has no security-only updates",
                    "It is a rolling release: update everything, or nothing.",
                )
            return ["zypper", "--non-interactive", "patch", "--category", "security"]
        if self.platform.rolling:
            return ["zypper", "--non-interactive", "dup"]
        if full:
            raise UnsupportedHostError(
                "A distribution upgrade is not offered on openSUSE Leap",
                "It moves the system to another release. Provision a new server instead.",
            )
        return ["zypper", "--non-interactive", "update"]

    def install_argv(self, packages: Sequence[str]) -> list[str]:
        return ["zypper", "--non-interactive", "install", *packages]

    def repair_commands(self) -> list[list[str]]:
        return []

    def clean_cache_argv(self) -> list[str] | None:
        return ["zypper", "clean", "--all"]

    # -- reboot and services ----------------------------------------------------

    def restart_probe(self) -> RestartProbe:
        result = self.probe(["zypper", "needs-rebooting"], timeout=NEEDRESTART_TIMEOUT)
        flag = self.host.zypper_reboot_needed
        required = result.exit_code == EXIT_REBOOT_NEEDED or flag.exists()
        services = []
        for line in self.probe(
            ["zypper", "ps", "-sss"], timeout=NEEDRESTART_TIMEOUT
        ).stdout.splitlines():
            unit = line.strip()
            if unit and _UNIT_NAME.match(unit):
                services.append(unit if "." in unit else f"{unit}.service")
        reasons = (
            ("zypper says a reboot is suggested for the updates installed",) if required else ()
        )
        return RestartProbe(
            reboot=RebootStatus(
                required=required,
                reasons=reasons,
                since=iso_from_mtime(flag) if flag.exists() else None,
                source="zypper needs-rebooting" if required else "",
            ),
            services=tuple(services),
        )

    # -- automatic updates -------------------------------------------------------

    def auto_status(self) -> AutoUpdates:
        if self.platform.transactional:
            return AutoUpdates(
                mechanism="none",
                detail=(
                    "This is a transactional system: transactional-update applies its own "
                    "updates and Noust only reports the state."
                ),
            )
        if not self.runner.exists("os-update"):
            return AutoUpdates(
                mechanism="none",
                detail=(
                    "This openSUSE has no automatic update mechanism of its own (os-update "
                    "arrived with Leap 15.6). Schedule zypper yourself."
                ),
            )
        text = read_text(self.host.os_update_conf) or ""
        command = read_shell_var(text, "UPDATE_CMD") or "up"
        reboot = read_shell_var(text, "REBOOT_CMD") or "none"
        enabled = (
            self.probe(["systemctl", "is-enabled", OS_UPDATE_TIMER]).stdout.strip() == "enabled"
        )
        return AutoUpdates(
            mechanism="os-update",
            supported=True,
            installed=True,
            enabled=enabled,
            security_only=(command == "security") if enabled else None,
            reboots=reboot != "none",
        )

    def set_auto(
        self, enabled: bool, security_only: bool, on_line: Callable[[str], None]
    ) -> list[str]:
        status = self.auto_status()
        if not status.supported:
            raise UnsupportedHostError("Automatic updates cannot be changed here", status.detail)
        steps: list[str] = []
        if enabled:
            if security_only and self.platform.rolling:
                raise UnsupportedHostError(
                    "Tumbleweed has no security-only updates",
                    "Turn the automatic updates on for everything, or leave them off.",
                )
            command = "security" if security_only else ("dup" if self.platform.rolling else "up")
            conf = self.host.os_update_conf
            current = read_text(conf) or ""
            text = set_shell_var(
                set_shell_var(current, "UPDATE_CMD", command), "REBOOT_CMD", "none"
            )
            if text != current:
                if current and not conf.with_name(conf.name + ".noust-bak").exists():
                    self.fs.write_text(conf.with_name(conf.name + ".noust-bak"), current)
                self.fs.write_text(conf, text)
                steps.append(f"Set UPDATE_CMD={command} and REBOOT_CMD=none in {conf}")
            self.require(
                self.runner.run(["systemctl", "enable", "--now", OS_UPDATE_TIMER], timeout=60),
                f"Could not enable {OS_UPDATE_TIMER}",
            )
            steps.append(f"Enabled {OS_UPDATE_TIMER}")
        else:
            self.require(
                self.runner.run(["systemctl", "disable", "--now", OS_UPDATE_TIMER], timeout=60),
                f"Could not disable {OS_UPDATE_TIMER}",
            )
            steps.append(f"Disabled {OS_UPDATE_TIMER}")

        after = self.auto_status()
        if after.enabled != enabled:
            raise ServerError(
                "The setting was applied but is not in effect",
                "Check 'systemctl list-timers' and /etc/os-update.conf.",
                output="\n".join(steps),
            )
        return steps


__all__ = [
    "ZypperBackend",
    "parse_removed",
    "parse_updates_xml",
    "read_shell_var",
    "set_shell_var",
]
