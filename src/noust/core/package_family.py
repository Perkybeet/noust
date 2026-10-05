# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which package manager this machine uses, and what to tell its operator to type.

There is one answer to "is this apt, dnf or zypper": :func:`detect_family`. The
server's update machinery (``managers/server/host.py``), the setup wizard and
every message that says "install X" ask it, because a hint that lists the three
distributions' commands makes the operator pick the one that is theirs, and on
an Ubuntu server the line starting ``zypper install`` is noise that reads like
a mistake.

The family is found by the executable that is there, not by ``/etc/os-release``:
it is the package manager Noust would actually run, which is what a hint is for.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import Enum

from noust.core.runner import CommandRunner, get_runner

log = logging.getLogger(__name__)


class PackageFamily(str, Enum):
    """The package managers whose commands Noust knows."""

    APT = "apt"
    DNF = "dnf"
    ZYPPER = "zypper"
    NONE = "none"


#: The executable each family is recognised by, in the order they are asked and
#: the order a list of them is written. apt is first because a machine that
#: carries both apt and rpm tools (a Debian with ``alien``) installs through apt.
FAMILY_PROGRAMS: tuple[tuple[PackageFamily, str], ...] = (
    (PackageFamily.APT, "apt-get"),
    (PackageFamily.DNF, "dnf"),
    (PackageFamily.ZYPPER, "zypper"),
)

#: What an operator types to install a package with each family's manager.
INSTALL_COMMANDS: Mapping[PackageFamily, str] = {
    PackageFamily.APT: "apt install",
    PackageFamily.DNF: "dnf install",
    PackageFamily.ZYPPER: "zypper install",
}


def detect_family(runner: CommandRunner) -> tuple[PackageFamily, str]:
    """
    Find the package manager by the executable that is there.

    Args:
        runner: Used only to ask whether a program exists.

    Returns:
        The family and its executable; ``(PackageFamily.NONE, "")`` when none of
        apt, dnf and zypper is installed.
    """
    for family, program in FAMILY_PROGRAMS:
        if runner.exists(program):
            return family, program
    return PackageFamily.NONE, ""


def package_family(runner: CommandRunner | None = None) -> PackageFamily:
    """
    Name this machine's package manager for a message, without ever failing it.

    Cheap (a lookup on ``PATH``, no process) and never cached, so a test that
    describes another machine through its runner gets that machine. A message
    about a missing package must not itself break, so a probe that cannot be
    made counts as "unknown".

    Args:
        runner: The command runner; the process-wide one by default.

    Returns:
        The family, or :attr:`PackageFamily.NONE` when it cannot be determined.
    """
    try:
        return detect_family(runner or get_runner())[0]
    except OSError as exc:
        log.debug("Could not look for a package manager: %s", exc)
        return PackageFamily.NONE


def _applicable(
    offered: Mapping[PackageFamily, object], detected: PackageFamily
) -> tuple[PackageFamily, ...]:
    """
    Decide which of the families a message knows about to show.

    Args:
        offered: The families the message has something for.
        detected: This machine's family, :attr:`PackageFamily.NONE` when unknown.

    Returns:
        This machine's family alone when the message has something for it;
        otherwise every family it has, in :data:`FAMILY_PROGRAMS` order, because
        a message that says nothing helps nobody and the operator can tell which
        one is theirs.
    """
    if detected in offered:
        return (detected,)
    return tuple(known for known, _ in FAMILY_PROGRAMS if known in offered)


def for_this_machine(
    options: Mapping[PackageFamily, str],
    *,
    runner: CommandRunner | None = None,
    family: PackageFamily | None = None,
) -> list[str]:
    """
    Pick, out of what is known per package manager, what applies here.

    An empty string says the family has nothing to offer: it is not listed, and
    it is not replaced by another family's text either.

    Args:
        options: Text per family: a command, or a step that only that family needs.
        runner: The command runner used to detect the family.
        family: The family, when the caller already knows it (a test, or code that
            holds a :class:`~noust.managers.server.host.Platform`).

    Returns:
        This machine's entry; every entry when its family is unknown or has none,
        without repeats (two families sharing one step show it once).
    """
    detected = family if family is not None else package_family(runner)
    chosen = (options[known] for known in _applicable(options, detected))
    return list(dict.fromkeys(text for text in chosen if text))


def install_hint(
    packages: str | Mapping[PackageFamily, str],
    *,
    notes: Mapping[PackageFamily, str] | None = None,
    runner: CommandRunner | None = None,
    family: PackageFamily | None = None,
) -> str:
    """
    Say how to install something on this machine, and only on this machine.

    Args:
        packages: What to install: one name that every family calls it, or each
            family's own names (``{PackageFamily.APT: "apache2", PackageFamily.DNF:
            "httpd"}``), several separated by spaces. A family with an empty entry
            has no package.
        notes: What a family needs besides the install, one sentence per family
            (the openSUSE ``php-fpm.conf`` copy). Shown with the commands of the
            same families, so an apt machine never reads an openSUSE step.
        runner: The command runner used to detect the family.
        family: The family, when the caller already knows it.

    Returns:
        The command (``apt install nginx``), followed by its note when it has
        one. When the family is unknown, or there is no package for it, every
        command separated by ``;`` and every note after them: the full list is
        the fallback, never the default.
    """
    by_family = dict.fromkeys(INSTALL_COMMANDS, packages) if isinstance(packages, str) else packages
    commands = {
        known: f"{INSTALL_COMMANDS[known]} {names}"
        for known, names in by_family.items()
        if names and known in INSTALL_COMMANDS
    }
    detected = family if family is not None else package_family(runner)
    text = "; ".join(for_this_machine(commands, family=detected))
    shown = _applicable(commands, detected)
    sentences = [notes[known] for known in shown if notes and notes.get(known)]
    if not sentences:
        return text
    return f"{text}. {' '.join(dict.fromkeys(sentences))}" if text else " ".join(sentences)
