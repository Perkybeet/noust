# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust setup doctor``, as data: what a deployment depends on, what is missing, how to fix it.

The doctor used to print as it checked, so the console - where an operator who has just
installed Noust is looking - could show none of it (checklist N-09). It now produces a
:class:`DoctorReport`: sections of items, each with a status and, when something is wrong,
the command that fixes it. The CLI draws it as it always did
(:func:`noust.cli.commands.setup._run_doctor`) and ``GET /api/system/doctor`` serialises it,
so both say the same thing (rule 3).

What it asks of the machine arrives in a :class:`DoctorEnvironment`, so the CLI keeps handing it
the names it always patched in tests, and the console builds the default from the real ones.
Every probe only reads.
"""

from __future__ import annotations

import logging
import platform
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from noust.core import paths
from noust.core.config import DEFAULT_APPS_DIR, DEFAULT_CONFIG_PATH, DEFAULT_LOG_DIR
from noust.core.exceptions import NoustError
from noust.core.package_family import INSTALL_COMMANDS, detect_family
from noust.core.runner import get_runner
from noust.core.utils import command_exists, run_command
from noust.managers.server import platforms, requirements

logger = logging.getLogger(__name__)

ItemStatus = Literal["ok", "info", "warn", "fail"]


@dataclass(frozen=True)
class DoctorItem:
    """
    One thing the doctor looked at.

    Attributes:
        status: ``ok``; ``info`` (optional or not applicable); ``warn`` (works, but something
            will not); ``fail`` (deployments will not work).
        text: What was found, in the doctor's words.
        fix: The command that fixes it, when there is one.
        notes: More lines to read under it (a URL, a reason).
        counted: Whether a ``warn`` adds to the summary's count. The GitHub SSH line never
            did and still does not: it says something about a place Noust does not need.
    """

    status: ItemStatus
    text: str
    fix: str | None = None
    notes: tuple[str, ...] = ()
    counted: bool = True


@dataclass(frozen=True)
class DoctorSection:
    """
    A group of items under one heading.

    Attributes:
        title: The heading (``Web Server``).
        items: What was looked at.
        explain: Whether the CLI should print a final explanation line for this section
            when it has warnings (the platform section points at ``docs/PLATFORMS.md``).
    """

    title: str
    items: tuple[DoctorItem, ...]
    explain: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    """
    Everything the doctor found.

    Attributes:
        sections: In the order the CLI prints them.
    """

    sections: tuple[DoctorSection, ...] = field(default_factory=tuple)

    @property
    def issues(self) -> int:
        """How many items are ``fail``: what deployments need and is missing."""
        return sum(1 for s in self.sections for i in s.items if i.status == "fail")

    @property
    def warnings(self) -> int:
        """How many ``warn`` items count towards the summary."""
        return sum(1 for s in self.sections for i in s.items if i.status == "warn" and i.counted)

    @property
    def ready(self) -> bool:
        """True when nothing a deployment needs is missing."""
        return self.issues == 0

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the report as JSON-ready data.

        Returns:
            ``ready``, ``issues``, ``warnings`` and ``sections`` (title, explain, items with
            status, text, fix, notes).
        """
        return {
            "ready": self.ready,
            "issues": self.issues,
            "warnings": self.warnings,
            "sections": [
                {
                    "title": s.title,
                    "explain": s.explain,
                    "items": [
                        {
                            "status": i.status,
                            "text": i.text,
                            "fix": i.fix,
                            "notes": list(i.notes),
                        }
                        for i in s.items
                    ],
                }
                for s in self.sections
            ],
        }


@dataclass(frozen=True)
class DoctorEnvironment:
    """
    What the doctor asks of the machine, replaceable.

    Attributes:
        command_exists: Whether a program is on ``PATH``.
        run: Runs a short read-only command and returns its outcome (``stdout``).
        install_hint: What an operator types to install a package here, such as
            ``apt install``; empty when no package manager is known.
        apps_dir: The applications directory.
        log_dir: The log directory.
        config_path: The configuration file.
        data_dir: Noust's own data directory, whose filesystem has to have room for the store.
        resources: Measures the machine's memory, processors and free disk; None reads the
            real one.
        listeners: ``(port, program names)`` of every listening TCP socket; None asks the
            kernel through ``ss``.
        include_ssh: Whether to try the connection to GitHub. The console leaves it out: a
            handshake with another host does not belong in a page load.
    """

    command_exists: Callable[[str], bool] = command_exists
    run: Callable[[list[str]], Any] = run_command
    install_hint: str = ""
    apps_dir: Path = DEFAULT_APPS_DIR
    log_dir: Path = DEFAULT_LOG_DIR
    config_path: Path = DEFAULT_CONFIG_PATH
    data_dir: Path = paths.STATE_DIR
    resources: Callable[[], requirements.Resources] | None = None
    listeners: Callable[[], list[tuple[int, tuple[str, ...]]]] | None = None
    include_ssh: bool = True

    @classmethod
    def default(cls, *, include_ssh: bool = False) -> DoctorEnvironment:
        """
        The environment of the running machine.

        Args:
            include_ssh: Whether to try the connection to GitHub.

        Returns:
            An environment whose install hint is this machine's package manager.
        """
        family, _program = detect_family(get_runner())
        return cls(install_hint=INSTALL_COMMANDS.get(family, ""), include_ssh=include_ssh)


# -- sections


def platform_section() -> DoctorSection:
    """
    Say where this machine stands against the platforms Noust declares.

    A release outside the table is a warning, not a failure: a server that already runs Noust is
    not made unusable by the table having no position on it, but nothing was tested there and
    the operator should know before a deployment depends on it (``docs/PLATFORMS.md``).

    Returns:
        The ``Platform`` section.
    """
    verdict = platforms.assess_this_machine(platform.machine())
    label = f"{verdict.pretty_name}, {verdict.arch}"
    items: list[DoctorItem] = []
    if verdict.status == platforms.SUPPORTED:
        items.append(DoctorItem("ok", f"{label}: supported"))
    elif verdict.status == platforms.PIP_ONLY:
        items.append(DoctorItem("info", f"{label}: installed with pip, there is no package for it"))
    elif verdict.status == platforms.BEST_EFFORT:
        items.append(DoctorItem("warn", f"{label}: best effort, not a tested platform"))
    else:
        items.append(DoctorItem("warn", f"{label}: not a platform Noust declares support for"))
    items += [DoctorItem("info", f"  {reason}") for reason in verdict.reasons]
    eol = verdict.eol
    if eol is not None and eol.status == "warn":
        items.append(
            DoctorItem(
                "warn", f"{verdict.pretty_name} stops receiving security updates on {eol.end_date}"
            )
        )
    warned = any(i.status == "warn" for i in items)
    return DoctorSection(
        "Platform",
        tuple(items),
        explain="The platforms Noust installs on are listed in docs/PLATFORMS.md"
        if warned
        else None,
    )


def _human(megabytes: int) -> str:
    """
    Args:
        megabytes: A size in megabytes.

    Returns:
        It as the documents spell it (``512 MB``, ``1 GB``, ``2.5 GB``).
    """
    return requirements.human_mb(megabytes)


def _sized_item(
    requirement: requirements.Requirement,
    amount: int | None,
    shown: str,
    *,
    unknown: str,
    low_fix: str | None,
    low_note: str,
) -> DoctorItem:
    """
    Judge one measured value against its requirement.

    Args:
        requirement: The resource.
        amount: What the machine has, or None when it could not be read.
        shown: How to write what it has (``2 GB``).
        unknown: What to say when it could not be read.
        low_fix: The command that helps below the minimum, when there is one.
        low_note: One line on why it matters, shown under a warning or a failure.

    Returns:
        ``ok``, ``warn`` (under the minimum) or ``fail`` (under the floor).
    """
    sizes = requirements.size_text
    if amount is None:
        return DoctorItem("info", unknown)
    state = requirements.verdict(requirement, amount)
    if state == "below-floor":
        return DoctorItem(
            "fail",
            f"{requirement.label}: {shown}, under the {sizes(requirement, requirement.floor)} "
            "Noust needs to run",
            fix=low_fix,
            notes=(low_note,),
        )
    if state == "below-minimum":
        return DoctorItem(
            "warn",
            f"{requirement.label}: {shown}, under the {sizes(requirement, requirement.minimum)} "
            "a server is sized for",
            fix=low_fix,
            notes=(low_note,),
        )
    return DoctorItem(
        "ok",
        f"{requirement.label}: {shown} (minimum {sizes(requirement, requirement.minimum)})",
    )


def _port_item(holder: requirements.PortHolder) -> DoctorItem:
    """
    Say who holds one of the web ports.

    Args:
        holder: The port and what holds it.

    Returns:
        ``ok`` for the web server, ``info`` for nothing, ``warn`` for anything else, with the
        command that finds or frees it.
    """
    label = f"Port {holder.port}"
    names = ", ".join(holder.processes)
    if holder.kind == "free":
        return DoctorItem("info", f"{label}: free (the web server is not listening on it yet)")
    if holder.kind == "web-server":
        return DoctorItem("ok", f"{label}: {names}")
    if holder.kind == "container":
        return DoctorItem(
            "warn",
            f"{label}: published by a container ({names})",
            fix=f"docker ps --filter publish={holder.port}",
            notes=(
                "Another platform's proxy or a Compose stack holds it, so nginx cannot listen "
                "on it until that stops.",
            ),
        )
    program = holder.processes[0] if holder.processes else "the process holding it"
    return DoctorItem(
        "warn",
        f"{label}: held by {names or program}, not by the web server",
        fix=f"sudo systemctl disable --now {program}" if holder.processes else None,
        notes=(
            "A certificate is asked for on port 80 and sites are served on 443: the web server "
            "cannot start while another program holds them.",
        ),
    )


def resources_section(env: DoctorEnvironment) -> DoctorSection:
    """
    Judge the machine against the sizes Noust declares, and say who holds ports 80 and 443.

    The sizes are :mod:`noust.managers.server.requirements`, the same ones the installer
    carries and ``docs/requirements.md`` shows. Everything here only reads.

    Args:
        env: The environment.

    Returns:
        The ``Resources`` section.
    """
    machine = (
        env.resources()
        if env.resources is not None
        else requirements.read_resources([env.apps_dir, env.data_dir])
    )
    items: list[DoctorItem] = []
    memory = machine.memory_mb
    swap = f", swap {_human(machine.swap_mb)}" if machine.swap_mb else ""
    items.append(
        _sized_item(
            requirements.MEMORY,
            memory,
            f"{_human(memory)}{swap}" if memory is not None else "",
            unknown="Memory: could not be read",
            low_fix="sudo noust server swap create",
            low_note="Builds are what need memory; a swap file lets one that needs more than "
            "the server has finish slowly instead of being killed.",
        )
    )
    items.append(
        _sized_item(
            requirements.CPU,
            machine.cpus,
            str(machine.cpus),
            unknown="Processors: could not be read",
            low_fix=None,
            low_note="Noust and a build need at least one.",
        )
    )
    if not machine.disks:
        items.append(DoctorItem("info", "Free disk: could not be read"))
    for disk in machine.disks:
        item = _sized_item(
            requirements.DISK,
            disk.free_mb,
            f"{_human(disk.free_mb)} free of {_human(disk.total_mb)} at {disk.path}",
            unknown="Free disk: could not be read",
            low_fix="noust server storage",
            low_note="'noust server cleanup' removes what Noust can prove is unused; every "
            "release on disk keeps its code and dependencies.",
        )
        items.append(item)

    listening = env.listeners() if env.listeners is not None else _listening_programs()
    items += [_port_item(holder) for holder in requirements.classify_port_holders(listening)]
    return DoctorSection("Resources", tuple(items))


def _listening_programs() -> list[tuple[int, tuple[str, ...]]]:
    """
    Returns:
        ``(port, programs)`` for every TCP socket the kernel says listens, through ``ss``.
        Empty when ``ss`` is missing, which reads as "free" rather than as a failure.
    """
    from noust.managers.database.exposure import listening_sockets

    return [(socket.port, socket.processes) for socket in listening_sockets(get_runner())]


def _unit_item(env: DoctorEnvironment, unit: str) -> DoctorItem:
    """
    Say whether a systemd unit is running.

    Args:
        env: The environment.
        unit: Systemd unit name.

    Returns:
        ``ok`` when active, otherwise a warning that says how to start it.
    """
    result = env.run(["systemctl", "is-active", unit])
    if result.stdout.strip() == "active":
        return DoctorItem("ok", f"  {unit} service: running")
    return DoctorItem("warn", f"  {unit} service: not running", fix=f"sudo systemctl start {unit}")


def _version(command: str, flag: str = "--version") -> str:
    """
    Args:
        command: The program.
        flag: The flag that makes it print its version.

    Returns:
        Its first output line, or ``OK`` when it prints nothing.
    """
    from noust.core.dependencies import DependencyChecker

    return DependencyChecker(verbose=False).get_version(command, flag) or "OK"


def collect(env: DoctorEnvironment | None = None) -> DoctorReport:
    """
    Check everything a deployment depends on.

    Args:
        env: What to ask of the machine; the running machine's by default (without the GitHub
            connection test).

    Returns:
        The report: each section in the order the CLI prints it.
    """
    env = env or DoctorEnvironment.default()
    hint = env.install_hint or "your package manager"
    exists = env.command_exists
    sections = [platform_section(), resources_section(env)]

    core: list[DoctorItem] = []
    for command in ("git", "curl"):
        if exists(command):
            core.append(DoctorItem("ok", f"{command}: {_version(command)}"))
        else:
            core.append(
                DoctorItem("fail", f"{command}: not installed", fix=f"sudo {hint} {command}")
            )
    sections.append(DoctorSection("Core Dependencies", tuple(core)))

    web: list[DoctorItem] = []
    if exists("nginx"):
        web.append(DoctorItem("ok", f"nginx: {_version('nginx', '-v')}"))
        web.append(_unit_item(env, "nginx"))
    elif exists("apache2") or exists("httpd"):
        unit = "apache2" if exists("apache2") else "httpd"
        web.append(DoctorItem("ok", f"{unit}: installed"))
        web.append(_unit_item(env, unit))
    else:
        web.append(DoctorItem("fail", "Web server: not installed", fix=f"sudo {hint} nginx"))
    sections.append(DoctorSection("Web Server", tuple(web)))

    node: list[DoctorItem] = []
    if exists("node"):
        node.append(DoctorItem("ok", f"node: {_version('node')}"))
        if exists("npm"):
            node.append(DoctorItem("ok", f"npm: {_version('npm')}"))
        else:
            node.append(DoctorItem("fail", "npm: not installed, although it ships with Node.js"))
        for manager in ("pnpm", "yarn", "bun"):
            if exists(manager):
                node.append(DoctorItem("ok", f"{manager}: {_version(manager)}"))
            else:
                node.append(DoctorItem("info", f"{manager}: not installed (optional)"))
    else:
        node.append(DoctorItem("fail", "node: not installed", fix="sudo noust setup init"))
    sections.append(DoctorSection("Node.js Environment", tuple(node)))

    py: list[DoctorItem] = []
    if exists("python3"):
        py.append(DoctorItem("ok", f"python3: {_version('python3')}"))
        if exists("pip3"):
            py.append(DoctorItem("ok", f"pip3: {_version('pip3')}"))
        else:
            py.append(DoctorItem("warn", "pip3: not installed", fix=f"sudo {hint} python3-pip"))
    else:
        py.append(DoctorItem("warn", "python3: not installed, needed for Python apps"))
    sections.append(DoctorSection("Python Environment", tuple(py)))

    if exists("certbot"):
        tls = DoctorItem("ok", f"certbot: {_version('certbot')}")
    else:
        tls = DoctorItem("warn", "certbot: not installed", fix=f"sudo {hint} certbot")
    sections.append(DoctorSection("SSL/TLS (Certbot)", (tls,)))

    config: list[DoctorItem] = []
    for label, path in (("Apps directory", env.apps_dir), ("Log directory", env.log_dir)):
        if path.exists():
            config.append(DoctorItem("ok", f"{label}: {path}"))
        else:
            config.append(
                DoctorItem("fail", f"{label}: {path} not found", fix="sudo noust setup init")
            )
    if env.config_path.exists():
        config.append(DoctorItem("ok", f"Config file: {env.config_path}"))
    else:
        config.append(
            DoctorItem(
                "warn", f"Config file: {env.config_path} not found", fix="sudo noust setup init"
            )
        )
    sections.append(DoctorSection("Noust Configuration", tuple(config)))

    sections.append(_ssh_section(env))

    relays = _relays_section()
    if relays is not None:
        sections.append(relays)
    names = _names_section()
    if names is not None:
        sections.append(names)
    return DoctorReport(tuple(sections))


def _ssh_section(env: DoctorEnvironment) -> DoctorSection:
    """
    Say whether a key exists for private repositories, and whether GitHub accepts it.

    Args:
        env: The environment.

    Returns:
        The ``SSH Configuration`` section.
    """
    from noust.validators.ssh import ssh_key_exists, test_ssh_connection

    key_exists, key_path = ssh_key_exists()
    if not key_exists:
        return DoctorSection(
            "SSH Configuration",
            (DoctorItem("warn", "SSH key: not found", fix="noust setup ssh --generate"),),
        )
    items = [DoctorItem("ok", f"SSH key: {key_path}")]
    if env.include_ssh:
        success, _ = test_ssh_connection("github.com")
        if success:
            items.append(DoctorItem("ok", "GitHub SSH: connected"))
        else:
            items.append(
                DoctorItem(
                    "warn",
                    "GitHub SSH: not configured",
                    notes=("Add your key to GitHub: https://github.com/settings/keys",),
                    counted=False,
                )
            )
    else:
        items.append(
            DoctorItem(
                "info", "GitHub SSH: not tested here", fix="noust setup ssh --test github.com"
            )
        )
    return DoctorSection("SSH Configuration", tuple(items))


def _names_section() -> DoctorSection | None:
    """
    Say what still has a name from before 4.0, and which of it waits for the operator.

    Returns:
        The section, or None when nothing carries an old name (or it could not be read: the
        other checks still stand, and this one is only a list).
    """
    from noust.managers import name_migration

    try:
        found = name_migration.findings()
    except (NoustError, OSError, sqlite3.Error) as exc:
        logger.debug("The names from before 4.0 were not read: %s", exc)
        return None
    if not found:
        return None
    return DoctorSection(
        "Names from before 4.0",
        tuple(
            DoctorItem(
                "warn" if finding.waits_for_operator else "info",
                finding.text,
                fix=finding.fix,
                notes=finding.notes,
            )
            for finding in found
        ),
        explain="Nothing here stops working: Noust accepts the old names while they are met. "
        "'noust setup rename' finishes what can be finished without restarting anything.",
    )


def _relays_section() -> DoctorSection | None:
    """
    Name the Compose stacks an interrupted update left served by a relay.

    Returns:
        The section, or None when there is none to name. A machine without a store, or one the
        doctor may not read, gives a section saying the check did not run: the other checks
        still stand.
    """
    from noust.deployers import compose_relay

    try:
        found = compose_relay.leftover_relays()
    except (NoustError, OSError, sqlite3.Error) as exc:
        return DoctorSection("Docker Compose relays", (DoctorItem("info", f"Not checked: {exc}"),))
    if not found:
        return None
    return DoctorSection(
        "Docker Compose relays",
        tuple(DoctorItem("warn", f"{left.domain}: {left.what}", fix=left.fix) for left in found),
    )
