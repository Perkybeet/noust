# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a terminal, or a one-off command, runs: the one place that says as whom and where.

Three targets, each a decision about how much a shell is allowed to reach:

- **An application on this server** (nodejs, python, php, a monorepo...): its
  own account, :func:`~noust.managers.app_identity.service_account`, the only
  answer to "as whom", in the regime a migration runs in
  (:func:`~noust.deployers.helpers.sandbox.release_spec`): the application's
  identity and its secrets, in a transient unit of its own, in the directory
  its running code is in. The person at the keyboard can read the
  application's own environment, which is why they are there; they cannot
  read another application's, Noust's or root's. Nothing here is root.
- **A container of a Compose or container application**: ``docker exec`` into
  the container its stack runs a service in, found by the labels Compose puts
  on it, so an adopted stack is found too. The container's own user and
  privileges are what the compose file gave it, which ``compose_guard`` watches.
- **The server itself**, as root: the ``bash`` an administrator would get over
  SSH. It exists only behind its own setting (``terminal.server.enabled``),
  which :mod:`noust.terminal.settings` keeps off until it is turned on and,
  under the ENS profile, until it is forced.

A plan never starts anything: it is the argument list, the directory, the
environment and the sandbox, which :class:`~noust.terminal.sessions.SessionRegistry`
hands to :meth:`~noust.core.runner.CommandRunner.pty` and the one-off command to
``stream`` (rule 1). It carries the scrubber of the secrets its recording must
not hold.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.redact import Scrubber, known_credentials, scrubber_for
from noust.core.runner import SANDBOX_PATH, CommandRunner, SandboxSpec
from noust.core.store import App, is_compose_backed

#: The shells a person may ask for, by name. Never a path and never an
#: argument: what runs is ``<shell>`` and nothing the client composed.
SHELLS: tuple[str, ...] = ("bash", "sh", "ash", "zsh", "dash")

#: What every terminal is told it is.
TERMINAL_ENV: dict[str, str] = {
    "TERM": "xterm-256color",
    "COLORTERM": "truecolor",
    "LANG": "C.UTF-8",
}

#: The most tasks a shell as an application may have: a fork bomb typed at a
#: prompt stops here instead of taking the server.
TERMINAL_TASKS_MAX = 1024

#: What ``docker ps`` is asked to print about each container of a stack.
_PS_FORMAT = '{{.ID}}|{{.Label "com.docker.compose.service"}}|{{.State}}'

#: Deadline of the one ``docker ps`` that finds a stack's containers.
_DOCKER_PS_TIMEOUT = 20

#: A Compose service name.
_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")

logger = logging.getLogger(__name__)

KIND_APP = "app"
KIND_SERVER = "server"

#: The label every container of a Compose project carries.
_PROJECT_LABEL = "com.docker.compose.project"
_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"


@dataclass(frozen=True)
class TerminalTarget:
    """
    Where a session is, as the console and the audit trail say it.

    Attributes:
        kind: ``app`` or ``server``.
        domain: The application, for an application's terminal.
        service: The Compose service, for a stack's.
        label: What a header shows: ``shop.example.com``, ``shop.example.com
            (web)`` or ``server``.
        account: Who the shell runs as: the application's account, ``root``,
            or ``container`` for the user the image says.
        container: The container's id, for a stack's terminal.
    """

    kind: str
    domain: str | None
    service: str | None
    label: str
    account: str
    container: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The target as JSON-ready data (no container id: it is Docker's).
        """
        return {
            "kind": self.kind,
            "domain": self.domain,
            "service": self.service,
            "label": self.label,
            "account": self.account,
        }


@dataclass(frozen=True)
class SessionPlan:
    """
    Everything needed to open a terminal, except opening it.

    Attributes:
        target: Where it is.
        argv: The program and its arguments; the shell, never a shell line.
        cwd: Its working directory, when the runner sets one.
        env: What the shell is told; the sandbox's file or the runner's allowed
            variables carry the rest.
        sandbox: The confinement, for an application on this server.
        scrubber: Knows the secrets a recording must not hold.
    """

    target: TerminalTarget
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    sandbox: SandboxSpec | None = None
    scrubber: Scrubber = field(default_factory=Scrubber, compare=False)


@dataclass(frozen=True)
class StackContainer:
    """
    A running container of an application's stack.

    Attributes:
        id: Docker's short identifier.
        service: The Compose service it runs.
        state: ``running``, normally.
    """

    id: str
    service: str
    state: str


def validate_shell(shell: str | None) -> str | None:
    """
    Check a shell asked for by name.

    Args:
        shell: The name the client sent, or None for the default.

    Returns:
        The name, or None.

    Raises:
        ValidationError: A name that is not one of :data:`SHELLS`.
    """
    if shell is None or shell == "":
        return None
    if shell not in SHELLS:
        raise ValidationError(
            f"Not a shell this terminal opens: {shell!r}",
            details=f"Use one of: {', '.join(SHELLS)}.",
            field="shell",
        )
    return shell


def validate_service(service: str | None) -> str | None:
    """
    Check a Compose service named by the client.

    Args:
        service: The name, or None.

    Returns:
        The name, or None.

    Raises:
        ValidationError: A name Compose would not accept.
    """
    if service is None or service == "":
        return None
    if not _SERVICE.fullmatch(service):
        raise ValidationError(
            f"Not a Compose service name: {service!r}",
            details="A service is letters, digits, dots, dashes and underscores.",
            field="service",
        )
    return service


def _host_shell(shell: str | None) -> str:
    """
    Find a shell on this server, by name.

    Args:
        shell: The name asked for, or None for the first of bash and sh.

    Returns:
        Its absolute path.

    Raises:
        DeploymentError: There is none.
    """
    for name in (shell,) if shell else ("bash", "sh"):
        found = shutil.which(name, path=SANDBOX_PATH)
        if found:
            return found
    raise DeploymentError(
        f"This server has no {shell or 'bash or sh'} to open a terminal with",
        details="Install it, or ask for another shell.",
    )


def terminal_environment(app_label: str | None = None) -> dict[str, str]:
    """
    The variables every terminal starts with.

    Args:
        app_label: What the prompt names, when there is one.

    Returns:
        The terminal's type and locale, and a prompt that says where the shell is.
    """
    env = dict(TERMINAL_ENV)
    if app_label:
        env["PS1"] = f"\\u@{app_label}:\\w\\$ "
    return env


def _unit_variables(app: App) -> dict[str, str]:
    """
    Read what the application's unit sets for it: its ``PATH`` (a pinned runtime),
    its port, its mode.

    Best effort: a shell is still wanted when the unit cannot be read.

    Args:
        app: The application.

    Returns:
        The variables its unit sets with ``Environment=``; empty when it has
        no unit of its own or the unit cannot be read.
    """
    from noust.deployers import unit_environment
    from noust.managers.service_manager import ServiceManager

    try:
        manager = ServiceManager(verbose=False)
        units = manager.app_units(app)
        if not units:
            return {}
        unit = manager.inspect_unit(units[0])
        path = unit.fragment_path or unit.path
        return unit_environment.inline_variables(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, DeploymentError) as exc:
        logger.debug("Cannot read the unit of %s: %s", app.domain, exc)
        return {}


def _app_row(domain: str) -> App:
    """
    Find an application the caller may reach.

    Args:
        domain: Its domain.

    Returns:
        The row.

    Raises:
        ValidationError: There is no such application (or it is another team's,
            which reads the same).
    """
    from noust.core.teams import scoped_store

    app = scoped_store().get_app(domain)
    if app is None:
        raise ValidationError(f"Application not found: {domain}", field="domain")
    return app


def _host_app_plan(app: App, shell: str | None, *, interactive: bool) -> SessionPlan:
    """
    Plan a shell as an application's own account.

    Args:
        app: The application.
        shell: The shell asked for, by name.
        interactive: A terminal; False for a one-off command's sandbox.

    Returns:
        The plan, with no argv yet when ``interactive`` is False.
    """
    from noust.deployers.helpers import sandbox as build_sandbox
    from noust.deployers.helpers.layout import code_path_for, env_file_for
    from noust.managers.app_identity import service_account

    if app.is_static or app.app_type == "static":
        # A request that does not apply to this application, not a fault of the server.
        raise ValidationError(
            f"{app.domain} is a static site: it has no process to open a terminal in",
            details="The web server serves its files directly. Its files are in "
            f"{code_path_for(app)}.",
            field="domain",
        )
    user, group = service_account(app)
    code = code_path_for(app)
    env_file = env_file_for(app)
    spec = build_sandbox.release_spec(
        app_name=app.domain,
        user=user,
        group=group or user,
        build_path=code,
        env_file=env_file if env_file.is_file() else None,
    )
    spec = replace(
        spec,
        name=f"{app.domain}-{'terminal' if interactive else 'exec'}",
        interactive=interactive,
        # Nothing of the console's own environment reaches an application's
        # shell: it is the application's, from its unit and its .env.
        env_allow=("LANG", "LC_*", "TZ"),
        tasks_max=TERMINAL_TASKS_MAX,
        memory_max_mb=app.memory_max_mb,
        cpu_quota_percent=app.cpu_quota_percent,
    )
    env = _unit_variables(app)
    if interactive:
        env.update(terminal_environment(app.domain))
    env.setdefault("PATH", SANDBOX_PATH)
    target = TerminalTarget(
        kind=KIND_APP, domain=app.domain, service=None, label=app.domain, account=user
    )
    return SessionPlan(
        target=target,
        argv=(),
        cwd=code,
        env=env,
        sandbox=spec,
        scrubber=scrubber_for(app.domain),
    )


def _compose_filters(app: App) -> list[str]:
    """
    Say which labels find a stack's containers.

    Args:
        app: The application.

    Returns:
        ``docker ps --filter`` values, most exact first: the project the store
        records or Compose would derive, then the directory the stack runs from.
    """
    from noust.deployers.docker_compose import COMPOSE_FILE_PRIORITY, compose_project_name
    from noust.deployers.helpers.container_compose import COMPOSE_FILE as CONTAINER_FILE
    from noust.deployers.helpers.layout import app_root

    root = app_root(app)
    filters: list[str] = []
    project = app.compose_project
    if not project:
        names = (CONTAINER_FILE, *COMPOSE_FILE_PRIORITY)
        compose = next((root / name for name in names if (root / name).is_file()), None)
        project = compose_project_name(root, compose)
    if project:
        filters.append(f"label={_PROJECT_LABEL}={project}")
    filters.append(f"label={_WORKING_DIR_LABEL}={root}")
    return filters


def find_stack_containers(app: App, runner: CommandRunner) -> list[StackContainer]:
    """
    List the running containers of an application's stack.

    Args:
        app: A Compose or container application.
        runner: Runs ``docker ps``.

    Returns:
        Its running containers, one per service instance, sorted by service.

    Raises:
        DeploymentError: Docker cannot be asked.
    """
    if not runner.exists("docker"):
        raise DeploymentError(
            "Docker is not installed on this server",
            details="The stack's containers cannot be listed without it.",
        )
    found: list[StackContainer] = []
    for label in _compose_filters(app):
        result = runner.run(
            ["docker", "ps", "--filter", label, "--format", _PS_FORMAT],
            timeout=_DOCKER_PS_TIMEOUT,
        )
        if not result.success:
            raise DeploymentError(
                "Docker could not list the stack's containers",
                details=(result.stderr or result.stdout).strip() or "docker ps failed",
            )
        for line in result.stdout.splitlines():
            container, _, rest = line.partition("|")
            service, _, state = rest.partition("|")
            if container and service:
                found.append(StackContainer(container.strip(), service.strip(), state.strip()))
        if found:
            break
    return sorted(found, key=lambda item: (item.service, item.id))


def choose_container(app: App, service: str | None, runner: CommandRunner) -> StackContainer:
    """
    Pick the container a terminal or a command goes into.

    Args:
        app: A Compose or container application.
        service: The service the person asked for, if any.
        runner: Runs ``docker ps``.

    Returns:
        The container: the one of the service asked for; or, when none was
        asked for, the only service the stack runs, or its ``app`` service (the
        one a container application runs).

    Raises:
        ValidationError: The stack runs several services and none was named, or
            the one named is not running; the message lists what is.
        DeploymentError: Docker cannot be asked, or nothing of the stack runs.
    """
    containers = find_stack_containers(app, runner)
    if not containers:
        raise DeploymentError(
            f"No container of {app.domain} is running",
            details=f"Start it first: noust service start {app.domain}",
        )
    services = sorted({item.service for item in containers})
    wanted = service
    if wanted is None:
        if len(services) == 1:
            wanted = services[0]
        elif "app" in services and app.app_type == "container":
            wanted = "app"
        else:
            raise ValidationError(
                f"{app.domain} runs several services; say which one",
                details=f"Running now: {', '.join(services)}.",
                field="service",
            )
    for item in containers:
        if item.service == wanted:
            return item
    raise ValidationError(
        f"{app.domain} has no running service named {wanted!r}",
        details=f"Running now: {', '.join(services)}.",
        field="service",
    )


def plan_app_terminal(
    domain: str, *, service: str | None, shell_name: str | None, runner: CommandRunner
) -> SessionPlan:
    """
    Plan a terminal in an application.

    Args:
        domain: The application. Judged by the caller's teams first.
        service: The Compose service, for a stack.
        shell_name: The shell asked for, by name.
        runner: Asked where a stack's containers are.

    Returns:
        The plan.

    Raises:
        ValidationError: Unknown application, shell or service.
        DeploymentError: Nothing to open a terminal in.
    """
    shell = validate_shell(shell_name)
    service = validate_service(service)
    app = _app_row(domain)
    if is_compose_backed(app.app_type):
        container = choose_container(app, service, runner)
        name = shell or "sh"
        target = TerminalTarget(
            kind=KIND_APP,
            domain=app.domain,
            service=container.service,
            label=f"{app.domain} ({container.service})",
            account="container",
            container=container.id,
        )
        return SessionPlan(
            target=target,
            argv=("docker", "exec", "-it", "-e", "TERM", "-e", "COLORTERM", container.id, name),
            env={**terminal_environment(), "HOME": "/root"},
            scrubber=scrubber_for(app.domain),
        )
    if service is not None:
        raise ValidationError(
            f"{app.domain} is not a stack of services: there is no service to choose",
            field="service",
        )
    plan = _host_app_plan(app, shell, interactive=True)
    # --norc and --noprofile: the application's account has no startup files,
    # and what the operator's own would do is not what an application sees.
    flags = ("--norc", "--noprofile", "-i") if _host_shell(shell).endswith("bash") else ()
    return replace(plan, argv=(_host_shell(shell), *flags))


def plan_server_terminal(*, shell_name: str | None) -> SessionPlan:
    """
    Plan a root shell on the server. The caller has checked that it is allowed.

    Args:
        shell_name: The shell asked for, by name.

    Returns:
        The plan: a login shell as root, from ``/root``.

    Raises:
        ValidationError: Unknown shell.
        DeploymentError: This server has none.
    """
    program = _host_shell(validate_shell(shell_name))
    env = {
        **terminal_environment(),
        "HOME": "/root",
        "USER": "root",
        "LOGNAME": "root",
        "SHELL": program,
        "PATH": SANDBOX_PATH,
    }
    target = TerminalTarget(
        kind=KIND_SERVER, domain=None, service=None, label="server", account="root"
    )
    return SessionPlan(
        target=target,
        argv=(program, "-l") if program.endswith("bash") else (program,),
        cwd=Path("/root"),
        env=env,
        scrubber=Scrubber(known_credentials()),
    )


@dataclass(frozen=True)
class ExecPlan:
    """
    Everything needed to run one command in an application, except running it.

    Attributes:
        target: Where it runs.
        argv: The command, as the client gave it, behind whatever carries it
            (``docker exec <container>`` for a stack).
        cwd: Its working directory on this server.
        env: Its environment, over the sandbox's allow-list.
        sandbox: The confinement, for an application on this server.
        scrubber: Knows the secrets its output must not show.
    """

    target: TerminalTarget
    argv: tuple[str, ...]
    cwd: Path | None
    env: dict[str, str]
    sandbox: SandboxSpec | None
    scrubber: Scrubber


def validate_command(argv: list[str]) -> tuple[str, ...]:
    """
    Check a command given as an argument vector.

    Args:
        argv: The program and its arguments.

    Returns:
        The vector.

    Raises:
        ValidationError: Empty, too long, or holding something no program
            argument can: a NUL byte, or an element that is not text.
    """
    if not argv or not isinstance(argv, list):
        raise ValidationError(
            "A command needs a program",
            details="Give the program and its arguments as a list.",
            field="argv",
        )
    for part in argv:
        if not isinstance(part, str) or "\x00" in part:
            raise ValidationError("A command's arguments are text without NUL bytes", field="argv")
    if len(argv) > 256 or sum(len(part) for part in argv) > 64 * 1024:
        raise ValidationError("The command is too long", field="argv")
    if not argv[0].strip():
        raise ValidationError("The program is empty", field="argv")
    return tuple(argv)


def plan_app_exec(
    domain: str, argv: list[str], *, service: str | None, runner: CommandRunner
) -> ExecPlan:
    """
    Plan one command in an application's context.

    Args:
        domain: The application. Judged by the caller's teams first.
        argv: The command: the program and its arguments, never a shell line.
        service: The Compose service, for a stack.
        runner: Asked where a stack's containers are.

    Returns:
        The plan.

    Raises:
        ValidationError: Unknown application, service or command.
        DeploymentError: Nothing to run it in.
    """
    command = validate_command(argv)
    service = validate_service(service)
    app = _app_row(domain)
    if is_compose_backed(app.app_type):
        container = choose_container(app, service, runner)
        target = TerminalTarget(
            kind=KIND_APP,
            domain=app.domain,
            service=container.service,
            label=f"{app.domain} ({container.service})",
            account="container",
            container=container.id,
        )
        return ExecPlan(
            target=target,
            argv=("docker", "exec", container.id, *command),
            cwd=None,
            env={},
            sandbox=None,
            scrubber=scrubber_for(app.domain),
        )
    if service is not None:
        raise ValidationError(
            f"{app.domain} is not a stack of services: there is no service to choose",
            field="service",
        )
    plan = _host_app_plan(app, None, interactive=False)
    return ExecPlan(
        target=plan.target,
        argv=command,
        cwd=plan.cwd,
        env=plan.env,
        sandbox=plan.sandbox,
        scrubber=plan.scrubber,
    )


__all__ = [
    "SHELLS",
    "ExecPlan",
    "SessionPlan",
    "StackContainer",
    "TerminalTarget",
    "choose_container",
    "find_stack_containers",
    "plan_app_exec",
    "plan_app_terminal",
    "plan_server_terminal",
    "terminal_environment",
    "validate_command",
    "validate_service",
    "validate_shell",
]
