# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What every Node framework with a server or a static output shares.

Nuxt, SvelteKit, Astro, Remix and React Router, and Angular each build a
project that is either a process (the framework's own server) or a directory
of files, depending on how the project is configured: an adapter, a preset, an
output mode. A deployer per framework says how it tells the two apart and what
runs; this class does the rest, once (rule 3): the package manager from the
lockfile, the install and build commands, the unit for a server and the site
for files, the health check, and the build block's say over all of it
(:mod:`noust.deployers.helpers.build_config`).

What a project's configuration says is read from the files themselves (never
run: the repository is untrusted), by looking for the names that decide it. A
project that configures something these files cannot show (a dynamic adapter
chosen by an environment variable) says what it wants with ``start`` or
``publish_directory`` in its build block, and that always wins.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from noust.core.exceptions import DeploymentError
from noust.core.fs import FileSystem, is_rehearsal
from noust.core.runner import CommandRunner
from noust.deployers.base import BaseDeployer
from noust.deployers.helpers.build_config import refuse_links

#: Largest ``package.json`` or framework configuration file read.
MAX_FILE_BYTES = 512 * 1024

#: A server bound where nginx proxies to, and nowhere else: the upstream is
#: always ``127.0.0.1:<port>``, so a process listening on every interface only
#: widens what answers the network.
LOOPBACK = "127.0.0.1"


@dataclass(frozen=True)
class FrameworkPlan:
    """
    What a project of a framework builds into, as its configuration says.

    Attributes:
        serves: Whether the build is a process to run (False: files to serve).
        start: The command that runs it, when it is; a program with an
            absolute path or one the system has.
        publish: The directory of files, relative to the project, when it is
            not a process.
        environment: What the unit sets besides the port, when it is a process.
        problem: Why the project cannot be deployed as it is configured (an
            adapter that builds for another platform), with what to do. Raised
            when the project is installed, and only when the build block does
            not say what to run itself.
    """

    serves: bool
    start: str = ""
    publish: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    problem: str | None = None


def read_package(directory: Path) -> dict:
    """
    Read a project's ``package.json``.

    Args:
        directory: The project.

    Returns:
        Its object; empty when it is missing, not JSON, too large or a link.
    """
    path = directory / "package.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def dependencies_of(package: Mapping[str, object]) -> dict[str, str]:
    """
    List every dependency a manifest names, whatever the section.

    Args:
        package: A ``package.json`` object.

    Returns:
        Name to version spec, for dependencies, dev dependencies and optional
        ones.
    """
    found: dict[str, str] = {}
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        value = package.get(section)
        if isinstance(value, dict):
            found.update({str(name): str(spec) for name, spec in value.items()})
    return found


def script_of(package: Mapping[str, object], name: str) -> str | None:
    """
    Read one of a manifest's scripts.

    Args:
        package: A ``package.json`` object.
        name: ``build``, ``start``...

    Returns:
        The script's command line, or None.
    """
    scripts = package.get("scripts")
    value = scripts.get(name) if isinstance(scripts, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def major_of(spec: str) -> int | None:
    """
    Read the major version a dependency spec asks for.

    Args:
        spec: ``^3.10.0``, ``~2.15``, ``3``, ``npm:nuxt-nightly@3.0.0-...``.

    Returns:
        The first number in it, None for a spec with none (``latest``, a git
        URL).
    """
    match = re.search(r"(\d+)(?:\.|\b)", spec.split("@")[-1])
    return int(match.group(1)) if match else None


def config_text(directory: Path, names: tuple[str, ...]) -> str:
    """
    Read the framework configuration files a project has, to look for names in.

    Args:
        directory: The project.
        names: The file names the framework reads.

    Returns:
        Their text, joined; empty when none exists. A link, an unreadable or
        an oversized file is skipped.
    """
    texts = []
    for name in names:
        path = directory / name
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            texts.append(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(texts)


def without_comments(text: str) -> str:
    """
    Take the comments out of a JavaScript or TypeScript configuration.

    A commented-out ``adapter: adapter-static`` is not the project's adapter.

    Args:
        text: A configuration file's text.

    Returns:
        The text without ``//`` and ``/* */`` comments.
    """
    without_blocks = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)(?<![:\"'])//.*$", "", without_blocks)


class NodeFrameworkDeployer(BaseDeployer):
    """
    Base of the deployers of a Node framework that builds a server or static files.

    A subclass names the framework (:attr:`MARKERS`, :attr:`CONFIG_FILES`) and
    says what a project of it builds into (:meth:`plan_for`).
    """

    DEFAULT_PORT = 3000

    #: Above Next.js and Vite: SvelteKit and Remix list ``vite`` as well, and
    #: the framework is the more specific answer.
    DETECTION_PRIORITY = 75

    SYSTEM_DEPS: ClassVar[list[str]] = ["node", "npm"]

    BUILD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"base_directory", "install", "build", "start", "publish_directory", "runtime"}
    )
    RUNTIME_LANGUAGES: ClassVar[frozenset[str]] = frozenset({"node"})
    PRIMARY_RUNTIME: ClassVar[str | None] = "node"

    #: Dependencies whose presence says the project is of this framework.
    MARKERS: ClassVar[tuple[str, ...]] = ()
    #: Configuration files whose presence says it too.
    CONFIG_FILES: ClassVar[tuple[str, ...]] = ()

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
            runner: Command runner used for installs and builds. Defaults to the
                process-wide runner.
            fs: Filesystem every change goes through. Defaults to the
                process-wide one.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        self.package_manager = "npm"

    # Detection ---------------------------------------------------------------

    def detect(self, path: Path) -> bool:
        """
        Tell whether a tree is a project of this framework.

        Args:
            path: The fetched source.

        Returns:
            True when its ``package.json`` depends on the framework, or it has
            the framework's configuration file next to a ``package.json``.
        """
        package = read_package(path)
        if not package:
            return False
        if any(marker in dependencies_of(package) for marker in self.MARKERS):
            return True
        return any((path / name).is_file() for name in self.CONFIG_FILES)

    # What the project builds into -------------------------------------------

    def plan_for(self, project: Path, package: Mapping[str, object]) -> FrameworkPlan:
        """
        Say what a project builds into.

        Args:
            project: The project directory.
            package: Its ``package.json``.

        Returns:
            The plan.
        """
        raise NotImplementedError

    def plan(self) -> FrameworkPlan:
        """
        Read what the project being deployed builds into.

        Returns:
            The plan, from the files of the project as they are now; the
            server plan for a tree that is not on disk yet, which is how a new
            application is registered before its source is fetched.
        """
        project = self.build_path
        if not project.is_dir():
            # Not fetched yet: a new application is registered as the server
            # it most likely is, and corrected once its files are there.
            return FrameworkPlan(serves=True, start=self._run_script("start"))
        return self.plan_for(project, read_package(project))

    def _serves(self) -> bool:
        """
        Tell whether this deployment runs a process.

        Returns:
            True for a ``start`` of the build block, False for its
            ``publish_directory``; the plan's answer otherwise.
        """
        block = self.build_config()
        if block.start:
            return True
        if block.publish_directory:
            return False
        return self.plan().serves

    def _publish_directory(self) -> str:
        """
        Name the directory of files a static build leaves.

        Returns:
            The block's ``publish_directory``, else the plan's, else ``dist``.
        """
        return self.build_config().publish_directory or self.plan().publish or "dist"

    def _run_script(self, name: str) -> str:
        """
        Write the command that runs a script of ``package.json``.

        Args:
            name: The script.

        Returns:
            ``npm run start``, ``yarn start``, ``pnpm run start``...
        """
        return shlex.join(self._get_pm_run_command(name))

    def _tool(self, name: str) -> str:
        """
        Name a program the project installed, by an absolute path.

        Args:
            name: A file of ``node_modules/.bin``.

        Returns:
            Where the service finds it once running.
        """
        return str(self._at_runtime(self.build_path / "node_modules" / ".bin" / name))

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

    # The deployer interface --------------------------------------------------

    def get_install_command(self) -> list[str]:
        """Get dependency installation command."""
        return self._get_pm_install_command()

    def get_build_command(self) -> list[str]:
        """Get the build command: the project's own ``build`` script."""
        if script_of(read_package(self.build_path), "build") is None:
            return []
        return self._get_pm_run_command("build")

    def get_start_command(self) -> str:
        """Get the command of the framework's server; empty for static files."""
        plan = self.plan()
        return plan.start if plan.serves else ""

    def get_nginx_template(self) -> str:
        """Get the Nginx template: a proxy for a server, the files otherwise."""
        return "proxy" if self._serves() else "static"

    def get_apache_template(self) -> str:
        """Get the Apache template: a proxy for a server, the files otherwise."""
        return "proxy" if self._serves() else "static"

    def _unit_environment(self) -> dict[str, str]:
        """
        Say what the unit sets: the port, the mode and what the framework's server reads.

        Returns:
            The variables.
        """
        environment = super()._unit_environment()
        if not self.build_config().start:
            environment.update(self.plan().environment)
        return environment

    def pre_install(self) -> bool:
        """
        Detect the package manager, and refuse a configuration that cannot be served.

        Returns:
            True.

        Raises:
            DeploymentError: The project is configured for a platform it
                cannot be deployed to, and the build block does not say what
                to run instead.
        """
        super().pre_install()
        block = self.build_config()
        plan = self.plan()
        if plan.problem and not (block.start or block.publish_directory):
            raise DeploymentError(plan.problem.split("\n", 1)[0], details=plan.problem)
        mode = "a server" if self._serves() else f"static files ({self._publish_directory()})"
        self.logger.debug(f"{self.DISPLAY_NAME} builds {mode}")
        return True

    def get_template_context(self) -> dict:
        """Get template context: where the static files are, or the proxy."""
        context = super().get_template_context()
        if not self._serves():
            publish = self._publish_directory()
            context.update(
                {
                    "is_static": True,
                    "static_dir": str(self._at_runtime(self.build_path / publish)),
                    "output_dir": publish,
                }
            )
        return context

    def create_service(self) -> bool:
        """Create the unit only for a server, but always register the application."""
        if self._serves():
            return super().create_service()
        self.logger.substep("Static output - no service needed")
        return True

    def start(self) -> bool:
        """Start the unit of a server; nothing for static files."""
        return super().start() if self._serves() else True

    def stop(self) -> bool:
        """Stop the unit of a server; nothing for static files."""
        return super().stop() if self._serves() else True

    def restart(self) -> bool:
        """Restart the unit of a server; nothing for static files."""
        return super().restart() if self._serves() else True

    def health_check(self, retries: int = 5, delay: float = 2.0) -> bool:
        """Check the server answers, or that the static output has its ``index.html``."""
        if self._serves():
            return super().health_check(retries, delay)
        index = self.build_path / self._publish_directory() / "index.html"
        if index.is_file() and not index.is_symlink():
            self.logger.debug("Static build verified")
            return True
        self.logger.warning(f"{index} is missing: the build did not produce the site")
        return False

    def post_build(self) -> bool:
        """
        Check a static build left what the site serves.

        Returns:
            True.

        Raises:
            DeploymentError: The output directory is missing or is a link.
        """
        if not self._serves() and not is_rehearsal():
            publish = refuse_links(
                self.build_path, self._publish_directory(), field="build.publish_directory"
            )
            if not publish.is_dir():
                raise DeploymentError(
                    f"The build left no directory {self._publish_directory()} to serve",
                    details="Set where it writes its files: noust app build set DOMAIN "
                    "--publish-directory DIR (or build.publish_directory in noust.yaml).",
                )
        return True


__all__ = [
    "LOOPBACK",
    "FrameworkPlan",
    "NodeFrameworkDeployer",
    "config_text",
    "dependencies_of",
    "major_of",
    "read_package",
    "script_of",
    "without_comments",
]
