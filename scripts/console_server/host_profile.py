# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The machine the console server models: a VPS, or the container the Noust central runs in on a NAS.

The sandbox's machine is an Ubuntu VPS with everything on it, so the faults that only another kind of
machine has never showed: the owner's hub is a container (``packaging/container/Dockerfile``: Python,
tini, ssh, openssl and git, run by an account that is not root), and in it there is no systemd to read
a clock from, no journal to search, no sshd to ask, no nginx to write a site for and nobody to be root.

A profile is that difference and nothing else, so the same seeds and the same runner serve both:

- which programs the machine has. The ones it lacks answer 127 ("Command not found"), exactly as the
  real :class:`~noust.core.runner.CommandRunner` reports a missing binary, and the runner's
  ``exists`` says so;
- whether the host tree has systemd (``/run/systemd/system``) and a journal, which is how
  :func:`noust.managers.server.host.detect_platform` knows;
- whether Noust runs as root, which is what ``noust.central.machine.is_root`` answers.

What the session says a machine can do (``central.features``) is then *read* from this model by the
code that reads a real machine, never written down here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostProfile:
    """
    One kind of machine.

    Attributes:
        name: The value of ``--host-profile``.
        programs: What is on its path; None for the VPS's own list (the runner's).
        packages: What its package manager says is installed, as ``(package, version)``; None for
            the VPS's own (Server > Software reads it).
        systemd: It was booted with systemd, and keeps a journal.
        root: Noust runs as root on it.
    """

    name: str
    programs: tuple[str, ...] | None
    packages: tuple[tuple[str, str], ...] | None
    systemd: bool
    root: bool


#: What python:3.12-slim-bookworm plus the central image's apt-get has on its path: the image's own
#: programs, and the shell and coreutils every Debian has.
_CONTAINER_PROGRAMS = (
    # What the Dockerfile installs and Noust calls.
    "ssh",
    "ssh-keygen",
    "ssh-keyscan",
    "openssl",
    "git",
    "python3",
    "python",
    "tini",
    "noust",
    # The package manager is there, as in any Debian; nothing is installed with it.
    "apt-get",
    "apt",
    "apt-cache",
    "dpkg",
    "dpkg-query",
    # The shell and the coreutils.
    "sh",
    "bash",
    "dash",
    "echo",
    "true",
    "false",
    "cat",
    "ls",
    "df",
    "du",
    "find",
    "grep",
    "sed",
    "uname",
    "hostname",
    "id",
    "date",
    "stat",
    "head",
    "tail",
    "sort",
    "readlink",
    "touch",
    "mkdir",
    "rm",
    "cp",
    "mv",
    "chmod",
    "chown",
    "env",
    "sleep",
    "tr",
    "wc",
    "cut",
    "uniq",
    "tee",
    "xargs",
    "which",
    "test",
    "ln",
    "basename",
)

#: What the image installs (the Dockerfile's ``apt-get install``) and Noust itself.
_CONTAINER_PACKAGES = (
    ("noust", "4.2.1"),
    ("tini", "0.19.0-1"),
    ("openssh-client", "1:9.2p1-2+deb12u3"),
    ("openssl", "3.0.15-1~deb12u1"),
    ("git", "1:2.39.5-0+deb12u1"),
    ("ca-certificates", "20230311"),
)

#: The default: the Ubuntu VPS the sandbox has always been, administered by root.
SERVER = HostProfile("server", programs=None, packages=None, systemd=True, root=True)

#: The Noust central's container.
CONTAINER = HostProfile(
    "container",
    programs=_CONTAINER_PROGRAMS,
    packages=_CONTAINER_PACKAGES,
    systemd=False,
    root=False,
)

PROFILES: dict[str, HostProfile] = {profile.name: profile for profile in (SERVER, CONTAINER)}


def use(profile: HostProfile) -> None:
    """
    Make Noust say, as it reads whether it is root, what the profile's machine says.

    The sandbox is run by the developer, who is not root, whatever machine it models; the session's
    ``root`` fact is read through :func:`noust.central.machine.is_root`, which is replaced here
    (the way the rest of this package replaces what it models) so that a modelled VPS has a Noust
    that is root and the modelled container one that is not.

    Args:
        profile: The machine being modelled.
    """
    from noust.central import machine

    machine.is_root = lambda: profile.root
    machine.reset_machine_facts()
