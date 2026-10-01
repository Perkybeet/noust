# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The hardening checks Noust runs, by stable id.

The ids are a contract: the console translates by id, an accepted risk is
recorded against an id, and the ENS compliance check (``noust ens check``)
maps its measures onto them. An id is never renamed or reused; a check that
goes away keeps its id retired.

There is no score. Scores in the style of Lynis invite polishing the number;
the checks are counted by severity instead, as cPanel's Security Advisor does.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Severities, most serious first.
SEVERITIES = ("critical", "warning", "low", "info")


@dataclass(frozen=True)
class CheckSpec:
    """
    What a check is about, independent of what it finds.

    Attributes:
        id: The stable id.
        group: ``ssh``, ``fail2ban``, ``firewall``, ``updates``, ``system`` or ``noust``.
        title: What a failing check means, in a sentence.
        severity: How serious a failure is, unless the check says otherwise
            (``fw.public_listener`` is critical for a database, less for a panel).
    """

    id: str
    group: str
    title: str
    severity: str


CATALOG: dict[str, CheckSpec] = {
    spec.id: spec
    for spec in (
        CheckSpec(
            "ssh.root_password", "ssh", "Root can log in over SSH with a password", "critical"
        ),
        CheckSpec("ssh.password_auth", "ssh", "SSH accepts passwords", "warning"),
        CheckSpec("ssh.root_login", "ssh", "Root can log in over SSH", "warning"),
        CheckSpec(
            "ssh.empty_passwords", "ssh", "Accounts can log in with an empty password", "critical"
        ),
        CheckSpec("ssh.keys", "ssh", "SSH keys that are weak, ignored or missing", "warning"),
        CheckSpec("ssh.defaults", "ssh", "SSH limits are looser than recommended", "low"),
        CheckSpec("ssh.loglevel", "ssh", "SSH does not log which key logged in", "info"),
        CheckSpec(
            "f2b.missing", "fail2ban", "Nothing slows down password guessing on SSH", "warning"
        ),
        CheckSpec("f2b.no_sshd_jail", "fail2ban", "fail2ban does not watch SSH", "warning"),
        CheckSpec("fw.inactive", "firewall", "No firewall is active", "critical"),
        CheckSpec(
            "fw.public_listener",
            "firewall",
            "A database or internal service is reachable from the internet",
            "critical",
        ),
        CheckSpec(
            "fw.docker_bypass", "firewall", "Docker publishes ports around the firewall", "critical"
        ),
        CheckSpec("fw.ipv6_mismatch", "firewall", "The firewall does not filter IPv6", "info"),
        CheckSpec(
            "fw.console_public", "firewall", "The console is reachable without TLS", "critical"
        ),
        CheckSpec("upd.security_pending", "updates", "Security updates are pending", "critical"),
        CheckSpec(
            "upd.reboot_required", "updates", "A reboot is needed to finish updates", "warning"
        ),
        CheckSpec(
            "upd.stale_services", "updates", "Services run with outdated libraries", "warning"
        ),
        CheckSpec("upd.auto_disabled", "updates", "Automatic security updates are off", "warning"),
        CheckSpec("upd.pkg_broken", "updates", "Packages are half installed", "critical"),
        CheckSpec("upd.lists_stale", "updates", "Package lists are more than a week old", "info"),
        CheckSpec("os.eol", "system", "The operating system is out of support", "critical"),
        CheckSpec("time.unsynced", "system", "The clock is not synchronised", "warning"),
        CheckSpec("mem.no_swap", "system", "Little memory and no swap", "warning"),
        CheckSpec("disk.full", "system", "A disk is nearly full", "warning"),
        CheckSpec("sys.degraded", "system", "Some system services have failed", "warning"),
        CheckSpec("sys.uid0", "system", "Another account has root's user id", "critical"),
        CheckSpec("sys.selinux", "system", "SELinux is not enforcing", "info"),
        CheckSpec(
            "sys.journal_volatile", "system", "The system journal is lost at every reboot", "info"
        ),
        CheckSpec(
            "noust.web_not_unit", "noust", "The console does not start with the server", "warning"
        ),
        CheckSpec(
            "noust.units_not_enabled",
            "noust",
            "Application services do not start with the server",
            "warning",
        ),
        CheckSpec("noust.fleet_key_root", "noust", "A central's tunnel logs in as root", "info"),
        CheckSpec(
            "noust.inline_secrets",
            "noust",
            "Application units carry variables any local user can read",
            "critical",
        ),
    )
}


def spec(check_id: str) -> CheckSpec:
    """
    Look a check up.

    Args:
        check_id: Its id.

    Returns:
        Its spec.

    Raises:
        KeyError: There is no such check.
    """
    return CATALOG[check_id]
