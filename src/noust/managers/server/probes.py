# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The commands the server managers run only to look, declared one by one.

``--dry-run`` lets a command through when it only observes, because a rehearsal
that cannot look at the machine reports fiction: the plan of an update is what
``apt-get -s`` says, and without it a rehearsed update has nothing to plan. What
counts as "only observes" is decided by the command runner
(:func:`noust.core.runner.is_read_only`), and a program name is not enough to
decide it: ``apt-get -s upgrade`` looks and ``apt-get upgrade`` does not, and
``ufw allow 22 comment status`` is not a status.

This is the server managers' side of that decision: every argv shape they run to
look, written out. An entry is a tuple of literal arguments; ``...`` at its end
means "then any further arguments" (a unit name, a path, a filter), and a
``*`` inside an argument matches any text in that position. The runner reads
:data:`READ_ONLY_PROBES` and nothing else of this package, and a test runs every
read path of the managers and fails on a command that is not declared here, so
neither a new probe nor a renamed flag can drift out of it unseen.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence

#: Every argv shape a manager runs to look and not to change.
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    # What machine this is.
    ("systemd-detect-virt", "-c"),
    ("dnf", "--version"),
    # apt: the simulation is what the upgrade would do, and changes nothing.
    ("apt-get", "-s", "-o", "Debug::NoLocking=1", "--with-new-pkgs", "upgrade"),
    ("apt-get", "-s", "-o", "Debug::NoLocking=1", "full-upgrade"),
    ("apt-mark", "showhold"),
    ("dpkg", "--audit"),
    ("dpkg-query", "-W", ...),
    ("apt-config", "dump"),
    ("needrestart", "-b"),
    # dnf and rpm.
    ("dnf", "-q", "-y", "check-update"),
    ("dnf", "-q", "-y", "check-update", "--security"),
    ("dnf", "-q", "updateinfo", "list", "--security"),
    ("dnf", "needs-restarting", "-r"),
    ("dnf", "needs-restarting", "-s"),
    ("rpm", "-q", ...),
    # zypper.
    (
        "zypper",
        "--non-interactive",
        "--xmlout",
        "--no-refresh",
        "list-updates",
        "-t",
        "*",
    ),
    ("zypper", "--non-interactive", "dist-upgrade", "--dry-run"),
    ("zypper", "needs-rebooting"),
    ("zypper", "ps", "-sss"),
    # systemd: state and listings, never a verb that acts.
    ("systemctl", "is-enabled", ...),
    ("systemctl", "is-system-running"),
    ("systemctl", "--failed", "--no-legend", "--plain", "--no-pager"),
    ("systemctl", "show", ...),
    ("systemctl", "list-timers", ...),
    ("journalctl", ...),
    ("timedatectl", "show"),
    ("timedatectl", "status"),
    ("hostnamectl", "--json=short"),
    ("hostnamectl", "status"),
    ("chronyc", "-c", "tracking"),
    ("findmnt", "--verify"),
    ("findmnt", "-no", "FSTYPE", "-T", "*"),
    ("swapon", "--show", "--bytes", "--raw", "--noheadings"),
    # Disks and containers.
    ("ionice", "-c3", "nice", "-n", "19", "du", "-sx", "-B1", "--", "*"),
    ("docker", "system", "df", "--format", "json"),
    ("docker", "image", "ls", "--format", "{{json .}}"),
    ("docker", "ps", "-a", "--format", "{{.Image}}"),
)


def matches(argv: Sequence[str], probe: Sequence[object]) -> bool:
    """
    Tell whether a command is one shape of a probe.

    Args:
        argv: The command.
        probe: One entry of :data:`READ_ONLY_PROBES`.

    Returns:
        True when every literal argument is equal (``*`` matching any text) and,
        for an entry ending in ``...``, whatever follows is not looked at.
    """
    open_ended = bool(probe) and probe[-1] is ...
    fixed = probe[:-1] if open_ended else probe
    if len(argv) < len(fixed) or (not open_ended and len(argv) != len(fixed)):
        return False
    return all(
        fnmatch.fnmatchcase(actual, str(expected))
        for actual, expected in zip(argv, fixed, strict=False)
    )


def is_declared_probe(argv: Sequence[str]) -> bool:
    """
    Tell whether a command is one the server managers declare read-only.

    Args:
        argv: The command.

    Returns:
        True when it matches an entry of :data:`READ_ONLY_PROBES`.
    """
    return any(matches(argv, probe) for probe in READ_ONLY_PROBES)
