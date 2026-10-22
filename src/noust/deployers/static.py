# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Static site deployer for Noust.
"""

from pathlib import Path
from typing import ClassVar

from noust.core.fs import FileSystem
from noust.core.logger import Icons
from noust.core.runner import CommandRunner
from noust.deployers.base import BaseDeployer
from noust.deployers.helpers.build_config import refuse_links
from noust.deployers.helpers.sandbox import BuildPhase
from noust.deployers.pipeline import DeployStep
from noust.deployers.registry import DeployerRegistry
from noust.validators.environment import validate_environment


class StaticDeployer(BaseDeployer):
    """
    Deployer for static HTML/CSS/JS sites.

    Serves static files directly through Nginx or Apache
    without any build process or application server.
    """

    APP_TYPE = "static"
    DISPLAY_NAME = "Static Site"

    DETECTION_FILES: ClassVar[list[str]] = ["index.html"]

    DEFAULT_PORT = 80  # Not used for static sites

    # See DEFAULT_DETECTION_PRIORITY in interface.py for the full order.
    DETECTION_PRIORITY = 10

    SYSTEM_DEPS: ClassVar[list[str]] = []

    #: A static site has nothing to run, but it may be built first (Hugo,
    #: Eleventy, Docusaurus: whatever the server has the toolchain for) and
    #: the build block says where the files end up.
    BUILD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"base_directory", "install", "build", "publish_directory", "runtime"}
    )
    RUNTIME_LANGUAGES: ClassVar[frozenset[str]] = frozenset({"node", "python"})

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Initialize the static deployer.

        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for installs and builds. Defaults to the
                process-wide runner.
            fs: Filesystem every change goes through. Defaults to the
                process-wide one.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        self.static_dir: Path | None = None

    def detect(self, path: Path) -> bool:
        """Detect if path contains a static site."""
        # Check for index.html
        if (path / "index.html").exists():
            # Make sure it's not a framework project
            framework_files = [
                "package.json",
                "requirements.txt",
                "pyproject.toml",
                "Cargo.toml",
                "go.mod",
            ]
            for f in framework_files:
                if (path / f).exists():
                    return False
            return True

        return False

    def get_install_command(self) -> list[str]:
        """No installation needed for static sites."""
        return []

    def get_build_command(self) -> list[str]:
        """No build needed for static sites."""
        return []

    def get_start_command(self) -> str:
        """No start command for static sites."""
        return ""

    def get_nginx_template(self) -> str:
        """Get Nginx template for static sites."""
        return "static"

    def get_apache_template(self) -> str:
        """Get Apache template for static sites."""
        return "static"

    def pre_install(self) -> bool:
        """Determine static directory."""
        self._detect_static_dir()
        return True

    def post_build(self) -> bool:
        """
        Find the directory the site serves once a build has made it.

        Returns:
            True.
        """
        self._detect_static_dir()
        return True

    def _tool_dirs(self) -> list[Path]:
        """
        List the ``node_modules/.bin`` directories of the project and its workspace.

        Returns:
            The project's first, then each ancestor's up to the tree's root.
        """
        return [
            directory / "node_modules" / ".bin"
            for directory in self._pm_helper.directories_up(self.build_path, self.tree_root)
        ]

    def inspect_site(self) -> None:
        """Find the directory the site serves, as a deploy would."""
        super().inspect_site()
        self._detect_static_dir()

    def _detect_static_dir(self) -> None:
        """
        Find the directory the site serves.

        The build block's ``publish_directory`` when it names one; otherwise
        the first common output directory that holds an ``index.html``.
        """
        published = self.build_config().publish_directory
        if published:
            self.static_dir = refuse_links(
                self.build_path, published, field="build.publish_directory"
            )
            self.logger.debug(f"Static directory: {self.static_dir}")
            return
        static_dirs = ["public", "dist", "build", "www", "html", "."]

        for dir_name in static_dirs:
            dir_path = self.build_path / dir_name
            if dir_name == ".":
                dir_path = self.build_path

            if (dir_path / "index.html").exists():
                self.static_dir = dir_path
                break

        if not self.static_dir:
            self.static_dir = self.build_path

        self.logger.debug(f"Static directory: {self.static_dir}")

    def get_template_context(self) -> dict:
        """Get template context for static site."""
        context = super().get_template_context()
        context.update(
            {
                "is_static": True,
                # The directory as the web server will see it: through
                # ``current`` on releases, so activation needs no reload.
                "static_dir": str(self._at_runtime(self.static_dir or self.build_path)),
            }
        )
        return context

    def _prepare_env(self) -> None:
        """
        Keep the variables given at create time out of the site's directory.

        A static site has no process to load an env file, and its directory
        may be the one the web server serves: Apache does not refuse dotfiles,
        so a ``.env`` written there - from ``--env-file`` or generated from
        ``.env.example`` - would publish every secret in it. The build still
        runs with the variables in its environment, so they are validated.

        Raises:
            EnvironmentValidationError: When a variable has an unusable name
                or a control character in its value.
        """
        self.env_vars = validate_environment(self.env_vars)

    def create_service(self) -> bool:
        """No service needed for static sites."""
        self.logger.substep("Static site - no service needed")
        return True

    def stop(self) -> bool:
        """No stop needed for static sites."""
        return True

    def restart(self) -> bool:
        """No restart needed for static sites."""
        return True

    def health_check(self, retries: int = 5, delay: float = 2.0) -> bool:
        """Check if static site files exist."""
        index_path = (self.static_dir or self.build_path) / "index.html"
        if index_path.exists():
            self.logger.debug("Static site verified")
            return True
        return False

    def _prepare_static(self) -> None:
        """
        Make the files the site serves, and find them.

        A build block may give a static site a toolchain (``install`` and
        ``build``: Eleventy, Hugo, Docusaurus, whatever the server has the
        tools for); what it does not declare is not run, so a site without
        one is prepared exactly as it always was. The block is read from the
        code once it is fetched, which is why this is a step of its own and
        not steps of the pipeline: the pipeline is described before that.
        """
        if self.install_command():
            with self._in_phase(BuildPhase.INSTALL):
                self.install_dependencies()
        if self.build_command():
            with self._in_phase(BuildPhase.BUILD):
                self.build()
        self.pre_install()

    def build_pipeline(self) -> list[DeployStep]:
        """
        Describe the shorter workflow a static site needs.

        There is nothing to install, nothing to build and no service to run, so
        those steps are dropped rather than made into no-ops. This used to be a
        full copy of ``deploy()`` with its own try/except and its own store
        updates, which is how it ended up without any rollback at all.

        Returns:
            The steps to execute, each with the undo that reverses it.
        """
        prepare = DeployStep(
            title="Preparing static files",
            icon=Icons.FOLDER,
            run=self._prepare_static,
        )
        if self.uses_releases:
            return self._release_pipeline([prepare], with_service=False)
        return [
            DeployStep(
                title="Fetching source code",
                icon=Icons.DOWNLOAD,
                run=self._step_fetch,
                undo=self.remove_source,
            ),
            prepare,
            DeployStep(
                title="Creating site configuration",
                icon=Icons.GLOBE,
                run=lambda: self.create_site(with_ssl=False),
                undo=self.remove_site,
            ),
            DeployStep(
                title="Obtaining SSL certificate",
                icon=Icons.LOCK,
                run=self._step_certificate,
                skip_if=lambda: not self.ssl,
            ),
            DeployStep(
                title="Verifying deployment",
                icon=Icons.CHECK,
                run=self._step_start,
            ),
        ]

    def start(self) -> bool:
        """
        Nothing to start: the web server serves the files directly.

        Returns:
            True.
        """
        return True


# Register the deployer
DeployerRegistry.register(StaticDeployer)
