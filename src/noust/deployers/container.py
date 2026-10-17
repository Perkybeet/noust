# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Container applications: a Dockerfile, an image from a registry, or Railpack.

Noust deploys what the host can run natively, and everything else as a
container (decision D1 of the 3.4 design). A container application is one of
three things, and runs the same way whichever it is:

- a repository with a Dockerfile, built by the Docker daemon;
- an image from a registry (``name[:tag][@sha256:digest]``), with an account
  when the registry is private;
- a repository built by Railpack, with no Dockerfile, when Railpack is on the
  server (``noust setup railpack``).

It runs as a Compose project **Noust writes itself** (see
:mod:`noust.deployers.helpers.container_compose`): one service, one image,
published on 127.0.0.1, behind the web server and the stack's own systemd
unit. So this deployer is a :class:`~noust.deployers.docker_compose.DockerComposeDeployer`
and reuses what that one already does (the unit, the site and its certificate,
the health gate, the containers' state, hooks that run in a container of the
new image, going back to what served): the only things it replaces are where
the compose file comes from and what "building" means.

**Every release is an image.** Each deployment gets a release id, builds or
pulls its image and names it ``noust-<app>:<release>``; the compose file of
that release names that image and nothing else. So going back to a release is
writing the compose file for it again and recreating the container: instant,
with no build and no network, behind the same health gate as an update. The
last ``keep_releases`` images are kept, the active one and the one a rollback
goes to always among them.

Building happens in the Docker daemon, outside the sandbox the other build
types use (decision D2): see :mod:`noust.deployers.helpers.container_image`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from noust.core import cancellation
from noust.core.container_store import (
    ContainerSettings,
    ContainerStore,
    forget_secrets,
)
from noust.core.exceptions import (
    DeploymentError,
    NoustError,
    RolledBackError,
)
from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.core.store import (
    App,
    AppStatus,
    AppType,
    DeploymentTrigger,
    ReleaseRecord,
    ReleaseStatus,
)
from noust.core.variables.model import BUILD
from noust.deployers.docker_compose import (
    DockerComposeDeployer,
    ServingState,
    parse_services,
)
from noust.deployers.helpers.compose_retention import ImageRelease
from noust.deployers.helpers.container_compose import (
    COMPOSE_FILE,
    SERVICE,
    ContainerPlan,
    build_document,
    image_tag,
    render_compose,
)
from noust.deployers.helpers.container_image import (
    DOCKERFILE_NAMES,
    ImageBuilder,
    exposed_port,
)
from noust.deployers.helpers.registration import StoreRegistrar
from noust.deployers.interface import StepReporter, UpdateResult
from noust.deployers.recorder import DeploymentRecorder, recorder_for
from noust.deployers.registry import DeployerRegistry
from noust.deployers.releases import ReleaseManager, release_order_key
from noust.managers import railpack
from noust.managers.app_mounts import MountManager
from noust.managers.service_manager import ResourceLimits, ServiceManager
from noust.validators.container import METHOD_DOCKERFILE, METHOD_IMAGE, METHOD_RAILPACK
from noust.validators.port import find_available_port

#: Root files of the stacks Noust has no deployer of its own for, and what each
#: is called. Railpack builds all of them, so when it is installed a repository
#: with one of these and no Dockerfile is a container application; when it is
#: not, ``noust inspect`` says to install it or add a Dockerfile.
RAILPACK_STACKS: tuple[tuple[str, str], ...] = (
    ("go.mod", "Go"),
    ("Cargo.toml", "Rust"),
    ("composer.json", "PHP"),
    ("Gemfile", "Ruby"),
    ("pom.xml", "Java (Maven)"),
    ("build.gradle", "Java (Gradle)"),
    ("build.gradle.kts", "Kotlin (Gradle)"),
    ("mix.exs", "Elixir"),
    ("deno.json", "Deno"),
    ("deno.jsonc", "Deno"),
)

#: How many releases an application keeps when its row says nothing.
_MIN_KEPT = 1


def pulls_image(app: App | None, store: ContainerStore | None = None) -> bool:
    """
    Tell whether a container application is an image from a registry.

    Such an application has no source to fetch: an update only pulls the image
    again.

    Args:
        app: The application's row.
        store: The container settings; the process-wide ones when None.

    Returns:
        True for a container application whose method is ``image``.
    """
    if app is None or app.app_type != AppType.CONTAINER.value:
        return False
    settings = (store or ContainerStore()).get(app.domain)
    return settings is not None and settings.method == METHOD_IMAGE


class ContainerDeployer(DockerComposeDeployer):
    """
    Deployer of applications that run as one container.

    Attributes:
        settings: How the application is built and run. Set by
            :meth:`configure`, or read from the store for an update.
        release_id: The release this deployment creates, once it has one.
    """

    APP_TYPE = "container"
    # A container application keeps one image per release already.
    RETAINS_RELEASES = False
    DISPLAY_NAME = "Container"

    # Last of all: a repository with a Dockerfile is a container only when
    # nothing that knows its framework claimed it first.
    DETECTION_PRIORITY = 5
    DEFAULT_PORT = 8080

    #: Where the search for a free port on 127.0.0.1 starts, as for every type.
    DEFAULT_PORT_START = 3000

    DETECTION_FILES: ClassVar[list[str]] = [*DOCKERFILE_NAMES]

    #: Programs a deployment of this type needs on the server.
    SYSTEM_DEPS: ClassVar[tuple[str, ...]] = ("docker",)

    #: A release is an image, not a directory under ``releases/``.
    SUPPORTS_RELEASES = False

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for every docker invocation.
            fs: Filesystem every change goes through.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        self.settings = ContainerSettings()
        self.release_id: str | None = None
        self._containers = ContainerStore(self.store)
        self._limits = ResourceLimits()
        self._limits_given = False
        self._initial_health: tuple[str | None, str | None, int | None] | None = None
        self._initial_marks: dict[str, bool] = {}
        self._preview_parent: str | None = None
        # Whether the operator chose the method, or Noust may pick Railpack
        # for a repository that has no Dockerfile.
        self._method_explicit = False
        # The release that serves before an update, which a failed one goes back to.
        self._serving_release: str | None = None

    # -- Detection and configuration -----------------------------------------

    def detect(self, path: Path) -> bool:
        """
        Detect a repository that is built into a container.

        Args:
            path: Path to check.

        Returns:
            True when it has a Dockerfile or a Containerfile. Railpack does not
            take part: whether it is installed is a fact about this server, and
            what a repository is must not depend on it (see
            :meth:`builds_with_railpack`).
        """
        return any((path / name).is_file() for name in DOCKERFILE_NAMES)

    @classmethod
    def builds_with_railpack(cls, path: Path, runner: CommandRunner | None = None) -> bool:
        """
        Tell whether Railpack can build a repository nothing else deploys.

        Asked only once :meth:`detect` and every other deployer declined it:
        Railpack is installed here and the repository is one of the stacks
        Noust has no deployer of its own for.

        Args:
            path: The repository.
            runner: The runner to look for Railpack through.

        Returns:
            True when it is a container application built by Railpack.
        """
        run = runner if runner is not None else get_runner()
        if not run.exists(railpack.BINARY_NAME):
            return False
        return any((path / marker).is_file() for marker, _ in RAILPACK_STACKS)

    @classmethod
    def inspection_note(cls, path: Path) -> str:
        """
        Say how a repository would be built, for ``noust inspect``.

        Args:
            path: The repository.

        Returns:
            A sentence naming the Dockerfile, or Railpack.
        """
        if cls().detect(path):
            return "Noust can deploy this as a container, built from its Dockerfile."
        return "Noust can deploy this as a container, built by Railpack: it has no Dockerfile."

    @classmethod
    def inspected_port(cls, path: Path) -> int | None:
        """
        Read the port a repository's Dockerfile says its process listens on.

        Args:
            path: The repository.

        Returns:
            The port, or None.
        """
        for name in DOCKERFILE_NAMES:
            try:
                return exposed_port((path / name).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
        return None

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
        Configure the deployment.

        Args:
            domain: Target domain name.
            source: Git URL or local path of the repository; for an image from
                a registry, the image reference.
            port: The port on 127.0.0.1 the web server forwards to.
            webserver: ``nginx``: a container is only ever served through it.
            ssl: Whether to obtain a certificate.
            branch: Git branch.
            env_vars: Environment variables.
            app_path: Override the directory the application lives in.
            package_manager: Ignored; images are built by docker.
            include_www: Ignored; one hostname.
            trigger: What initiated this deployment, recorded in the history.
            **options: ``container`` holds the :class:`ContainerSettings` (or
                the mapping of them a job carries); without it a Dockerfile in
                the repository is built, or Railpack builds it when there is
                none and Railpack is installed. ``memory_max_mb``,
                ``cpu_quota_percent`` and ``tasks_max`` become the container's
                limits; ``resource_limits_given``, ``initial_health``,
                ``env_secret_marks`` and ``preview_parent`` are what every
                deployer takes of a new application.

        Raises:
            DeploymentError: ``webserver`` is ``apache``.
            ValidationError: The settings or a limit are not valid.
        """
        limits = ResourceLimits(
            memory_max_mb=options.pop("memory_max_mb", None),
            cpu_quota_percent=options.pop("cpu_quota_percent", None),
            tasks_max=options.pop("tasks_max", None),
        ).validated()
        super().configure(
            domain,
            source,
            port=port,
            webserver=webserver,
            ssl=ssl,
            branch=branch,
            env_vars=env_vars,
            app_path=app_path,
            package_manager=package_manager,
            include_www=include_www,
            trigger=trigger,
            **options,
        )
        raw = options.get("container")
        self._method_explicit = raw is not None
        if isinstance(raw, ContainerSettings):
            settings = raw
        elif isinstance(raw, Mapping):
            settings = ContainerSettings.from_mapping(raw)
        else:
            settings = ContainerSettings()
        self.settings = settings.checked()
        if self.settings.method == METHOD_IMAGE:
            self.source = self.settings.image or source
        self.compose_file = COMPOSE_FILE
        self._limits = limits
        self._limits_given = bool(options.get("resource_limits_given", False))
        self._initial_health = options.get("initial_health")
        self._initial_marks = dict(options.get("env_secret_marks") or {})
        self._preview_parent = options.get("preview_parent")
        self.release_id = None

    # -- Plan, image and compose file ----------------------------------------

    def _secrets(self) -> SecretStore:
        """
        Returns:
            The secret store beside this deployer's store, on its seams.
        """
        return SecretStore(
            root=self.store.db_path.parent / "secrets", fs=self._fs, runner=self._runner
        )

    def _images(self) -> ImageBuilder:
        """
        Returns:
            What builds, pulls and removes this application's images.
        """
        return ImageBuilder(
            runner=self._runner,
            fs=self._fs,
            logger=self.logger,
            app_name=self.app_name,
            domain=self.domain,
            app_path=self.app_path,
            secrets=self._secrets(),
            work_dir=self.store.db_path.parent / "container" / self.app_name,
            build_timeout=self.build_timeout(),
        )

    def _host_port(self) -> int:
        """
        Returns:
            The port on 127.0.0.1 the container is published on.
        """
        return self._get_primary_port()

    def _plan(self, release_id: str) -> ContainerPlan:
        """
        Gather what the compose file of a release is made from.

        Args:
            release_id: The release the file is for.

        Returns:
            The plan.
        """
        return ContainerPlan(
            app_name=self.app_name,
            domain=self.domain,
            release_id=release_id,
            host_port=self._host_port(),
            settings=self.settings,
            limits=self._limits,
            bind_mounts=MountManager(store=self.store).bind_mounts(self.domain),
        )

    def _write_compose(self, release_id: str) -> None:
        """
        Write the compose file of a release where the unit reads it.

        Args:
            release_id: The release whose image the file names.
        """
        self.fs.write_text(self._compose_file_path(), render_compose(self._plan(release_id)))

    def _ensure_release(self) -> str:
        """
        Name this deployment's release, once.

        Returns:
            The release id: the stamp, the commit it builds (``nogit`` for an
            image) and a sequence, as the other layouts name theirs.
        """
        if self.release_id is None:
            commit = self._git_info()()[0] if self.settings.builds else None
            app = self.store.get_app(self.domain)
            taken = (
                {row.id for row in self.store.list_releases(app.id)}
                if app is not None and app.id is not None
                else set()
            )
            self.release_id = ReleaseManager(
                self.app_path, fs=self._fs, runner=self._runner, logger=self.logger
            ).next_release_id(commit, taken)
        return self.release_id

    def _plan_release(self) -> str | None:
        """
        Name the release this deployment is, which a container application does before it builds.

        Returns:
            The release id: the image's name is made of it.
        """
        return self._ensure_release()

    def _choose_method(self) -> None:
        """
        Let Railpack build a repository that has no Dockerfile, when it can.

        Only when the operator did not choose a method: naming one is a decision
        this does not second-guess.
        """
        if self._method_explicit or self.settings.method != METHOD_DOCKERFILE:
            return
        if any((self.app_path / name).is_file() for name in DOCKERFILE_NAMES):
            return
        if self.runner.exists(railpack.BINARY_NAME):
            self.settings = replace(self.settings, method=METHOD_RAILPACK)
            self.logger.substep("No Dockerfile in the repository: Railpack builds the image")

    def _infer_port(self) -> None:
        """Read where the process listens from the Dockerfile, when nobody said."""
        if self.settings.container_port is not None or self.settings.method != METHOD_DOCKERFILE:
            return
        try:
            text = self._images().locate_dockerfile(self.settings).read_text(encoding="utf-8")
        except (NoustError, OSError, UnicodeDecodeError) as exc:
            self.logger.debug(f"The Dockerfile was not read for its port: {exc}")
            return
        port = exposed_port(text)
        if port is not None:
            self.settings = replace(self.settings, container_port=port)
            self.logger.substep(f"The Dockerfile exposes port {port}")

    # -- Deploying ------------------------------------------------------------

    def deploy(self) -> bool:
        """
        Run the full deployment.

        Returns:
            True when the application ended up deployed.

        Raises:
            DeploymentError: A step failed; a new application is removed, with
                the secrets it was given.
            AppBusyError: Another operation is running on the application.
        """
        if not self.port:
            # The port on 127.0.0.1 is Noust's to give: a container publishes
            # the one the web server is told, there is no compose file to read it from.
            self.port = find_available_port(
                preferred=self.DEFAULT_PORT_START, exclude=self.store.ports_owned_by_apps()
            )
            if not self.port:
                raise DeploymentError(
                    "No available port found",
                    details="Free a port in the range Noust allocates from, or pass --port.",
                )
        deployed = False
        try:
            deployed = super().deploy()
            return deployed
        finally:
            # What was given for an application that never came to exist is
            # not kept: a password nobody can see is a password to forget.
            if not deployed and self.store.get_app(self.domain) is None:
                forget_secrets(self.app_name, self._secrets())

    def _fetch_source(self) -> None:
        """Fetch the repository; an image from a registry has nothing to fetch."""
        if self.settings.method != METHOD_IMAGE:
            super()._fetch_source()
            return
        self.fs.make_dir(self.app_path)
        self.logger.substep("The image comes from a registry: there is no source to fetch")

    def _discover_compose_file(self) -> None:
        """Point at the compose file Noust writes, which no repository supplies."""
        self.compose_file = COMPOSE_FILE
        self.compose_path = self.app_path / COMPOSE_FILE

    def _compose(self, *args: str, project: str | None = None) -> list[str]:
        """
        Build a ``docker compose`` command for this application.

        The file is always the generated one, so any caller (the logs, a
        status, a hook) can ask without having discovered it first.

        Args:
            args: Subcommand and its arguments.
            project: The project to address instead of the derived one.

        Returns:
            The full argument vector.
        """
        if self.compose_path is None:
            self._discover_compose_file()
        return super()._compose(*args, project=project)

    def _parse_compose_services(self, *, guard: bool = True) -> None:
        """
        Read the services this deployment will run, from the plan and not a file.

        Nothing in the compose file comes from the repository, so there is no
        privilege to guard against; the service is the one this module writes.

        Args:
            guard: Ignored for the reason above.
        """
        self._choose_method()
        release = self._ensure_release()
        self.services = parse_services(build_document(self._plan(release)))
        self.logger.substep(f"Release {release}: {self.settings.method}")

    def _check_update_privileges(self, document: Any) -> None:
        """
        Nothing to check: the compose file is Noust's own.

        Args:
            document: Ignored.
        """

    def _configure_environment(self) -> None:
        """Write the environment, and make sure the file the container reads exists."""
        from noust.core.fs import SECRET_MODE

        env_file = self.app_path / ".env"
        if env_file.is_symlink():
            # A repository is untrusted input and Compose reads this file as
            # root: a link in it would put any file of this server into the
            # container's environment. What Noust writes is a file of its own.
            self.logger.warning(f"{env_file} is a link in the repository: it is not followed")
            self.fs.remove(env_file)
        super()._configure_environment()
        if not env_file.exists():
            self.fs.write_text(env_file, "", mode=SECRET_MODE)

    def _build_images(self, no_cache: bool = False) -> None:
        """
        Build or pull the image of this release, and write its compose file.

        Args:
            no_cache: Build without BuildKit's layer cache and pull the base
                images again. An image from a registry is always pulled.

        Raises:
            DeploymentError: A path leaves the repository, there is no
                Dockerfile or Railpack is not installed.
            DockerError: The build or the pull failed.
        """
        release = self._ensure_release()
        tag = image_tag(self.app_name, release)
        images = self._images()
        # An operator can stop the pull or the build: nothing of what serves is touched until the
        # compose file below is written.
        with cancellation.interruptible("building the image"):
            if self.settings.method == METHOD_IMAGE:
                pulled = images.pull(self.settings, tag)
                changes: dict[str, Any] = {"digest": pulled.digest}
                if self.settings.container_port is None and len(pulled.exposed_ports) == 1:
                    changes["container_port"] = pulled.exposed_ports[0]
                    self.logger.substep(f"The image exposes port {pulled.exposed_ports[0]}")
                self.settings = replace(self.settings, **changes)
            else:
                self._infer_port()
                images.build(
                    self.settings,
                    tag,
                    no_cache=self._builds_without_cache(no_cache),
                    variables=self._variables.build_environment(),
                )
        self._write_compose(release)

    def _start_and_verify(self) -> None:
        """
        Start the container behind the health gate.

        The first start is judged exactly as an update is: a container that
        does not answer fails the deployment, and a new application is undone.
        The application is registered first, in the state of one being
        deployed, so the gate asks the check the operator chose.

        Raises:
            DeploymentError: The container did not pass the gate.
        """
        self._register_app(status=AppStatus.DEPLOYING.value)
        services = ServiceManager(verbose=self.verbose, runner=self._runner)
        healthy, evidence = self._activate(lambda: services.start(self.app_name))
        if not healthy:
            raise DeploymentError(f"{self.domain} did not pass its health check", details=evidence)
        self.logger.substep("The container answers")

    def _register_app(self, status: str = AppStatus.RUNNING.value) -> None:
        """
        Register the application, its container settings and this release.

        Args:
            status: The state to record; the final call says it is running.
        """
        try:
            row = StoreRegistrar(self.store).register_app(
                domain=self.domain,
                app_type=AppType.CONTAINER.value,
                source=self.source,
                branch=self.branch if self.settings.builds else None,
                port=self._host_port(),
                app_path=self.app_path,
                webserver=self.webserver,
                ssl_enabled=self.ssl,
                status=status,
                is_static=False,
                env_vars={
                    name: value
                    for name, value in self.env_vars.items()
                    if self._env_audience.get(name) != BUILD
                },
                memory_max_mb=self._limits.memory_max_mb,
                cpu_quota_percent=self._limits.cpu_quota_percent,
                tasks_max=self._limits.tasks_max,
                limits_given=self._limits_given,
                preview_parent=self._preview_parent,
                env_secret_marks=self._initial_marks,
                initial_health=self._initial_health,
            )
            # What the variables given at creation are made of and for can be recorded now
            # that the application has a row.
            self._variables.flush()
            # The release is the one that serves only once the gate said so.
            if status == AppStatus.RUNNING.value:
                self._keep_state(row)
        except (NoustError, sqlite3.Error) as exc:
            self.logger.warning(f"Could not register app in store: {exc}")

    def _recorder(self) -> DeploymentRecorder:
        """
        Build the recorder, which also says which release a deployment built.

        Returns:
            A recorder.
        """
        return recorder_for(self, git_info=self._git_info(), release_id=lambda: self.release_id)

    # -- Releases ------------------------------------------------------------

    def _keep_state(self, app: App) -> None:
        """
        Record this release as the one that serves, and what the deployment learned.

        A new application's settings are written whole; an existing one's only
        where the deployment found out something (the digest it pulled, the
        port the image exposes), through the setters, so what an operator
        changed while it ran is not written back over.

        Args:
            app: The application's row.
        """
        if app.id is None or self.release_id is None:
            return
        actor = self.trigger
        stored = self._containers.get(self.domain)
        if stored is None:
            self._containers.save(self.domain, self.settings, actor=actor)
        else:
            if stored.digest != self.settings.digest:
                self._containers.set_digest(self.domain, self.settings.digest)
            if stored.container_port != self.settings.container_port:
                self._containers.set_container_port(self.domain, self.settings.container_port)
        self._record_release(app.id, ReleaseStatus.BUILT)
        self.store.mark_release_active(app.id, self.release_id)
        self._prune(app)

    def _record_release(self, app_id: int, status: ReleaseStatus) -> None:
        """
        Remember this release, when the store does not know it yet.

        Args:
            app_id: The application.
            status: The status to record it with.
        """
        if self.release_id is None or self.store.get_release(app_id, self.release_id) is not None:
            return
        commit = self._git_info()()[0] if self.settings.builds else None
        self.store.record_release(
            ReleaseRecord(
                id=self.release_id,
                app_id=app_id,
                git_commit=commit[:7] if commit else None,
                created_at=datetime.now(timezone.utc).isoformat(),
                status=status.value,
                path=image_tag(self.app_name, self.release_id),
            )
        )

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
        return self._prune(app)

    def _prune(self, app: App) -> list[str]:
        """
        Remove the images (and the records) of releases past the retention.

        The release that serves and the one a rollback goes to are kept
        whatever the retention is, and so is any image no record has aged out.

        Args:
            app: The application's row.

        Returns:
            The ids of the releases whose record was forgotten.
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
        keep = set(ordered[: max(app.keep_releases, _MIN_KEPT)])
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
        images = self._images()
        try:
            present = images.present_releases()
        except NoustError as exc:
            self.logger.warning(f"Old images were not pruned: {exc.message}")
            return []
        for failure in images.remove_images(sorted(present - keep)):
            self.logger.warning(f"An old image was not removed: {failure}")
        forgotten = [row.id for row in rows if row.id not in keep]
        if not self._rehearsing():
            self.store.delete_releases(app.id, forgotten)
        return forgotten

    # -- Updating ------------------------------------------------------------

    def _load_applied(self) -> None:
        """
        Read what is deployed: the settings, the port, the limits, the release that serves.

        Raises:
            DeploymentError: The application is not deployed, or has no
                container settings.
        """
        app = self.store.get_app(self.domain)
        stored = self._containers.get(self.domain)
        if app is None or stored is None:
            raise DeploymentError(
                f"{self.domain} has no container settings",
                details="It is not a container application Noust deployed. Create it with: "
                f"noust create --type container -d {self.domain} ...",
            )
        self.settings = stored.checked()
        self.port = app.port
        self.webserver = app.webserver
        self.ssl = app.ssl_enabled
        self.branch = app.branch
        self.source = app.source
        self._limits = ResourceLimits.of(app)
        self._is_new_deployment = False
        self.release_id = None
        active = next(
            (
                row
                for row in self.store.list_releases(app.id or 0)
                if row.status == ReleaseStatus.ACTIVE.value
            ),
            None,
        )
        self._serving_release = active.id if active is not None else None
        self._discover_compose_file()

    def update(self, on_step: StepReporter | None = None) -> UpdateResult:
        """
        Build or pull a new release, and put it behind the health gate.

        What serves is recorded first. A build or a pull that fails recreates
        nothing; a container that does not pass the gate is replaced by the
        one that served, from its image, and the update fails with the
        probes, the unit's journal and the container's own output.

        Args:
            on_step: Called as each step begins.

        Returns:
            What was done.

        Raises:
            DeploymentError: Not a container application, or the new
                container did not pass the gate (what served is back).
            DockerError: The build or the pull failed.
        """
        self._load_applied()
        return super().update(on_step)

    def _update_steps(self, report: StepReporter) -> UpdateResult:
        """
        Run the update, then record how the release ended.

        Args:
            report: Called as each step begins.

        Returns:
            What was done.
        """
        if not self._compose_file_path().exists() and self._serving_release:
            # The generated file is gone (a restored backup, a hand edit):
            # the release that serves is what it says again.
            self._write_compose(self._serving_release)
        try:
            result = super()._update_steps(report)
        except NoustError as exc:
            self._release_failed(exc)
            raise
        app = self.store.get_app(self.domain)
        if app is not None:
            try:
                self._keep_state(app)
            except (NoustError, sqlite3.Error) as exc:
                self.logger.warning(f"Could not record release {self.release_id}: {exc}")
        return result

    def _release_failed(self, error: NoustError) -> None:
        """
        Record that the release being activated did not pass, and drop its image.

        Args:
            error: What stopped the update.
        """
        app = self.store.get_app(self.domain)
        if app is None or app.id is None or self.release_id is None:
            return
        if cancellation.is_cancellation(error):
            # A release an operator stopped was never made: no failed row, and no half-built
            # image left behind.
            for failure in self._images().remove_images([self.release_id]):
                self.logger.debug(f"The stopped image was kept: {failure}")
            return
        self._record_release(app.id, ReleaseStatus.FAILED)
        self.store.set_release_status(app.id, self.release_id, ReleaseStatus.FAILED.value)
        if isinstance(error, RolledBackError):
            # What served is back: the image that did not pass has no use.
            for failure in self._images().remove_images([self.release_id]):
                self.logger.debug(f"The failed image was kept: {failure}")

    def _put_back_files(self, serving: ServingState) -> list[str]:
        """
        Put the tree, the image names and the compose file back as they were.

        Args:
            serving: What served.

        Returns:
            What could not be put back, one line each.
        """
        problems = super()._put_back_files(serving)
        if self._serving_release:
            try:
                self._write_compose(self._serving_release)
            except OSError as exc:
                problems.append(f"The compose file of release {self._serving_release}: {exc}")
        return problems

    # -- Going back ------------------------------------------------------------

    def release_images(self) -> list[ImageRelease]:
        """
        List the releases of the application, newest first.

        Returns:
            One entry per release the store remembers, saying whether its
            image is still on this host (only then can it be activated).

        Raises:
            DeploymentError: The application is not deployed.
            DockerError: Docker could not list its images.
        """
        app = self.store.get_app(self.domain)
        if app is None or app.id is None:
            raise DeploymentError(f"{self.domain} is not deployed")
        present = self._images().present_releases()
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
                present=row.id in present,
            )
            for row in rows
        ]

    def activate_release(self, release_id: str, *, went_back: bool) -> None:
        """
        Make a release the one that serves, by writing its compose file and recreating.

        Nothing is built or pulled: the image is the one the release made.
        It passes the health gate like any activation; one that does not is
        recorded as failed and the release that served is recreated, so the
        operator ends where they started.

        Args:
            release_id: The release to activate.
            went_back: The release is older than the one it replaces, so the
                one it replaces is recorded as rolled back.

        Raises:
            DeploymentError: The image is no longer on this host, or the
                release did not pass the gate (the one that served is back by
                then, and a :class:`~noust.core.exceptions.RolledBackError`
                says so).
        """
        self._load_applied()
        app = self.store.get_app(self.domain)
        if app is None or app.id is None:
            raise DeploymentError(f"{self.domain} is not deployed")
        if release_id not in self._images().present_releases():
            raise DeploymentError(
                f"The image of release {release_id} is no longer on this server",
                details="It was pruned: rebuild its commit instead "
                f"(noust update {self.domain} --commit COMMIT).",
            )
        previous = self._serving_release
        self.release_id = release_id
        # The container is told which release it is, and its commit.
        self._write_predefined(of_release=self.store.get_release(app.id, release_id))
        self._write_compose(release_id)
        self.logger.substep(
            f"Activating release {release_id}" + (f" (was {previous})" if previous else "")
        )
        healthy, evidence = self._activate(self._recreate)
        if healthy:
            self.store.mark_release_active(app.id, release_id)
            if went_back and previous is not None:
                self.store.set_release_status(app.id, previous, ReleaseStatus.ROLLED_BACK.value)
            return
        self.store.set_release_status(app.id, release_id, ReleaseStatus.FAILED.value)
        if previous is None:
            raise DeploymentError(
                f"Release {release_id} did not pass its health check", details=evidence
            )
        self.logger.warning(f"Release {release_id} did not pass its health check")
        for problem in self._restore_predefined():
            self.logger.warning(problem)
        self._write_compose(previous)
        restored, after = self._activate(self._recreate)
        if restored:
            raise RolledBackError(
                f"Release {release_id} did not pass its health check; release {previous} "
                "is active again",
                details=evidence,
            )
        raise DeploymentError(
            f"Release {release_id} did not pass its health check; release {previous} is "
            "active again but is not answering either",
            details=f"{evidence}\n\nAfter going back:\n{after}",
        )

    # -- Limits --------------------------------------------------------------

    def change_limits(self, limits: ResourceLimits, *, restart: bool) -> tuple[bool, str]:
        """
        Give the container other limits: the compose file again, and the container recreated.

        Args:
            limits: The limits; None removes one.
            restart: Recreate the container now, behind the health gate. Without
                it the file is rewritten and the container keeps what it has
                until the unit starts it again.

        Returns:
            Whether the container answers under them (always True without
            ``restart``), and the evidence when it does not. When it does not,
            the previous limits are written back and the container recreated.

        Raises:
            DeploymentError: The application has no release that serves.
        """
        self._load_applied()
        release = self._serving_release
        if release is None:
            raise DeploymentError(
                f"{self.domain} has no active release",
                details="Deploy it first: noust update " + self.domain,
            )
        previous = self._limits
        self._limits = limits
        self._write_compose(release)
        if not restart:
            return True, ""
        healthy, evidence = self._activate(self._recreate)
        if healthy:
            return True, ""
        self._limits = previous
        self._write_compose(release)
        self._activate(self._recreate)
        return False, evidence

    # -- Taking it down ------------------------------------------------------

    def _rollback(self) -> None:
        """Undo a failed first deployment, images and secrets included."""
        super()._rollback()
        try:
            images = self._images()
            images.remove_images(sorted(images.present_releases()))
        except NoustError as exc:
            self.logger.debug(f"Image cleanup failed: {exc.message}")
        forget_secrets(self.app_name, self._secrets())

    def remove_kept_images(self) -> list[str]:
        """
        Remove every image this application has: its releases and the tags updates keep.

        Returns:
            The references removed.

        Raises:
            DockerError: Docker could not list its images.
        """
        images = self._images()
        releases = sorted(images.present_releases())
        failures = images.remove_images(releases)
        if failures:
            self.logger.warning("Some images were not removed: " + "; ".join(failures))
        removed = [image_tag(self.app_name, release) for release in releases]
        try:
            removed.extend(super().remove_kept_images())
        except DeploymentError as exc:
            self.logger.debug(f"The tags updates keep were not looked for: {exc.message}")
        return removed

    def default_hook_service(self) -> str | None:
        """
        Returns:
            The one service there is, which every hook runs in.
        """
        return SERVICE


DeployerRegistry.register(ContainerDeployer)
