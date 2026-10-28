# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a deployer is made of and where it works: its collaborators, the layout it is on and
the three paths (application, build, runtime), with the abstract members a type fills in.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar, Literal

from noust.core import delivery
from noust.core.appdir.layout import RELEASES, choose_layout
from noust.core.appdir.releases import CURRENT_LINK, ReleaseManager, persistent_path
from noust.core.config import Config
from noust.core.exceptions import NoustError, ValidationError
from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, DeploymentTrigger, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.helpers import (
    EnvManager,
    NginxConfigBuilder,
    PackageManagerHelper,
    PathResolver,
    PrismaHelper,
)
from noust.deployers.helpers.build_config import (
    BuildConfig,
    project_dir,
    refuse_unsupported,
    resolve_build,
)
from noust.deployers.helpers.deployment_variables import DeploymentVariables
from noust.deployers.helpers.hooks import DeploymentHooks
from noust.deployers.helpers.nginx_config import NginxAdvancedConfig
from noust.deployers.helpers.process_declaration import procfile_honoured
from noust.deployers.helpers.processes import WEB, ProcessSet, resolve_processes
from noust.deployers.helpers.project_file import parse_build_document
from noust.deployers.helpers.registration import StoreRegistrar
from noust.deployers.helpers.release_build import StagedRelease
from noust.deployers.helpers.sandbox import BuildPhase, SandboxState
from noust.deployers.helpers.seed_files import seeded_paths
from noust.deployers.interface import AppDeployer
from noust.deployers.recorder import CapturingLogger
from noust.managers.cert_manager import CertManager
from noust.managers.runtimes import RuntimeChoice, RuntimeManager
from noust.managers.service_manager import ServiceManager
from noust.managers.source_manager import SourceManager

# Type for package managers
PackageManager = Literal["npm", "pnpm", "bun", "yarn", "auto"]


#: Deadlines for the two commands that legitimately take minutes. Generous, but
#: finite: an npm install that wedges on a private registry must eventually fail
#: the deploy instead of holding the terminal forever.
INSTALL_TIMEOUT = delivery.DEFAULT_INSTALL_TIMEOUT


BUILD_TIMEOUT = delivery.DEFAULT_BUILD_TIMEOUT


#: Everything else in a deployer is a quick local command.
COMMAND_TIMEOUT = 300


#: A local, read-only ``git log`` never touches the network; this is generous
#: only against a checkout on a badly loaded disk.
GIT_LOG_TIMEOUT = 10


#: Failures while writing release bookkeeping. The release on disk is the
#: truth; a row that could not be written is reported, never fatal.
_RECORDING_ERRORS = (NoustError, sqlite3.Error)


def _dotenv_files(tree: Path) -> list[Path]:
    """
    List the dotenv files that are really in a directory.

    Args:
        tree: A release, or ``shared/``.

    Returns:
        Regular ``.env*`` files at its top level. A release's ``.env`` is a
        link into ``shared/`` and is left out: it is protected where it lives.
    """
    if not tree.is_dir():
        return []
    return sorted(path for path in tree.glob(".env*") if path.is_file() and not path.is_symlink())


class DeployerState(AppDeployer):
    """
    What a deployer is made of and where it works: its collaborators, the layout it is on and
    """

    # Deployer identification
    APP_TYPE: str = "base"
    DISPLAY_NAME: str = "Base Application"

    # Files used to detect this app type
    DETECTION_FILES: ClassVar[list[str]] = []
    DETECTION_PATTERNS: ClassVar[list[str]] = []

    # Default port
    DEFAULT_PORT: int = 3000

    # System dependencies
    SYSTEM_DEPS: ClassVar[list[str]] = []

    #: Whether this deployer can build releases. Monorepo and docker-compose
    #: do not derive from this class and do not have the attribute, which the
    #: layout choice reads as False.
    SUPPORTS_RELEASES: ClassVar[bool] = True

    #: The keys of the ``build:`` block this type honours (see
    #: :mod:`noust.deployers.helpers.build_config`); a block that sets another
    #: is refused naming it, never half applied. A type that runs a process
    #: takes the commands and the base directory; one that serves files adds
    #: ``publish_directory``.
    BUILD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"base_directory", "install", "build", "start", "runtime"}
    )

    #: The runtimes ``build.runtime`` may pin for this type, and the one its
    #: version files are read for (``.nvmrc`` for Node, ``composer.json`` for
    #: PHP); None for a type that has none.
    RUNTIME_LANGUAGES: ClassVar[frozenset[str]] = frozenset()
    PRIMARY_RUNTIME: ClassVar[str | None] = None

    #: Class-level defaults for the layout state, so an instance assembled
    #: without ``__init__`` (as some tests do) still behaves in place.
    _layout: str | None = None
    _staged: StagedRelease | None = None
    _app_record: App | None = None
    _replace_existing: bool = False
    _gate_in_place: bool = False
    #: The build sandbox, per deployment (see _execution). Class-level too, for
    #: the instances tests assemble without __init__.
    _phases: tuple[BuildPhase, ...] = ()
    _sandbox_forced: bool = False
    _sandbox_regime: SandboxState | bool | None = False
    _sandbox_cache: Path | None = None
    _sandbox_tree: Path | None = None
    #: Deploy hooks (3.2): what this deployment's hooks are and did. None
    #: until asked (see deploy_hooks), for the instances tests assemble
    #: without __init__.
    _deploy_hooks_state: DeploymentHooks | None = None
    #: The build block (4.0) that applies to the tree being deployed, the tree
    #: it was read from, and the runtime chosen for it. None until asked (see
    #: build_config), for the same instances.
    _build_block: BuildConfig | None = None
    _build_block_tree: Path | None = None
    #: The operator's block given at creation (``noust create --build-file``):
    #: what the first deployment builds with, and what is then stored.
    _initial_build: BuildConfig | None = None
    _runtime_state: RuntimeChoice | None = None
    _runtime_tree: Path | None = None
    _runtime_manager: RuntimeManager | None = None
    #: The process types the project declares (A-52), what its ``web`` process changed of the
    #: build block, and whether this application honours a ``Procfile``: decided once per
    #: deployment, because deciding it reads the store.
    _process_set: ProcessSet | None = None
    _start_from_process: bool = False
    _procfile_decision: bool | None = None

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Initialize the deployer.

        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for install and build commands. Defaults
                to the process-wide runner, which is what enforces --dry-run.
            fs: Filesystem every change goes through. Defaults to the
                process-wide one, for the same reason.
        """
        self.verbose = verbose
        self.config = Config()
        # Capturable so a deployment recording can mirror the full build
        # output, including the detail a non-verbose console suppresses.
        self.logger = CapturingLogger(verbose=verbose)
        self.store = get_store()
        self._runner = runner
        self._fs = fs

        # Managers are built on first use. Detection instantiates every
        # registered deployer just to ask "is this yours?", and constructing
        # four managers and a store connection to answer no is waste that also
        # made a deployer impossible to create without a working system.
        self._source_manager: SourceManager | None = None
        self._service_manager: ServiceManager | None = None
        self._cert_manager: CertManager | None = None
        self._registrar: StoreRegistrar | None = None

        # Helpers
        self._pm_helper = PackageManagerHelper(logger=self.logger, runner=self.runner)
        self._path_resolver = PathResolver(logger=self.logger)
        self._prisma_helper: PrismaHelper | None = None  # Initialized after app_path is set

        # Deployment configuration. These are empty, not None, until configure()
        # runs: every one of them is fed straight to a manager that requires a
        # value, and "Optional everywhere" only moved the check to twenty call
        # sites that did not make it.
        self.domain: str = ""
        self.source: str = ""
        self.port: int = self.DEFAULT_PORT
        self.app_path: Path = Path()
        self.app_name: str = ""
        self.webserver: str = "nginx"
        self.ssl: bool = True
        self.include_www: bool = False
        self.branch: str | None = None
        self.env_vars: dict[str, str] = {}
        #: A first deploy's database variables; see ``configure``.
        self._database_env: dict[str, str] = {}
        #: Files the deployment is seeded with once (relative path to content)
        #: and the permissions each is written with; see ``configure``.
        self._seed_files: dict[str, str] = {}
        self._seed_modes: dict[str, int] = {}
        #: Which part of a deployment each variable given at create time is for
        #: (``build`` or ``runtime``; the rest are for both); see ``configure``.
        self._env_audience: dict[str, str] = {}
        #: The deployment's variables (references, audiences, what the build is
        #: given), shared by every type of application; built on first use.
        self._deployment_variables: DeploymentVariables | None = None
        self.trigger: str = DeploymentTrigger.CLI.value
        #: The background job that started this deployment, when one did.
        #: None for the CLI and for a webhook, which run with nothing queuing
        #: them.
        self.job_id: str | None = None
        #: The id of the history row :meth:`deploy` or :meth:`update` just
        #: wrote, once it returns. None until then, and None again if
        #: recording itself failed (see :class:`DeploymentRecorder`) or the
        #: run was a dry one.
        self.last_deployment_id: int | None = None
        self.memory_max_mb: int | None = None
        self.cpu_quota_percent: int | None = None
        self.tasks_max: int | None = None
        self._resource_limits_given: bool = False
        # What a new application's row is created with, so the first
        # deployment already reports and scrubs as what it is (a preview).
        self._preview_parent: str | None = None
        self._initial_health: tuple[str | None, str | None, int | None] | None = None
        self._initial_secret_marks: dict[str, bool] = {}

        # Package manager (auto = auto-detect)
        self._package_manager: PackageManager = "auto"
        self.package_manager: str = "npm"  # Resolved package manager

        # Prisma support
        self.has_prisma: bool = False

        # Deployment progress, shared between pipeline steps
        self._ssl_obtained: bool = False
        # How the certificate asked for by this run ended: ("issued", "") or ("failed", what the
        # authority said). None until a certificate step has run.
        self._certificate_outcome: tuple[str, str] | None = None
        self._app_record: App | None = None
        # What an application's row said before a redeployment marked it
        # "deploying": what it says again when the attempt is cancelled.
        self._status_before_deploy: str | None = None
        self._is_new_deployment: bool = True

        # Layout. The request is what configure() was told; the layout is
        # decided once per deploy or update, against the store, by
        # resolve_layout(). Until then everything behaves in place.
        self._requested_layout: str | None = None
        self._layout: str | None = None
        self._persistent_request: list[str] | None = None
        self._releases: ReleaseManager | None = None
        self._staged: StagedRelease | None = None
        #: The release whose dependencies the last build reused, if any.
        self.dependencies_reused_from: str | None = None
        #: Whether this one build ignores every cache (``noust update --no-cache``).
        self.no_cache: bool = False

        # Advanced nginx config (detected from noust.nginx.yaml)
        self._nginx_config_builder = NginxConfigBuilder(verbose=verbose)
        self._nginx_advanced_config: NginxAdvancedConfig | None = None

        # Env manager
        self._env_manager = EnvManager(verbose=verbose)

        # The build sandbox: the phase commands run in, the regime this
        # deployment builds under (False until decided), and what was
        # prepared for it once.
        self._phases = ()
        self._sandbox_forced = False
        self._sandbox_regime = False
        self._sandbox_cache = None
        self._sandbox_tree = None

    @property
    def runner(self) -> CommandRunner:
        """The command runner this deployer executes through."""
        return self._runner if self._runner is not None else get_runner()

    @property
    def source_manager(self) -> SourceManager:
        """The manager that fetches source code."""
        if self._source_manager is None:
            self._source_manager = SourceManager(
                verbose=self.verbose, fs=self._fs, domain=self.domain or None
            )
        return self._source_manager

    @source_manager.setter
    def source_manager(self, manager: SourceManager) -> None:
        """
        Replace the source manager.

        Args:
            manager: The manager to use instead of the default.
        """
        self._source_manager = manager

    @property
    def service_manager(self) -> ServiceManager:
        """The manager that writes and drives systemd units."""
        if self._service_manager is None:
            self._service_manager = ServiceManager(verbose=self.verbose)
        return self._service_manager

    @service_manager.setter
    def service_manager(self, manager: ServiceManager) -> None:
        """
        Replace the service manager.

        Args:
            manager: The manager to use instead of the default.
        """
        self._service_manager = manager

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
            manager: The manager to use instead of the default.
        """
        self._cert_manager = manager

    @property
    def registrar(self) -> StoreRegistrar:
        """The component that writes app, site and service rows."""
        if self._registrar is None:
            self._registrar = StoreRegistrar(self.store)
        return self._registrar

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
        layout: str | None = None,
        persistent_paths: Sequence[str] | None = None,
        job_id: str | None = None,
        memory_max_mb: int | None = None,
        cpu_quota_percent: int | None = None,
        tasks_max: int | None = None,
        resource_limits_given: bool = False,
        preview_parent: str | None = None,
        env_secret_marks: dict[str, bool] | None = None,
        initial_health: tuple[str | None, str | None, int | None] | None = None,
        **options: Any,
    ) -> None:
        """
        Configure the deployer.

        Args:
            domain: Target domain.
            source: Source URL or path.
            port: Application port.
            webserver: Web server to use (nginx/apache).
            ssl: Enable SSL.
            branch: Git branch.
            env_vars: Environment variables.
            app_path: Custom application path.
            package_manager: Package manager to use (npm/pnpm/bun/auto).
            include_www: Also answer on ``www.<domain>``, recorded as a domain
                of kind ``redirect`` to the primary unless the application
                already has that name in some other role.
            trigger: What initiated this deployment, recorded in the history:
                ``cli`` (the default), ``panel`` or ``webhook``.
            layout: ``inplace`` or ``releases`` for a new application,
                ``default`` for the server's configured layout, or None for
                in place. An existing application always keeps its own; see
                :func:`noust.core.appdir.layout.choose_layout`.
            persistent_paths: Paths, relative to the application, that live
                in ``shared/`` and are linked into every release (uploads,
                storage). None keeps what the application already has.
            job_id: The background job driving this deployment, when the
                panel queued it. Recorded on the deployment history row so
                the two can be linked; None for the CLI and for a webhook.
            memory_max_mb: ``MemoryMax`` the unit is created with, in MB.
                Only takes effect when ``resource_limits_given`` is true; see
                :meth:`~noust.deployers.helpers.registration.StoreRegistrar.register_app`.
            cpu_quota_percent: ``CPUQuota`` the unit is created with, in
                percent of one CPU. Same rule as ``memory_max_mb``.
            tasks_max: ``TasksMax`` the unit is created with. Same rule as
                ``memory_max_mb``.
            resource_limits_given: Whether the three limits above were part of
                this call at all. False (the default, and every caller except
                a fresh ``POST /api/apps`` with limits in the body) leaves an
                existing application's limits exactly as they were - set once
                through ``PATCH .../limits`` or at creation, a redeploy or an
                update must not silently clear them.
            preview_parent: For a new application only: the application it
                is the pull request preview of. Recorded on the row the
                deployment creates, before anything runs, so the deployment's
                own events (GitHub deployment statuses, notifications)
                already call it a preview.
            env_secret_marks: For a new application only: the secret marks
                its row starts with, so its first build's log is scrubbed of
                the values its parent marked secret.
            initial_health: For a new application only: ``(path, expect,
                timeout)`` its health check starts with, so the first
                deployment's gate already asks what the application answers
                (another platform's configuration, say).
            **options: ``no_cache`` builds this once without the build cache
                or the dependencies of the release that serves.
                ``replace_existing`` deploys into a directory that
                already holds files (``noust create --force``): in place they
                are replaced, on releases a release is added beside them.
                ``database_env`` holds the variables of a database created
                for this first deploy (``DatabaseService.prepare_for_new_app``),
                written with ``env_vars`` before the build; kept apart from
                them because they are not the operator's, and a
                ``.env.example`` is still filled in when the operator gave
                none. Anything else is accepted and ignored, so a caller can pass
                the union of every deployer's settings without knowing which
                one it got.
        """
        from noust.validators.domain import should_include_www

        self.domain = domain
        if self._source_manager is not None:
            self._source_manager.domain = domain
        self.source = source
        self.port = port or self.DEFAULT_PORT
        self.webserver = webserver
        self.ssl = ssl
        self.include_www = include_www and should_include_www(domain)
        self.branch = branch
        self.env_vars = env_vars or {}
        self.trigger = trigger
        self.job_id = job_id
        self.memory_max_mb = memory_max_mb
        self.cpu_quota_percent = cpu_quota_percent
        self.tasks_max = tasks_max
        self._resource_limits_given = resource_limits_given
        self._preview_parent = preview_parent
        self._initial_secret_marks = dict(env_secret_marks or {})
        self._initial_health = initial_health
        self._package_manager = package_manager  # type: ignore[assignment]
        self._requested_layout = layout
        self._persistent_request = list(persistent_paths) if persistent_paths is not None else None
        self._layout = None
        self._releases = None
        self._staged = None
        self._build_block = None
        self._build_block_tree = None
        self._runtime_state = None
        self._runtime_tree = None
        self._initial_build = self._read_initial_build(options.get("build_document"))
        # Deploying over a directory that already holds files is refused
        # unless asked for (noust create --force); see claim_deploy_target.
        self._replace_existing = bool(options.get("replace_existing", False))
        self.no_cache = bool(options.get("no_cache", False))
        self._database_env = dict(options.get("database_env") or {})
        self._seed_files = seeded_paths(options.get("seed_files") or options.get("php_files") or {})
        self._seed_modes = {
            str(persistent_path(path)): int(mode)
            for path, mode in dict(options.get("seed_modes") or {}).items()
        }
        self._env_audience = dict(options.get("env_audience") or {})
        # Built again for this application: the domain and the audience just changed.
        self._deployment_variables = None
        # An in-place update that must restart behind the health gate itself
        # (going back to a deployment), rather than leave the restart to the
        # caller; see _update_in_place.
        self._gate_in_place = bool(options.get("gate_in_place", False))
        self.deploy_target = None

        # Set app name and path
        self.app_name = domain_to_app_name(domain)
        self.app_path = app_path or (self.config.apps_directory / self.app_name)

    # Layout and paths -----------------------------------------------------

    def resolve_layout(self, existing: App | None = None) -> str:
        """
        Decide, once, whether this deployment builds a release or works in place.

        Args:
            existing: The application's store row, when the caller already
                read it. Read here otherwise.

        Returns:
            ``inplace`` or ``releases``.

        Raises:
            DeploymentError: When the requested layout conflicts with the
                application's, or this deployer cannot build releases.
        """
        if self._layout is None:
            if existing is None and self.domain:
                existing = self.store.get_app(self.domain)
            self._layout = choose_layout(
                existing,
                self._requested_layout,
                app_type=self.APP_TYPE,
                supports_releases=self.SUPPORTS_RELEASES,
                config=self.config,
            )
        return self._layout

    @property
    def uses_releases(self) -> bool:
        """Whether this deployment builds a release. False until the layout is resolved."""
        return self._layout == RELEASES

    @property
    def releases(self) -> ReleaseManager:
        """The manager of this application's releases."""
        if self._releases is None:
            self._releases = ReleaseManager(
                self.app_path, fs=self._fs, runner=self._runner, logger=self.logger
            )
        return self._releases

    @property
    def tree_root(self) -> Path:
        """
        The tree being deployed: the new release, or the application directory.

        The project may live in a subdirectory of it (:attr:`build_path`); what
        belongs to the whole tree - handing it over to the build account, the
        ``.env`` links, the dependencies of a workspace - uses this.
        """
        if self._staged is not None:
            return self._staged.path
        return self.app_path

    @property
    def build_path(self) -> Path:
        """
        Where the project being deployed is built.

        The new release, or the application itself, or the ``base_directory``
        of the build block inside either. In place and without a base
        directory it is :attr:`tree_root`, which is what keeps that layout
        exactly as it was.
        """
        base = self.build_config().base_directory
        return self.tree_root / base if base else self.tree_root

    @property
    def runtime_path(self) -> Path:
        """What the unit and the web server point at: ``current``, or the app itself."""
        root = self.app_path / CURRENT_LINK if self.uses_releases else self.app_path
        base = self.build_config().base_directory
        return root / base if base else root

    # The build block and the runtime ----------------------------------------

    def build_config(self) -> BuildConfig:
        """
        Name the build block that applies to the tree being deployed.

        Read once per tree, from the operator's document in the store or the
        repository's ``noust.yaml`` (:func:`resolve_build`), and checked
        against what this type honours. A tree that is not on disk yet (before
        the fetch, or under ``--dry-run``) has none, and that is not
        remembered: the fetch steps read it again once the code is there.

        Returns:
            The block; an empty one when nothing declares it.

        Raises:
            ValidationError: The block is not valid, or sets what this type
                cannot do; ``field`` names the key.
        """
        tree = self.tree_root
        if self._build_block is not None and self._build_block_tree == tree:
            return self._build_block
        if not tree.is_dir():
            return BuildConfig()
        block = (
            self._initial_build
            if self._initial_build is not None
            else resolve_build(self._app_row(), tree, self.store)
        )
        refuse_unsupported(block, self.APP_TYPE, self.BUILD_KEYS, self.RUNTIME_LANGUAGES)
        block = self._with_web_process(block, tree)
        self._build_block = block
        self._build_block_tree = tree
        return block

    def _with_web_process(self, block: BuildConfig, tree: Path) -> BuildConfig:
        """
        Let the project's ``web`` process be the command the unit runs, when nothing says otherwise.

        The project's process types are read here, once per tree, with the build block: the
        block names the base directory they are read from. A ``start`` of the block (the
        operator's, or the repository's) wins; so does a publish directory, which makes the
        application files and not a process. A ``Procfile`` only counts for an application that
        honours it (:meth:`_procfile_applies`): a repository that carried one before Noust read
        it keeps starting the way it was deployed.

        Args:
            block: The block that applies, already checked.
            tree: The tree being deployed.

        Returns:
            The block, with the ``web`` process as its ``start`` when it applies.

        Raises:
            ValidationError: The ``processes:`` block of ``noust.yaml`` is not valid.
        """
        self._process_set = None
        self._start_from_process = False
        try:
            project = project_dir(tree, block.base_directory)
        except ValidationError:
            # A base directory that is a link, or not there: _prime_build refuses it, naming
            # the field, and nothing is read through it before.
            return block
        found = resolve_processes(project)
        self._process_set = found
        if found.problem:
            self.logger.warning(f"The Procfile is not used: {found.problem}")
        web = found.named(WEB)
        if (
            web is None
            or block.start is not None
            or block.publish_directory
            or block.source == "operator"
            or "start" not in self.BUILD_KEYS
            or (found.source == "Procfile" and not self._procfile_applies())
        ):
            return block
        self.logger.substep(f"Starting with the web process of {found.source}: {web.command}")
        self._start_from_process = True
        return replace(block, start=web.argv, source=block.source if block.declared else "repo")

    def _procfile_applies(self) -> bool:
        """
        Tell whether this application starts and releases from its ``Procfile``.

        A new application does. One that exists keeps doing what it did, unless its unit was
        already written from the Procfile (the launch record says so): reading a Procfile on an
        update would change a working application's start command, and run a ``release``
        command on a database nothing had ever migrated that way.

        Returns:
            True for a new application, or one deployed from its Procfile.
        """
        if self._procfile_decision is None:
            self._procfile_decision = self._app_row() is None or procfile_honoured(
                self.domain, self.store
            )
        return self._procfile_decision

    def _read_initial_build(self, document: object) -> BuildConfig | None:
        """
        Read the build block an application is created with.

        Args:
            document: The YAML document ``noust create --build-file`` read, or
                None.

        Returns:
            The operator's block, checked against what this type honours; None
            when there is none.

        Raises:
            ValidationError: Not valid, or sets what the type cannot do.
        """
        if not document:
            return None
        block = parse_build_document(str(document), source="operator")
        refuse_unsupported(block, self.APP_TYPE, self.BUILD_KEYS, self.RUNTIME_LANGUAGES)
        return block

    def _app_row(self) -> App | None:
        """
        Return the application's store row.

        Returns:
            The row this deployment registered, or the stored one, or None.
        """
        if self._app_record is not None:
            return self._app_record
        return self.store.get_app(self.domain) if self.domain else None
