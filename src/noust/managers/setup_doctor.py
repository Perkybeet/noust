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
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from noust.core import paths
from noust.core.config import DEFAULT_APPS_DIR, DEFAULT_CONFIG_PATH, DEFAULT_LOG_DIR
from noust.core.dependencies import NODE_RUNTIME_TYPES
from noust.core.exceptions import NoustError
from noust.core.package_family import INSTALL_COMMANDS, detect_family
from noust.core.runner import get_runner
from noust.core.server_text import Param, ServerText, say
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
        coded: ``text`` as a sentence with a code, for the console to translate; None for a
            line that is another module's words (why a platform is best effort, what still
            carries an old name), which the console shows as written.
        notes_coded: The same for each of ``notes``, in order; empty when none is coded.
    """

    status: ItemStatus
    text: str
    fix: str | None = None
    notes: tuple[str, ...] = ()
    counted: bool = True
    coded: ServerText | None = None
    notes_coded: tuple[ServerText, ...] = ()


def item_of(
    status: ItemStatus,
    code: str,
    params: Mapping[str, Param],
    *,
    fix: str | None = None,
    notes: tuple[ServerText, ...] = (),
    counted: bool = True,
) -> DoctorItem:
    """
    Make an item out of a sentence of the catalog, so that its words exist once.

    Args:
        status: The outcome.
        code: The sentence's code in :mod:`noust.core.server_text`.
        params: The values the sentence is said with.
        fix: The command that fixes it, when there is one.
        notes: Sentences to read under it.
        counted: Whether a ``warn`` adds to the summary's count.

    Returns:
        The item, whose ``text`` is the sentence in English and whose ``coded`` is its code.
    """
    sentence = ServerText(code, dict(params))
    return DoctorItem(
        status,
        sentence.text,
        fix,
        tuple(note.text for note in notes),
        counted,
        sentence,
        notes,
    )


def item(
    status: ItemStatus,
    code: str,
    *,
    fix: str | None = None,
    notes: tuple[ServerText, ...] = (),
    counted: bool = True,
    **params: Param,
) -> DoctorItem:
    """
    :func:`item_of` with the values as keyword arguments, for a sentence whose values are written
    out at the call.

    Args:
        status: The outcome.
        code: The sentence's code in :mod:`noust.core.server_text`.
        fix: The command that fixes it, when there is one.
        notes: Sentences to read under it.
        counted: Whether a ``warn`` adds to the summary's count.
        **params: The values the sentence is said with.

    Returns:
        The item.
    """
    return item_of(status, code, params, fix=fix, notes=notes, counted=counted)


@dataclass(frozen=True)
class DoctorSection:
    """
    A group of items under one heading.

    Attributes:
        title: The heading (``Web Server``).
        items: What was looked at.
        explain: Whether the CLI should print a final explanation line for this section
            when it has warnings (the platform section points at ``docs/PLATFORMS.md``).
        key: What the heading is called in the console's catalog (``web_server``): the title is
            English, and a console in another language says it by this.
        explain_coded: ``explain`` as a sentence with a code.
    """

    title: str
    items: tuple[DoctorItem, ...]
    explain: str | None = None
    key: str = ""
    explain_coded: ServerText | None = None


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
            ``ready``, ``issues``, ``warnings`` and ``sections`` (title, key, explain, items with
            status, text, fix, notes), each sentence beside its code and values (``coded``,
            ``notes_coded``, ``explain_coded``) for the console to translate.
        """
        return {
            "ready": self.ready,
            "issues": self.issues,
            "warnings": self.warnings,
            "sections": [
                {
                    "title": s.title,
                    "key": s.key,
                    "explain": s.explain,
                    "explain_coded": s.explain_coded.to_dict() if s.explain_coded else None,
                    "items": [
                        {
                            "status": i.status,
                            "text": i.text,
                            "fix": i.fix,
                            "notes": list(i.notes),
                            "coded": i.coded.to_dict() if i.coded else None,
                            "notes_coded": [note.to_dict() for note in i.notes_coded],
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
        app_types: The kinds of application the store holds (``nodejs``, ``docker-compose``...);
            None reads the real store. What a server runs decides whether Node.js missing is an
            error or only a note.
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
    app_types: Callable[[], Collection[str]] | None = None

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
        items.append(item("ok", "doctor.platform.supported", label=label))
    elif verdict.status == platforms.PIP_ONLY:
        items.append(item("info", "doctor.platform.pip_only", label=label))
    elif verdict.status == platforms.BEST_EFFORT:
        items.append(item("warn", "doctor.platform.best_effort", label=label))
    else:
        items.append(item("warn", "doctor.platform.unsupported", label=label))
    # Why, in the words of the table of platforms: another module's sentences, shown as written.
    items += [DoctorItem("info", f"  {reason}") for reason in verdict.reasons]
    eol = verdict.eol
    if eol is not None and eol.status == "warn":
        items.append(
            item(
                "warn",
                "doctor.platform.eol",
                release=verdict.pretty_name,
                date=str(eol.end_date),
            )
        )
    warned = any(i.status == "warn" for i in items)
    return DoctorSection(
        "Platform",
        tuple(items),
        explain="The platforms Noust installs on are listed in docs/PLATFORMS.md"
        if warned
        else None,
        key="platform",
        explain_coded=say("doctor.platform.explain") if warned else None,
    )


def _human(megabytes: int) -> str:
    """
    Args:
        megabytes: A size in megabytes.

    Returns:
        It as the documents spell it (``512 MB``, ``1 GB``, ``2.5 GB``).
    """
    return requirements.human_mb(megabytes)


@dataclass(frozen=True)
class _SizedCodes:
    """
    The sentences one measured resource can be judged with.

    Attributes:
        unreadable: It could not be measured.
        below_floor: Under what Noust needs to run.
        below_minimum: Under what a server is sized for.
        ok: Enough.
        note: One line on why it matters, read under a warning or a failure.
    """

    unreadable: str
    below_floor: str
    below_minimum: str
    ok: str
    note: str


_MEMORY = _SizedCodes(
    "doctor.memory.unreadable",
    "doctor.memory.below_floor",
    "doctor.memory.below_minimum",
    "doctor.memory.ok",
    "doctor.memory.note",
)
#: Memory when the machine has swap: swap is said in the same sentence, not in a second one,
#: because where it goes in the sentence is the language's to decide.
_MEMORY_WITH_SWAP = _SizedCodes(
    "doctor.memory.unreadable",
    "doctor.memory.below_floor_swap",
    "doctor.memory.below_minimum_swap",
    "doctor.memory.ok_swap",
    "doctor.memory.note",
)
_CPU = _SizedCodes(
    "doctor.cpu.unreadable",
    "doctor.cpu.below_floor",
    "doctor.cpu.below_minimum",
    "doctor.cpu.ok",
    "doctor.cpu.note",
)
_DISK = _SizedCodes(
    "doctor.disk.unreadable",
    "doctor.disk.below_floor",
    "doctor.disk.below_minimum",
    "doctor.disk.ok",
    "doctor.disk.note",
)


def _sized_item(
    requirement: requirements.Requirement,
    amount: int | None,
    codes: _SizedCodes,
    values: dict[str, Param],
    *,
    low_fix: str | None,
) -> DoctorItem:
    """
    Judge one measured value against its requirement.

    Args:
        requirement: The resource.
        amount: What the machine has, or None when it could not be read.
        codes: The sentences to judge it with.
        values: What the sentences say about the machine (how much it has).
        low_fix: The command that helps below the minimum, when there is one.

    Returns:
        ``ok``, ``warn`` (under the minimum) or ``fail`` (under the floor).
    """
    if amount is None:
        return item("info", codes.unreadable)
    state = requirements.verdict(requirement, amount)
    # The bounds are numbers for a count of processors and sizes (``512 MB``) for the rest, as
    # the table of requirements writes them.
    bound = (
        (lambda size: size)
        if requirement.key == "cpu"
        else (lambda size: requirements.size_text(requirement, size))
    )
    if state == "below-floor":
        return item_of(
            "fail",
            codes.below_floor,
            {**values, "floor": bound(requirement.floor)},
            fix=low_fix,
            notes=(say(codes.note),),
        )
    if state == "below-minimum":
        return item_of(
            "warn",
            codes.below_minimum,
            {**values, "minimum": bound(requirement.minimum)},
            fix=low_fix,
            notes=(say(codes.note),),
        )
    return item_of("ok", codes.ok, {**values, "minimum": bound(requirement.minimum)})


def _port_item(holder: requirements.PortHolder) -> DoctorItem:
    """
    Say who holds one of the web ports.

    Args:
        holder: The port and what holds it.

    Returns:
        ``ok`` for the web server, ``info`` for nothing, ``warn`` for anything else, with the
        command that finds or frees it.
    """
    names = ", ".join(holder.processes)
    if holder.kind == "free":
        return item("info", "doctor.port.free", port=holder.port)
    if holder.kind == "web-server":
        return item("ok", "doctor.port.web_server", port=holder.port, holders=names)
    if holder.kind == "container":
        return item(
            "warn",
            "doctor.port.container",
            fix=f"docker ps --filter publish={holder.port}",
            notes=(say("doctor.port.container_note"),),
            port=holder.port,
            holders=names,
        )
    program = holder.processes[0] if holder.processes else "the process holding it"
    return item(
        "warn",
        "doctor.port.held",
        fix=f"sudo systemctl disable --now {program}" if holder.processes else None,
        notes=(say("doctor.port.held_note"),),
        port=holder.port,
        holders=names or program,
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
    memory_values: dict[str, Param] = {}
    if memory is not None:
        memory_values["amount"] = _human(memory)
        if machine.swap_mb:
            memory_values["swap"] = _human(machine.swap_mb)
    items.append(
        _sized_item(
            requirements.MEMORY,
            memory,
            _MEMORY_WITH_SWAP if machine.swap_mb else _MEMORY,
            memory_values,
            low_fix="sudo noust server swap create",
        )
    )
    items.append(
        _sized_item(
            requirements.CPU,
            machine.cpus,
            _CPU,
            {"amount": machine.cpus} if machine.cpus is not None else {},
            low_fix=None,
        )
    )
    if not machine.disks:
        items.append(item("info", _DISK.unreadable))
    for disk in machine.disks:
        items.append(
            _sized_item(
                requirements.DISK,
                disk.free_mb,
                _DISK,
                {
                    "free": _human(disk.free_mb),
                    "total": _human(disk.total_mb),
                    "path": str(disk.path),
                },
                low_fix="noust server storage",
            )
        )

    listening = env.listeners() if env.listeners is not None else _listening_programs()
    items += [_port_item(holder) for holder in requirements.classify_port_holders(listening)]
    return DoctorSection("Resources", tuple(items), key="resources")


def _listening_programs() -> list[tuple[int, tuple[str, ...]]]:
    """
    Returns:
        ``(port, programs)`` for every TCP socket the kernel says listens, through ``ss``.
        Empty when ``ss`` is missing, which reads as "free" rather than as a failure.
    """
    from noust.managers.database.exposure import listening_sockets

    return [(socket.port, socket.processes) for socket in listening_sockets(get_runner())]


def _registered_app_types() -> set[str]:
    """
    Returns:
        The type of every application the store holds.

    Raises:
        NoustError: When the store cannot be read.
    """
    from noust.core.store import get_store

    try:
        return {app.app_type for app in get_store().list_apps()}
    except (sqlite3.Error, OSError) as exc:
        raise NoustError("The applications could not be read", details=str(exc)) from exc


def _node_is_needed(env: DoctorEnvironment) -> bool:
    """
    Whether an application registered here runs on the server's Node.js.

    A server that only runs Compose stacks, or nothing yet, has no use for it, and calling its
    absence an error sends the operator to install what nothing uses. What cannot be read is
    taken as needed: an answer that cleared a missing Node because the store would not open
    would be wrong exactly when it matters.

    Args:
        env: The environment.

    Returns:
        True when some application needs Node.js, or when that could not be told.
    """
    try:
        kinds = (env.app_types or _registered_app_types)()
    except NoustError as exc:
        logger.debug("The applications were not read, so Node.js is taken as needed: %s", exc)
        return True
    return any(kind in NODE_RUNTIME_TYPES for kind in kinds)


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
        return item("ok", "doctor.web.running", unit=unit)
    return item("warn", "doctor.web.stopped", fix=f"sudo systemctl start {unit}", unit=unit)


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

    def present(command: str, flag: str = "--version") -> DoctorItem:
        # The version is what the program printed: shown as it is, never translated.
        return item(
            "ok", "doctor.program.version", command=command, version=_version(command, flag)
        )

    core: list[DoctorItem] = []
    for command in ("git", "curl"):
        if exists(command):
            core.append(present(command))
        else:
            core.append(
                item(
                    "fail",
                    "doctor.program.missing",
                    fix=f"sudo {hint} {command}",
                    command=command,
                )
            )
    sections.append(DoctorSection("Core Dependencies", tuple(core), key="core"))

    web: list[DoctorItem] = []
    if exists("nginx"):
        web.append(present("nginx", "-v"))
        web.append(_unit_item(env, "nginx"))
    elif exists("apache2") or exists("httpd"):
        unit = "apache2" if exists("apache2") else "httpd"
        web.append(item("ok", "doctor.program.installed", command=unit))
        web.append(_unit_item(env, unit))
    else:
        web.append(item("fail", "doctor.web.none", fix=f"sudo {hint} nginx"))
    sections.append(DoctorSection("Web Server", tuple(web), key="web_server"))

    node: list[DoctorItem] = []
    if exists("node"):
        node.append(present("node"))
        if exists("npm"):
            node.append(present("npm"))
        else:
            node.append(item("fail", "doctor.node.npm_missing"))
        for manager in ("pnpm", "yarn", "bun"):
            if exists(manager):
                node.append(present(manager))
            else:
                node.append(item("info", "doctor.program.optional_missing", command=manager))
    elif _node_is_needed(env):
        node.append(
            item("fail", "doctor.program.missing", fix="sudo noust setup init", command="node")
        )
    else:
        node.append(item("info", "doctor.node.not_needed", fix="sudo noust setup init"))
    sections.append(DoctorSection("Node.js Environment", tuple(node), key="node"))

    py: list[DoctorItem] = []
    if exists("python3"):
        py.append(present("python3"))
        if exists("pip3"):
            py.append(present("pip3"))
        else:
            py.append(
                item(
                    "warn",
                    "doctor.program.missing",
                    fix=f"sudo {hint} python3-pip",
                    command="pip3",
                )
            )
    else:
        py.append(item("warn", "doctor.python.missing"))
    sections.append(DoctorSection("Python Environment", tuple(py), key="python"))

    if exists("certbot"):
        tls = present("certbot")
    else:
        tls = item("warn", "doctor.program.missing", fix=f"sudo {hint} certbot", command="certbot")
    sections.append(DoctorSection("SSL/TLS (Certbot)", (tls,), key="tls"))

    config: list[DoctorItem] = []
    if env.apps_dir.exists():
        config.append(item("ok", "doctor.config.apps_dir_ok", path=str(env.apps_dir)))
    else:
        config.append(
            item(
                "fail",
                "doctor.config.apps_dir_missing",
                fix="sudo noust setup init",
                path=str(env.apps_dir),
            )
        )
    if env.log_dir.exists():
        config.append(item("ok", "doctor.config.log_dir_ok", path=str(env.log_dir)))
    else:
        config.append(
            item(
                "fail",
                "doctor.config.log_dir_missing",
                fix="sudo noust setup init",
                path=str(env.log_dir),
            )
        )
    if env.config_path.exists():
        config.append(item("ok", "doctor.config.file_ok", path=str(env.config_path)))
    else:
        config.append(
            item(
                "warn",
                "doctor.config.file_missing",
                fix="sudo noust setup init",
                path=str(env.config_path),
            )
        )
    sections.append(DoctorSection("Noust Configuration", tuple(config), key="config"))

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
            (item("warn", "doctor.ssh.no_key", fix="noust setup ssh --generate"),),
            key="ssh",
        )
    items = [item("ok", "doctor.ssh.key_ok", path=str(key_path))]
    if env.include_ssh:
        success, _ = test_ssh_connection("github.com")
        if success:
            items.append(item("ok", "doctor.ssh.github_connected"))
        else:
            items.append(
                item(
                    "warn",
                    "doctor.ssh.github_refused",
                    notes=(say("doctor.ssh.github_note"),),
                    counted=False,
                )
            )
    else:
        items.append(
            item("info", "doctor.ssh.github_untested", fix="noust setup ssh --test github.com")
        )
    return DoctorSection("SSH Configuration", tuple(items), key="ssh")


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
    explained = say("doctor.names.explain")
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
        explain=explained.text,
        key="names",
        explain_coded=explained,
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
        return DoctorSection(
            "Docker Compose relays",
            (item("info", "doctor.relays.unread", reason=str(exc)),),
            key="relays",
        )
    if not found:
        return None
    return DoctorSection(
        "Docker Compose relays",
        tuple(
            item("warn", "doctor.relays.left", fix=left.fix, domain=left.domain, what=left.what)
            for left in found
        ),
        key="relays",
    )
