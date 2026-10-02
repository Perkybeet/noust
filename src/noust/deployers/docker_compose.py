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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import yaml

from noust.central import require_server_role
from noust.core import audit
from noust.core.applock import app_lock
from noust.core.config import Config
from noust.core.exceptions import (
    DeploymentError,
    DockerError,
    NoustError,
    RolledBackError,
    SecurityError,
    ValidationError,
)
from noust.core.fs import DryRunFileSystem, FileSystem
from noust.core.logger import Logger
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.core.store import App, AppStatus, AppType, DeploymentTrigger, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.helpers.compose_ports import (
    is_headless_stack,
    published_port,
    web_root_service,
)
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
from noust.deployers.releases import persistent_path
from noust.managers.cert_manager import CertManager
from noust.managers.nginx_manager import NginxManager
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

#: Characters an image repository name cannot hold.
_IMAGE_NAME_INVALID = re.compile(r"[^a-z0-9._-]+")

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
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
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
    name = _IMAGE_NAME_INVALID.sub("-", f"{project}-{service}".lower()).strip("._-")
    return f"{name or 'wasm'}:{PREVIOUS_TAG}"


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
                there is one, and ``memory_max_mb``, ``cpu_quota_percent`` and
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
        self.env_vars = env_vars or {}
        compose_file = options.get("compose_file")
        # Checked here, before anything is fetched, because configure() is the
        # one call the CLI, the API's deploy job and the auto deployer share.
        self.compose_file = str(compose_file_option(str(compose_file))) if compose_file else None
        self.compose_profiles = options.get("compose_profiles") or []
        self.port = port
        # A directory that already holds files - bind-mounted data among
        # them - is only deployed into when asked for (noust create --force).
        self.replace_existing = bool(options.get("replace_existing", False))
        self.deploy_target = None

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
        if stream:
            return self.runner.stream(
                command, on_line=self.logger.debug, cwd=self.app_path, timeout=timeout
            )
        result = self.runner.run(command, cwd=self.app_path, timeout=timeout)
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

    def deploy(self) -> bool:
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

            headless = self._is_headless()

            self.logger.step(4, total_steps, "Configuring environment")
            self._configure_environment()

            self.logger.step(5, total_steps, "Building Docker images")
            self._build_images()
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
            SourceManager(verbose=self.verbose, fs=self._fs),
            self.source,
            branch=self.branch,
            domain=self.domain,
            logger=self.logger,
        )
        self.logger.substep(f"Source fetched to {self.app_path}")

    def _discover_compose_file(self) -> None:
        """
        Find the compose file to use, inside the application directory.

        Raises:
            DeploymentError: When the named file is missing, when no default
                name is present, or when the file found resolves outside the
                application through a symlink.
        """
        if self.compose_file:
            self.compose_path = compose_file_in(self.app_path, self.compose_file)
            self.logger.substep(f"Using specified: {self.compose_file}")
            return

        for filename in COMPOSE_FILE_PRIORITY:
            if (self.app_path / filename).exists():
                # A default name is as much repository content as a named one.
                self.compose_path = compose_file_in(self.app_path, filename)
                self.logger.substep(f"Found: {filename}")
                return

        raise DeploymentError(
            "No Docker Compose file found",
            "Expected one of: " + ", ".join(COMPOSE_FILE_PRIORITY),
        )

    def _parse_compose_services(self, *, guard: bool = True) -> None:
        """
        Parse Docker Compose file and extract service definitions.

        Args:
            guard: Run the host privilege guard; an adoption runs it itself,
                once it knows whether the stack runs as the file says.
        """
        try:
            data = yaml.safe_load(self._compose_file_path().read_text(encoding="utf-8"))
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
            return yaml.safe_load(self._compose_file_path().read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            self.logger.debug(f"Compose file not inspected: {exc}")
            return None

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
                    accepted = self._refusals_in(yaml.safe_load(served))
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

    def _build_images(self, no_cache: bool = False) -> None:
        """
        Build Docker images defined in the compose file.

        Args:
            no_cache: Rebuild every layer from scratch.

        Raises:
            DockerError: When the build fails.
        """
        cmd = self._compose("build", "--no-cache") if no_cache else self._compose("build")

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
        config_path = builder.detect(self.app_path)
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

    def obtain_certificate(self) -> None:
        """
        Order a certificate covering every name the stack answers on, redirects included.

        Raises:
            CertificateError: When certbot fails, with its output.
        """
        obtain_app_certificate(
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
            )
        except (NoustError, sqlite3.Error) as e:
            self.logger.warning(f"Could not register app in store: {e}")

    def _recorder(self) -> DeploymentRecorder:
        """
        Build the recorder a deploy or an update writes history with.

        Returns:
            A recorder, built where every deployer's is.
        """
        return recorder_for(self, git_info=self._git_info())

    def _source_manager(self) -> SourceManager:
        """
        Build the source manager git goes through, on this deployer's runner.

        Returns:
            The manager.
        """
        return SourceManager(verbose=self.verbose, runner=self._runner, fs=self._fs)

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
        Get Docker Compose logs.

        Args:
            service: Specific service name (None for all).
            lines: Number of lines to show.

        Returns:
            Log output string.
        """
        cmd = self._compose("logs", "--tail", str(lines))
        if service:
            cmd.append(service)

        result = self._run(cmd)
        return result.stdout if result.success else result.stderr

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
        # What the gate and the hooks need to know of the stack: whether it
        # is a web at all, and which service a hook without one runs in.
        self.services = parse_services(document)
        hooks = self._resolve_hooks()
        self._resolve_leftover_relays()

        report("Recording what is serving")
        serving = self._record_serving()

        report("Rebuilding Docker images")
        try:
            self._build_images()
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
        listed = self._run(self._compose("ps", "-q"))
        if not listed.success:
            self.logger.warning(
                "Could not list the running containers; a failed update cannot put them back: "
                + (listed.stderr.strip() or listed.stdout.strip())
            )
            return ServingState(project=None, commit=self.previous_commit, images=())
        ids = [line.strip() for line in listed.stdout.splitlines()]
        ids = [cid for cid in ids if _CONTAINER_ID.match(cid)]
        if not ids:
            self.logger.substep("No containers are running: there is nothing to go back to")
            return ServingState(project=None, commit=self.previous_commit, images=())

        inspected = self._run(["docker", "inspect", "--format", _INSPECT_FORMAT, *ids])
        if not inspected.success:
            self.logger.warning(
                "Could not read the images of the running containers; a failed update cannot "
                f"put them back: {inspected.stderr.strip()}"
            )
            return ServingState(project=None, commit=self.previous_commit, images=())

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
        result = self._run(self._compose("up", "-d", "--remove-orphans"), timeout=BUILD_TIMEOUT)
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
            Compose's output verbatim, or why it could not be read.
        """
        result = self._run(self._compose("logs", "--tail", str(COMPOSE_LOG_LINES), "--no-color"))
        if result.success:
            return result.stdout.strip() or "(the containers printed nothing)"
        return f"(the containers' output could not be read: {result.stderr.strip()})"

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
        if self.compose_path is None:
            self._discover_compose_file()
        compose_path = self._compose_file_path()
        try:
            document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
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
            return []

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
        if failures:
            raise DockerError(
                "Some images kept for going back were not removed", "\n".join(failures)
            )
        return removed

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

    deployer = DockerComposeDeployer(verbose=verbose, runner=runner)
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
    return deployer


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
