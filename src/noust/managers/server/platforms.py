# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The platforms Noust declares, stated once.

Which distributions and which processor architectures Noust installs on, where
its packages come from, and what has actually been tested on each, is a table
here and nowhere else (rule 3). Three things read it:

- ``packaging/install.sh`` carries a generated copy of the table in its
  ``BEGIN TARGETS`` block, because a script that installs Noust cannot import
  Noust. ``tests/test_platforms.py`` fails when the two differ, and
  ``scripts/sync_install_targets.py`` rewrites the block.
- ``noust setup doctor`` tells the operator where this machine stands.
- ``docs/PLATFORMS.md`` shows the same table to a person.

The repository behind every package target is the openSUSE Build Service
project ``home:Perkybeet``. Its directories are the names OBS gives them, which
is why Leap is ``15.6`` and not ``openSUSE_Leap_15.6`` (the latter only works
because the download server redirects it). A distribution OBS does not build
for (the RHEL family, as of this table) is reachable only through pip, and the
table says so instead of leaving the operator to find out at the first install.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import date

from noust.managers.server.eol import EolStatus, status_of
from noust.managers.server.host import HostPaths, OsRelease, read_os_release

#: A package from the OBS repository of this target, installed by the family's manager.
SUPPORTED = "supported"
#: Installable but not promised: an interim release, a release past its end of
#: life, or a newer one that borrows the previous release's repository.
BEST_EFFORT = "best-effort"
#: No package exists for it; the installer builds a virtual environment with pip.
PIP_ONLY = "pip"
#: Refused by the installer unless the operator forces it.
UNSUPPORTED = "unsupported"

STATUSES: tuple[str, ...] = (SUPPORTED, BEST_EFFORT, PIP_ONLY, UNSUPPORTED)

#: Where the packages are. ``<target>`` is a :attr:`Target.repo`.
REPOSITORY_BASE = "https://download.opensuse.org/repositories/home:/Perkybeet"

#: The fingerprint of the key that signs the whole ``home:Perkybeet`` project
#: (RSA 4096, created 2025-12-18, expires 2028-02-26). The installer pins it:
#: a key that is served over TLS but does not have this fingerprint is refused.
#: OBS rotates project keys when they expire; the new fingerprint is committed
#: here (and in the script, by ``scripts/sync_install_targets.py``) before the
#: old key does, and ``NOUST_REPO_KEY_FPR`` lets an operator move first.
REPOSITORY_KEY_FINGERPRINT = "D9FA6122D7F154D75A588A501945D169A62CFF65"

#: Normalised architectures.
ARCH_AMD64 = "amd64"
ARCH_ARM64 = "arm64"

#: What each ``uname -m`` spelling means, for the ones Noust supports.
_ARCH_SPELLINGS: dict[str, str] = {
    "x86_64": ARCH_AMD64,
    "amd64": ARCH_AMD64,
    "aarch64": ARCH_ARM64,
    "arm64": ARCH_ARM64,
}

#: Where a release was run: an install of the published package in a container
#: (``tests/integration/install_matrix.py``), and the real-machine harness on a systemd
#: container (``tests/integration/run.py``), each on one architecture. Only what was
#: run is written in the table: a row without any says "not tested".
INSTALL_AMD64 = "install:amd64"
INSTALL_ARM64 = "install:arm64"
HARNESS_AMD64 = "harness:amd64"


@dataclass(frozen=True)
class Target:
    """
    One operating system release Noust has a position on.

    Attributes:
        os_id: ``ID`` in ``/etc/os-release``.
        versions: Patterns of ``VERSION_ID`` the row covers, shell ``case``
            style (``9`` and ``9.*`` for RHEL 9, which says ``9.5``).
        name: What to call it.
        family: ``apt``, ``dnf`` or ``zypper``.
        status: One of :data:`STATUSES`.
        repo: The OBS repository directory its packages are in, or None.
        python: The prefix its console packages carry (``python3``, or
            ``python311`` on Leap 15.x).
        tested: Where it has been run (:data:`TEST_INSTALL`, :data:`TEST_HARNESS`),
            per architecture as ``<test>:<arch>``.
        note: One sentence on why it is not simply supported, or what to know.
    """

    os_id: str
    versions: tuple[str, ...]
    name: str
    family: str
    status: str
    repo: str | None = None
    python: str = "python3"
    tested: tuple[str, ...] = ()
    note: str = ""

    def matches(self, os_id: str, version_id: str) -> bool:
        """
        Tell whether an ``os-release`` identity is this row.

        Args:
            os_id: ``ID``.
            version_id: ``VERSION_ID``.

        Returns:
            True when the ``ID`` is the row's and a pattern covers the version.
        """
        return os_id == self.os_id and any(
            fnmatch.fnmatchcase(version_id, pattern) for pattern in self.versions
        )

    @property
    def repository_url(self) -> str | None:
        """The repository's address, or None when no package exists."""
        return f"{REPOSITORY_BASE}/{self.repo}/" if self.repo else None


def _row(
    os_id: str,
    version: str,
    name: str,
    family: str,
    status: str,
    repo: str | None = None,
    *,
    versions: tuple[str, ...] | None = None,
    python: str = "python3",
    tested: tuple[str, ...] = (),
    note: str = "",
) -> Target:
    return Target(
        os_id=os_id,
        versions=versions or (version,),
        name=name,
        family=family,
        status=status,
        repo=repo,
        python=python,
        tested=tested,
        note=note,
    )


#: Every release the table has a position on, in the order the docs show them.
#: A release that is not here is :data:`UNSUPPORTED` with the generic reason.
TARGETS: tuple[Target, ...] = (
    _row(
        "debian",
        "12",
        "Debian 12 (Bookworm)",
        "apt",
        SUPPORTED,
        "Debian_12",
        tested=(INSTALL_AMD64, INSTALL_ARM64),
    ),
    _row(
        "debian",
        "13",
        "Debian 13 (Trixie)",
        "apt",
        SUPPORTED,
        "Debian_13",
        tested=(INSTALL_AMD64,),
    ),
    _row(
        "debian",
        "11",
        "Debian 11 (Bullseye)",
        "apt",
        UNSUPPORTED,
        note="Python 3.9; Noust needs 3.10 or later.",
    ),
    _row(
        "ubuntu",
        "22.04",
        "Ubuntu 22.04 LTS",
        "apt",
        SUPPORTED,
        "xUbuntu_22.04",
        tested=(INSTALL_AMD64,),
    ),
    _row(
        "ubuntu",
        "24.04",
        "Ubuntu 24.04 LTS",
        "apt",
        SUPPORTED,
        "xUbuntu_24.04",
        tested=(INSTALL_AMD64, HARNESS_AMD64),
    ),
    _row(
        "ubuntu",
        "26.04",
        "Ubuntu 26.04 LTS",
        "apt",
        SUPPORTED,
        "xUbuntu_26.04",
        tested=(INSTALL_AMD64,),
    ),
    _row(
        "ubuntu",
        "",
        "Ubuntu 24.10, 25.04 and 25.10",
        "apt",
        UNSUPPORTED,
        versions=("24.10", "25.04", "25.10"),
        note="Interim releases, all past their end of life: use an LTS release.",
    ),
    _row(
        "ubuntu",
        "20.04",
        "Ubuntu 20.04 LTS",
        "apt",
        UNSUPPORTED,
        note="Python 3.8; Noust needs 3.10 or later.",
    ),
    _row(
        "fedora",
        "43",
        "Fedora 43",
        "dnf",
        SUPPORTED,
        "Fedora_43",
        tested=(INSTALL_AMD64,),
    ),
    _row(
        "fedora",
        "42",
        "Fedora 42",
        "dnf",
        BEST_EFFORT,
        "Fedora_42",
        tested=(INSTALL_AMD64,),
        note="Past its end of life (2026-05-27); its repository is kept for now.",
    ),
    _row(
        "fedora",
        "44",
        "Fedora 44",
        "dnf",
        BEST_EFFORT,
        "Fedora_43",
        tested=(INSTALL_AMD64,),
        note="No Fedora 44 repository is published yet: the Fedora 43 one is used (noarch packages).",
    ),
    _row(
        "fedora",
        "",
        "Fedora 40 and 41",
        "dnf",
        UNSUPPORTED,
        versions=("40", "41"),
        note="End of life.",
    ),
    _row(
        "opensuse-leap",
        "16.0",
        "openSUSE Leap 16.0",
        "zypper",
        SUPPORTED,
        "16.0",
        tested=(INSTALL_AMD64,),
    ),
    _row(
        "opensuse-leap",
        "15.6",
        "openSUSE Leap 15.6",
        "zypper",
        BEST_EFFORT,
        "15.6",
        python="python311",
        tested=(INSTALL_AMD64,),
        note="Past its end of life (2026-04-30); its repository is kept for now.",
    ),
    _row(
        "opensuse-tumbleweed",
        "",
        "openSUSE Tumbleweed",
        "zypper",
        SUPPORTED,
        "openSUSE_Tumbleweed",
        versions=("*",),
        tested=(INSTALL_AMD64,),
    ),
    _row(
        "rocky",
        "",
        "Rocky Linux 9 and 10",
        "dnf",
        PIP_ONLY,
        versions=("9", "9.*", "10", "10.*"),
        tested=(INSTALL_AMD64,),
        note="OBS has no RHEL-family target: Noust is installed with pip into /opt/noust.",
    ),
    _row(
        "almalinux",
        "",
        "AlmaLinux 9 and 10",
        "dnf",
        PIP_ONLY,
        versions=("9", "9.*", "10", "10.*"),
        tested=(INSTALL_AMD64,),
        note="OBS has no RHEL-family target: Noust is installed with pip into /opt/noust.",
    ),
    _row(
        "rhel",
        "",
        "Red Hat Enterprise Linux 9 and 10",
        "dnf",
        PIP_ONLY,
        versions=("9", "9.*", "10", "10.*"),
        tested=(INSTALL_AMD64,),
        note="Installed with pip into /opt/noust like Rocky and AlmaLinux; run in Red Hat's UBI 9 "
        "and 10 images, not on a subscribed host.",
    ),
    _row(
        "centos",
        "",
        "CentOS Stream 9 and 10",
        "dnf",
        PIP_ONLY,
        versions=("9", "9.*", "10", "10.*"),
        tested=(INSTALL_AMD64,),
        note="Installed with pip into /opt/noust like Rocky and AlmaLinux; run in the Stream 9 and "
        "10 images.",
    ),
    _row(
        "ol",
        "",
        "Oracle Linux 9 and 10",
        "dnf",
        PIP_ONLY,
        versions=("9", "9.*", "10", "10.*"),
        tested=(INSTALL_AMD64,),
        note="Installed with pip into /opt/noust like Rocky and AlmaLinux; run in the Oracle Linux 9 "
        "and 10 images.",
    ),
    _row(
        "arch",
        "",
        "Arch Linux",
        "pacman",
        UNSUPPORTED,
        versions=("*",),
        note="Rolling release with systemd, so it could run Noust, but Noust has no pacman support "
        "yet: neither its installer, its updates nor its setup would work.",
    ),
    _row(
        "alpine",
        "",
        "Alpine Linux",
        "apk",
        UNSUPPORTED,
        versions=("*",),
        note="No systemd (OpenRC) and musl: Noust runs every application as a systemd unit.",
    ),
)

#: The packages of Noust's own console that the RPM families only Suggest, which
#: ``dnf`` and ``zypper`` therefore do not install by themselves.
CONSOLE_PACKAGES: tuple[str, ...] = (
    "fastapi",
    "starlette",
    "pydantic",
    "uvicorn",
    "psutil",
    "httpx",
    "websockets",
    "cryptography",
)


def normalise_arch(machine: str) -> str:
    """
    Name a processor architecture the way the table does.

    Args:
        machine: ``uname -m`` (``x86_64``, ``aarch64``, ``armv7l``) or a
            Debian architecture name (``amd64``, ``arm64``, ``armhf``).

    Returns:
        ``amd64`` or ``arm64``; any other value, as it was given, lower-cased.
    """
    text = machine.strip().lower()
    return _ARCH_SPELLINGS.get(text, text)


def find_target(os_id: str, version_id: str) -> Target | None:
    """
    Look an ``os-release`` identity up in the table.

    Args:
        os_id: ``ID``.
        version_id: ``VERSION_ID``.

    Returns:
        The row, or None when the table has no position on that release.
    """
    for target in TARGETS:
        if target.matches(os_id, version_id):
            return target
    return None


@dataclass(frozen=True)
class Verdict:
    """
    Where a machine stands against the table.

    Attributes:
        status: One of :data:`STATUSES`.
        target: The row that matched, or None.
        pretty_name: What the machine calls itself.
        arch: The architecture, normalised.
        reasons: Sentences saying why the status is not plain ``supported``,
            for the installer's refusal and the doctor's warning.
        eol: The end-of-life status, when the table or the distribution knows it.
        derivative_of: The ``ID`` of the distribution the machine's was matched
            as, when it is a derivative of it.
    """

    status: str
    target: Target | None
    pretty_name: str
    arch: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    eol: EolStatus | None = None
    derivative_of: str | None = None

    @property
    def installable(self) -> bool:
        """Whether Noust can be installed here without forcing anything."""
        return self.status in (SUPPORTED, BEST_EFFORT, PIP_ONLY)


def assess(os_release: OsRelease, machine: str, *, today: date | None = None) -> Verdict:
    """
    Decide where a machine stands against the table.

    A derivative (Pop!_OS, whose ``ID_LIKE`` is ``ubuntu debian`` and whose
    ``VERSION_ID`` is Ubuntu's) is judged as the distribution it says it is
    like, and is never better than best-effort: its own packages and kernel
    are not what was tested. A 32-bit machine is refused whatever it runs.

    Args:
        os_release: ``/etc/os-release`` as :func:`read_os_release` reads it.
        machine: ``uname -m``.
        today: The date for the end-of-life check; injectable for tests.

    Returns:
        The verdict.
    """
    arch = normalise_arch(machine)
    reasons: list[str] = []
    derivative: str | None = None

    target = find_target(os_release.id, os_release.version_id)
    if target is None:
        for parent in os_release.id_like:
            target = find_target(parent, os_release.version_id)
            if target is not None:
                derivative = parent
                break

    status = target.status if target else UNSUPPORTED
    if target is None:
        reasons.append(
            f"{os_release.pretty_name} is not a release Noust declares support for "
            "(docs/PLATFORMS.md lists the ones it does)."
        )
    elif target.note:
        reasons.append(f"{target.name}: {target.note}")
    if derivative is not None and status == SUPPORTED:
        status = BEST_EFFORT
        reasons.append(
            f"{os_release.pretty_name} is a derivative of {derivative}: its packages are not "
            "the ones the repository was tested against."
        )

    if arch not in (ARCH_AMD64, ARCH_ARM64):
        status = UNSUPPORTED
        reasons.append(
            f"The architecture {machine.strip() or 'unknown'} is not supported: Noust declares "
            "amd64 and arm64 only (a 32-bit Raspberry Pi OS is neither)."
        )

    eol = status_of(os_release, today=today)
    if eol.status == "expired" and status == SUPPORTED:
        status = BEST_EFFORT
        reasons.append(
            f"{os_release.pretty_name} stopped receiving security updates on {eol.end_date}."
        )
    return Verdict(
        status=status,
        target=target,
        pretty_name=os_release.pretty_name,
        arch=arch,
        reasons=tuple(reasons),
        eol=eol,
        derivative_of=derivative,
    )


def assess_this_machine(
    machine: str, *, host: HostPaths | None = None, today: date | None = None
) -> Verdict:
    """
    Decide where the machine this runs on stands.

    Args:
        machine: ``uname -m`` (``platform.machine()``).
        host: Where the system files are; the real machine by default.
        today: The date for the end-of-life check; injectable for tests.

    Returns:
        The verdict.
    """
    return assess(read_os_release(host), machine, today=today)


def _tested(target: Target) -> str:
    if not target.tested:
        return "not tested"
    return ", ".join(target.tested)


def markdown_table() -> str:
    """
    Render the distributions as the Markdown table ``docs/PLATFORMS.md`` shows.

    Returns:
        One row per release in :data:`TARGETS`, with a trailing newline.
    """
    rows = [
        "| Distribution | Status | Packages from | Tested | Notes |",
        "|---|---|---|---|---|",
    ]
    for target in TARGETS:
        source = (
            f"`{target.repo}`" if target.repo else ("pip" if target.status == PIP_ONLY else "-")
        )
        rows.append(
            f"| {target.name} | {target.status} | {source} | {_tested(target)} | {target.note} |"
        )
    return "\n".join(rows) + "\n"


def installer_block() -> str:
    """
    Render the table the installer carries, as POSIX ``sh``.

    One ``case`` arm per release, mapping ``<ID>:<VERSION_ID>`` to the
    repository, the family, the status, the name and the console's package
    prefix. The line the script's ``BEGIN TARGETS`` marker opens is not part of
    this text; :func:`replace_installer_block` puts it between the markers.

    Returns:
        The ``target_for`` function and the pinned fingerprint, with a trailing
        newline.
    """
    lines = [
        f'REPO_KEY_FPR_PINNED="{REPOSITORY_KEY_FINGERPRINT}"',
        f'REPO_BASE_DEFAULT="{REPOSITORY_BASE}"',
        "",
        "# target_for ID VERSION_ID: sets T_NAME, T_FAMILY, T_STATUS, T_REPO, T_PYTHON and",
        "# T_NOTE for a release the table knows, and returns 1 for one it does not.",
        "target_for() {",
        '    case "$1:$2" in',
    ]
    for target in TARGETS:
        patterns = "|".join(f"{target.os_id}:{pattern}" for pattern in target.versions)
        note = target.note.replace("'", "'\\''")
        lines.append(
            f"        {patterns})"
            f" T_NAME='{target.name}' T_FAMILY={target.family} T_STATUS={target.status}"
            f" T_REPO={target.repo or '-'} T_PYTHON={target.python} T_NOTE='{note}' ;;"
        )
    lines += [
        "        *) return 1 ;;",
        "    esac",
        "}",
    ]
    return "\n".join(lines) + "\n"


#: The markers the generated block sits between in ``packaging/install.sh``.
BLOCK_BEGIN = "# BEGIN TARGETS (generated: scripts/sync_install_targets.py; do not edit by hand)"
BLOCK_END = "# END TARGETS"


def replace_installer_block(script: str) -> str:
    """
    Put the current table into an installer's text.

    Args:
        script: The text of ``packaging/install.sh``.

    Returns:
        The same text with the block between the markers regenerated.

    Raises:
        ValueError: When the markers are missing or out of order.
    """
    begin = script.find(BLOCK_BEGIN)
    end = script.find(BLOCK_END)
    if begin < 0 or end < begin:
        raise ValueError("install.sh has no BEGIN TARGETS / END TARGETS markers")
    return f"{script[:begin]}{BLOCK_BEGIN}\n{installer_block()}{script[end:]}"
