# Copyright (c) 2024-2026 Yago Lopez Prado
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
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.exceptions import DeploymentError, NoustError, ServiceError
from noust.core.fs import is_rehearsal
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import (
    _COMPOSE_PROJECT,
    AppLayout,
    AppStatus,
    AppType,
    DeploymentTrigger,
    NoustStore,
    get_store,
)
from noust.deployers.deploy_events import operation
from noust.deployers.docker_compose import DockerComposeDeployer, compose_project_name
from noust.deployers.helpers.registration import StoreRegistrar
from noust.deployers.helpers.target import claim_deploy_target
from noust.deployers.recorder import checkout_git_info, recorder_for, recording
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ServiceManager
from noust.managers.source_manager import SourceManager
from noust.managers.webserver import config_serves_tls
from noust.validators.domain import validate_domain
from noust.validators.names import resolve_within, validate_filename

#: The labels Compose puts on every container it creates.
PROJECT_LABEL = "com.docker.compose.project"
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


class AdoptionRefusedError(DeploymentError):
    """
    Docker Compose did not show that adopting would leave the stack as it is.

    Attributes:
        output: Compose's own output for the rehearsed ``up``, verbatim.
        changes: The lines of it that would create, recreate or remove
            something.
    """

    def __init__(
        self, message: str, *, details: str, output: str, changes: tuple[str, ...] = ()
    ) -> None:
        """
        Args:
            message: What was refused.
            details: How to go on.
            output: Compose's output, verbatim.
            changes: The lines that would change something.
        """
        super().__init__(message, details=details)
        self.output = output
        self.changes = changes


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
    """

    id: str
    name: str
    state: str
    project: str
    config_files: tuple[str, ...]
    working_dir: str


@dataclass(frozen=True)
class AdoptionPlan:
    """
    What adopting a stack would record, found without changing anything.

    Attributes:
        domain: The application's domain.
        app_name: Its unit's name.
        app_path: The directory the stack runs from, as it is.
        compose_file: The compose file, relative to ``app_path``.
        project: The Compose project pinned for it; None when the stack names
            its own (``name:`` or ``COMPOSE_PROJECT_NAME``) and nothing ran.
        project_from: ``containers`` (their labels), ``compose`` (the name
            Compose derives) or ``stack`` (the stack names it itself).
        containers: The names of the containers from that file, in that project.
        running: Whether any of them is running.
        source: Where updates fetch from.
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
        return data


@dataclass(frozen=True)
class AdoptionResult:
    """
    What an adoption did.

    Attributes:
        plan: What was adopted.
        adopted: False for a rehearsal, which records nothing.
        unit: The unit created for the stack.
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


def _default_web() -> NginxManager:
    """
    Returns:
        The nginx manager sites are looked for with; a seam for tests.
    """
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
        if len(parts) != 6 or not parts[0].strip() or not parts[3].strip():
            continue
        container_id, name, state, project, files, working_dir = (p.strip() for p in parts)
        found.append(
            StackContainer(
                id=container_id,
                name=name,
                state=state.lower(),
                project=project,
                config_files=tuple(f.strip() for f in files.split(",") if f.strip()),
                working_dir=working_dir,
            )
        )
    return found


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
    return False


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
        timeout=DOCKER_TIMEOUT,
    )
    if not result.success:
        raise DeploymentError(
            "Docker could not be asked which containers run",
            details=(result.stderr or result.stdout).strip()
            or "Check the daemon: systemctl status docker",
        )
    made = [c for c in parse_containers(result.stdout) if _made_from(c, compose_path)]
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
    domain: str, web: NginxManager, *, requested: str | None = None
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


def _checked_path(path: Path) -> Path:
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
            "/opt/proggest.",
        )
    if not path.exists():
        raise DeploymentError(
            f"{path} does not exist", details="Name the directory the stack runs from."
        )
    if path.is_symlink() or not path.is_dir():
        raise DeploymentError(
            f"{path} is not a directory",
            details="Name the real directory the stack runs from, not a link to it.",
        )
    return path


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
        source: Where updates fetch from; the checkout's ``origin`` when None.
        branch: The branch updates follow; the one checked out when None.
        site: The site file that serves the domain; found when None.
        port: The port to register; the compose file's when None.
        accept_recreate: Adopt even when the rehearsed ``up`` would change
            something, which is then a warning.
        runner: The runner docker and git go through.
        store: The store checked for the domain.
        web: The web server's manager sites are looked for with.

    Returns:
        The plan.

    Raises:
        DeploymentError: The domain is deployed already, the directory or the
            compose file is not usable, it is not a git checkout with a
            source, its unit's name is taken, or the site is ambiguous.
        AdoptionRefusedError: The rehearsed ``up`` failed or would change
            something, and that was not accepted.
    """
    domain = validate_domain(domain)
    runner = runner if runner is not None else get_runner()
    store = store if store is not None else get_store()
    path = _checked_path(path)
    if store.get_app(domain) is not None:
        raise DeploymentError(
            f"{domain} is already deployed",
            details=f"Adopting registers a stack Noust has no record of. See it with: "
            f"noust status {domain}",
        )
    warnings: list[str] = []

    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(
        domain, source or str(path), app_path=path, compose_file=compose_file, port=port
    )
    # A stack that runs is warned about what it asks of the host, never refused.
    deployer._is_new_deployment = False
    deployer._discover_compose_file()
    deployer._parse_compose_services()
    compose_path = deployer._compose_file_path()
    claim_deploy_target(path, domain=domain, existing=None, replace=False, adopt=True)

    unit = ServiceManager(runner=runner).inspect_unit(deployer.app_name, serving=False)
    if unit.exists:
        raise DeploymentError(
            f"A unit called {unit.unit} already exists",
            details=f"Adopting creates {unit.unit} for the stack. Remove the one at "
            f"{unit.path} if it is left over, or adopt under the domain it belongs to.",
        )

    repo = SourceManager(runner=runner).get_repo_info(path)
    if not repo["is_git"]:
        raise DeploymentError(
            f"{path} is not a git checkout",
            details="An update brings an adopted stack up to date by resetting its checkout "
            "to the branch, keeping what git does not track. Clone the repository there "
            "(or git init and add its remote), or deploy it with noust create.",
        )
    fetch_from = source or repo["remote"]
    if not fetch_from:
        raise DeploymentError(
            f"The checkout in {path} has no remote 'origin'",
            details="Name where updates fetch from with --source (source in the API).",
        )
    if repo["dirty"]:
        warnings.append(
            "The checkout has changes to tracked files; the next update resets them to the "
            "branch (files git does not track, such as .env and data, are kept)."
        )

    project, containers = find_stack_project(compose_path, runner=runner)
    project_from: ProjectFrom = "containers"
    if project is None:
        project = compose_project_name(path, compose_path)
        project_from = "compose" if project is not None else "stack"
        warnings.append(
            f"Nothing made from {deployer.compose_file or compose_path.name} has run; the "
            "project is the name Compose gives it, and the rehearsal below creates everything."
        )

    dry_run, changes = _rehearse(deployer, project, accept_recreate=accept_recreate)
    if changes:
        warnings.append("Accepted: the next start or update changes " + "; ".join(changes) + ".")
    for line in _starting_lines(dry_run):
        warnings.append(f"Stopped now, started by the next start or update: {line}.")

    headless = deployer._is_headless()
    site_file: str | None = None
    site_text = ""
    if not headless:
        site_file, site_text, enabled = find_site(domain, web or _default_web(), requested=site)
        if site_file is None:
            warnings.append(
                f"No site in sites-available answers on {domain}; nothing serves it through "
                "the web server until one does."
            )
        elif not enabled:
            warnings.append(f"The site {site_file} is not enabled.")

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
        port=None if headless else deployer._get_primary_port(),
        headless=headless,
        dry_run=dry_run,
        changes=changes,
        warnings=tuple(warnings),
    )


def _rehearse(
    deployer: DockerComposeDeployer, project: str | None, *, accept_recreate: bool
) -> tuple[str, tuple[str, ...]]:
    """
    Ask Compose what ``up`` would do to the stack, and refuse what changes it.

    Args:
        deployer: The deployer, with the compose file discovered.
        project: The project to address.
        accept_recreate: Return the changes instead of refusing them.

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
            details="Adopting needs 'docker compose up --dry-run' (Docker Compose 2.20 or "
            "later) to prove nothing would be recreated. Fix what the output says, or adopt "
            "with --accept-recreate (accept_recreate in the API) after reading it.",
            output=output,
        )
    changes = changed_lines(output)
    if changes and not accept_recreate:
        raise AdoptionRefusedError(
            f"Starting {deployer.domain}'s stack as Noust would changes what runs",
            details="Compose would create, recreate or remove: "
            + "; ".join(changes)
            + ". The running containers differ from the compose file (another project, "
            "file, profile or environment). Bring the stack up the way it is meant to run, "
            "or adopt with --accept-recreate (accept_recreate in the API) to accept it.",
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
    require_server_role("Applications")
    if is_rehearsal():
        return AdoptionResult(plan=plan, adopted=False, unit=plan.app_name, deployment_id=None)
    runner = runner if runner is not None else get_runner()
    store = store if store is not None else get_store()

    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(
        plan.domain,
        plan.source,
        app_path=plan.app_path,
        compose_file=plan.compose_file,
        branch=plan.branch,
        trigger=trigger,
        job_id=job_id,
    )
    deployer._discover_compose_file()
    log = deployer.logger
    services = ServiceManager(verbose=deployer.verbose, runner=runner)
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
                webserver="" if plan.headless else "nginx",
                ssl_enabled=plan.ssl,
                status=(AppStatus.RUNNING if plan.running else AppStatus.STOPPED).value,
                is_static=False,
                env_vars={},
                layout=AppLayout.INPLACE.value,
            )
            created = False
            try:
                store.set_app_compose_project(plan.domain, plan.project)
                store.set_app_site_name(plan.domain, plan.site_name)
                log.step(2, 2, "Creating its unit, enabled and not started")
                deployer._create_systemd_service(project=plan.project)
                created = True
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
        },
    )
    return AdoptionResult(
        plan=plan, adopted=True, unit=plan.app_name, deployment_id=recorder.deployment_id
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
    "parse_containers",
    "plan_adoption",
]
