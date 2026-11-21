# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Adopt a Docker Compose stack that already runs, without touching it.

``noust app adopt`` and ``POST /api/apps/adopt`` (spec 3.2 section 1.4) are for
a stack an operator brought up by hand - Proggest, in ``/opt/proggest``, run by
its own ``deploy.sh`` under the project ``proggest`` and served by a site file
called ``proggest``. Deploying it with ``noust create`` would clone into a
directory that holds its data, derive a project name the containers do not
have (and so create new, empty volumes) and write a second site next to the
operator's. Adopting registers it as it is:

- **Nothing is fetched or cleaned.** The directory is claimed as an adoption
  (:func:`~noust.deployers.helpers.target.claim_deploy_target`), which accepts
  the files there and never removes them.
- **The project is the one the containers run under**, read from their
  ``com.docker.compose.project`` label, and pinned in the store
  (``apps.compose_project``): every compose command and the unit pass it with
  ``-p`` from then on. Only when nothing from that compose file has ever run
  is it the name Compose itself would derive.
- **Nothing would change.** ``docker compose -p P -f F up -d --remove-orphans
  --dry-run --no-build`` - what the unit and an update run, rehearsed - must
  say every container is already as it would be; otherwise the adoption is
  refused with that output, unless the operator read it and accepts it.
- **The site is the operator's**: the file in ``sites-available`` whose
  ``server_name`` lists the domain. A name other than the domain is recorded
  (``apps.site_name``), so ``config_path`` resolves the domain to it, and the
  file is never rewritten (spec section 1.5).
- **A unit is created with ``-p`` and enabled, not started**: the stack
  already runs, and starting the unit is an ``up`` nobody asked for.
- The application is registered ``inplace`` at its real path, and its history
  starts with one deployment, the adoption, at the commit checked out.

:func:`plan_adoption` does every check and changes nothing; :func:`adopt`
writes what a plan says. The CLI shows the plan and asks before adopting; the
API previews with ``preview: true``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from noust.central import require_server_role
from noust.core import audit, paths
from noust.core.applock import app_lock
from noust.core.config import Config
from noust.core.deploy_events import operation
from noust.core.docker.engine import compose_version, version_tuple
from noust.core.docker.gate import DockerGate
from noust.core.exceptions import DeploymentError, NoustError, ServiceError
from noust.core.fs import is_rehearsal
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.server_text import ServerText, say
from noust.core.store import (
    _COMPOSE_PROJECT,
    AppLayout,
    AppStatus,
    AppType,
    DeploymentTrigger,
    NoustStore,
    get_store,
)
from noust.core.utils import domain_to_app_name
from noust.deployers.docker_compose import DockerComposeDeployer, compose_project_name
from noust.deployers.helpers import edge as edges
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.compose_owners import declared_project
from noust.deployers.helpers.compose_ports import loopback_only
from noust.deployers.helpers.registration import StoreRegistrar
from noust.deployers.helpers.target import claim_deploy_target
from noust.deployers.recorder import checkout_git_info, recorder_for, recording
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ServiceManager
from noust.managers.source_manager import SourceManager
from noust.managers.supervision import keeps_alive_with_docker, remember_stack, service_control
from noust.managers.webserver import WebServerManager, config_serves_tls
from noust.validators.domain import validate_domain
from noust.validators.names import resolve_within, validate_filename

#: The labels Compose puts on every container it creates.
PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_VERSION_LABEL = "com.docker.compose.version"
CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"
WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"

#: One line per container: what it is, and which project and files made it.
_PS_FORMAT = "\t".join(
    (
        "{{.ID}}",
        "{{.Names}}",
        "{{.State}}",
        f'{{{{.Label "{PROJECT_LABEL}"}}}}',
        f'{{{{.Label "{CONFIG_FILES_LABEL}"}}}}',
        f'{{{{.Label "{WORKING_DIR_LABEL}"}}}}',
        f'{{{{.Label "{COMPOSE_VERSION_LABEL}"}}}}',
    )
)

#: Seconds ``docker ps`` may take.
DOCKER_TIMEOUT = 30

#: Seconds the rehearsed ``up`` may take: Compose reads every image it names.
DRY_RUN_TIMEOUT = 180

#: What the rehearsal runs after ``docker compose -p P -f F``: what the unit's
#: ExecStart and an update's recreate run, minus the build.
DRY_RUN_ARGS = ("up", "-d", "--remove-orphans", "--dry-run", "--no-build")

#: The start of a rehearsed status word that means a container, network or
#: volume would be made, made again or removed.
_CHANGE_WORDS = ("Recreat", "Creat", "Remov")

#: The prefix Compose puts on each line of a rehearsal, with its tick.
_DRY_RUN_PREFIX = re.compile(r"^.*?DRY-RUN MODE -\s*")
_TIMING = re.compile(r"\s+\d+(?:\.\d+)?s$")

#: Read-only probes this module runs (see :mod:`noust.core.runner`): a
#: rehearsed ``up`` changes nothing, so ``--dry-run`` may run it to show what
#: an adoption would find.
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    ("docker", "compose", "-p", "*", "-f", "*", *DRY_RUN_ARGS),
    ("docker", "compose", "-f", "*", *DRY_RUN_ARGS),
)

ProjectFrom = Literal["containers", "compose", "stack"]


def host_access_digest_of(lines: Iterable[str]) -> str:
    """
    Fingerprint the lines of a compose file that give this server to a container.

    What a person accepts when they adopt such a stack is the lines in front of them, and the file is a file:
    between the request that listed them and the one that accepts there is time. The digest says which lines
    were in front of them, so that accepting is accepting those.

    Args:
        lines: The lines, as the refusal lists them.

    Returns:
        A hexadecimal digest of the lines whatever their order; ``""`` for no lines.
    """
    ordered = sorted(lines)
    if not ordered:
        return ""
    return hashlib.sha256(json.dumps(ordered, ensure_ascii=False).encode("utf-8")).hexdigest()


class AdoptionRefusedError(DeploymentError):
    """
    Docker Compose did not show that adopting would leave the stack as it is.

    Attributes:
        output: Compose's own output for the rehearsed ``up``, verbatim.
        changes: The lines of it that would create, recreate or remove
            something.
        host_access: The lines of the compose file that give this server to a container, when that
            is what the stack has to accept before it is adopted.
    """

    def __init__(
        self,
        message: str,
        *,
        details: str,
        output: str = "",
        changes: tuple[str, ...] = (),
        host_access: tuple[str, ...] = (),
        coded: ServerText | None = None,
    ) -> None:
        """
        Args:
            message: What was refused.
            details: How to go on.
            output: Compose's output, verbatim.
            changes: The lines that would change something.
            host_access: The lines that give this server to a container.
            coded: The same sentence as a code, for the console to translate.
        """
        super().__init__(message, details=details, coded=coded)
        self.output = output
        self.changes = changes
        self.host_access = host_access

    @property
    def host_access_digest(self) -> str:
        """The digest of the lines it lists (:func:`host_access_digest_of`); empty when it lists none."""
        return host_access_digest_of(self.host_access)


@dataclass(frozen=True)
class StackContainer:
    """
    A container Compose made, as ``docker ps`` lists it.

    Attributes:
        id: Full container id.
        name: Container name.
        state: ``running``, ``exited``...
        project: Its Compose project.
        config_files: The compose files it was made from, as Compose recorded them.
        working_dir: The project directory Compose recorded.
        compose_version: The version of Compose that made it, as Compose recorded it; empty when it did not.
    """

    id: str
    name: str
    state: str
    project: str
    config_files: tuple[str, ...]
    working_dir: str
    compose_version: str = ""


#: The two things that can keep an adopted stack alive: a unit of systemd on a server, and Docker's own
#: restart policy on the central of a NAS (``central.deploy_here: containers``), which has no systemd.
KEPT_BY_UNIT = "unit"
KEPT_BY_DOCKER = "docker"


@dataclass(frozen=True)
class LostOnRestart:
    """
    A container of the stack that the machine restarting would leave down.

    Attributes:
        container: Its name.
        policy: Its restart policy.
        why: ``policy`` (it runs and its policy does not bring it back) or ``stopped`` (it was stopped
            under ``unless-stopped``).
    """

    container: str
    policy: str
    why: str


@dataclass(frozen=True)
class AdoptionPlan:
    """
    What adopting a stack would record, found without changing anything.

    Attributes:
        domain: The application's domain.
        app_name: Its unit's name on a server; what the application is called to Docker, on a NAS.
        app_path: The directory the stack runs from, as it is.
        compose_file: The compose file, relative to ``app_path``.
        project: The Compose project pinned for it; None when the stack names
            its own (``name:`` or ``COMPOSE_PROJECT_NAME``) and nothing ran.
        project_from: ``containers`` (their labels), ``compose`` (the name
            Compose derives) or ``stack`` (the stack names it itself).
        containers: The names of the containers from that file, in that project.
        running: Whether any of them is running.
        source: Where updates fetch from; empty for a stack that is not a checkout.
        branch: The branch updates follow.
        commit: The commit checked out, the adoption's history row's.
        site: The file in ``sites-available`` that serves the domain, or None.
        site_name: That file's name when it is not the domain, recorded so
            the domain resolves to it; None otherwise.
        ssl: Whether that site serves TLS.
        port: The port registered for the stack; None for a worker.
        headless: The stack publishes no port.
        dry_run: Compose's output for the rehearsed ``up``, verbatim.
        changes: The lines of it that would change something (accepted).
        warnings: What the operator should know, one sentence each.
        warnings_coded: ``warnings`` as sentences of the catalog, in the same order, for a console to say in
            its own language; None where the line has no sentence of its own yet.
        kept_by: ``unit`` (a unit is created, enabled and not started) or ``docker`` (no systemd here:
            the restart policy of the containers keeps the stack alive and nothing is written for it).
        address: Where the stack is reached when there is no site for it, ``http://<machine>:<port>``;
            None where a site serves it, and for a stack that serves no web.
        restart_loss: What a restart of the machine would not bring back, said where nothing but Docker
            brings it back; empty on a server, whose unit starts the whole stack at boot.
        host_access: The lines of the compose file that give this server to a container and that
            adopting records as accepted (the application's compose exception); empty when there are
            none or an exception was recorded already.
        accepted_by: Who accepted them.
    """

    domain: str
    app_name: str
    app_path: Path
    compose_file: str
    project: str | None
    project_from: ProjectFrom
    containers: tuple[str, ...]
    running: bool
    source: str
    branch: str | None
    commit: str | None
    site: str | None
    site_name: str | None
    ssl: bool
    port: int | None
    headless: bool
    dry_run: str
    changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    warnings_coded: tuple[ServerText | None, ...] = ()
    kept_by: str = KEPT_BY_UNIT
    address: str | None = None
    restart_loss: tuple[LostOnRestart, ...] = ()
    host_access: tuple[str, ...] = ()
    accepted_by: str = ""

    @property
    def unit(self) -> str:
        """The unit created for the stack; empty where Docker keeps it alive and there is none."""
        return self.app_name if self.kept_by == KEPT_BY_UNIT else ""

    @property
    def host_access_digest(self) -> str:
        """The digest of the lines that give this server to a container (:func:`host_access_digest_of`)."""
        return host_access_digest_of(self.host_access)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan as JSON-ready values.
        """
        data = dataclasses.asdict(self)
        data["app_path"] = str(self.app_path)
        data["containers"] = list(self.containers)
        data["changes"] = list(self.changes)
        data["warnings"] = list(self.warnings)
        data["warnings_coded"] = [
            None if sentence is None else sentence.to_dict() for sentence in self.warnings_coded
        ]
        data["restart_loss"] = [dataclasses.asdict(item) for item in self.restart_loss]
        data["host_access"] = list(self.host_access)
        data["host_access_digest"] = self.host_access_digest
        return data


@dataclass(frozen=True)
class AdoptionResult:
    """
    What an adoption did.

    Attributes:
        plan: What was adopted.
        adopted: False for a rehearsal, which records nothing.
        unit: The unit created for the stack; empty where Docker keeps it alive.
        deployment_id: The adoption's history row, when one was recorded.
    """

    plan: AdoptionPlan
    adopted: bool
    unit: str
    deployment_id: int | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The plan's values, with what was done.
        """
        return {
            **self.plan.to_dict(),
            "adopted": self.adopted,
            "unit": self.unit,
            "deployment_id": self.deployment_id,
        }


def _default_web() -> NginxManager | None:
    """
    Returns:
        The nginx manager sites are looked for with, a seam for tests; None where there is no site
        to find, because the central of a NAS has no web server to put one in.
    """
    if not edges.edge().has_site:
        return None
    return NginxManager()


# -- Reading what runs --------------------------------------------------------------


def parse_containers(stdout: str) -> list[StackContainer]:
    """
    Read the containers ``docker ps --format`` listed with :data:`_PS_FORMAT`.

    Args:
        stdout: Its output.

    Returns:
        One entry per well-formed line.
    """
    found: list[StackContainer] = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) not in (6, 7) or not parts[0].strip() or not parts[3].strip():
            continue
        container_id, name, state, project, files, working_dir, *made_by = (
            p.strip() for p in parts
        )
        found.append(
            StackContainer(
                id=container_id,
                name=name,
                state=state.lower(),
                project=project,
                config_files=tuple(f.strip() for f in files.split(",") if f.strip()),
                working_dir=working_dir,
                compose_version=made_by[0] if made_by else "",
            )
        )
    return found


def list_stack_containers(
    *, runner: CommandRunner, timeout: int = DOCKER_TIMEOUT
) -> list[StackContainer]:
    """
    List every container Compose made on this host, running or not, in one call.

    The one place that asks Docker which Compose containers exist (rule 3):
    adopting a stack reads its project from them, and an application's state
    reads from them whether a stack runs outside its unit.

    Args:
        runner: The runner docker is asked through.
        timeout: Seconds Docker has to answer; a status probe gives it less.

    Returns:
        One entry per container that carries a Compose project label.

    Raises:
        DeploymentError: Docker cannot be asked.
    """
    result = runner.run(
        [
            "docker",
            "ps",
            "-a",
            "--no-trunc",
            "--filter",
            f"label={PROJECT_LABEL}",
            "--format",
            _PS_FORMAT,
        ],
        timeout=timeout,
    )
    if not result.success:
        raise DeploymentError(
            "Docker could not be asked which containers run",
            details=(result.stderr or result.stdout).strip()
            or "Check the daemon: systemctl status docker",
        )
    return parse_containers(result.stdout)


def _made_from(container: StackContainer, compose_path: Path) -> bool:
    """
    Tell whether a container was made from a compose file.

    Args:
        container: The container.
        compose_path: The compose file.

    Returns:
        True when one of the files Compose recorded for it is that file.
    """
    wanted = os.path.realpath(compose_path)
    for entry in container.config_files:
        candidate = entry if os.path.isabs(entry) else os.path.join(container.working_dir, entry)
        if os.path.realpath(candidate) == wanted:
            return True
    return _made_through_a_mount(container, compose_path)


def _made_through_a_mount(container: StackContainer, compose_path: Path) -> bool:
    """
    Tell whether a container was made from a compose file by a tool that mounted its directory.

    Coolify runs ``docker compose`` in a helper container with the stack's directory
    (``/data/coolify/applications/<uuid>``) mounted at ``/artifacts/<id>``, so the labels name a
    path that does not exist on the host and the comparison by path finds nothing: the plan said
    that nothing from the file had ever run, and rehearsed creating everything, for a stack that had
    been serving for days. What survives the mount is the project, which such a tool names after the
    directory (so it is the name Compose gives the file), and the file's own name.

    Args:
        container: The container.
        compose_path: The compose file, on the host.

    Returns:
        True when the container's project is the one Compose derives from the file's directory and
        one of its files has the file's name.
    """
    derived = compose_project_name(compose_path.parent, compose_path)
    if derived is None or container.project != derived:
        return False
    return any(os.path.basename(entry) == compose_path.name for entry in container.config_files)


def find_stack_project(
    compose_path: Path, *, runner: CommandRunner
) -> tuple[str | None, tuple[StackContainer, ...]]:
    """
    Read the Compose project a compose file's containers run under.

    Args:
        compose_path: The compose file, absolute.
        runner: The runner docker is asked through.

    Returns:
        The project and its containers made from that file; ``(None, ())``
        when no container was ever made from it. Running containers decide;
        stopped ones only when none runs.

    Raises:
        DeploymentError: Docker cannot be asked, or containers from the file
            run under more than one project, or under a name Compose would
            not accept back as ``-p``.
    """
    made = [c for c in list_stack_containers(runner=runner) if _made_from(c, compose_path)]
    if not made:
        return None, ()
    running = sorted({c.project for c in made if c.state == "running"})
    projects = running or sorted({c.project for c in made})
    if len(projects) > 1:
        raise DeploymentError(
            f"{compose_path} runs as more than one Compose project",
            details=f"Containers made from it run as: {', '.join(projects)}. Take down the "
            "ones that are not the application (docker compose -p NAME down, without -v "
            "to keep their volumes) and adopt again.",
        )
    project = projects[0]
    if not _COMPOSE_PROJECT.match(project):
        raise DeploymentError(
            f"The stack runs as {project!r}, which is not a name Compose accepts with -p",
            details="Bring it up again under a project of lower-case letters, digits, '-' "
            "and '_' (docker compose -p NAME up -d), then adopt it.",
        )
    return project, tuple(c for c in made if c.project == project)


def changed_lines(output: str) -> tuple[str, ...]:
    """
    Pick out what a rehearsed ``up`` would create, recreate or remove.

    Args:
        output: Compose's output for ``up --dry-run``.

    Returns:
        Each such line, without the rehearsal prefix and timing.
    """
    changes: list[str] = []
    for raw in output.splitlines():
        line = _TIMING.sub("", _DRY_RUN_PREFIX.sub("", raw.strip())).strip()
        words = line.split()
        if len(words) >= 2 and words[-1].startswith(_CHANGE_WORDS):
            changes.append(line)
    return tuple(changes)


def _starting_lines(output: str) -> tuple[str, ...]:
    """
    Args:
        output: Compose's output for ``up --dry-run``.

    Returns:
        The containers it would start, which is not a change to what they are.
    """
    lines = []
    for raw in output.splitlines():
        line = _TIMING.sub("", _DRY_RUN_PREFIX.sub("", raw.strip())).strip()
        words = line.split()
        if len(words) >= 2 and words[-1] in ("Starting", "Started"):
            lines.append(line)
    return tuple(lines)


# -- Finding the site -----------------------------------------------------------------


def find_site(
    domain: str, web: WebServerManager, *, requested: str | None = None
) -> tuple[str | None, str, bool]:
    """
    Find the file in ``sites-available`` that serves a domain.

    Args:
        domain: The domain.
        web: The web server's manager.
        requested: The file the operator named (``--site``), checked to serve
            the domain; None to look for it by the names each file serves.

    Returns:
        The file's name (without the backend's suffix), its text and whether
        it is enabled; ``(None, "", False)`` when no file serves the domain.

    Raises:
        DeploymentError: The named file does not exist or does not answer on
            the domain, or more than one enabled file answers on it.
    """
    suffix = web.backend.config_suffix
    if requested is not None:
        path = resolve_within(web.sites_available, validate_filename(f"{requested}{suffix}"))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise DeploymentError(
                f"No site called {requested} in {web.sites_available}",
                details=f"{exc}. Name the file as it is in sites-available, or leave --site "
                "out to find the one that answers on the domain.",
            ) from exc
        names = web.names_in(text)
        if domain not in names:
            raise DeploymentError(
                f"The site {requested} does not answer on {domain}",
                details=f"It serves: {', '.join(names) or 'no name'}. Name the file whose "
                f"server_name lists {domain}, or leave --site out to find it.",
            )
        link = web.sites_enabled / path.name
        return requested, text, link.exists() or link.is_symlink()

    candidates: list[tuple[str, str, bool]] = []
    for entry in web.list_sites():
        try:
            text = Path(entry.config_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            # A file that cannot be read serves nothing anyone can adopt.
            continue
        if domain in web.names_in(text):
            candidates.append((entry.domain, text, entry.enabled))
    if not candidates:
        return None, "", False
    for candidate in candidates:
        if candidate[0] == domain:
            return candidate
    enabled = [c for c in candidates if c[2]]
    if len(candidates) == 1:
        return candidates[0]
    if len(enabled) == 1:
        return enabled[0]
    raise DeploymentError(
        f"More than one site answers on {domain}",
        details=f"{', '.join(c[0] for c in candidates)}. Name the one that serves it with "
        "--site (site in the API).",
    )


# -- The plan ---------------------------------------------------------------------------


def checked_directory(path: Path) -> Path:
    """
    Args:
        path: The directory the operator named.

    Returns:
        It, unchanged.

    Raises:
        DeploymentError: It is relative, missing, or not a real directory.
    """
    if not path.is_absolute():
        raise DeploymentError(
            f"{path} is not an absolute path",
            details="Name the directory the stack runs from as an absolute path, such as "
            "/opt/shop.",
        )
    if not path.exists():
        if keeps_alive_with_docker():
            raise DeploymentError(
                f"The central does not see {path}: it does not exist where this central runs",
                details=(
                    "The central runs in a container and sees only the folders mounted into it, at "
                    "the same path as on the NAS. Add this line under volumes: in its compose.yaml "
                    f"(or mount the folder that holds all your stacks, once), recreate the central "
                    f"and adopt again:\n  - {path}:{path}"
                ),
            )
        raise DeploymentError(
            f"{path} does not exist", details="Name the directory the stack runs from."
        )
    if path.is_symlink() or not path.is_dir():
        raise DeploymentError(
            f"{path} is not a directory",
            details="Name the real directory the stack runs from, not a link to it.",
        )
    return path


#: Directories that are the system's, never one application's: adopting one
#: would let an update reset it with git as root.
_SYSTEM_DIRECTORIES = frozenset(
    Path(p)
    for p in (
        "/",
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/home",
        "/lib",
        "/lib64",
        "/media",
        "/mnt",
        "/opt",
        "/proc",
        "/root",
        "/run",
        "/sbin",
        "/srv",
        "/sys",
        "/tmp",  # noqa: S108 - refused, never written
        "/usr",
        "/usr/local",
        "/var",
        "/var/lib",
        "/var/log",
        "/var/www",
        "/var/tmp",  # noqa: S108 - refused, never written
    )
)

#: Trees nothing is adopted inside.
_SYSTEM_TREES = tuple(
    Path(p)
    for p in (
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/lib",
        "/lib64",
        "/proc",
        "/run",
        "/sbin",
        "/sys",
        "/usr/bin",
        "/usr/lib",
        "/usr/sbin",
        "/usr/share",
    )
)


def refuse_shared_directory(path: Path, domain: str, store: NoustStore) -> None:
    """
    Refuse a directory that is not one stack's alone.

    Args:
        path: The directory, checked by :func:`checked_directory`.
        domain: The domain being adopted.
        store: The store other applications are read from.

    Raises:
        DeploymentError: It is a system directory, Noust's own, the apps
            directory or in it (other than this domain's own directory), or
            it is, holds or is inside another application's directory.
    """
    real = path.resolve()
    noust_dirs = {
        paths.config_dir().resolve(),
        paths.state_dir().resolve(),
        paths.backup_dir().resolve(),
        paths.log_dir().resolve(),
    }
    if (
        real in _SYSTEM_DIRECTORIES
        or any(real == tree or real.is_relative_to(tree) for tree in _SYSTEM_TREES)
        or any(real == d or real.is_relative_to(d) or d.is_relative_to(real) for d in noust_dirs)
    ):
        raise DeploymentError(
            f"{path} is a system directory; it is never adopted",
            details="Adopt the directory the stack's own checkout is in, such as /opt/<name>.",
        )
    apps = Config().apps_directory.resolve()
    own = apps / domain_to_app_name(domain)
    if real != own and (real == apps or real.is_relative_to(apps) or apps.is_relative_to(real)):
        raise DeploymentError(
            f"{path} is Noust's apps directory or in it; it is not adopted",
            details=f"Applications there are Noust's own. A stack for {domain} may be adopted "
            f"from {own}, or from its own directory outside {apps}.",
        )
    for app in store.list_apps():
        if app.domain == domain or not app.app_path:
            continue
        theirs = Path(app.app_path).resolve()
        if real == theirs or real.is_relative_to(theirs) or theirs.is_relative_to(real):
            raise DeploymentError(
                f"{path} overlaps {app.domain}'s directory ({app.app_path})",
                details="An adopted stack's updates reset its directory with git; it must "
                "not hold, or be inside, another application's.",
            )


class _Warnings:
    """
    What a plan found worth saying: each line in English and, when it is a sentence of the catalog, as that.

    The command line prints the English, a console says the sentence in its own language from the code, and
    the two lists must stay the same length, so a line is added in one place that keeps both.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.sentences: list[ServerText | None] = []

    def add(self, warning: str | ServerText) -> None:
        """
        Args:
            warning: A sentence of the catalog, or a line that has none yet.
        """
        if isinstance(warning, ServerText):
            self.lines.append(warning.text)
            self.sentences.append(warning)
        else:
            self.lines.append(warning)
            self.sentences.append(None)

    def extend(self, warnings: Iterable[str | ServerText]) -> None:
        """
        Args:
            warnings: Several, as :meth:`add` takes them.
        """
        for warning in warnings:
            self.add(warning)


def plan_adoption(
    domain: str,
    path: Path,
    *,
    compose_file: str | None = None,
    source: str | None = None,
    branch: str | None = None,
    site: str | None = None,
    port: int | None = None,
    accept_recreate: bool = False,
    accept_host_access: bool = False,
    host_access_digest: str | None = None,
    accepted_by: str = "",
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
    web: NginxManager | None = None,
) -> AdoptionPlan:
    """
    Find everything adopting a stack would record, changing nothing.

    Args:
        domain: The domain the stack serves.
        path: The directory it runs from.
        compose_file: Its compose file, relative to ``path``; found among the
            usual names when None.
        source: Where updates fetch from; the checkout's ``origin`` when None. A
            directory that is not a checkout is adopted with no source at all.
        branch: The branch updates follow; the one checked out when None.
        site: The site file that serves the domain; found when None.
        port: The port to register; the compose file's when None.
        accept_recreate: Adopt even when the rehearsed ``up`` would change
            something, which is then a warning.
        accept_host_access: Adopt although the stack runs with what gives this server to a container
            (the lines of its compose file that the guard refuses a new stack): the plan carries them
            and adopting records them as the application's compose exception, in the name of
            ``accepted_by``. Whoever says so is equivalent to root.
        host_access_digest: The digest of the lines the person was shown (:func:`host_access_digest_of`), which is
            what accepting them covers: the lines that are there now have to be those, and when they are not
            the refusal lists the ones that are. ``""`` is a person who was shown none (a client that left it
            out) and accepts nothing. None is for a caller that holds this very plan and shows it before it
            records it (the command line), and accepts what the plan finds.
        accepted_by: Who accepts it, for the record.
        runner: The runner docker and git go through.
        store: The store checked for the domain.
        web: The web server's manager sites are looked for with.

    Returns:
        The plan.

    Raises:
        DeploymentError: The domain is deployed already, the directory or the
            compose file is not usable, a source is named for a directory that is
            not a git checkout (or a checkout has no source), its unit's name is
            taken, or the site is ambiguous.
        AdoptionRefusedError: The rehearsed ``up`` failed or would change
            something, or the stack runs with what gives this server to a container, and that was
            not accepted, or not as it is now.
    """
    domain = validate_domain(domain)
    runner = runner if runner is not None else get_runner()
    store = store if store is not None else get_store()
    path = checked_directory(path)
    refuse_shared_directory(path, domain, store)
    if store.get_app(domain) is not None:
        raise DeploymentError(
            f"{domain} is already deployed",
            details=f"Adopting registers a stack Noust has no record of. See it with: "
            f"noust status {domain}",
        )
    warnings = _Warnings()

    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(
        domain, source or str(path), app_path=path, compose_file=compose_file, port=port
    )
    deployer._is_new_deployment = False
    docker_keeps_it = keeps_alive_with_docker()
    if docker_keeps_it:
        # First of all: looking for the compose file in a folder that cannot be opened is a bare PermissionError.
        _require_readable(path, None)
    deployer._discover_compose_file()
    # The host privilege guard runs below, once the containers say whether
    # the stack really runs as this file says.
    deployer._parse_compose_services(guard=False)
    compose_path = deployer._compose_file_path()
    claim_deploy_target(path, domain=domain, existing=None, replace=False, adopt=True)

    if docker_keeps_it:
        _require_readable(path, compose_path)
    else:
        unit = ServiceManager(runner=runner).inspect_unit(deployer.app_name, serving=False)
        if unit.exists:
            raise DeploymentError(
                f"A unit called {unit.unit} already exists",
                details=f"Adopting creates {unit.unit} for the stack. Remove the one at "
                f"{unit.path} if it is left over, or adopt under the domain it belongs to.",
            )

    repo = SourceManager(runner=runner).get_repo_info(path)
    if not repo["is_git"] and source:
        raise DeploymentError(
            f"{path} is not a git checkout",
            details="A source is where an update fetches from, and it resets the checkout to "
            "the branch, keeping what git does not track. Clone the repository there (or git "
            "init and add its remote), or adopt the stack without --source.",
        )
    fetch_from = source or repo["remote"]
    if not repo["is_git"]:
        # Coolify, Portainer and a tarball unpacked by hand leave stacks that never were a
        # checkout. What adopting is for, keeping what runs, does not depend on one; an update
        # then downloads the images the compose file names instead of fetching code.
        fetch_from = ""
        repo = {**repo, "branch": None, "commit": None}
        warnings.add(say("compose_adopt.no_repository", path=str(path)))
    elif not fetch_from:
        raise DeploymentError(
            f"The checkout in {path} has no remote 'origin'",
            details="Name where updates fetch from with --source (source in the API).",
        )
    if repo["dirty"]:
        warnings.add(
            "The checkout has changes to tracked files; the next update resets them to the "
            "branch (files git does not track, such as .env and data, are kept)."
        )

    project, containers = find_stack_project(compose_path, runner=runner)
    project_from: ProjectFrom = "containers"
    if project is None:
        project = compose_project_name(path, compose_path)
        project_from = "compose" if project is not None else "stack"
        shown_file = deployer.compose_file or compose_path.name
        warnings.add(
            say("compose_adopt.nothing_ran", file=shown_file, project=project)
            if project is not None
            else say("compose_adopt.nothing_ran_named", file=shown_file)
        )

    dry_run, changes = rehearse_up(
        deployer,
        project,
        accept_recreate=accept_recreate,
        started_by=tuple(sorted({c.compose_version for c in containers if c.compose_version})),
    )
    # The project the stack runs under is the one adopting pins: the guard judges the file against it.
    deployer._stored_project = project or declared_project(compose_path) or ""
    host_access = guard_running_stack(
        deployer,
        running=any(c.state == "running" for c in containers),
        dry_run=dry_run,
        changes=changes,
    )
    if host_access and not accept_host_access:
        raise _refused_for_host_access(domain, path, host_access)
    if host_access and host_access_digest is not None:
        if host_access_digest == "":
            raise _refused_for_host_access(domain, path, host_access)
        if host_access_digest != host_access_digest_of(host_access):
            raise _refused_for_changed_host_access(domain, path, host_access)
    if host_access:
        accepted_by = accepted_by or "adoption"
        warnings.add(
            say(
                "compose_guard.adopted_host_access",
                who=accepted_by,
                domain=domain,
                count=len(host_access),
                command=f"noust app sandbox compose-exception {domain} --revoke",
            )
        )
    if changes:
        warnings.add("Accepted: the next start or update changes " + "; ".join(changes) + ".")
    for line in _starting_lines(dry_run):
        warnings.add(f"Stopped now, started by the next start or update: {line}.")

    headless = deployer._is_headless()
    site_file: str | None = None
    site_text = ""
    has_site = edges.edge().has_site
    if not headless and has_site:
        found_web = web or _default_web()
        if found_web is None:
            raise DeploymentError(
                "There is no web server to look for the stack's site in",
                details="Install nginx or Apache with: noust setup init",
            )
        site_file, site_text, enabled = find_site(domain, found_web, requested=site)
        if site_file is None:
            warnings.add(
                f"No site in sites-available answers on {domain}; nothing serves it through "
                "the web server until one does."
            )
        elif not enabled:
            warnings.add(f"The site {site_file} is not enabled.")

    port = None if headless else deployer._get_primary_port()
    address: str | None = None
    lost: tuple[LostOnRestart, ...] = ()
    if docker_keeps_it:
        if port is not None:
            address = edges.address(port, runner=runner)
            if loopback_only(deployer.services):
                warnings.add(
                    "It publishes its port on 127.0.0.1 only: it is reachable from the NAS itself "
                    "and not from your network. Publish it on the network in your compose file, or "
                    "reach it through a reverse proxy on the NAS."
                )
        lost = _lost_on_restart(project, runner=runner, store=store)
        warnings.extend(_restart_warnings(lost))

    relative = str(compose_path.relative_to(path))
    return AdoptionPlan(
        domain=domain,
        app_name=deployer.app_name,
        app_path=path,
        compose_file=relative,
        project=project,
        project_from=project_from,
        containers=tuple(c.name for c in containers),
        running=any(c.state == "running" for c in containers),
        source=fetch_from,
        branch=branch or repo["branch"],
        commit=repo["commit"],
        site=site_file,
        site_name=site_file if site_file is not None and site_file != domain else None,
        ssl=config_serves_tls(site_text) if site_text else False,
        port=port,
        headless=headless,
        dry_run=dry_run,
        changes=changes,
        warnings=tuple(warnings.lines),
        warnings_coded=tuple(warnings.sentences),
        kept_by=KEPT_BY_DOCKER if docker_keeps_it else KEPT_BY_UNIT,
        address=address,
        restart_loss=lost,
        host_access=host_access,
        accepted_by=accepted_by if host_access else "",
    )


def _refused_for_host_access(domain: str, path: Path, lines: Sequence[str]) -> AdoptionRefusedError:
    """
    Make the refusal that shows what adopting would accept, line by line.

    Args:
        domain: The application.
        path: The directory the stack runs from.
        lines: What the compose file gives this server to a container with.

    Returns:
        The refusal, with the sentence a console translates and the lines it lists.
    """
    said = say(
        "compose_guard.adopt_host_access",
        domain=domain,
        lines="\n".join(f"- {line}" for line in lines),
        command=f"noust app adopt {domain} --path {path} --accept-host-access",
    )
    return AdoptionRefusedError(
        said.text, details=said.hint or "", host_access=tuple(lines), coded=said
    )


def _refused_for_changed_host_access(
    domain: str, path: Path, lines: Sequence[str]
) -> AdoptionRefusedError:
    """
    Make the refusal that says what a stack gives this server is not what the person was shown.

    Args:
        domain: The application.
        path: The directory the stack runs from.
        lines: What the compose file gives this server to a container with, as it is now.

    Returns:
        The refusal, with the lines of now for the person to read again.
    """
    said = say(
        "compose_guard.host_access_changed",
        domain=domain,
        lines="\n".join(f"- {line}" for line in lines),
        command=f"noust app adopt {domain} --path {path} --accept-host-access",
    )
    return AdoptionRefusedError(
        said.text, details=said.hint or "", host_access=tuple(lines), coded=said
    )


def _require_readable(folder: Path, compose_path: Path | None) -> None:
    """
    Refuse a stack whose files the central's own user cannot read, before Compose is asked.

    The central runs as an unprivileged user inside its container, and ``docker compose`` runs there: it
    reads the compose file and the ``.env`` beside it as that user. A NAS's shared folders belong to
    whoever created them, so this is the first thing that goes wrong, and Compose's own message for it
    (a bare permission error from a rehearsal) does not say whose permission.

    Args:
        folder: The directory the stack runs from.
        compose_path: Its compose file; None before it is known, when only the folder can be judged.

    Raises:
        DeploymentError: The folder, the compose file or the ``.env`` cannot be read by this user.
    """
    uid, gid = os.getuid(), os.getgid()
    for target in (folder, compose_path, folder / ".env"):
        if target is None or not target.exists():
            continue
        needed = os.R_OK | os.X_OK if target.is_dir() else os.R_OK
        if os.access(target, needed):
            continue
        name = str(target.relative_to(folder)) if target != folder else str(folder)
        raise DeploymentError(
            f"The central cannot read {name}: it runs as user {uid}",
            details=(
                f"Give the central's user ({uid}:{gid}) read access to {target}: a group it is in, an "
                "ACL for it, or the owner. The central only reads it, so that Compose can read the "
                "project; it never writes to a stack it adopts."
            ),
        )


def _lost_on_restart(
    project: str | None, *, runner: CommandRunner, store: NoustStore
) -> tuple[LostOnRestart, ...]:
    """
    Find what the NAS restarting would not bring back of a stack, as Docker's zone says it.

    Args:
        project: The Compose project the stack runs under.
        runner: The runner Docker is asked through.
        store: The store the owners are read from.

    Returns:
        The containers of the project whose policy would leave them down; empty when the stack does not
        run yet (nothing is down to lose) or Docker cannot say.
    """
    if not project:
        return ()
    try:
        found = DockerGate(runner, store=store).restart_loss()
    except NoustError:
        # Said as nothing rather than as a refusal: the adoption proved the stack with Docker a moment
        # ago, and what a restart would lose is a warning, never a reason not to register it.
        return ()
    return tuple(
        LostOnRestart(item.name, item.policy, item.why) for item in found if item.project == project
    )


def _restart_warnings(lost: Sequence[LostOnRestart]) -> list[str]:
    """
    Say, once each, what a restart of the machine would leave down.

    Args:
        lost: What would not come back.

    Returns:
        One sentence per container, with the line that fixes it; Noust does not write it, because the
        compose file is the operator's.
    """
    said = []
    for item in lost:
        if item.why == "stopped":
            said.append(
                f"{item.container} is stopped under the policy '{item.policy}': after the NAS restarts "
                "nothing starts it again, and without a unit of Noust's nothing else does."
            )
        else:
            said.append(
                f"{item.container} has the restart policy '{item.policy}': after the NAS restarts "
                "nothing starts it again, and without a unit of Noust's nothing else does. Noust does "
                "not change your compose file; add restart: unless-stopped to the service."
            )
    return said


def guard_running_stack(
    deployer: DockerComposeDeployer,
    *,
    running: bool,
    dry_run: str,
    changes: Sequence[str],
) -> tuple[str, ...]:
    """
    Apply the host privilege guard to a stack Noust is about to take charge of.

    The one rule adopting a stack and handing one back share. What the guard refuses (``privileged``,
    the Docker socket, a device, a path of the server...) is accepted only from a stack proven to run
    with it: containers made from this file running, and an ``up`` that would neither recreate nor
    start anything. Otherwise the file is a new stack's, and taking charge of it would start what it
    asks for as root.

    Args:
        deployer: The deployer, with the compose file discovered.
        running: Whether a container made from the file runs.
        dry_run: Compose's output for the rehearsed ``up``.
        changes: The lines of it that would change something (accepted).

    Returns:
        What the stack is proven to run with that the guard refuses a new stack, and the
        application has no exception for: the lines adopting has to be told about. Empty for a stack
        that runs with none, and for an application that has an exception already.

    Raises:
        DeploymentError: The file asks for what the stack is not proven to have, without an
            exception.
    """
    document = deployer._load_compose_document()
    proven = running and not changes and not _starting_lines(dry_run)
    # What runs unchanged from this file is proven, and the file is on disk, so what Compose
    # resolves it to is asked as well: an include's service is as proven as the file's own.
    refusals = deployer._refusals_in(document, resolve=True) if proven else []
    deployer._check_host_privileges(document, accepted=refusals)
    if build_sandbox.get_compose_exception(deployer.domain, store=deployer.store) is not None:
        return ()
    return tuple(refusals)


def _another_compose(runner: CommandRunner, started_by: Sequence[str]) -> str:
    """
    Say when the containers were made by another Compose than the one asked now.

    A stack started by the machine's own Compose (a NAS's Container Manager, a person at its shell) and read by the
    Compose that is in this image comes out as changed in every container, whatever the file says, because Compose
    keeps a hash of each service's configuration in a label and another version does not read a service to the same
    hash. The rehearsal cannot tell that from a real change, so the sentence says what the labels say.

    Args:
        runner: Asks Docker for the version of its Compose.
        started_by: The versions of Compose that made the containers.

    Returns:
        A sentence that ends with a space, or nothing when the Compose is the same or it is not known.
    """
    current = compose_version(runner)
    other = [version for version in started_by if version_tuple(version) != current]
    if not current or not other:
        return ""
    shown = ".".join(str(number) for number in current)
    return (
        f"These containers were made by Docker Compose {', '.join(other)} and this is Compose {shown}: another "
        "version does not read a service to the same configuration hash, so it takes containers for changed "
        "that are not. "
    )


def rehearse_up(
    deployer: DockerComposeDeployer,
    project: str | None,
    *,
    accept_recreate: bool,
    action: str = "Adopting",
    retry: str = "adopt",
    started_by: Sequence[str] = (),
) -> tuple[str, tuple[str, ...]]:
    """
    Ask Compose what ``up`` would do to the stack, and refuse what changes it.

    The one proof that starting a stack as Noust would leave the containers
    that run as they are: adopting a stack asks it, and so does handing back
    one that runs outside its unit (:mod:`noust.deployers.compose_reclaim`).

    Args:
        deployer: The deployer, with the compose file discovered.
        project: The project to address.
        accept_recreate: Return the changes instead of refusing them.
        action: What needs the proof, capitalised, for the refusal
            (``Adopting``).
        retry: The command, as a verb, that takes ``--accept-recreate``
            (``adopt``).
        started_by: The versions of Compose that made the containers that run, when they said.

    Returns:
        Compose's output and the lines that would change something.

    Raises:
        AdoptionRefusedError: The rehearsal failed, or would change something,
            and that was not accepted.
    """
    argv = deployer._compose(*DRY_RUN_ARGS, project=project)
    result = deployer.runner.run(argv, cwd=deployer.app_path, timeout=DRY_RUN_TIMEOUT)
    output = "\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p)
    if not result.success:
        if accept_recreate:
            return output, ("the rehearsal itself failed, so nothing was proven",)
        raise AdoptionRefusedError(
            "Docker Compose could not show what starting the stack would change",
            details=f"{action} needs 'docker compose up --dry-run' (Docker Compose 2.20 or "
            f"later) to prove nothing would be recreated. Fix what the output says, or {retry} "
            "with --accept-recreate (accept_recreate in the API) after reading it.",
            output=output,
        )
    changes = changed_lines(output)
    if changes and not accept_recreate:
        raise AdoptionRefusedError(
            f"Starting {deployer.domain}'s stack as Noust would changes what runs",
            details="Compose would create, recreate or remove: "
            + "; ".join(changes)
            + ". "
            + _another_compose(deployer.runner, started_by)
            + "The running containers differ from the compose file (another project, "
            "file, profile or environment). Bring the stack up the way it is meant to run, "
            f"or {retry} with --accept-recreate (accept_recreate in the API) to accept it.",
            output=output,
            changes=changes,
        )
    return output, changes


# -- Adopting --------------------------------------------------------------------------


def adopt(
    plan: AdoptionPlan,
    *,
    trigger: str = DeploymentTrigger.CLI.value,
    job_id: str | None = None,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
) -> AdoptionResult:
    """
    Record what a plan found: the application, its project, its site, a unit, a first row.

    Args:
        plan: What :func:`plan_adoption` found.
        trigger: Who asked, for the history row.
        job_id: The background job adopting, when one is.
        runner: The runner systemd goes through.
        store: The store written to.

    Returns:
        What was done; nothing under ``--dry-run``.

    Raises:
        DeploymentError: The domain was deployed meanwhile, or the unit
            could not be created; nothing stays registered.
        AppBusyError: Another operation is running on the domain.
    """
    require_server_role("container_deploy")
    if is_rehearsal():
        return AdoptionResult(plan=plan, adopted=False, unit=plan.unit, deployment_id=None)
    runner = runner if runner is not None else get_runner()
    store = store if store is not None else get_store()

    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(
        plan.domain,
        plan.source or str(plan.app_path),
        app_path=plan.app_path,
        compose_file=plan.compose_file,
        branch=plan.branch,
        trigger=trigger,
        job_id=job_id,
    )
    deployer._discover_compose_file()
    deployer.store = store
    log = deployer.logger
    services = service_control(ServiceManager, verbose=deployer.verbose, runner=runner)
    source = SourceManager(runner=runner)

    with app_lock(plan.domain, "adopt"), operation("adopt"):
        if store.get_app(plan.domain) is not None:
            raise DeploymentError(
                f"{plan.domain} was deployed while it was being adopted",
                details=f"See it with: noust status {plan.domain}",
            )
        recorder = recorder_for(deployer, git_info=checkout_git_info(source, plan.app_path))
        with recording(recorder, git_branch=plan.branch):
            log.step(1, 2, "Registering the stack as it runs")
            app = StoreRegistrar(store).register_app(
                domain=plan.domain,
                app_type=AppType.DOCKER_COMPOSE.value,
                source=plan.source,
                branch=plan.branch,
                port=plan.port,
                app_path=plan.app_path,
                # A machine that keeps stacks alive with Docker has no web server of Noust's to record.
                webserver="" if plan.headless or plan.kept_by == KEPT_BY_DOCKER else "nginx",
                ssl_enabled=plan.ssl,
                status=(AppStatus.RUNNING if plan.running else AppStatus.STOPPED).value,
                is_static=False,
                env_vars={},
                layout=AppLayout.INPLACE.value,
            )
            created = False
            try:
                if plan.host_access:
                    build_sandbox.set_compose_exception(
                        plan.domain,
                        allowed=True,
                        actor=plan.accepted_by or trigger,
                        reason=_exception_reason(plan),
                        store=store,
                    )
                store.set_app_compose_project(plan.domain, plan.project)
                store.set_app_site_name(plan.domain, plan.site_name)
                # What the stack runs with is what its unit's start is held to from here on: the
                # plan only said it; this writes it down, once the application exists.
                deployer._check_host_privileges(
                    deployer._load_compose_document(),
                    accepted=list(plan.host_access),
                    final=True,
                    record="adopt",
                )
                if plan.kept_by == KEPT_BY_UNIT:
                    log.step(2, 2, "Creating its unit, enabled and not started")
                else:
                    log.step(2, 2, "Recording its file and project: Docker keeps it running")
                deployer._create_systemd_service(project=plan.project)
                remember_stack(deployer)
                created = plan.kept_by == KEPT_BY_UNIT
                services.enable(plan.app_name)
                _link_unit(store, plan.app_name, app.id)
            except (NoustError, sqlite3.Error):
                _undo(plan, store=store, services=services if created else None, log=log)
                raise
            log.success(
                f"Adopted {plan.domain}: {plan.app_path}, project "
                f"{plan.project or '(named by the stack)'}, "
                f"site {plan.site or '(none)'}, commit {plan.commit or 'unknown'}"
            )
            for warning in plan.warnings:
                log.warning(warning)

    audit.record(
        "apps.adopt",
        target=f"app:{plan.domain}",
        details={
            "path": str(plan.app_path),
            "compose_file": plan.compose_file,
            "project": plan.project,
            "site": plan.site,
            "commit": plan.commit,
            "accepted_changes": list(plan.changes),
            "accepted_host_access": list(plan.host_access),
        },
    )
    return AdoptionResult(
        plan=plan, adopted=True, unit=plan.unit, deployment_id=recorder.deployment_id
    )


def _exception_reason(plan: AdoptionPlan) -> str:
    """
    Args:
        plan: The adoption.

    Returns:
        The reason recorded for the compose exception adopting makes: that the stack already ran with
        these lines, which are listed, so the exception says what it covers.
    """
    return "Adopted while running with what gives this server to a container: " + "; ".join(
        plan.host_access
    )


def _link_unit(store: NoustStore, unit: str, app_id: int | None) -> None:
    """
    Tie the unit's row to the application, so deleting it finds the unit.

    Args:
        store: The store.
        unit: The unit's name.
        app_id: The application's id.
    """
    service = store.get_service(unit)
    if service is not None and app_id is not None:
        store.update_service(dataclasses.replace(service, app_id=app_id))


def _undo(
    plan: AdoptionPlan, *, store: NoustStore, services: ServiceManager | None, log: Logger
) -> None:
    """
    Take back a half-done adoption: the unit it created and the row it registered.

    The stack, its directory and its site were never touched, so they are
    not part of it.

    Args:
        plan: The adoption.
        store: The store the row is removed from.
        services: The manager the unit is removed with; None when none was created.
        log: Where what could not be undone is reported.
    """
    if plan.host_access:
        build_sandbox.set_compose_exception(
            plan.domain, allowed=False, actor=plan.accepted_by or "adoption", store=store
        )
    if services is not None:
        try:
            services.delete_service(plan.app_name)
        except ServiceError as exc:
            log.warning(f"The unit {plan.app_name} was left behind: {exc}")
    try:
        store.delete_app(plan.domain)
    except (NoustError, sqlite3.Error) as exc:
        log.warning(f"{plan.domain} is still registered: {exc}")


def adopt_stack(
    domain: str,
    path: Path,
    *,
    compose_file: str | None = None,
    source: str | None = None,
    branch: str | None = None,
    site: str | None = None,
    port: int | None = None,
    accept_recreate: bool = False,
    accept_host_access: bool = False,
    host_access_digest: str | None = None,
    accepted_by: str = "",
    trigger: str = DeploymentTrigger.CLI.value,
    job_id: str | None = None,
    runner: CommandRunner | None = None,
    store: NoustStore | None = None,
    web: NginxManager | None = None,
) -> AdoptionResult:
    """
    Plan an adoption and carry it out.

    Args:
        domain: The domain the stack serves.
        path: The directory it runs from.
        compose_file: Its compose file, relative to ``path``.
        source: Where updates fetch from.
        branch: The branch updates follow.
        site: The site file that serves the domain.
        port: The port to register.
        accept_recreate: Adopt even when the rehearsed ``up`` would change something.
        accept_host_access: Adopt although the stack runs with what gives this server to a container.
        host_access_digest: The digest of the lines that were shown; see :func:`plan_adoption`.
        accepted_by: Who accepts it, for the record.
        trigger: Who asked, for the history row.
        job_id: The background job adopting, when one is.
        runner: The runner docker, git and systemd go through.
        store: The store.
        web: The web server's manager.

    Returns:
        What was done.

    Raises:
        DeploymentError: See :func:`plan_adoption` and :func:`adopt`.
    """
    plan = plan_adoption(
        domain,
        path,
        compose_file=compose_file,
        source=source,
        branch=branch,
        site=site,
        port=port,
        accept_recreate=accept_recreate,
        accept_host_access=accept_host_access,
        host_access_digest=host_access_digest,
        accepted_by=accepted_by,
        runner=runner,
        store=store,
        web=web,
    )
    return adopt(plan, trigger=trigger, job_id=job_id, runner=runner, store=store)


__all__ = [
    "READ_ONLY_PROBES",
    "AdoptionPlan",
    "AdoptionRefusedError",
    "AdoptionResult",
    "StackContainer",
    "adopt",
    "adopt_stack",
    "changed_lines",
    "find_site",
    "find_stack_project",
    "guard_running_stack",
    "host_access_digest_of",
    "list_stack_containers",
    "parse_containers",
    "plan_adoption",
    "rehearse_up",
]
