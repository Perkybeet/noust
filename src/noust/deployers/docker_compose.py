# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Docker Compose deployer for Noust.

Handles deployment of applications defined by Docker Compose files,
including multi-container setups with path-based Nginx routing,
environment variable management, and systemd integration.

A stack cannot build releases, so an update rebuilds it in place. What makes
that safe is that the update records what serves before it builds - the
commit of the tree and the image every running container was created from -
and puts exactly that back when the new containers do not pass the health
gate: the tree is checked out at that commit, each image gets the name the
compose file uses for it again, and ``docker compose up -d --no-build``
recreates the containers from them. Nothing on the way touches a volume: no
``down``, no ``-v``, no ``--renew-anon-volumes``; recreating a container keeps
its named volumes by name and carries its anonymous ones over.
"""

import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import yaml

from noust.central import require_server_role
from noust.core import app_compose_store, audit, compose_release_store
from noust.core.applock import app_lock
from noust.core.compose_yaml import load_compose
from noust.core.config import Config
from noust.core.exceptions import (
    DatabaseQueryError,
    DeploymentError,
    DockerError,
    NoustError,
    PortConflictError,
    RolledBackError,
    SecurityError,
    ValidationError,
)
from noust.core.fs import DryRunFileSystem, FileSystem
from noust.core.logger import Logger
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.core.store import (
    App,
    AppStatus,
    AppType,
    DeploymentTrigger,
    ReleaseRecord,
    ReleaseStatus,
    get_store,
)
from noust.core.utils import domain_to_app_name
from noust.deployers.helpers import compose_preview, compose_retention, compose_variables
from noust.deployers.helpers.build_config import refuse_unsupported, resolve_build
from noust.deployers.helpers.compose_ports import (
    PortMapping,
    host_port_variables,
    is_headless_stack,
    parse_ports,
    published_port,
    web_root_service,
)
from noust.deployers.helpers.compose_retention import ImageRelease
from noust.deployers.helpers.health import wait_until_healthy
from noust.deployers.helpers.health_gate import HealthCheck, HealthGate
from noust.deployers.helpers.hooks import (
    DeploymentHooks,
    Hook,
    HookFailedError,
    HookSet,
)
from noust.deployers.helpers.registration import StoreRegistrar
from noust.deployers.helpers.site import (
    WriteOutcome,
    refresh_app_site,
    remove_app_site,
    write_template_site,
)
from noust.deployers.helpers.site import has_certificate as certificate_on_disk
from noust.deployers.helpers.site import obtain_certificate as obtain_app_certificate
from noust.deployers.helpers.site import reduce_certificate as reduce_app_certificate
from noust.deployers.helpers.target import (
    DeployTarget,
    claim_deploy_target,
    fetch_into_target,
)
from noust.deployers.interface import AppDeployer, StepReporter, UpdateResult
from noust.deployers.recorder import (
    CapturingLogger,
    DeploymentRecorder,
    GitInfo,
    checkout_git_info,
    recorder_for,
    recording,
)
from noust.deployers.registry import DeployerRegistry
from noust.deployers.releases import ReleaseManager, persistent_path, release_order_key
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
from noust.managers.port_claims import PortClaims
from noust.managers.service_manager import ServiceManager
from noust.managers.source_manager import SourceManager
from noust.validators.names import resolve_within

if TYPE_CHECKING:
    from noust.deployers.compose_relay import ComposeRelay

#: Building images pulls layers and compiles; give it room but not forever.
BUILD_TIMEOUT = 1800

#: Bringing a stack up or down, and every query about it.
COMPOSE_TIMEOUT = 300

#: The tag the image each container ran before an update is kept under, as
#: ``<project>-<service>:wasm-previous``. Recreating a container from a new
#: build leaves its old image without a name, and ``docker image prune``
#: deletes an image without a name; this one is what a failed update goes
#: back to. Each update moves the tag, so one image per service is kept.
PREVIOUS_TAG = "wasm-previous"

#: Lines of the containers' own output attached to a failed health gate.
COMPOSE_LOG_LINES = 40

#: How many times, and how far apart, the containers' state is read after
#: they are recreated. A container that crashes on start is running for a
#: moment first, so one reading is not enough.
CONTAINER_CHECK_ATTEMPTS = 10
CONTAINER_CHECK_DELAY = 3.0

#: What ``docker inspect`` is asked about each running container: its image,
#: the reference it was created from, and the service and project Compose
#: labelled it with. Go templates are the one output format every Docker
#: version shares.
_INSPECT_FORMAT = (
    "{{.Image}}|{{.Config.Image}}"
    '|{{index .Config.Labels "com.docker.compose.service"}}'
    '|{{index .Config.Labels "com.docker.compose.project"}}'
)

#: A container id as ``docker compose ps -q`` prints it.
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")

#: A restart policy that keeps bringing a container back hides a crash from any one look at it:
#: between two runs the container is ``running`` for as long as the program lives. Restarts
#: counted by Docker, with a current run younger than ``CRASH_LOOP_RUN_SECONDS``, are the loop.
#: Ten seconds is Docker's own: a container that has run that long starts its back-off over.
CRASH_LOOP_RESTARTS = 3
CRASH_LOOP_RUN_SECONDS = 10

#: What ``docker inspect`` is asked about a running container to tell a loop from a steady one:
#: its name, how many times Docker restarted it, and when the current run began.
_RESTART_FORMAT = "{{.Name}}|{{.RestartCount}}|{{.State.StartedAt}}"

# Compose file priority order
COMPOSE_FILE_PRIORITY = [
    "docker-compose.prod.yml",
    "docker-compose.prod.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
]

#: What Compose itself strips from a directory name before using it as a
#: project name: everything outside [a-z0-9_-], then any leading run of
#: characters that are not a letter or digit.
_PROJECT_NAME_INVALID_CHARS = re.compile(r"[^a-z0-9_-]+")
_PROJECT_NAME_LEADING_JUNK = re.compile(r"^[^a-z0-9]+")

#: ``COMPOSE_PROJECT_NAME`` set in the project's ``.env``, which Compose reads.
_ENV_PROJECT_NAME = re.compile(r"^\s*(?:export\s+)?COMPOSE_PROJECT_NAME\s*=", re.MULTILINE)


def _names_its_own_project(compose_path: Path) -> bool:
    """
    Tell whether a stack chooses its project name itself.

    Args:
        compose_path: The compose file in use.

    Returns:
        True when the file has a top-level ``name:`` or the project's ``.env``
        sets ``COMPOSE_PROJECT_NAME``; also when the file exists but cannot
        be read, so that nothing is pinned over a name that could not be
        checked. A file that is not there names nothing.
    """
    try:
        document = load_compose(compose_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return True
    if isinstance(document, dict) and document.get("name"):
        return True
    env_file = compose_path.parent / ".env"
    try:
        env_text = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return True
    return _ENV_PROJECT_NAME.search(env_text) is not None


def _env_project_name(compose_path: Path) -> str | None:
    """
    Read the project name a stack's ``.env`` sets, if it sets one.

    Args:
        compose_path: The compose file in use; the ``.env`` beside it is read.

    Returns:
        The value of ``COMPOSE_PROJECT_NAME``, or None.
    """
    env_file = compose_path.parent / ".env"
    try:
        text = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        if _ENV_PROJECT_NAME.match(line):
            value = line.split("=", 1)[1].strip().strip("'\"")
            return value or None
    return None


def compose_project_name(app_path: Path, compose_path: Path | None) -> str | None:
    """
    Reproduce the Compose project name this stack has always had.

    Every ``docker compose`` invocation in this module runs with ``app_path``
    as its working directory and an absolute ``-f`` built from it; the
    systemd unit does the same with ``COMPOSE_FILE`` and ``WorkingDirectory``.
    With no ``-p``, no ``COMPOSE_PROJECT_NAME`` and no top-level ``name:`` in
    the compose file, Compose names the project - and therefore every
    container, network and named volume (``<project>_<volume>``) - after the
    directory of that first file: lower-cased, restricted to
    ``[a-z0-9_-]``, and trimmed to start on a letter or digit. That directory
    is ``app_path`` itself unless ``compose_file`` names a subdirectory, in
    which case it is that subdirectory.

    Passing this value back as ``-p`` does not change what Compose would
    already have inferred for either layout; it stops that inference from
    depending on the working directory, the exact ``-f`` path built, or
    Compose's own algorithm never changing - any of which silently renames
    the project, and the day one does, ``docker compose up`` creates new,
    empty volumes instead of reusing the ones with the data. It deliberately
    keeps naming a subdirectory-based project after that subdirectory rather
    than after ``app_path``: repointing it would be exactly the rename this
    exists to prevent, for every application already deployed that way.

    A stack that names its project itself - a top-level ``name:`` in the
    compose file, or ``COMPOSE_PROJECT_NAME`` in the project's ``.env`` - is
    not pinned at all: ``-p`` overrides both, and the name 1.x and the unit
    (which never passes ``-p``) have always used is the stack's own.

    Args:
        app_path: The application directory the source was fetched to.
        compose_path: The compose file in use, or ``None`` before discovery,
            in which case ``app_path`` itself is normalised.

    Returns:
        The Compose project name for this stack, or None when the stack
        names its own project and no ``-p`` must be passed.
    """
    if compose_path is not None and _names_its_own_project(compose_path):
        return None
    source_dir = compose_path.parent if compose_path is not None else app_path
    name = _PROJECT_NAME_INVALID_CHARS.sub("", source_dir.name.lower())
    name = _PROJECT_NAME_LEADING_JUNK.sub("", name)
    return name or "default"


def compose_file_option(raw: str) -> PurePosixPath:
    """
    Validate the ``compose_file`` a deployment names, before anything is fetched.

    The same rule as a persistent path, applied by the same function: the file
    is handed to ``docker compose -f``, which runs as root, so a name that is
    absolute or climbs with ``..`` would read a file from anywhere on the host.
    Whether it exists, and whether a symlink on the way leads out, can only be
    known once the source is on disk; :func:`compose_file_in` checks that.

    Args:
        raw: The path as the operator gave it, relative to the project root.

    Returns:
        The normalized relative path.

    Raises:
        DeploymentError: When the path is empty, absolute, contains ``..`` or
            a NUL byte.
    """
    try:
        return persistent_path(raw)
    except DeploymentError as exc:
        raise DeploymentError(
            f"Compose file {raw!r} is not a path inside the application",
            details="Name the compose file relative to the project root, such as "
            "'docker-compose.prod.yml' or 'docker/compose.yml'. Absolute paths and "
            "'..' are refused because the file is read as root.",
        ) from exc


_UNIT_COMPOSE_FILE = re.compile(
    r'^Environment="COMPOSE_FILE=((?:[^"\\]|\\.)*)"[ \t]*$', re.MULTILINE
)


_UNIT_COMPOSE_PROFILES = re.compile(
    r'^Environment="COMPOSE_PROFILES=((?:[^"\\]|\\.)*)"[ \t]*(?:\r?\n|\Z)', re.MULTILINE
)


def compose_file_from_unit(unit_text: str | None) -> str | None:
    """
    Read back the compose file a Compose application's unit names.

    A deploy records a compose file other than the default names only in the
    unit, as ``Environment="COMPOSE_FILE=..."``. An update reads it from there,
    because rediscovering would find only the root-level default names and
    rebuild a different stack, or none.

    Args:
        unit_text: The unit file's content, or None when there is no unit.

    Returns:
        The compose file relative to the application, as the deploy chose it,
        or None when the unit names none.
    """
    if not unit_text:
        return None
    match = _UNIT_COMPOSE_FILE.search(unit_text)
    if match is None:
        return None
    # Undo the template's env_value escaping: specifiers doubled, backslashes and quotes escaped.
    value = re.sub(r"\\(.)", r"\1", match.group(1).replace("%%", "%"))
    return value or None


def compose_file_in(app_path: Path, relative: PurePosixPath | str) -> Path:
    """
    Locate a compose file in a fetched project, refusing any way out of it.

    Args:
        app_path: The application directory the source was fetched to.
        relative: The compose file, relative to ``app_path``.

    Returns:
        ``app_path / relative``, unresolved, so it can still be expressed
        relative to ``app_path`` for the systemd unit.

    Raises:
        DeploymentError: When a symlink in the project resolves the path
            outside ``app_path``, or when it is not an existing file.
    """
    path = app_path / relative
    try:
        resolve_within(app_path, str(relative))
    except (SecurityError, ValidationError) as exc:
        raise DeploymentError(
            f"Compose file {str(relative)!r} resolves outside the application",
            details=f"A symlink in the repository leads out of {app_path}. Commit the "
            "compose file itself instead of a link to it; the file is read as root, so "
            "only files inside the project are accepted.",
        ) from exc
    if not path.is_file():
        raise DeploymentError(
            f"Specified compose file not found: {relative}",
            details=f"Looked for {path}. Check the path is relative to the project root "
            "and that the file is committed on the branch being deployed.",
        )
    return path


@dataclass(frozen=True)
class ServingImage:
    """
    The image one service of a stack was running before an update.

    Attributes:
        service: The Compose service.
        reference: The image name the container was created from, which is
            the name the compose file makes Compose look up.
        image_id: The image's immutable id.
    """

    service: str
    reference: str
    image_id: str


@dataclass(frozen=True)
class ServingState:
    """
    What a stack was serving before an update: what a failed update puts back.

    Attributes:
        project: The Compose project its containers belonged to, or None
            when none was running.
        commit: The commit the tree was on, or None when it is not a git
            checkout or the commit could not be read.
        images: The image of each service that had a running container.
    """

    project: str | None
    commit: str | None
    images: tuple[ServingImage, ...]


@dataclass(frozen=True)
class RunningImages:
    """
    What a stack runs now, as Docker reports it.

    Attributes:
        project: The Compose project the containers belong to, when any run.
        images: The image of each service with a running container.
        running: Whether any container runs at all.
    """

    project: str | None
    images: dict[str, ServingImage]
    running: bool


def parse_compose_ps(stdout: str) -> list[dict[str, Any]]:
    """
    Read what ``docker compose ps --format json`` printed, in any Compose v2 format.

    Compose before 2.21 prints one JSON array; 2.21 and later print one object
    per line. Both come from the same flag, so both must be read.

    Args:
        stdout: The command's output.

    Returns:
        One mapping per container; lines that are not JSON objects are skipped.
    """
    text = stdout.strip()
    if not text:
        return []
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, list):
        return [entry for entry in whole if isinstance(entry, dict)]
    if isinstance(whole, dict):
        return [whole]
    containers: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            containers.append(entry)
    return containers


def container_problem(container: dict[str, Any]) -> str | None:
    """
    Say what is wrong with a container, if anything.

    A one-shot container (a migration, a seed) that ran and exited 0 did its
    job; one that exited with an error, one Docker keeps restarting and one
    whose own health check says unhealthy did not.

    Args:
        container: One entry of :func:`parse_compose_ps`.

    Returns:
        A line naming the container and its state, or None when it is fine.
    """
    name = container.get("Name") or container.get("Service") or "a container"
    state = str(container.get("State") or "").lower()
    health = str(container.get("Health") or "").lower()
    exit_code = container.get("ExitCode")
    if state in ("restarting", "dead"):
        suffix = f" (last exit code {exit_code})" if exit_code not in (None, 0, "0") else ""
        return f"{name}: {state}{suffix}"
    if state == "exited" and exit_code not in (None, 0, "0"):
        return f"{name}: exited with code {exit_code}"
    if health == "unhealthy":
        return f"{name}: unhealthy"
    return None


def keep_tag(project: str, service: str) -> str:
    """
    Name the tag an image that served is kept under while an update runs.

    Args:
        project: The Compose project.
        service: The service.

    Returns:
        ``<project>-<service>:wasm-previous``, restricted to what an image
        name may hold.
    """
    return f"{compose_retention.repository_name(project, service)}:{PREVIOUS_TAG}"


@dataclass
class DockerComposeService:
    """Represents a service from a Docker Compose file."""

    name: str = ""
    image: str | None = None
    build: str | None = None
    #: Each entry as the file gives it: a string, or a long-form mapping.
    ports: list[Any] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    healthcheck: dict | None = None
    is_web: bool = False


def parse_services(document: Any) -> list[DockerComposeService]:
    """
    Read the services of a parsed compose file.

    The one reading of a compose file's services: the deploy, the site and
    the headless check all start from it.

    Args:
        document: The parsed compose file.

    Returns:
        One record per service, in file order; empty when the document has
        no ``services`` mapping.
    """
    raw = document.get("services") if isinstance(document, dict) else None
    if not isinstance(raw, dict):
        return []
    services: list[DockerComposeService] = []
    for name, data in raw.items():
        data = data if isinstance(data, dict) else {}
        ports = data.get("ports") or []
        ports = [str(p) if not isinstance(p, dict) else p for p in ports]
        depends = data.get("depends_on") or []
        depends = list(depends.keys()) if isinstance(depends, dict) else list(depends)
        env_list = data.get("environment") or []
        env: dict[str, str] = {}
        if isinstance(env_list, list):
            for item in env_list:
                key, sep, val = str(item).partition("=")
                if sep:
                    env[key] = val
        elif isinstance(env_list, dict):
            env = {str(k): str(v) if v is not None else "" for k, v in env_list.items()}
        service = DockerComposeService(
            name=str(name),
            image=data.get("image"),
            build=str(data["build"]) if data.get("build") else None,
            ports=ports,
            volumes=[str(v) for v in data.get("volumes") or []],
            depends_on=[str(d) for d in depends],
            environment=env,
            healthcheck=data.get("healthcheck"),
        )
        service.is_web = not is_headless_stack([service])
        services.append(service)
    return services


class DockerComposeDeployer(AppDeployer):
    """
    Deployer for Docker Compose applications.

    Standalone deployer (does not inherit BaseDeployer) that manages
    the full lifecycle of Docker Compose applications including
    deployment, updates, and removal.
    """

    APP_TYPE = "docker-compose"
    DISPLAY_NAME = "Docker Compose"

    # A compose file states how the author wants the whole thing run, which
    # beats guessing from a package.json. See interface.py for the full order.
    DETECTION_PRIORITY = 80
    DEFAULT_PORT = 3000

    DETECTION_FILES: ClassVar[list[str]] = [
        "docker-compose.prod.yml",
        "docker-compose.prod.yaml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    ]

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for every docker invocation. Defaults to
                the process-wide runner, which is what enforces --dry-run.
            fs: Filesystem every change goes through. Defaults to the
                process-wide one, for the same reason.
        """
        self.verbose = verbose
        self._runner = runner
        self._fs = fs
        # Capturable, so the deployment history holds the whole build log.
        self.logger = CapturingLogger(verbose=verbose)
        self.config = Config()
        self.store = get_store()
        self.trigger: str = DeploymentTrigger.CLI.value
        self._is_new_deployment = True
        self.replace_existing = False
        #: Whether this one build ignores the image layer cache.
        self.no_cache = False
        #: Extra arguments of ``docker compose build`` and ``up``, and the services
        #: they address (noust app compose); empty is plain Compose.
        self.compose_build_args: tuple[str, ...] = ()
        self.compose_up_args: tuple[str, ...] = ()
        self.compose_services: tuple[str, ...] = ()
        #: The registry accounts this deployment logs in (noust create --registry),
        #: before the application exists to have any attached; None uses the attached.
        self.registry_logins: list[str] | None = None
        #: The environment docker commands run with while a registry session is open.
        self._registry_env: dict[str, str] = {}

        # Deployment state
        self.domain = ""
        self.app_name = ""
        self.app_path = Path()
        self.source = ""
        self.branch: str | None = None
        self.webserver = "nginx"
        self.ssl = True
        self.env_vars: dict[str, str] = {}
        self.compose_file: str | None = None
        self.compose_profiles: list[str] = []
        self.port: int | None = None
        # The application this stack is a preview of: it is then run from a copy of
        # its compose file that cannot collide with the original's.
        self.preview_parent: str | None = None
        self._env_secret_marks: dict[str, bool] = {}
        # The commit the tree was on before the update pulled, which only the
        # caller that pulled can know: what a failed update checks out again.
        self.previous_commit: str | None = None
        # What the guard refuses that the stack already ran with, proven by the
        # compose file that served (a redeploy, an adoption): None until proven,
        # and then nothing is accepted.
        self._accepted_refusals: list[str] | None = None
        # The commit and branch of the attempt, kept when a failed update
        # puts the previous commit back, so its history row names what failed.
        self._attempted_git: tuple[str | None, str | None] | None = None
        # Whether the update got as far as recording what serves: a refusal
        # before that has the pulled tree to put back, and nothing else.
        self._serving_recorded = False

        # The release whose images now serve, once they are kept (B-16): what
        # the deployment's history row names, so it can be gone back to.
        self.release_id: str | None = None
        # Whether the containers are recreated from images already on the host
        # (going back to a release): nothing is built then.
        self._reuse_images = False

        # Parsed state
        self.services: list[DockerComposeService] = []
        self.compose_path: Path | None = None
        # The project the store pins (an adopted stack's), read once: None
        # until read, then the value or "" for none.
        self._stored_project: str | None = None
        self._cert_manager: CertManager | None = None
        # What this deployment's hooks ran, for its history row.
        self._recording: DeploymentRecorder | None = None
        self._hooks = DeploymentHooks()

    #: Whether each release that passes the gate keeps the images it ran, so going
    #: back to it is instant. A container application keeps its own.
    RETAINS_RELEASES: ClassVar[bool] = True

    # Framework config files that indicate docker-compose.yml is likely
    # just for local development (databases, caches, etc.)
    FRAMEWORK_CONFIG_FILES: ClassVar[list[str]] = [
        # Next.js
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
        # Vite
        "vite.config.js",
        "vite.config.ts",
        "vite.config.mjs",
        # Angular
        "angular.json",
        # Nuxt
        "nuxt.config.js",
        "nuxt.config.ts",
        # Svelte
        "svelte.config.js",
        # Astro
        "astro.config.mjs",
        "astro.config.ts",
        # Remix
        "remix.config.js",
        "remix.config.ts",
        # Django
        "manage.py",
    ]

    def detect(self, path: Path) -> bool:
        """
        Detect if a path contains a Docker Compose project.

        Detection priority:
        - If docker-compose.prod.yml exists -> True (strong signal)
        - If only docker-compose.yml exists AND monorepo signals -> False
        - If only docker-compose.yml exists AND framework config files -> False
        - If only docker-compose.yml exists without other signals -> True

        Args:
            path: Path to check.

        Returns:
            True if Docker Compose project detected.
        """
        has_prod_compose = any(
            (path / f).exists() for f in ["docker-compose.prod.yml", "docker-compose.prod.yaml"]
        )

        if has_prod_compose:
            return True

        has_compose = any((path / f).exists() for f in COMPOSE_FILE_PRIORITY)

        if not has_compose:
            return False

        # Check for monorepo signals
        has_turbo = (path / "turbo.json").exists()
        if has_turbo:
            apps_dir = path / "apps"
            if apps_dir.is_dir():
                app_count = sum(1 for d in apps_dir.iterdir() if d.is_dir())
                if app_count >= 2:
                    return False

        # Check for framework config files - if present, docker-compose.yml
        # is likely just for local development (databases, caches, etc.)
        has_framework = any((path / f).exists() for f in self.FRAMEWORK_CONFIG_FILES)
        if has_framework:
            return False

        return True

    @property
    def runner(self) -> CommandRunner:
        """The command runner this deployer executes through."""
        return self._runner if self._runner is not None else get_runner()

    def configure(
        self,
        domain: str,
        source: str,
        *,
        port: int | None = None,
        webserver: str = "nginx",
        ssl: bool = True,
        branch: str | None = None,
        env_vars: dict[str, str] | None = None,
        app_path: Path | None = None,
        package_manager: str = "auto",
        include_www: bool = False,
        trigger: str = DeploymentTrigger.CLI.value,
        **options: Any,
    ) -> None:
        """
        Configure the deployer.

        Args:
            domain: Target domain name.
            source: Git URL or local path.
            port: Override port for the Nginx proxy.
            webserver: Web server to use.
            ssl: Whether to enable SSL.
            branch: Git branch.
            env_vars: Environment variables.
            app_path: Override the directory the application lives in.
            package_manager: Ignored; images are built by docker.
            include_www: Ignored; compose stacks are proxied on one hostname.
            trigger: What initiated this deployment, recorded in the history.
            **options: ``compose_file`` selects a specific compose file,
                ``compose_profiles`` activates Docker Compose profiles,
                ``job_id`` is the background job driving this deployment, when
                there is one, ``preview_parent`` names the application this
                stack is a preview of (see
                :mod:`noust.deployers.helpers.compose_preview`),
                ``env_secret_marks`` are the secret marks a new row starts
                with, ``database_env`` the variables of a database made for the
                stack, and ``memory_max_mb``, ``cpu_quota_percent`` and
                ``tasks_max`` are refused rather than accepted (see raises).

        Raises:
            DeploymentError: When ``webserver`` is ``apache``, which every
                site-creation path here builds through nginx regardless of
                what is asked for; or when a memory, CPU or task limit is
                given, since this stack's containers are not in the systemd
                unit's cgroup for it to limit - the unit only runs
                ``docker compose up -d`` once and exits.
        """
        if webserver == "apache":
            raise DeploymentError(
                "Docker Compose applications can only be served through nginx",
                details="Every site this deployer creates - the simple proxy and the "
                "multi-service advanced config - is built with nginx; there is no Apache "
                "equivalent yet. Deploy with --webserver nginx (the default), or configure "
                "Apache by hand outside Noust.",
            )
        given_limits = {
            key: options.get(key)
            for key in ("memory_max_mb", "cpu_quota_percent", "tasks_max")
            if options.get(key) is not None
        }
        if given_limits:
            raise DeploymentError(
                f"{domain} runs in Docker containers, which its systemd unit's limits do not reach",
                details="The unit only runs 'docker compose up -d' and exits; the containers "
                "are not in its cgroup. Set deploy.resources.limits for each service in the "
                "compose file instead of passing memory, CPU or task limits at creation.",
            )

        self.domain = domain
        self.source = source
        self.trigger = trigger
        self.job_id = options.get("job_id")
        self.app_name = domain_to_app_name(domain)
        self.app_path = app_path or (self.config.apps_directory / self.app_name)
        self.webserver = webserver
        self.ssl = ssl
        self.branch = branch
        # A database made for the stack is a variable of it like any other.
        self.env_vars = {**(env_vars or {}), **(options.get("database_env") or {})}
        self.preview_parent = options.get("preview_parent")
        self._env_secret_marks = dict(options.get("env_secret_marks") or {})
        compose_file = options.get("compose_file")
        # Checked here, before anything is fetched, because configure() is the
        # one call the CLI, the API's deploy job and the auto deployer share.
        self.compose_file = str(compose_file_option(str(compose_file))) if compose_file else None
        self.compose_profiles = options.get("compose_profiles") or []
        self.port = port
        # A directory that already holds files - bind-mounted data among
        # them - is only deployed into when asked for (noust create --force).
        self.replace_existing = bool(options.get("replace_existing", False))
        self.no_cache = bool(options.get("no_cache", False))
        given = options.get("registry_logins")
        self.registry_logins = [str(name) for name in given] if given else None
        self.deploy_target = None

    def profiles_from_unit(self, unit_text: str | None) -> None:
        """
        Read back the profiles a Compose application's unit activates.

        An update used to run without them: only the unit's own start had
        ``COMPOSE_PROFILES`` in its environment, so a service under a profile
        was left out of the build and the recreate.

        Args:
            unit_text: The unit file's content, or None when there is no unit.
        """
        match = _UNIT_COMPOSE_PROFILES.search(unit_text or "")
        if match is None:
            return
        value = re.sub(r"\\(.)", r"\1", match.group(1).replace("%%", "%"))
        self.compose_profiles = [name for name in value.split(",") if name]

    def _compose(self, *args: str, project: str | None = None) -> list[str]:
        """
        Build a ``docker compose`` argument vector for this stack.

        ``-p`` is the project the store records for the application (an
        adopted stack keeps the name it already ran under), or else the name
        Compose would already derive from ``app_path`` and the compose file
        (see :func:`compose_project_name`), so the containers, networks and
        named volumes this stack owns keep their name regardless of the
        working directory a future change might run this from.

        Args:
            args: Subcommand and its arguments.
            project: The project to address instead of the derived one: the
                one the containers that served belonged to, when a failed
                update puts them back.

        Returns:
            The full argument vector, including the project, file and
            profile flags.
        """
        cmd = ["docker", "compose"]
        if self.compose_path:
            if project is None:
                project = self._pinned_project() or compose_project_name(
                    self.app_path, self.compose_path
                )
            if project is not None:
                cmd.extend(["-p", project])
            cmd.extend(["-f", str(self.compose_path)])
        for profile in self.compose_profiles:
            cmd.extend(["--profile", profile])
        cmd.extend(args)
        return cmd

    def _pinned_project(self) -> str | None:
        """
        Read the Compose project the store records for this application, once.

        Returns:
            ``apps.compose_project``, or None when the application has no row
            or the row names none.
        """
        if self._stored_project is None:
            app = self.store.get_app(self.domain) if self.domain else None
            self._stored_project = (app.compose_project if app is not None else None) or ""
        return self._stored_project or None

    def _run(
        self,
        command: Sequence[str],
        timeout: int = COMPOSE_TIMEOUT,
        *,
        stream: bool = False,
    ) -> CommandResult:
        """
        Execute a command in the application directory.

        Args:
            command: Program and arguments.
            timeout: Deadline in seconds.
            stream: Report output line by line, for image builds.

        Returns:
            The command outcome.
        """
        # Set while a registry session is open (see _registry_session); docker reads
        # the account from the configuration directory it names.
        env = self._registry_env or None
        if stream:
            return self.runner.stream(
                command, on_line=self.logger.debug, cwd=self.app_path, timeout=timeout, env=env
            )
        result = self.runner.run(command, cwd=self.app_path, timeout=timeout, env=env)
        # Printed only with --verbose, captured into the deployment log always.
        self.logger.command_output(result.stdout, result.stderr)
        return result

    def _is_headless(self) -> bool:
        """
        Check if the application has no web-facing services.

        Returns:
            True if no services expose ports (headless/worker mode).
        """
        return is_headless_stack(self.services)

    # -- Deploy hooks ---------------------------------------------------------

    def _resolve_hooks(self) -> HookSet:
        """
        Name the hooks this deployment runs: the operator's, or the repository's.

        Returns:
            The hooks; none when nothing declares any.

        Raises:
            ValidationError: The document that applies is not valid.
        """
        app = self.store.get_app(self.domain) if self.domain else None
        # A stack is built by its compose file: a build block would be read and
        # never applied, which is how a start command nobody runs goes unseen.
        refuse_unsupported(resolve_build(app, self.app_path, self.store), self.APP_TYPE)
        return self._hooks.resolve(
            app, self.app_path, self.store, app_type=self.APP_TYPE, serves=True
        )

    def _run_pre_deploy(self, *, with_dependencies: bool = False) -> None:
        """
        Run the ``pre_deploy`` hooks in one-off containers of the images just built.

        Args:
            with_dependencies: Let Compose start the services a hook's service
                depends on; only for a first deploy, where nothing serves yet.

        Raises:
            HookFailedError: A hook failed; what ran is kept for the history.
        """
        executor = ComposeHookExecutor(self, with_dependencies=with_dependencies)
        self._hooks.run_pre(executor, self.logger)

    def _run_post_deploy(self) -> None:
        """
        Run the ``post_deploy`` hooks once the new containers serve.

        A failure leaves the stack deployed with warnings: undoing what
        already serves is worse than saying so.
        """
        self._hooks.run_post(ComposeHookExecutor(self, with_dependencies=False), self.logger)

    def _record_hooks(self) -> None:
        """Write what the hooks did to the history row, once, at the end of the deployment."""
        recorder = self._recording
        self._hooks.record(
            self.store,
            recorder.deployment_id if recorder is not None else None,
            self.logger,
            domain=self.domain,
        )

    def default_hook_service(self) -> str | None:
        """
        Name the service a hook without ``service:`` runs in.

        Returns:
            The first service with ``build:``, whose image this deployment
            builds; None when the stack builds nothing.
        """
        return next((svc.name for svc in self.services if svc.build), None)

    @contextmanager
    def _registry_session(self) -> Iterator[None]:
        """
        Log in the registry accounts of this stack for the length of an operation.

        The accounts are those attached to the application, or the ones named for
        a first deployment, which has none attached yet and gets them attached
        once it exists. They are logged in to a configuration directory of the
        operation's own, which is removed afterwards.

        Yields:
            Nothing; ``docker`` commands run through :meth:`_run` see the login.
        """
        from noust.managers.registry_logins import RegistryManager

        manager = RegistryManager(runner=self.runner, fs=self._fs)
        names = self.registry_logins
        if names is None:
            names = [login.name for login in manager.accounts.assigned_to(self.domain)]
        if not names:
            yield
            return
        with manager.session_for(self.domain, names) as env:
            self._registry_env = env
            try:
                yield
            finally:
                self._registry_env = {}
        if self.registry_logins and self.store.get_app(self.domain) is not None:
            manager.attach(self.domain, self.registry_logins, actor=self.trigger)

    def deploy(self) -> bool:
        """
        Deploy the stack, with the registry accounts it uses logged in.

        Returns:
            True when the stack ended up deployed.
        """
        with self._registry_session():
            return self._deploy_stack()

    def _deploy_stack(self) -> bool:
        """
        Execute the full deployment workflow.

        For web-facing apps (services with ports):
        1. Fetch source code
        2. Discover compose file
        3. Parse compose services
        4. Configure environment
        5. Build Docker images
        6. Create Nginx site
        7. Obtain SSL certificate
        8. Create systemd service
        9. Start and verify

        For headless/worker apps (no exposed ports):
        Steps 6 and 7 are skipped automatically.

        Returns:
            True when the stack ended up deployed.

        Every attempt is recorded in the deployment history with its log. A
        failed first deployment is undone; a failed redeployment leaves the
        application as it is and marks it failed, because undoing it would
        delete a stack that was serving.

        Raises:
            DeploymentError: If any deployment step fails, or the application
                directory already holds files and replacing them was not
                asked for.
            AppBusyError: Another operation is running on the application.
        """
        require_server_role("Applications")
        # Held for the whole deploy, and the directory claimed before the
        # fetch, which empties it: a stack's bind-mounted data lives there.
        with app_lock(self.domain, "deploy"):
            existing = self.store.get_app(self.domain)
            self._is_new_deployment = existing is None
            if self.deploy_target is None:
                self.deploy_target = claim_deploy_target(
                    self.app_path,
                    domain=self.domain,
                    existing=existing,
                    replace=self.replace_existing,
                )
            self._hooks = DeploymentHooks()
            with recording(self._recorder(), git_branch=self.branch) as recorder:
                self._recording = recorder
                try:
                    result = self._deploy_steps()
                finally:
                    # Whichever way it ended: a failed deployment's row says
                    # what its hooks ran, and whether a migration applied.
                    self._record_hooks()
            self.last_deployment_id = recorder.deployment_id
            return result

    def _deploy_steps(self) -> bool:
        """
        Run the deployment steps.

        Returns:
            True when the stack ended up deployed.
        """
        total_steps = 9

        try:
            if not self._is_new_deployment:
                self._remember_serving_privileges()
            self.logger.step(1, total_steps, "Fetching source code")
            self._fetch_source()

            self.logger.step(2, total_steps, "Discovering compose file")
            self._discover_compose_file()

            self.logger.step(3, total_steps, "Parsing compose services")
            self._parse_compose_services()
            # Read before anything is built, so a hook that cannot be run is
            # refused before minutes of image builds.
            self._resolve_hooks()
            self._require_ports_free()

            headless = self._is_headless()

            self.logger.step(4, total_steps, "Configuring environment")
            self._configure_environment()
            self._require_variables()

            self.logger.step(5, total_steps, "Building Docker images")
            self._build_images(no_cache=self.no_cache)
            # Nothing serves yet on a first deploy, so the hooks' services may
            # bring up what they depend on (the database a migration needs).
            self._run_pre_deploy(with_dependencies=self._is_new_deployment)

            if headless:
                self.logger.step(6, total_steps, "Skipping Nginx (headless worker)")
                self.logger.substep("No services expose ports, web server not needed")
                self.logger.step(7, total_steps, "Skipping SSL (headless worker)")
                self.logger.substep("No web-facing services, certificate not needed")
                self.ssl = False
            else:
                self.logger.step(6, total_steps, "Creating site configuration")
                self._create_site()

                self.logger.step(7, total_steps, "Obtaining SSL certificate")
                self._obtain_certificate()

            self.logger.step(8, total_steps, "Creating systemd service")
            self._create_systemd_service()

            self.logger.step(9, total_steps, "Starting and verifying")
            self._start_and_verify()

            # Register in store
            self._register_app()
            self._retain_release()
            self._run_post_deploy()

            self.logger.success(f"Docker Compose application deployed: {self.domain}")
            self.logger.blank()
            self.logger.key_value("Domain", self.domain)
            self.logger.key_value("Path", str(self.app_path))
            self.logger.key_value("Compose File", self._compose_file_path().name)
            self.logger.key_value("Services", str(len(self.services)))
            if headless:
                self.logger.key_value("Mode", "Headless worker (no web server)")
            else:
                self.logger.key_value("SSL", "Yes" if self.ssl else "No")

            return True

        except Exception as e:
            self.logger.error(f"Deployment failed: {e}")
            if self._is_new_deployment:
                self._rollback()
            else:
                self.store.update_app_status(self.domain, AppStatus.FAILED.value)
            raise

    def _compose_file_path(self) -> Path:
        """
        Return the compose file this stack is deployed from.

        Returns:
            The discovered compose file.

        Raises:
            DeploymentError: When called before discovery ran.
        """
        if self.compose_path is None:
            raise DeploymentError(
                "No compose file has been discovered yet",
                details="_discover_compose_file() must run before the stack is used.",
            )
        return self.compose_path

    def _fetch_source(self) -> None:
        """
        Fetch source code via git clone or local copy, never emptying a stack's directory.

        A stack's directory holds what git does not track - its ``.env`` and
        the data its bind mounts write - so a checkout already there is
        brought to the branch in place (``reset --hard``, no ``clean``), and
        a directory with files that is not a checkout is refused rather than
        emptied. Only an empty or missing directory is cloned into.

        Raises:
            DeploymentError: The directory holds files and is not a git checkout.
        """
        if self.source_already_fetched:
            # AutoDeployer already placed the code here; fetching again would
            # clean the directory and clone a second time.
            self.logger.substep(f"Source already present at {self.app_path}")
            return
        holds_files = self.app_path.is_dir() and any(self.app_path.iterdir())
        fetch_into_target(
            DeployTarget(path=self.app_path, existed=self.app_path.exists(), had_files=holds_files),
            SourceManager(verbose=self.verbose, fs=self._fs, domain=self.domain or None),
            self.source,
            branch=self.branch,
            domain=self.domain,
            logger=self.logger,
        )
        self.logger.substep(f"Source fetched to {self.app_path}")

    def _discover_compose_file(self) -> None:
        """
        Find the compose file to use, inside the application directory.

        A preview does not run the repository's file but a copy of it that
        cannot collide with the original stack's (see :meth:`_isolated_for_preview`).

        Raises:
            DeploymentError: When the named file is missing, when no default
                name is present, or when the file found resolves outside the
                application through a symlink, or the stack cannot run beside
                its original.
        """
        if self.preview_parent is not None:
            self.compose_path = self._isolated_for_preview(self._find_compose_file())
            return
        self.compose_path = self._find_compose_file()

    def _find_compose_file(self) -> Path:
        """
        Locate the repository's compose file.

        Returns:
            Its path inside the application directory.

        Raises:
            DeploymentError: When the named file is missing, when no default
                name is present, or when the file found resolves outside the
                application through a symlink.
        """
        if self.compose_file:
            if self.preview_parent is not None and compose_preview.is_derived(self.compose_file):
                # The unit names the copy a preview runs; the original is what it says it is.
                return self._original_of_derived(self.compose_file)
            path = compose_file_in(self.app_path, self.compose_file)
            self.logger.substep(f"Using specified: {self.compose_file}")
            return path

        for filename in COMPOSE_FILE_PRIORITY:
            if (self.app_path / filename).exists():
                # A default name is as much repository content as a named one.
                path = compose_file_in(self.app_path, filename)
                self.logger.substep(f"Found: {filename}")
                return path

        raise DeploymentError(
            "No Docker Compose file found",
            "Expected one of: " + ", ".join(COMPOSE_FILE_PRIORITY),
        )

    def _original_of_derived(self, derived: str) -> Path:
        """
        Find the repository's file a preview's copy was made from.

        Args:
            derived: The copy's path relative to the application, as the unit names it.

        Returns:
            The original's path.

        Raises:
            DeploymentError: Neither the marker in the copy nor the copy's name
                leads to a file in the repository.
        """
        copy_path = self.app_path / derived
        named: list[str] = []
        try:
            marked = compose_preview.original_of(copy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            marked = None
        if marked:
            named.append(marked)
        stem = derived[: -len(compose_preview.DERIVED_SUFFIX)]
        named.extend(f"{stem}{extension}" for extension in (".yml", ".yaml"))
        for candidate in named:
            try:
                return compose_file_in(self.app_path, compose_file_option(candidate))
            except DeploymentError:
                continue
        raise DeploymentError(
            f"The compose file {derived!r} was made from a file that is not there",
            details="A preview runs a copy of the repository's compose file. Check that the "
            "branch the preview follows still has it, then redeploy the preview.",
        )

    def _isolated_for_preview(self, original: Path) -> Path:
        """
        Write the copy of the compose file a preview runs, and return its path.

        The copy is made again at every deploy and update, from the original as
        the branch has it now, so a push that changes the compose file reaches
        the preview. It sits beside the original, because the paths in a
        compose file are relative to the file. A file by that name that is not
        ours, or a link, is the repository's and is never written over.

        Args:
            original: The repository's compose file.

        Returns:
            The path of the copy.

        Raises:
            DeploymentError: The stack cannot run beside its original, no port
                was given to publish it on, or the name of the copy is taken.
        """
        if self.port is None:
            app = self.store.get_app(self.domain)
            self.port = app.port if app is not None else None
        if self.port is None:
            raise DeploymentError(
                "A preview needs a port to publish the stack on",
                details="Previews are given one when they are created; this one has none "
                "recorded. Remove it and make it again.",
            )
        document = self._load_compose_document_at(original)
        isolated = compose_preview.isolate(
            document,
            port=self.port,
            app_path=self.app_path,
            compose_dir=original.parent,
        )
        relative = original.relative_to(self.app_path).as_posix()
        target = self.app_path / compose_preview.derived_name(relative)
        if target.is_symlink() or (
            target.exists()
            and compose_preview.original_of(target.read_text(encoding="utf-8", errors="replace"))
            is None
        ):
            raise DeploymentError(
                f"{target.name} is in the repository, so a preview cannot write its own compose file",
                details="Noust writes the compose file a preview runs beside the original, with "
                "this name. Rename or remove the file in the repository.",
            )
        self.fs.write_text(
            target, compose_preview.render(isolated, domain=self.domain, original=relative)
        )
        self.logger.substep(f"Preview of {self.preview_parent}: running {target.name}")
        for change in isolated.changes:
            self.logger.substep(f"  {change}")
        for warning in isolated.warnings:
            self.logger.warning(f"Compose: {warning}")
        return compose_file_in(self.app_path, target.relative_to(self.app_path).as_posix())

    def _load_compose_document_at(self, path: Path) -> Any:
        """
        Read a compose file that must be valid.

        Args:
            path: The file.

        Returns:
            The parsed document.

        Raises:
            DeploymentError: The file is not YAML.
        """
        try:
            return load_compose(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise DeploymentError(f"Invalid compose file: {exc}") from exc

    def _parse_compose_services(self, *, guard: bool = True) -> None:
        """
        Parse Docker Compose file and extract service definitions.

        Args:
            guard: Run the host privilege guard; an adoption runs it itself,
                once it knows whether the stack runs as the file says.
        """
        try:
            data = load_compose(self._compose_file_path().read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise DeploymentError(f"Invalid compose file: {e}") from e

        if not data or "services" not in data:
            raise DeploymentError("No services defined in compose file")
        if guard:
            self._check_host_privileges(data)

        self.services = parse_services(data)

        self.logger.substep(f"Found {len(self.services)} services")
        for svc in self.services:
            port_info = f" (ports: {', '.join(map(str, svc.ports))})" if svc.ports else ""
            self.logger.substep(f"  - {svc.name}{port_info}")

    def _load_compose_document(self) -> Any:
        """
        Read the compose file, for a look that must not fail the operation.

        Returns:
            The parsed document, or None when it cannot be read.
        """
        try:
            return load_compose(self._compose_file_path().read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            self.logger.debug(f"Compose file not inspected: {exc}")
            return None

    def _require_ports_free(self) -> None:
        """
        Refuse a stack that would publish a host port something else holds (checklist E-44).

        Docker's own error for this, found when the containers are recreated, says only that the
        address is in use. This asks the registry of who holds which port first, before anything
        is built or stopped, and names the holder. The ports the stack's own containers hold now
        are not in its way.

        Raises:
            PortConflictError: A published port is held by something else.
        """
        mappings = [
            mapping
            for service in self.services
            for mapping in parse_ports(list(service.ports))
            if mapping.host_port is not None
        ]
        if not mappings:
            return
        claims = PortClaims(self.runner, self.store)
        try:
            claims.require_stack_free(self.domain, mappings)
            return
        except PortConflictError:
            # The common case of a conflict is the stack itself: its containers listen on the
            # ports its file publishes. Only then are they listed, so a stack with nothing in the
            # way asks Docker for nothing beyond what it always did.
            own = self._ports_published_by_containers()
            if not own:
                raise
        claims.require_stack_free(self.domain, mappings, own=own)

    def _ports_published_by_containers(self) -> set[tuple[int, str]]:
        """
        Returns:
            ``(port, protocol)`` for every host port the stack's running containers publish.
            Asked whatever ``_is_new_deployment`` says: only ``deploy`` sets it, an update reaches
            the check with its default, and a stack with no containers lists none.
        """
        own: set[tuple[int, str]] = set()
        result = self._run(self._compose("ps", "--format", "json"))
        if result.success:
            for container in parse_compose_ps(result.stdout):
                for published in container.get("Publishers") or []:
                    number = published.get("PublishedPort")
                    if number:
                        own.add((int(number), str(published.get("Protocol") or "tcp")))
        return own

    def _check_host_privileges(
        self, document: Any, *, accepted: Sequence[str] | None = None
    ) -> None:
        """
        Refuse, or warn about, what in the compose file is root on this server.

        ``privileged`` and a mount of the Docker socket are refused unless an
        operator recorded an exception for the domain (see
        :mod:`noust.deployers.helpers.compose_guard`), or the stack is proven
        to run with that very finding already: the compose file that served
        had it. Only then is it warned about, so the stack is never stopped;
        whatever a push, a redeploy or an adopted directory adds is refused.

        Args:
            document: The parsed compose file.
            accepted: The findings the stack already ran with; None reads
                :attr:`_accepted_refusals`, which is empty for a new stack.

        Raises:
            DeploymentError: The file asks for root the stack did not have,
                without an exception.
        """
        from noust.deployers.helpers import sandbox as build_sandbox
        from noust.deployers.helpers.compose_guard import inspect_compose

        findings = inspect_compose(document, self.app_path)
        for warning in findings.warned:
            self.logger.warning(f"Compose: {warning}")
        if not findings.refused:
            return
        exception = build_sandbox.get_compose_exception(self.domain, store=self.store)
        if exception is not None:
            for problem in findings.refused:
                self.logger.warning(
                    f"Compose: {problem}; allowed for {self.domain} by "
                    f"{exception.allowed_by} ({exception.reason})"
                )
            return
        already = set(accepted if accepted is not None else self._accepted_refusals or ())
        added = [problem for problem in findings.refused if problem not in already]
        for problem in findings.refused:
            if problem in already:
                self.logger.warning(
                    f"Compose: {problem}. A new stack is refused this without an exception; "
                    f"record why this one needs it: noust app sandbox compose-exception "
                    f"{self.domain} --reason '...'"
                )
        if not added:
            return
        raise DeploymentError(
            f"{self.domain}'s compose file asks for root on this server",
            details="\n".join(f"- {problem}" for problem in added)
            + "\n\nA privileged container or the Docker socket is root on the host, and the "
            "compose file comes from the repository; the stack did not run with this before. "
            "Remove them, or record why this stack needs them and deploy again: noust app "
            f"sandbox compose-exception {self.domain} --reason '...'",
        )

    def _refusals_in(self, document: Any) -> list[str]:
        """
        List what the guard refuses in a compose document.

        Args:
            document: The parsed compose file, or None.

        Returns:
            The refused findings; empty for no document.
        """
        from noust.deployers.helpers.compose_guard import inspect_compose

        return inspect_compose(document, self.app_path).refused if document is not None else []

    def _remember_serving_privileges(self) -> None:
        """
        Record what the compose file on disk asks of the host, before a redeploy replaces it.

        What served is the only proof a finding is not new; when the file
        cannot be read, nothing is accepted.
        """
        try:
            self._discover_compose_file()
        except DeploymentError as exc:
            self.logger.debug(f"No compose file served before this deploy: {exc.message}")
            self._accepted_refusals = []
            return
        self._accepted_refusals = self._refusals_in(self._load_compose_document())
        self.compose_path = None

    def _require_variables(self, document: Any = None) -> None:
        """
        Refuse to build while the compose file requires a value the stack does not have.

        Compose refuses to run on ``${NAME:?message}`` with no value, minutes into a build; the
        file is read here, before anything is built or touched, and a variable with neither a
        default nor a value is said in the log. A preview has its own variables, derived from its
        parent's, and is not judged here.

        Args:
            document: The compose file already parsed; read when None.

        Raises:
            DeploymentError: A required variable has no value (see
                :func:`~noust.deployers.helpers.compose_variables.require`).
        """
        if self.preview_parent is not None:
            return
        parsed = document if document is not None else self._load_compose_document()
        if parsed is None:
            return
        compose = self._compose_file_path()
        values, env_file = compose_variables.read_values(compose, self.app_path)
        report = compose_variables.judge(parsed, values, file=compose.name, env_file=env_file)
        for variable in report.unset:
            self.logger.warning(
                f"{variable.name} is used by {compose.name} and nothing sets it: Compose will "
                "use an empty string"
            )
        compose_variables.require(report, domain=self.domain)

    def _check_update_privileges(self, document: Any) -> None:
        """
        Run the guard on an update, against the compose file of the commit that served.

        Args:
            document: The pulled compose file, parsed.

        Raises:
            DeploymentError: The pull added a refused finding.
        """
        accepted: list[str] = []
        compose = self._compose_file_path()
        if self.previous_commit and compose.is_relative_to(self.app_path):
            served = self._source_manager().file_at_commit(
                self.app_path, self.previous_commit, str(compose.relative_to(self.app_path))
            )
            if served is not None:
                try:
                    accepted = self._refusals_in(load_compose(served))
                except yaml.YAMLError as exc:
                    self.logger.debug(f"The compose file that served is not valid YAML: {exc}")
        else:
            self.logger.debug("No commit served before this update: nothing to compare with")
        self._check_host_privileges(document, accepted=accepted)

    def _configure_environment(self) -> None:
        """Configure environment variables using EnvManager."""
        from noust.deployers.helpers.env_manager import EnvManager

        manager = EnvManager(verbose=self.verbose)

        # Discover variables from .env.example files
        variables = manager.discover(self.app_path)

        if variables:
            # Get existing values
            existing = manager.get_current_values(self.app_path)
            existing.update(self.env_vars)

            # Use non-interactive mode (secrets auto-generated, defaults used)
            # What the .env or the request already give is not asked for.
            values = manager.prompt_non_interactive(variables, existing)
            values.update(existing)

            # Write .env file
            manager.write_env_files(self.app_path, values)
            self.logger.substep(f"Configured {len(values)} environment variables")
        elif self.env_vars:
            # Write provided env vars
            manager.write_env_files(self.app_path, self.env_vars)
            self.logger.substep(f"Wrote {len(self.env_vars)} environment variables")
        else:
            self.logger.substep("No environment configuration needed")
        self._own_the_port_variable(manager)

    def _own_the_port_variable(self, manager: Any) -> None:
        """
        Give ``PORT`` the port the site proxies to, when the document publishes on it.

        The site goes to the port the application was given, and a document that publishes its
        web service on ``127.0.0.1:${PORT:-80}:80`` (every template converted from Coolify's
        catalogue does) published on its default instead: 80, which nginx holds, so the stack
        failed to start, or whatever number the site did not know about, behind a 502. ``PORT``
        is the one value Noust owns in a stack's ``.env``; a recipe writes it the same way, and
        a value the operator already set is theirs.

        Args:
            manager: The environment manager that writes the file.
        """
        if not self.port or "PORT" in self.env_vars:
            return
        published = [entry for service in self.services for entry in service.ports]
        if "PORT" not in host_port_variables(published):
            return
        if manager.add_if_missing(self.app_path / ".env", "PORT", str(self.port)):
            self.logger.substep(f"PORT={self.port}: where the site proxies to")

    def _build_images(self, no_cache: bool = False) -> None:
        """
        Build Docker images defined in the compose file.

        Args:
            no_cache: Rebuild every layer from scratch.

        Raises:
            DockerError: When the build fails.
        """
        extra = tuple(
            arg for arg in self.compose_build_args if not (no_cache and arg == "--no-cache")
        )
        cmd = self._compose(
            "build", *(("--no-cache",) if no_cache else ()), *extra, *self.compose_services
        )

        # Streamed: an image build is minutes of silence otherwise.
        result = self._run(cmd, timeout=BUILD_TIMEOUT, stream=True)
        if not result.success:
            raise DockerError(
                "Failed to build Docker images",
                result.stderr,
            )
        self.logger.substep("Images built successfully")

    def _get_primary_port(self) -> int:
        """
        Determine the host port the site proxies ``/`` to.

        Returns:
            The port given at deploy time; otherwise the one the web root
            service publishes (see :func:`web_root_service`); otherwise the
            first published TCP port of any service; otherwise
            :attr:`DEFAULT_PORT`.
        """
        if self.port:
            return self.port
        root = web_root_service(self.services)
        ordered = sorted(self.services, key=lambda svc: svc.name != root)
        for svc in ordered:
            port = published_port(svc)
            if port is not None:
                return port
        return self.DEFAULT_PORT

    def _unrouted_web_services(self) -> list[str]:
        """
        List the web services the site does not route to, with their ports.

        Without a ``noust.nginx.yaml`` only the web root gets ``/``; routes
        are no longer invented for the others (``/frontend`` broke Next.js).

        Returns:
            ``name (port)`` for each other service publishing a TCP port.
        """
        root = web_root_service(self.services)
        others: list[str] = []
        for svc in self.services:
            port = published_port(svc)
            if port is not None and svc.name != root:
                others.append(f"{svc.name} ({port})")
        return others

    # -- The site and its certificate ----------------------------------------

    @property
    def cert_manager(self) -> CertManager:
        """The manager that obtains certificates."""
        if self._cert_manager is None:
            self._cert_manager = CertManager(verbose=self.verbose)
        return self._cert_manager

    @cert_manager.setter
    def cert_manager(self, manager: CertManager) -> None:
        """
        Replace the certificate manager.

        Args:
            manager: The manager to use instead.
        """
        self._cert_manager = manager

    def webserver_manager(self) -> NginxManager:
        """
        Return the manager of the web server that serves this stack.

        Returns:
            The nginx manager: a stack is only ever served through nginx.
        """
        return NginxManager(verbose=self.verbose)

    def _site_template(self, *, with_ssl: bool) -> tuple[str, dict[str, Any]]:
        """
        Choose the template of this stack's site and build its context.

        A valid ``noust.nginx.yaml`` with routes gives the site its routes,
        one without tunes the proxy site; otherwise ``/`` goes to the web
        root service (see :func:`web_root_service`).

        Args:
            with_ssl: Render the TLS server blocks.

        Returns:
            The template name and its context.
        """
        from noust.deployers.helpers.nginx_config import NginxConfigBuilder

        builder = NginxConfigBuilder(verbose=self.verbose)
        # A preview publishes its web root alone: the routes a repository names are
        # to the ports of the stack it is a preview of.
        config_path = None if self.preview_parent is not None else builder.detect(self.app_path)
        if config_path is not None:
            try:
                config = builder.parse(config_path)
            except ValueError as exc:
                self.logger.warning(f"{config_path.name} was not used: {exc}")
            else:
                errors = builder.validate(config)
                if not errors and not config.proxies_the_app:
                    from noust.deployers.compose_relay import advanced_relay_context

                    return "advanced", advanced_relay_context(
                        self,
                        builder.build_context(
                            config, self.domain, ssl=with_ssl, app_path=str(self.app_path)
                        ),
                    )
                if not errors:
                    return "proxy", {
                        **self._proxy_context(with_ssl=with_ssl),
                        **builder.proxy_context(config, self.domain, app_path=str(self.app_path)),
                    }
                # The same as an application of any other type: a file that
                # does not validate is not half-applied.
                self.logger.warning(
                    f"{config_path.name} was not used, the site proxies / to one service: "
                    + "; ".join(errors)
                )
        return "proxy", self._proxy_context(with_ssl=with_ssl)

    def _proxy_context(self, *, with_ssl: bool) -> dict[str, Any]:
        """
        Build the context of the plain proxy site: ``/`` to the web root service.

        Args:
            with_ssl: Render the TLS server blocks.

        Returns:
            The context; with the relay on, ``/`` goes through the upstream
            that includes the service's servers file.
        """
        from noust.deployers.compose_relay import proxy_relay_context

        port = self._get_primary_port()
        return {
            "domain": self.domain,
            "port": port,
            "app_path": str(self.app_path),
            "ssl": with_ssl,
            **proxy_relay_context(self, port),
        }

    def _write_site(self, *, with_ssl: bool) -> WriteOutcome:
        """
        Render the site and put it on disk, unless the operator wrote it; no reload.

        Args:
            with_ssl: Render the TLS server blocks.

        Returns:
            Whether it was written or the operator's was kept.
        """
        template, context = self._site_template(with_ssl=with_ssl)
        return write_template_site(
            self.webserver_manager(),
            self.domain,
            template=template,
            context=context,
            logger=self.logger,
        )

    def _create_site(self) -> None:
        """Create the stack's site, without TLS, and load it."""
        from noust.deployers.helpers.nginx_config import NginxConfigBuilder

        others = self._unrouted_web_services()
        if others and NginxConfigBuilder().detect(self.app_path) is None:
            root = web_root_service(self.services)
            self.logger.warning(
                f"Only {root} is routed (/); {', '.join(others)} also publish a port and are "
                "not routed. Route them with a noust.nginx.yaml at the root of the repository, "
                "for example:\n  routes:\n    - path: /api\n      port: <its port>\n"
                "    - path: /\n      port: " + str(self._get_primary_port())
            )
        self._write_site(with_ssl=False)
        self.webserver_manager().reload()
        self.logger.substep(f"Nginx configured (port {self._get_primary_port()})")

    def has_certificate(self) -> bool:
        """
        Tell whether a certificate lineage for this stack is on disk.

        Returns:
            True when certbot's live directory holds one for the domain.
        """
        return certificate_on_disk(self.cert_manager, self.domain)

    def obtain_certificate(self, *, force: bool = False) -> None:
        """
        Order a certificate covering every name the stack answers on, redirects included.

        Args:
            force: Order again although the certificate covers every name already.

        Raises:
            CertificateError: When certbot fails, with its output.
        """
        obtain_app_certificate(
            self.cert_manager,
            self.store,
            self.domain,
            webserver="nginx",
            logger=self.logger,
            force=force,
        )

    def reduce_certificate(self) -> bool:
        """
        Order the certificate again with only the names the stack answers on.

        Returns:
            True when it was ordered again.

        Raises:
            CertificateError: When certbot fails, with its output.
        """
        return reduce_app_certificate(
            self.cert_manager, self.store, self.domain, webserver="nginx", logger=self.logger
        )

    def refresh_site(self, *, with_ssl: bool) -> None:
        """
        Render the deployed stack's site again, test the whole configuration and load it.

        How a change to the stack's domains reaches nginx. Nothing is built
        and no container is touched; the compose file is the one the unit
        names, as an update reads it.

        Args:
            with_ssl: Render the TLS server blocks.

        Raises:
            ValidationError: The stack is headless and has no site, or nginx
                rejects the new configuration (the previous file is back).
            DeploymentError: nginx did not reload.
        """
        if self.compose_path is None:
            if self.compose_file is None:
                unit = ServiceManager(verbose=self.verbose).get_service_config(self.app_name)
                named = compose_file_from_unit(unit)
                self.compose_file = str(compose_file_option(named)) if named else None
            self._discover_compose_file()
        self.services = parse_services(self._load_compose_document())
        if self._is_headless():
            raise ValidationError(
                f"{self.domain} publishes no port: it has no site to answer on other names",
                details="A stack without web services is judged by its containers and is not "
                "served by nginx. Publish a port in the compose file and redeploy to serve it.",
            )
        refresh_app_site(
            self.webserver_manager(),
            self.domain,
            lambda: self._write_site(with_ssl=with_ssl),
        )

    def _obtain_certificate(self) -> None:
        """Obtain the certificate and load the site with TLS, keeping an operator's site."""
        if not self.ssl:
            self.logger.substep("SSL disabled, skipping")
            return

        try:
            self.obtain_certificate()
            # Rewritten in place: deleting the site first, as this once did,
            # left the domain unserved in between and removed an operator's
            # own configuration for good.
            if self._write_site(with_ssl=True) == "written":
                self.webserver_manager().reload()
            self.logger.substep("SSL certificate obtained")

        except NoustError as e:
            # A missing certificate is not a failed deployment: the stack still
            # answers over HTTP, and DNS often needs longer than the deploy.
            self.logger.warning(f"SSL certificate failed: {e}")
            self.logger.warning("Continuing without SSL")
            self.ssl = False

    def _create_systemd_service(self, *, project: str | None = None) -> None:
        """
        Create systemd service for Docker Compose management.

        Args:
            project: The Compose project the unit passes with ``-p``. None
                takes the one the store pins (an adopted stack's); with
                neither, the unit leaves Compose to derive it, as 1.x did.
        """
        service_manager = ServiceManager(verbose=self.verbose)

        # Build environment dict for the service
        service_env = {}
        if self.compose_profiles:
            service_env["COMPOSE_PROFILES"] = ",".join(self.compose_profiles)

        compose_file_rel = None
        if self.compose_path:
            compose_file_rel = str(self.compose_path.relative_to(self.app_path))

        service_manager.create_service(
            name=self.app_name,
            working_directory=str(self.app_path),
            description=f"Docker Compose app: {self.domain}",
            environment=service_env,
            template="docker-compose",
            compose_file=compose_file_rel,
            compose_project=project or self._pinned_project(),
        )
        self.logger.substep(f"Created systemd service: {self.app_name}")

    def _start_and_verify(self) -> None:
        """Start containers and verify they're healthy."""
        service_manager = ServiceManager(verbose=self.verbose)
        service_manager.start(self.app_name)

        # Health check
        healthy = self._health_check()
        if healthy:
            self.logger.substep("All services running and healthy")
        else:
            self.logger.warning("Some services may not be fully healthy yet")
            self.logger.warning(f"Check with: docker compose -f {self.compose_path} ps")

    def _health_check(self, retries: int = 10, delay: int = 3) -> bool:
        """
        Check if all Docker Compose services are running.

        Args:
            retries: Number of retry attempts.
            delay: Seconds between retries.

        Returns:
            True if all services are running.
        """
        cmd = self._compose("ps", "--format", "json")

        for attempt in range(retries):
            result = self._run(cmd)
            if not result.success:
                time.sleep(delay)
                continue

            all_running = True
            for svc_info in parse_compose_ps(result.stdout):
                state = str(svc_info.get("State") or "").lower()
                health = str(svc_info.get("Health") or "").lower()
                if state != "running" or health not in ("healthy", ""):
                    all_running = False
                    break

            if all_running:
                return True

            if attempt < retries - 1:
                time.sleep(delay)

        return False

    def _register_app(self) -> None:
        """
        Register or update the application in the Noust store.

        Through the same registrar as every other deployer: a redeploy
        updates the row it has instead of failing to insert a second one,
        and keeps what this deployment does not know (the layout, the
        retention, the resource limits).
        """
        headless = self._is_headless()
        try:
            StoreRegistrar(self.store).register_app(
                domain=self.domain,
                app_type=AppType.DOCKER_COMPOSE.value,
                source=self.source,
                branch=self.branch,
                # No port at all for a headless stack: 1.x stored the default
                # (3000) for a worker, and everything that read it probed a
                # port nothing listens on and called the worker down.
                port=None if headless else self._get_primary_port(),
                app_path=self.app_path,
                webserver="" if headless else self.webserver,
                ssl_enabled=self.ssl,
                status=AppStatus.RUNNING.value,
                is_static=False,
                env_vars=self.env_vars,
                preview_parent=self.preview_parent,
                env_secret_marks=self._env_secret_marks,
            )
            if self.preview_parent is not None and self._is_new_deployment:
                # A preview's data is disposable: dumping its databases before every push would
                # fill the backup directory with copies of what is thrown away with it.
                self.store.set_app_backup_before_update(self.domain, False)
        except (NoustError, sqlite3.Error) as e:
            self.logger.warning(f"Could not register app in store: {e}")

    def _recorder(self) -> DeploymentRecorder:
        """
        Build the recorder a deploy or an update writes history with.

        Returns:
            A recorder, built where every deployer's is.
        """
        return recorder_for(self, git_info=self._git_info(), release_id=lambda: self.release_id)

    def _source_manager(self) -> SourceManager:
        """
        Build the source manager git goes through, on this deployer's runner.

        Returns:
            The manager.
        """
        return SourceManager(
            verbose=self.verbose, runner=self._runner, fs=self._fs, domain=self.domain or None
        )

    def _git_info(self) -> GitInfo:
        """
        Answer the commit and branch a history row records.

        Returns:
            A reader of the checkout, which answers what was attempted
            instead once a failed update has put the previous commit back.
        """
        checkout = checkout_git_info(self._source_manager(), self.app_path)

        def read() -> tuple[str | None, str | None]:
            if self._attempted_git is not None:
                return self._attempted_git
            return checkout()

        return read

    def _rollback(self) -> None:
        """Clean up on deployment failure."""
        self.logger.info("Rolling back deployment...")

        # Stop containers
        if self.compose_path and self.compose_path.exists():
            self._run(self._compose("down", "--remove-orphans"))

        # Remove systemd service
        try:
            service_manager = ServiceManager(verbose=self.verbose)
            service_manager.delete_service(self.app_name)
        except (NoustError, OSError) as e:
            self.logger.debug(f"Service cleanup failed: {e}")

        # Remove nginx config (only if web-facing); an operator's is kept.
        if not self._is_headless():
            try:
                remove_app_site(self.domain, logger=self.logger, manager=self.webserver_manager())
            except (NoustError, OSError) as e:
                self.logger.debug(f"Site cleanup failed: {e}")

        # Remove app directory: only what this deploy put there.
        if self.deploy_target is not None:
            self.deploy_target.undo_fetch(self.fs, self.logger)
        elif self.app_path.exists():
            try:
                self.fs.remove_tree(self.app_path)
            except OSError as e:
                self.logger.debug(f"File cleanup failed: {e}")

        # Clean store
        try:
            self.store.delete_app(self.domain)
        except (NoustError, sqlite3.Error) as e:
            self.logger.debug(f"Store cleanup failed: {e}")

        self.logger.info("Rollback complete")

    # =========================================================================
    # Lifecycle methods
    # =========================================================================

    def start(self) -> None:
        """Start the Docker Compose application."""
        service_manager = ServiceManager(verbose=self.verbose)
        service_manager.start(self.app_name)
        self.store.update_app_status(self.domain, AppStatus.RUNNING.value)

    def stop(self) -> None:
        """Stop the Docker Compose application."""
        service_manager = ServiceManager(verbose=self.verbose)
        service_manager.stop(self.app_name)
        self.store.update_app_status(self.domain, AppStatus.STOPPED.value)

    def restart(self) -> None:
        """Restart (rebuild and recreate) the Docker Compose application."""
        service_manager = ServiceManager(verbose=self.verbose)
        # reload triggers ExecReload which does `docker compose up -d --build`
        result = self.runner.run(
            ["systemctl", "reload", f"{self.app_name}.service"], timeout=COMPOSE_TIMEOUT
        )
        if not result.success:
            # Fallback to restart
            service_manager.restart(self.app_name)

    def logs(self, service: str | None = None, lines: int = 50) -> str:
        """
        Get Docker Compose logs, secrets masked.

        Args:
            service: Specific service name (None for all).
            lines: Number of lines to show.

        Returns:
            The masked output, or why it could not be read.
        """
        return self.container_logs(lines, service=service)[1]

    def service_names(self) -> list[str]:
        """
        List the services of this stack, as Compose reads them from its file.

        The list comes from the file, not from the containers that exist: the
        service that crashed, or never started, is the one whose log is worth
        reading, and it has no running container to be found by.

        Returns:
            The service names in the file's order; empty when Compose could not
            read the stack. A name Compose would not accept as one (and that
            could not be passed on to it) is left out.
        """
        from noust.terminal.targets import validate_service

        result = self._run(self._compose("config", "--services"))
        if not result.success:
            return []
        names: list[str] = []
        for line in result.stdout.splitlines():
            try:
                name = validate_service(line.strip())
            except ValidationError:
                continue
            if name is not None:
                names.append(name)
        return names

    def container_logs(self, lines: int, *, service: str | None = None) -> tuple[bool, str]:
        """
        Read the last lines the stack's containers printed, with their secrets masked.

        The one reader of a stack's output: the diagnosis a viewer reads, the
        evidence a failed update keeps in its job and deployment records, the
        CLI. A container's log carries what its entrypoint printed - MySQL's
        generated root password, a connection URL with its password, a
        statement with ``IDENTIFIED BY`` - so every secret value of every
        container's environment, and every pattern
        :func:`~noust.managers.database.instances.mask_container_log` knows,
        is masked before the text goes anywhere.

        Args:
            lines: How many lines of each container.
            service: Only this service's containers.

        Returns:
            Whether Compose answered, and the masked output (or, when it did
            not, its masked error). When a container's environment cannot be
            read, the log is not shown at all, since it could not be masked.
        """
        from noust.managers.database.instances import (
            container_secret_values,
            mask_container_log,
        )

        listed = self._run(self._compose("ps", "-a", "-q"))
        if not listed.success:
            return False, (
                "(not shown: Compose did not list the containers, so their output could not "
                f"be masked: {(listed.stderr or listed.stdout).strip()})"
            )
        ids = [line.strip() for line in listed.stdout.splitlines()]
        secrets: list[str] = []
        try:
            for cid in (cid for cid in ids if _CONTAINER_ID.match(cid)):
                secrets.extend(container_secret_values(cid, self.runner))
        except DatabaseQueryError as exc:
            return False, f"(not shown: {exc.message}; {exc.output or exc.details})"
        cmd = self._compose("logs", "--tail", str(int(lines)), "--no-color")
        if service:
            cmd.append(service)
        result = self._run(cmd)
        text = result.stdout if result.success else result.stderr
        return result.success, mask_container_log(text, secrets)

    def status(self) -> dict[str, Any]:
        """
        Get Docker Compose service status.

        Returns:
            Dictionary with service status information.
        """
        result = self._run(self._compose("ps", "--format", "json"))
        services = parse_compose_ps(result.stdout) if result.success else []

        return {
            "domain": self.domain,
            "services": services,
            "total": len(services),
            "running": sum(1 for s in services if s.get("State") == "running"),
        }

    def update(self, on_step: StepReporter | None = None) -> UpdateResult:
        """
        Update the stack, with the registry accounts it uses logged in.

        Args:
            on_step: Called as each step begins.

        Returns:
            What was done, for the caller to present.
        """
        with self._registry_session():
            return self._update_stack(on_step)

    def _update_stack(self, on_step: StepReporter | None = None) -> UpdateResult:
        """
        Rebuild the images of this stack and recreate its containers, behind the health gate.

        This used to take no arguments, return None and pull the source itself,
        which is why the CLI could not drive it through the same call as every
        other deployer and grew a third copy of the update flow instead. The
        source is now fetched by whoever owns that step, exactly as
        :meth:`~noust.deployers.base.BaseDeployer.update` expects.

        What serves is recorded first (see :class:`ServingState`). A build
        that fails puts the tree and the image names back and recreates
        nothing, since the containers were never touched. New containers that
        do not pass the gate are replaced by the ones that served, from the
        images they ran, and the update fails with the probes, the unit's
        journal and the containers' own output.

        Args:
            on_step: Called as each step begins.

        Returns:
            What was done, for the caller to present.

        Raises:
            DeploymentError: When no compose file can be found, or the new
                containers did not pass the health gate; by then what served
                has been put back, and the message says whether it answers.
            DockerError: When the build fails.
        """
        self._hooks = DeploymentHooks()
        self._serving_recorded = False
        with recording(self._recorder(), git_branch=self.branch) as recorder:
            self._recording = recorder
            try:
                result = self._update_steps(on_step or (lambda _message: None))
            except NoustError as exc:
                if not self._serving_recorded:
                    self._refused_after_the_pull(exc)
                if isinstance(exc, DeploymentError):
                    # Going back puts the previous containers in front of the
                    # new schema: the operator has to read that first.
                    self._hooks.say_schema_changed(exc)
                raise
            finally:
                # A failed update's row says what its hooks ran too: whether
                # a migration already applied is what going back depends on.
                self._record_hooks()
        self.last_deployment_id = recorder.deployment_id
        return result

    def _update_steps(self, report: StepReporter) -> UpdateResult:
        """
        Record what serves, rebuild, recreate and judge the new containers.

        Args:
            report: Called as each step begins.

        Returns:
            What was done.

        Raises:
            DeploymentError: The new containers did not pass the health gate.
            DockerError: The build failed.
        """
        if self.compose_path is None:
            self._discover_compose_file()
        document = self._load_compose_document()
        self._check_update_privileges(document)
        self._require_variables(document)
        # What the gate and the hooks need to know of the stack: whether it
        # is a web at all, and which service a hook without one runs in.
        self.services = parse_services(document)
        hooks = self._resolve_hooks()
        self._require_ports_free()
        self._resolve_leftover_relays()

        report("Recording what is serving")
        serving = self._record_serving()

        report("Rebuilding Docker images")
        try:
            self._build_images(no_cache=self.no_cache)
        except DockerError as exc:
            if not serving.images:
                raise
            # The containers still run the old images, but the tree and some
            # image names already say otherwise: the next start of the unit
            # would bring up whatever half of the stack did build.
            self.logger.warning("The build failed; putting the tree and the image names back")
            self._attempted_git = self._git_info()()
            problems = self._put_back_files(serving)
            if problems:
                raise DockerError(
                    exc.message, details=_paragraphs(exc.details, _problem_list(problems))
                ) from exc
            raise

        if hooks.pre_deploy:
            report("Running pre_deploy hooks")
            try:
                self._run_pre_deploy()
            except HookFailedError as exc:
                # Nothing was recreated, but the tree and the image names
                # already say otherwise, exactly as after a failed build.
                self._attempted_git = self._git_info()()
                problems = self._put_back_files(serving) if serving.images else []
                if problems:
                    exc.details = _paragraphs(exc.details, _problem_list(problems))
                raise

        report("Recreating containers")
        healthy, evidence = self._activate(self._recreate)
        if not healthy:
            report("Putting back what was serving")
            raise self._go_back(serving, evidence)

        self.store.update_app_status(self.domain, AppStatus.RUNNING.value)
        self._retain_release()
        if hooks.post_deploy:
            report("Running post_deploy hooks")
        self._run_post_deploy()

        return UpdateResult(
            package_manager="docker compose",
            prisma_updated=False,
            # The containers were recreated by the command above, so there is
            # no unit for the caller to restart afterwards.
            is_static=True,
            start_command=" ".join(self._compose("up", "-d")),
            hooks=tuple(self._hooks.runs),
            schema_changed=self._hooks.schema_changed,
            warnings=tuple(self._hooks.warnings),
        )

    def _rehearsing(self) -> bool:
        """
        Report whether this is a ``--dry-run``, where nothing was recreated to probe.

        Returns:
            True under a rehearsing filesystem.
        """
        return isinstance(self.fs, DryRunFileSystem)

    def _running_images(self) -> RunningImages:
        """
        Read the image each service of the stack runs now.

        Returns:
            The project, and the image of every service with a running container
            (a digest names one image for ever, so a service pinned to one has no
            name to move back and is left out). ``running`` is False when no
            container runs at all.

        Raises:
            DockerError: Docker could not list the containers or read their images.
        """
        listed = self._run(self._compose("ps", "-q"))
        if not listed.success:
            raise DockerError(
                "Could not list the running containers",
                listed.stderr.strip() or listed.stdout.strip(),
            )
        ids = [line.strip() for line in listed.stdout.splitlines()]
        ids = [cid for cid in ids if _CONTAINER_ID.match(cid)]
        if not ids:
            return RunningImages(project=None, images={}, running=False)

        inspected = self._run(["docker", "inspect", "--format", _INSPECT_FORMAT, *ids])
        if not inspected.success:
            raise DockerError(
                "Could not read the images of the running containers", inspected.stderr.strip()
            )

        project: str | None = None
        images: dict[str, ServingImage] = {}
        for line in inspected.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) != 4 or not all(parts[:3]):
                continue
            image_id, reference, service, owner = parts
            project = project or owner or None
            # A digest names one image for ever; there is no name to move back.
            if "@" not in reference:
                images.setdefault(service, ServingImage(service, reference, image_id))
        return RunningImages(project=project, images=images, running=True)

    def _record_serving(self) -> ServingState:
        """
        Record what the stack serves now, and keep its images from being pruned.

        Returns:
            The project, the commit the caller read before pulling, and the
            image of every service with a running container. Nothing is
            recorded when Docker cannot say, which is reported: that update
            has nothing to go back to.
        """
        self._serving_recorded = True
        try:
            running = self._running_images()
        except DockerError as exc:
            self.logger.warning(
                f"{exc.message}; a failed update cannot put them back: {exc.details}"
            )
            return ServingState(project=None, commit=self.previous_commit, images=())
        if not running.running:
            self.logger.substep("No containers are running: there is nothing to go back to")
            return ServingState(project=None, commit=self.previous_commit, images=())

        project, images = running.project, running.images
        for image in images.values():
            tag = keep_tag(project or self.app_name, image.service)
            kept = self._run(["docker", "image", "tag", image.image_id, tag])
            if not kept.success:
                # Still usable by its id until something prunes it.
                self.logger.warning(
                    f"Could not tag {image.image_id} as {tag}: {kept.stderr.strip()}"
                )
        if images:
            self.logger.substep(
                "Serving: " + ", ".join(f"{i.service} ({i.reference})" for i in images.values())
            )
        return ServingState(
            project=project, commit=self.previous_commit, images=tuple(images.values())
        )

    # -- Releases (B-16) ------------------------------------------------------------------

    def _retain_release(self) -> None:
        """
        Keep the images that serve now as a release, so going back to it is instant.

        A failure to keep them is a warning, never a failed deployment: the stack
        serves, and the commit can still be built again.
        """
        if not self.RETAINS_RELEASES or self._rehearsing():
            return
        try:
            self._keep_release()
        except (NoustError, sqlite3.Error, OSError) as exc:
            self.logger.warning(
                "The images of this release were not kept, so it cannot be gone back to "
                f"without building it again: {exc}"
            )

    def _keep_release(self) -> None:
        """
        Name the running images after a new release and record it.

        Raises:
            NoustError: Docker could not say what runs, or the release could not be named.
            sqlite3.Error: The store refused the record.
        """
        app = self.store.get_app(self.domain)
        if app is None or app.id is None:
            return
        if not (self.app_path / ".git").exists():
            self.logger.debug("Not a git checkout: there is no commit to go back to")
            return
        commit = self._source_manager().get_repo_info(self.app_path).get("commit")
        if not commit:
            return
        running = self._running_images()
        if not running.images:
            return
        existing = {row.id for row in self.store.list_releases(app.id)}
        release_id = ReleaseManager(
            self.app_path, fs=self._fs, runner=self._runner, logger=self.logger
        ).next_release_id(str(commit), existing)
        kept, problems = compose_retention.retain(
            self.runner,
            running.project or self.app_name,
            [(i.service, i.reference, i.image_id) for i in running.images.values()],
            release_id,
        )
        if problems:
            # A release with an image missing cannot be gone back to whole.
            compose_retention.remove(self.runner, [image.tag for image in kept])
            self.logger.warning(
                "The images of this release were not all kept: " + "; ".join(problems)
            )
            return
        self.store.record_release(
            ReleaseRecord(
                id=release_id,
                app_id=app.id,
                git_commit=str(commit)[:7],
                created_at=datetime.now(timezone.utc).isoformat(),
                status=ReleaseStatus.BUILT.value,
                path=str(self.app_path),
            )
        )
        compose_release_store.put_images(app.id, release_id, kept, store=self.store)
        self.store.mark_release_active(app.id, release_id)
        self.release_id = release_id
        self.logger.substep(f"Release {release_id} kept: its images can be gone back to")
        self._prune_releases(app)

    def _prune_releases(self, app: App) -> list[str]:
        """
        Remove the images (and the records) of releases past the retention.

        The release that serves and the one a rollback goes to are kept whatever
        the retention is.

        Args:
            app: The application's row.

        Returns:
            The ids of the releases forgotten.
        """
        if app.id is None:
            return []
        rows = sorted(
            self.store.list_releases(app.id),
            key=lambda row: release_order_key(row.id),
            reverse=True,
        )
        ordered = [row.id for row in rows]
        active = next((row.id for row in rows if row.status == ReleaseStatus.ACTIVE.value), None)
        keep = set(ordered[: max(app.keep_releases, 2)])
        if active is not None:
            keep.add(active)
            older = ordered[ordered.index(active) + 1 :]
            target = next(
                (
                    row.id
                    for row in rows
                    if row.id in older and row.status != ReleaseStatus.FAILED.value
                ),
                None,
            )
            if target is not None:
                keep.add(target)
        forgotten = [release for release in ordered if release not in keep]
        if not forgotten or self._rehearsing():
            return []
        kept_images = compose_release_store.images_by_release(app.id, store=self.store)
        tags = [image.tag for release in forgotten for image in kept_images.get(release, ())]
        for failure in compose_retention.remove(self.runner, tags):
            self.logger.warning(f"An old image was not removed: {failure}")
        compose_release_store.delete_images(app.id, forgotten, store=self.store)
        self.store.delete_releases(app.id, forgotten)
        return forgotten

    def prune_releases(self) -> list[str]:
        """
        Remove the images and the records of the releases past the retention.

        What ``noust releases keep`` does after it sets the number.

        Returns:
            The ids of the releases forgotten.

        Raises:
            DeploymentError: The application is not deployed.
        """
        app = self.store.get_app(self.domain)
        if app is None:
            raise DeploymentError(f"{self.domain} is not deployed")
        return self._prune_releases(app)

    def release_images(self) -> list[ImageRelease]:
        """
        List the releases of the stack, newest first.

        Returns:
            One entry per release the store remembers, saying whether every image
            of it is still on this host (only then can it be activated).

        Raises:
            DeploymentError: The application is not deployed.
            DockerError: Docker could not list its images.
        """
        app = self.store.get_app(self.domain)
        if app is None or app.id is None:
            raise DeploymentError(f"{self.domain} is not deployed")
        present = compose_retention.kept_tags(self.runner)
        if present is None:
            raise DockerError("Docker could not list the images of this application")
        images = compose_release_store.images_by_release(app.id, store=self.store)
        rows = sorted(
            self.store.list_releases(app.id),
            key=lambda row: release_order_key(row.id),
            reverse=True,
        )
        return [
            ImageRelease(
                id=row.id,
                commit=row.git_commit,
                created_at=row.created_at or "",
                activated_at=row.activated_at,
                status=row.status,
                active=row.status == ReleaseStatus.ACTIVE.value,
                present=bool(images.get(row.id))
                and all(image.tag in present for image in images[row.id]),
            )
            for row in rows
        ]

    def activate_release(self, release_id: str, *, went_back: bool) -> None:
        """
        Make a kept release the one that serves: its commit, and its images, unbuilt.

        The tree is put on the release's commit (its compose file and its
        ``noust.yaml``), the images are named as that file looks them up, and the
        containers are recreated from them without building. They pass the health
        gate like any activation; ones that do not are replaced by the ones that
        served, and the release is recorded as failed.

        Args:
            release_id: The release to activate.
            went_back: The release is older than the one it replaces, so the
                one it replaces is recorded as rolled back.

        Raises:
            DeploymentError: The release is unknown, its images were pruned, it
                has no commit to put the tree on, or it did not pass the gate
                (what served is back by then, and a
                :class:`~noust.core.exceptions.RolledBackError` says so).
            DockerError: Docker could not name an image or recreate the containers.
        """
        app = self.store.get_app(self.domain)
        if app is None or app.id is None:
            raise DeploymentError(f"{self.domain} is not deployed")
        record = self.store.get_release(app.id, release_id)
        images = compose_release_store.images_by_release(app.id, store=self.store).get(
            release_id, ()
        )
        if record is None or not images:
            raise DeploymentError(
                f"Release {release_id!r} has no kept images",
                details="Releases made before images were kept cannot be activated: rebuild "
                f"the commit instead (noust update {self.domain} --commit COMMIT).",
            )
        present = compose_retention.kept_tags(self.runner)
        if present is None:
            raise DockerError("Docker could not list the images of this application")
        if any(image.tag not in present for image in images):
            raise DeploymentError(
                f"The images of release {release_id} are no longer on this server",
                details="They were pruned: rebuild its commit instead "
                f"(noust update {self.domain} --commit COMMIT).",
            )
        if not record.git_commit or not (self.app_path / ".git").exists():
            raise DeploymentError(
                f"Release {release_id} cannot put the compose file back",
                details="Its commit is not known, or the tree is not a git checkout.",
            )

        previous = next(
            (
                row.id
                for row in self.store.list_releases(app.id)
                if row.status == ReleaseStatus.ACTIVE.value
            ),
            None,
        )
        source = self._source_manager()
        self.previous_commit = source.get_repo_info(self.app_path).get("commit") or None
        self._hooks = DeploymentHooks()
        serving = self._record_serving()
        self.logger.substep(
            f"Activating release {release_id}" + (f" (was {previous})" if previous else "")
        )
        full = source.checkout_commit(self.app_path, record.git_commit)
        self.logger.substep(f"Tree on commit {full[:7]}")
        try:
            self.compose_path = None
            self._discover_compose_file()
            document = self._load_compose_document()
            self._check_update_privileges(document)
            self.services = parse_services(document)
        except NoustError:
            # Nothing was recreated: the tree goes back to what serves.
            self._put_back_files(serving)
            raise
        problems = compose_retention.restore(self.runner, images)
        if problems:
            self._put_back_files(serving)
            raise DockerError(
                f"The images of release {release_id} could not be named as the compose file "
                "looks them up",
                "\n".join(problems),
            )

        self.release_id = release_id
        self._reuse_images = True
        try:
            healthy, evidence = self._activate(self._recreate)
        finally:
            self._reuse_images = False
        if healthy:
            self.store.update_app_status(self.domain, AppStatus.RUNNING.value)
            self.store.mark_release_active(app.id, release_id)
            if went_back and previous is not None:
                self.store.set_release_status(app.id, previous, ReleaseStatus.ROLLED_BACK.value)
            return
        self.store.set_release_status(app.id, release_id, ReleaseStatus.FAILED.value)
        raise self._go_back(serving, evidence)

    def _relay(self) -> "ComposeRelay":
        """
        Build the relay engine over this stack.

        Returns:
            The engine, with the probe this module holds (looked up now, so it
            is the one a test replaced).
        """
        from noust.deployers.compose_relay import ComposeRelay

        return ComposeRelay(self, probe=wait_until_healthy)

    def _relayed_services(self) -> list[str]:
        """
        Name the web services an update recreates behind a relay.

        Returns:
            The services the site reaches through a servers file, when the
            stack is in zero-downtime mode; empty otherwise.
        """
        from noust.deployers.compose_relay import relayed_services

        return relayed_services(self)

    def _resolve_leftover_relays(self) -> None:
        """
        Give back what a relay left by an interrupted update still serves, before building.

        Raises:
            DeploymentError: A relay still serves and its service's own
                container does not answer; nothing was touched.
        """
        relayed = self._relayed_services()
        if not relayed or self._rehearsing():
            return
        problem = self._relay().resolve_leftovers(relayed)
        if problem is not None:
            raise DeploymentError(
                f"The update of {self.domain} did not start: a relay still serves", details=problem
            )

    def _recreate(self) -> None:
        """
        Recreate the containers from the images just built.

        In zero-downtime mode each web service the site reaches through a
        servers file is recreated first, in ``depends_on`` order, while a relay
        serves it (:mod:`noust.deployers.compose_relay`); the rest of the stack
        follows with ``up -d``, which leaves those already recreated alone.

        Raises:
            DockerError: Compose failed, with its own output.
            DeploymentError: A relay did not pass its gate (the service was not
                touched), or a recreated service did not answer (its relay
                still serves); the evidence names which.
        """
        relayed = self._relayed_services()
        if relayed:
            healthy, evidence = self._relay().recreate(relayed)
            if not healthy:
                raise DeploymentError(
                    f"The web services of {self.domain} were not recreated behind their relays",
                    details=evidence,
                )
        # Services that name an image instead of building one pull it here,
        # which is a download, not a local recreate.
        flags = ["--no-build", "--remove-orphans"] if self._reuse_images else ["--remove-orphans"]
        result = self._run(
            self._compose("up", "-d", *flags, *self.compose_up_args, *self.compose_services),
            timeout=BUILD_TIMEOUT,
        )
        if not result.success:
            raise DockerError("Failed to update containers", result.stderr)

    def _recreate_previous(self, serving: ServingState) -> None:
        """
        Recreate the containers from the images that served, without building.

        ``--no-build`` is what makes this the previous stack and not a second
        build of the tree; ``--remove-orphans`` removes the containers of
        services the failed update added, and only the containers.

        Args:
            serving: What served.

        Raises:
            DockerError: Compose failed, with its own output.
        """
        command = self._compose(
            "up", "-d", "--no-build", "--remove-orphans", project=serving.project
        )
        result = self._run(command, timeout=BUILD_TIMEOUT)
        if not result.success:
            raise DockerError("Failed to recreate the containers that were serving", result.stderr)
        relayed = self._relayed_services()
        if relayed:
            # A relay of the failed image may still be serving: once the
            # previous container answers, the traffic goes back to it.
            problem = self._relay().resolve_leftovers(relayed)
            if problem is not None:
                raise DockerError("A relay of the failed update still serves", problem)

    def _health_gate(self, restart: Callable[[], object]) -> HealthGate | None:
        """
        Build the gate the stack's web port must pass, with the application's own check.

        Args:
            restart: What brings the containers up.

        Returns:
            The gate, or None for a headless stack, which has no port to ask
            and is judged by its containers alone.
        """
        if self.services and self._is_headless():
            # Whatever port a 1.x deploy recorded for a worker: nothing
            # listens on it, and probing it would roll back a healthy stack.
            return None
        app = self.store.get_app(self.domain)
        port = app.port if app is not None and app.port else self.port
        if not port:
            return None
        check = HealthCheck.for_app(app)
        return HealthGate(
            unit=self.app_name or None,
            url=check.url(port),
            check=check,
            services=ServiceManager(verbose=self.verbose, runner=self._runner),
            logger=self.logger,
            # Looked up here, at call time, so it is the one this module holds.
            probe=wait_until_healthy,
            restart=restart,
        )

    def _activate(self, restart: Callable[[], object]) -> tuple[bool, str]:
        """
        Bring the containers up and decide whether the stack is up.

        Args:
            restart: What brings them up.

        Returns:
            Whether it passed, and when it did not, the evidence verbatim.
        """
        if self._rehearsing():
            # A rehearsal built and recreated nothing; there is nothing to ask.
            restart()
            return True, ""
        gate = self._health_gate(restart)
        if gate is not None:
            healthy, evidence = gate.restart_and_probe()
            if not healthy:
                return False, evidence
        else:
            try:
                restart()
            except NoustError as exc:
                return False, str(exc)
        return self._containers_settle()

    def _containers_settle(self) -> tuple[bool, str]:
        """
        Read the containers' state until none is failing, or the attempts run out.

        Returns:
            Whether every container is running (or exited cleanly), and when
            not, which ones and how.
        """
        problems: list[str] = []
        starting = False
        for attempt in range(CONTAINER_CHECK_ATTEMPTS):
            if attempt:
                time.sleep(CONTAINER_CHECK_DELAY)
            result = self._run(self._compose("ps", "-a", "--format", "json"))
            if not result.success:
                problems = [f"docker compose ps failed: {result.stderr.strip()}"]
                continue
            containers = parse_compose_ps(result.stdout)
            problems = [p for p in map(container_problem, containers) if p is not None]
            starting = any(str(c.get("Health") or "").lower() == "starting" for c in containers)
            if not problems and not starting:
                return True, ""
        if not problems:
            # Only a health check still in its start period: the stack's own
            # check has not decided, and the gate above already has.
            self.logger.warning("Some containers are still starting their own health check")
            return True, ""
        return False, "Containers that are not running:\n" + "\n".join(f"  {p}" for p in problems)

    def _containers_output(self) -> str:
        """
        Read the last lines of every container's output, before they are replaced.

        Returns:
            Compose's output with its secrets masked, or why it could not be read.
        """
        answered, text = self.container_logs(COMPOSE_LOG_LINES)
        if answered:
            return text.strip() or "(the containers printed nothing)"
        return f"(the containers' output could not be read: {text.strip()})"

    def _refused_after_the_pull(self, exc: NoustError) -> None:
        """
        Put the tree back on the commit that serves, after a refusal before anything was built.

        The caller pulled the new code before this deployer read it, so a
        refusal of what it found (the compose file gone, an invalid
        ``noust.yaml``, a relay that cannot be resolved, a host privilege)
        would leave the tree on code that never ran, and the next start of the
        unit would bring it up. It goes back exactly as after a failed build;
        what cannot be put back is added to the error.

        Args:
            exc: The refusal, raised again by the caller.
        """
        if not self.previous_commit:
            return
        self._attempted_git = self._git_info()()
        problems = self._put_back_files(
            ServingState(project=None, commit=self.previous_commit, images=())
        )
        if problems:
            exc.details = _paragraphs(exc.details, _problem_list(problems))

    def _put_back_files(self, serving: ServingState) -> list[str]:
        """
        Put the tree and the image names back as they were before the update.

        Every step is attempted even when an earlier one failed.

        Args:
            serving: What served.

        Returns:
            What could not be put back, one line each; empty when all was.
        """
        problems: list[str] = []
        if serving.commit:
            try:
                full = self._source_manager().checkout_commit(self.app_path, serving.commit)
            except NoustError as exc:
                problems.append(f"The tree could not be checked out at {serving.commit[:7]}: {exc}")
            else:
                self.logger.substep(f"Tree back on commit {full[:7]}")
        for image in serving.images:
            tagged = self._run(["docker", "image", "tag", image.image_id, image.reference])
            if not tagged.success:
                problems.append(
                    f"The image {image.service} ran could not be named {image.reference} again: "
                    + (tagged.stderr.strip() or tagged.stdout.strip())
                )
        return problems

    def _go_back(self, serving: ServingState, evidence: str) -> DeploymentError:
        """
        Put back what served after the new containers failed the gate.

        Args:
            serving: What served.
            evidence: What the gate saw.

        Returns:
            The error to raise, which says whether what served is back and answering.
        """
        attempted = f"The update of {self.domain} did not pass its health check"
        self.logger.warning(attempted)
        # Read now: going back replaces the containers that wrote it.
        output = self._containers_output()
        evidence = _paragraphs(
            evidence,
            f"Last lines of the containers' output (docker compose logs --tail "
            f"{COMPOSE_LOG_LINES}):\n{output}",
        )
        if not serving.images:
            self.store.update_app_status(self.domain, AppStatus.FAILED.value)
            return DeploymentError(
                f"{attempted}; nothing was running before it, so nothing was put back",
                details=evidence,
            )

        self._attempted_git = self._git_info()()
        problems = self._put_back_files(serving)
        notes = []
        if not serving.commit:
            notes.append(
                "The tree is not a git checkout at a known commit, so the compose file was not "
                "put back: the previous images run with the compose file of this update."
            )
        try:
            self._recreate_previous(serving)
        except DockerError as exc:
            problems.append(str(exc))

        if problems:
            self.store.update_app_status(self.domain, AppStatus.FAILED.value)
            return DeploymentError(
                f"{attempted}; what was serving could not be put back entirely",
                details=_paragraphs(evidence, *notes, _problem_list(problems)),
            )

        restored, after = self._activate(lambda: None)
        if restored:
            self.store.update_app_status(self.domain, AppStatus.RUNNING.value)
            return RolledBackError(
                f"{attempted}; the containers that were serving are running again",
                details=_paragraphs(evidence, *notes),
            )
        self.store.update_app_status(self.domain, AppStatus.FAILED.value)
        return DeploymentError(
            f"{attempted}; the previous containers are back but are not answering either",
            details=_paragraphs(evidence, *notes, f"After going back:\n{after}"),
        )

    def down(self, remove_volumes: bool = False) -> bool:
        """
        Stop and remove the stack's containers, with the file and project it runs as.

        What :func:`noust.deployers.lifecycle.delete_app` takes a stack down
        with: the same compose file discovery and the same project name as
        every other command here, so ``docker-compose.prod.yml`` and a
        pinned project are honoured.

        Args:
            remove_volumes: Also remove the named volumes. They hold the
                stack's databases, so only when asked for explicitly.

        Returns:
            Whether docker reported success.

        Raises:
            DeploymentError: No compose file is left to take the stack down with.
        """
        if self.compose_path is None:
            self._discover_compose_file()
        down = (
            ["down", "--volumes", "--remove-orphans"]
            if remove_volumes
            else ["down", "--remove-orphans"]
        )
        result = self._run(self._compose(*down))
        if not result.success:
            self.logger.warning(f"docker compose down failed: {result.stderr.strip()}")
        return result.success

    def remove_kept_images(self) -> list[str]:
        """
        Remove the images updates kept under :data:`PREVIOUS_TAG` for this stack.

        What :func:`noust.deployers.lifecycle.delete_app` calls once the stack
        is down. An update tags the image each service ran as
        ``<project>-<service>:wasm-previous`` (see :func:`keep_tag`), and
        nothing else ever removes it. The tags this stack can have are derived
        from its compose file (every service, under every name its project can
        have had) and only those that exist are removed; another stack's are
        left alone. ``docker image rm`` of a tag only untags an image that has
        other names.

        Returns:
            The tags removed.

        Raises:
            DeploymentError: No compose file is left to read the services from.
            DockerError: Docker could not list or remove them, with its output.
        """
        # The images of the releases kept (B-16) first: they are named by the
        # store, not by the compose file, so a tree that is already gone has them too.
        released_failures: list[str] = []
        released = self._remove_release_images(released_failures)
        if self.compose_path is None:
            self._discover_compose_file()
        compose_path = self._compose_file_path()
        try:
            document = load_compose(compose_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise DeploymentError(
                f"Cannot read {compose_path} to name the images kept for going back",
                details=str(exc),
            ) from exc
        document = document if isinstance(document, dict) else {}
        services = document.get("services")
        names = [str(name) for name in services] if isinstance(services, dict) else []
        projects = {
            name
            for name in (
                self._pinned_project(),
                compose_project_name(self.app_path, compose_path),
                document.get("name") if isinstance(document.get("name"), str) else None,
                _env_project_name(compose_path),
                self.app_name,
            )
            if name
        }
        wanted = {keep_tag(project, service) for project in projects for service in names}
        if not wanted:
            if released_failures:
                raise DockerError(
                    "Some images kept for going back were not removed",
                    "\n".join(released_failures),
                )
            return released

        listed = self._run(
            [
                "docker",
                "image",
                "ls",
                "--filter",
                f"reference=*:{PREVIOUS_TAG}",
                "--format",
                "{{.Repository}}:{{.Tag}}",
            ]
        )
        if not listed.success:
            raise DockerError("Could not list the images kept for going back", listed.stderr)
        present = {line.strip() for line in listed.stdout.splitlines() if line.strip()}

        removed: list[str] = []
        failures: list[str] = []
        for tag in sorted(wanted & present):
            result = self._run(["docker", "image", "rm", tag])
            if result.success:
                removed.append(tag)
            else:
                failures.append(f"{tag}: {(result.stderr or result.stdout).strip()}")
        failures = [*released_failures, *failures]
        if failures:
            raise DockerError(
                "Some images kept for going back were not removed", "\n".join(failures)
            )
        return [*released, *removed]

    def _remove_release_images(self, failures: list[str]) -> list[str]:
        """
        Remove the images kept for the releases of this stack.

        Args:
            failures: Where a tag that could not be removed is reported.

        Returns:
            The tags removed.
        """
        app = self.store.get_app(self.domain)
        if app is None or app.id is None:
            return []
        kept = compose_release_store.images_by_release(app.id, store=self.store)
        tags = [image.tag for images in kept.values() for image in images]
        failed = compose_retention.remove(self.runner, tags)
        failures.extend(failed)
        compose_release_store.delete_images(app.id, kept, store=self.store)
        return [tag for tag in tags if not any(line.startswith(f"{tag}:") for line in failed)]

    def delete(self, remove_volumes: bool = False) -> None:
        """
        Delete the Docker Compose application.

        Args:
            remove_volumes: Also remove Docker volumes.
        """
        # Stop and remove containers
        self.down(remove_volumes=remove_volumes)

        # Remove systemd service
        try:
            service_manager = ServiceManager(verbose=self.verbose)
            service_manager.delete_service(self.app_name)
        except (NoustError, OSError) as e:
            self.logger.debug(f"Service cleanup failed: {e}")

        # Remove nginx config; an operator's is kept, and said so.
        try:
            remove_app_site(self.domain, logger=self.logger, manager=self.webserver_manager())
        except (NoustError, OSError) as e:
            self.logger.debug(f"Site cleanup failed: {e}")

        # Clean store
        try:
            self.store.delete_site(self.domain)
            self.store.delete_service(self.app_name)
            self.store.delete_app(self.domain)
        except (NoustError, sqlite3.Error) as e:
            self.logger.debug(f"Store cleanup failed: {e}")


@dataclass(frozen=True)
class HeadlessStackState:
    """
    How a stack that publishes no port is, judged by its containers (owner item 57).

    A worker has no port to probe; it is fine when every container runs (or
    ran and exited 0) and none restarts in a loop, and not otherwise.

    Attributes:
        healthy: Whether every container is fine.
        summary: One line saying so, or naming what is not.
        problems: One line per container that is not fine.
        output: Docker's own answer, verbatim, for the evidence.
        recorded_port: A port the store still records for it - a 1.x deploy
            stored one for every stack - which nothing listens on; cleared
            with ``noust app headless DOMAIN``.
    """

    healthy: bool
    summary: str
    problems: tuple[str, ...]
    output: str
    recorded_port: int | None


def stack_deployer(
    app: App, *, runner: CommandRunner | None = None, verbose: bool = False
) -> DockerComposeDeployer:
    """
    Build the deployer that addresses a deployed stack, from its store row.

    The one way to reach a stack (rule 3): an update, a deletion, the copy of
    its databases and every reader go through it, so an adopted stack is
    found the same way by all of them. The compose file is the one the unit
    names, as an update reads it; the project the one the store pins, if any
    (read by :meth:`DockerComposeDeployer._compose`); the site the one the
    store names (read by the web server's ``config_path``).

    Args:
        app: The application's row.
        runner: The runner docker goes through; the process-wide one by default.
        verbose: Verbosity of the deployer.

    Returns:
        The deployer, configured enough to run ``docker compose`` against the
        stack; nothing is fetched or built.

    Raises:
        ServiceError: The unit exists and is not Noust's.
        ValidationError: The compose file the unit names is not a relative
            path inside the application.
    """
    from noust.core.paths import LEGACY_UNIT_PREFIX
    from noust.deployers.helpers.layout import app_root

    deployer_class: type[DockerComposeDeployer] = DockerComposeDeployer
    if app.app_type == AppType.CONTAINER.value:
        # The same machinery with Noust's own compose file (rule 3): imported
        # here because that module builds on this one.
        from noust.deployers.container import ContainerDeployer

        deployer_class = ContainerDeployer
    deployer = deployer_class(verbose=verbose, runner=runner)
    deployer.app_path = app_root(app)
    deployer.domain = app.domain
    deployer.app_name = domain_to_app_name(app.domain)
    if deployer.app_path.name == LEGACY_UNIT_PREFIX + deployer.app_name:
        # Deployed before 0.14.1: the directory and the unit both kept the
        # prefix, and an update addresses it by that name.
        deployer.app_name = deployer.app_path.name
    unit = ServiceManager(verbose=verbose, runner=runner).get_service_config(deployer.app_name)
    named = compose_file_from_unit(unit)
    if named:
        deployer.compose_file = str(compose_file_option(named))
    deployer.profiles_from_unit(unit)
    # What the operator chose (noust app compose) wins over what the unit recorded.
    chosen = app_compose_store.get_options(app.domain)
    if chosen.compose_file:
        deployer.compose_file = chosen.compose_file
    if chosen.profiles:
        deployer.compose_profiles = list(chosen.profiles)
    deployer.compose_build_args = chosen.build_args
    deployer.compose_up_args = chosen.up_args
    deployer.compose_services = chosen.services
    if app.preview_parent and app.app_type == AppType.DOCKER_COMPOSE.value:
        # A preview runs the copy of the compose file made for it, on the port it was given.
        deployer.preview_parent = app.preview_parent
        deployer.port = app.port
    return deployer


def stack_published_ports(app: App, *, runner: CommandRunner | None = None) -> list[PortMapping]:
    """
    Read the host ports a deployed stack's compose file publishes.

    What the file declares, whether or not the containers run: a stopped stack still holds
    its ports, because starting it would take them. The one reader a port check asks (E-44),
    over the same compose file an update reads.

    Args:
        app: The application's row.
        runner: The runner the unit is read through.

    Returns:
        One mapping per published container port that has a host port the file names, ranges
        expanded; empty for anything but a Compose stack, or one whose file cannot be read.
    """
    if app.app_type != AppType.DOCKER_COMPOSE.value:
        return []
    deployer = stack_deployer(app, runner=runner)
    if not deployer.app_path.is_dir():
        return []
    try:
        deployer._discover_compose_file()
    except DeploymentError:
        return []
    document = deployer._load_compose_document()
    if document is None:
        return []
    return [
        mapping
        for service in parse_services(document)
        for mapping in parse_ports(list(service.ports))
        if mapping.host_port is not None
    ]


def headless_stack_state(
    app: App, *, runner: CommandRunner | None = None
) -> HeadlessStackState | None:
    """
    Judge a deployed stack by its containers when it publishes no port.

    The one place every reader of an application's health - ``noust list``
    and ``noust health``, the console's state, ``noust diagnose`` - asks
    about a worker, instead of probing a port nothing listens on.

    Args:
        app: The application's row.
        runner: The runner docker goes through.

    Returns:
        The state; None when the application is not a Compose stack, its
        compose file cannot be read, or it publishes a port (it is a web,
        judged as one).
    """
    if app.app_type != AppType.DOCKER_COMPOSE.value:
        return None
    deployer = stack_deployer(app, runner=runner)
    if not deployer.app_path.is_dir():
        return None
    try:
        deployer._discover_compose_file()
    except DeploymentError:
        return None
    document = deployer._load_compose_document()
    if document is None or not is_headless_stack(parse_services(document)):
        return None
    result = deployer._run(deployer._compose("ps", "-a", "--format", "json"))
    recorded = app.port or None
    if not result.success:
        output = (result.stderr or result.stdout).strip()
        return HeadlessStackState(
            False, "docker compose could not list its containers", (), output, recorded
        )
    containers = parse_compose_ps(result.stdout)
    problems = tuple(p for p in map(container_problem, containers) if p is not None)
    if not problems:
        problems = _crash_loops(deployer, containers)
    if not containers:
        summary = "none of its containers exists; start it with: noust start " + app.domain
        return HeadlessStackState(False, summary, (), result.stdout.strip(), recorded)
    if problems:
        return HeadlessStackState(
            False,
            "containers that are not running: " + "; ".join(problems),
            problems,
            result.stdout.strip(),
            recorded,
        )
    return HeadlessStackState(
        True,
        f"all {len(containers)} of its containers are running or finished cleanly",
        (),
        result.stdout.strip(),
        recorded,
    )


#: A moment as Docker prints it: RFC 3339 in UTC with as many fractional digits as it has (Go trims
#: trailing zeros), which ``datetime.fromisoformat`` of Python 3.10 reads only at 3 or 6 digits.
_DOCKER_TIME = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z")


def _docker_time(text: str) -> datetime:
    """
    Read a timestamp as ``docker inspect`` prints it.

    Args:
        text: Such as ``2026-10-17T04:17:13.66889812Z``.

    Returns:
        The moment, in UTC.

    Raises:
        ValueError: The text is not in that shape.
    """
    match = _DOCKER_TIME.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"not a Docker timestamp: {text!r}")
    microseconds = (match.group(2) or "").ljust(6, "0")[:6]
    return datetime.fromisoformat(f"{match.group(1)}.{microseconds}+00:00")


def _crash_loops(
    deployer: DockerComposeDeployer, containers: Sequence[dict[str, Any]]
) -> tuple[str, ...]:
    """
    Name the running containers Docker keeps restarting.

    ``docker compose ps`` is one instant: a container in a crash loop is ``running`` for the
    second it takes to fail, and looks fine in about half the looks. Docker counts the restarts
    its policy made, and when the current run began; a container restarted several times whose
    current run is young is in a loop whatever the instant says.

    Args:
        deployer: The stack's deployer, which runs docker.
        containers: What :func:`parse_compose_ps` read.

    Returns:
        A line per container in a loop, naming how often it restarted and for how long it has
        run; empty when none is, or when Docker could not say (the instant's verdict stands).
    """
    ids = [
        str(entry["ID"])
        for entry in containers
        if entry.get("ID") and str(entry.get("State") or "").lower() == "running"
    ]
    if not ids:
        return ()
    inspected = deployer._run(["docker", "inspect", "--format", _RESTART_FORMAT, *ids])
    if not inspected.success:
        deployer.logger.debug(f"Could not read the restart counts: {inspected.stderr.strip()}")
        return ()
    now = datetime.now(timezone.utc)
    loops: list[str] = []
    for line in inspected.stdout.splitlines():
        name, _, rest = line.strip().partition("|")
        count, _, started = rest.partition("|")
        try:
            restarts = int(count)
            began = _docker_time(started)
        except ValueError:
            continue
        ran = (now - began).total_seconds()
        if restarts >= CRASH_LOOP_RESTARTS and ran < CRASH_LOOP_RUN_SECONDS:
            loops.append(
                f"{name.lstrip('/')}: restarting in a loop ({restarts} restarts, "
                f"the current run began {ran:.0f} s ago)"
            )
    return tuple(loops)


@dataclass(frozen=True)
class HeadlessChange:
    """
    What ``noust app headless`` did to a worker registered as a web.

    Attributes:
        domain: The application.
        previous_port: The port the store recorded, now cleared.
        site: What became of its site: ``removed``, ``kept`` (not asked to
            remove it), ``kept_operator`` (the operator wrote it),
            ``kept_aliases`` (it answers on other names too) or ``absent``.
    """

    domain: str
    previous_port: int | None
    site: Literal["removed", "kept", "kept_operator", "kept_aliases", "absent"]


def site_retirable(domain: str, *, manager: NginxManager | None = None) -> bool:
    """
    Tell whether a worker's site is one ``noust app headless`` may remove.

    Args:
        domain: The application.
        manager: The nginx manager; a fresh one by default.

    Returns:
        True when the site exists, Noust wrote it and the application has no
        alias or redirect (a name the operator added is a reason to keep it).
    """
    manager = manager or NginxManager()
    if not manager.site_exists(domain) or not manager.site_is_noust(domain):
        return False
    return all(record.domain == domain for record in get_store().list_domains(domain))


@dataclass(frozen=True)
class HeadlessCheck:
    """
    What ``noust app headless`` would find, asked before it is run.

    Attributes:
        domain: The application.
        headless: It is a Compose stack whose compose file publishes no port.
        recorded_port: The port the store records for it, or None.
        site_retirable: Its site could be removed with it (see
            :func:`site_retirable`).
    """

    domain: str
    headless: bool
    recorded_port: int | None
    site_retirable: bool


def headless_check(domain: str, *, manager: NginxManager | None = None) -> HeadlessCheck:
    """
    Say whether an application is a worker and what clearing its port would offer.

    Reads the compose file and the site only: the console asks this before
    offering :func:`make_headless`, and offers removing the site only when
    the command would ask about it.

    Args:
        domain: The application.
        manager: The nginx manager; a fresh one by default.

    Returns:
        The check. ``headless`` is False for anything but a Compose stack
        whose compose file can be read and publishes no port.

    Raises:
        NoustError: Nothing is deployed at that domain.
    """
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}",
            details="Run 'noust list' to see what is deployed.",
        )
    headless = False
    if app.app_type == AppType.DOCKER_COMPOSE.value:
        deployer = stack_deployer(app)
        try:
            deployer._discover_compose_file()
        except DeploymentError:
            pass
        else:
            services = parse_services(deployer._load_compose_document())
            headless = bool(services) and is_headless_stack(services)
    return HeadlessCheck(
        domain=app.domain,
        headless=headless,
        recorded_port=app.port or None,
        site_retirable=headless and site_retirable(app.domain, manager=manager),
    )


def make_headless(
    domain: str,
    *,
    remove_site: bool,
    logger: Logger,
    runner: CommandRunner | None = None,
    manager: NginxManager | None = None,
) -> HeadlessChange:
    """
    Record a Compose worker as what it is: no port, and optionally no site.

    For the stacks a 1.x deploy registered with the default port (3000) and a
    site although they publish nothing, which every health reader then
    called down. Only ever done when asked: removing a site is the
    operator's call.

    Args:
        domain: The application.
        remove_site: Also remove its site, when Noust wrote it and it has no
            alias or redirect (see :func:`site_retirable`).
        logger: Where each step is reported.
        runner: The runner docker goes through.
        manager: The nginx manager; a fresh one by default.

    Returns:
        What was done.

    Raises:
        NoustError: Nothing is deployed at that domain.
        ValidationError: It is not a Compose stack, its compose file cannot
            be read, or it publishes a port.
        AppBusyError: Another operation is running on the application.
    """
    store = get_store()
    with app_lock(domain, "headless"):
        app = store.get_app(domain)
        if app is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        if app.app_type != AppType.DOCKER_COMPOSE.value:
            raise ValidationError(
                f"{domain} is a {app.app_type} application, not a Docker Compose stack",
                details="Only a Compose stack can be a worker without a port.",
            )
        deployer = stack_deployer(app, runner=runner)
        try:
            deployer._discover_compose_file()
        except DeploymentError as exc:
            raise ValidationError(
                f"The compose file of {domain} cannot be read", details=exc.details
            ) from exc
        services = parse_services(deployer._load_compose_document())
        if not services or not is_headless_stack(services):
            published = [
                f"{svc.name} ({', '.join(map(str, svc.ports))})" for svc in services if svc.ports
            ]
            raise ValidationError(
                f"{domain} publishes ports, so it is a web and keeps its port",
                details="Published: "
                + (", ".join(published) or "its compose file could not be read")
                + ". Remove the ports from the compose file and update it first if it is a worker.",
            )

        previous = app.port
        if previous is not None:
            app.port = None
            store.update_app(app)
            logger.substep(f"Port {previous} cleared: {domain} is judged by its containers")

        manager = manager or NginxManager()
        site: Literal["removed", "kept", "kept_operator", "kept_aliases", "absent"]
        if not manager.site_exists(domain):
            site = "absent"
        elif not remove_site:
            site = "kept"
        elif any(record.domain != domain for record in store.list_domains(domain)):
            logger.warning(
                f"{domain} answers on other names too; its site is kept. Remove them first: "
                f"noust domain list {domain}"
            )
            site = "kept_aliases"
        else:
            outcome = remove_app_site(domain, logger=logger, manager=manager)
            site = "removed" if outcome == "removed" else "kept_operator"
            if site == "removed":
                logger.substep(f"Site of {domain} removed; its certificate is left as it was")
        audit.record(
            "apps.headless",
            target=f"app:{app.domain}",
            details={"previous_port": previous, "site": site},
        )
        return HeadlessChange(domain=domain, previous_port=previous, site=site)


class ComposeHookExecutor:
    """
    Run a hook in a one-off container of a service's newly built image.

    ``docker compose run --rm --no-deps [-w workdir] --entrypoint "" <service>
    <argv>``: the service's own environment, networks and volumes, the image
    the deployment just built, and nothing recreated. The privileges are the
    ones the compose file already gives the service, which ``compose_guard``
    watches; a hook adds none.
    """

    def __init__(self, deployer: DockerComposeDeployer, *, with_dependencies: bool) -> None:
        """
        Args:
            deployer: The deployer whose stack the hook runs in.
            with_dependencies: Drop ``--no-deps``, so Compose starts the
                services the hook's service depends on. Only for a first
                deploy, where nothing serves yet and the database a migration
                needs is not running; an update never starts or recreates
                anything before its gate.
        """
        self.deployer = deployer
        self.with_dependencies = with_dependencies

    def execute(self, hook: Hook) -> CommandResult:
        """
        Run a hook to completion.

        Args:
            hook: The hook.

        Returns:
            Its outcome.

        Raises:
            ValidationError: It names a service the stack does not have, or
                names none and the stack builds no image.
        """
        names = [svc.name for svc in self.deployer.services]
        service = hook.service or self.deployer.default_hook_service()
        if service is None:
            raise ValidationError(
                "The hook names no service, and the stack builds no image to run it in",
                details="Add 'service: <name>' to the hook, naming the service whose image "
                f"should run it. The stack's services: {', '.join(names) or 'none'}.",
                field="service",
            )
        if service not in names:
            raise ValidationError(
                f"The hook names the service {service!r}, which the compose file does not have",
                details=f"The stack's services: {', '.join(names) or 'none'}.",
                field="service",
            )
        args = ["run", "--rm"]
        if not self.with_dependencies:
            args.append("--no-deps")
        if hook.workdir:
            args.extend(["-w", hook.workdir])
        # An empty entrypoint, so the hook is the whole command and not
        # arguments to whatever the image's entrypoint starts.
        args.extend(["--entrypoint", "", service, *hook.run])
        return self.deployer._run(self.deployer._compose(*args), timeout=hook.timeout)


def _paragraphs(*parts: str | None) -> str:
    """
    Join the non-empty parts of an error's details with a blank line.

    Args:
        parts: The parts, in order.

    Returns:
        The details.
    """
    return "\n\n".join(part for part in parts if part)


def _problem_list(problems: Sequence[str]) -> str:
    """
    Say what could not be put back.

    Args:
        problems: One line per step that failed.

    Returns:
        A paragraph listing them.
    """
    return "Putting back what was serving failed:\n" + "\n".join(f"  - {p}" for p in problems)


DeployerRegistry.register(DockerComposeDeployer)
