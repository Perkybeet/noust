# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
.NET applications: ``dotnet publish`` in the sandbox, ``dotnet app.dll`` as the unit (A-40).

Until 4.0 a repository with a ``.csproj`` was only ever a container. ASP.NET Core publishes to
a directory of its own (the program, its dependencies, ``appsettings.json`` and ``wwwroot``), so
a native deployment is the directory in the release and a rollback is a symlink to it.

What is specific to .NET:

- **A web project is the program.** The project that applies the web SDK
  (``Microsoft.NET.Sdk.Web``) or references ASP.NET Core is found at the root, or under
  ``src/`` and one more level for a solution; a repository of libraries and tests has none, and
  the verdict says so. Several web projects need the build block to say which.
- **Restore is the install phase.** ``dotnet restore`` has the network and the exact result,
  so the publish runs ``--no-restore`` and works with the ``--network strict`` profile. A
  committed ``packages.lock.json`` makes the restore ``--locked-mode``.
- **No shared compilers, no apphost.** MSBuild's node reuse and the Roslyn server would outlive
  the sandbox's unit, and the unit runs the ``.dll`` through ``dotnet``, which needs no native
  host built for the server's processor.
- **The content root is the publish directory.** ASP.NET Core takes ``wwwroot`` and
  ``appsettings.json`` from it, not from the working directory, so the unit sets
  ``ASPNETCORE_CONTENTROOT``; the program listens on ``ASPNETCORE_URLS`` on the loopback address.
- **The SDK is the server's own** (a distribution package or Microsoft's feed); the project's
  target framework is checked against it before anything is built.
- **A Dockerfile means the container**, for the reason :mod:`noust.deployers.compiled` gives.

Data Protection keys (cookies, anti-forgery) live in the service account's home, which a system
account does not have: ASP.NET Core then keeps them in memory and warns. An application that
needs them to survive a restart points them at a directory in its code or ``shared/``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from noust.core.exceptions import DeploymentError
from noust.core.fs import FileSystem
from noust.core.package_family import PackageFamily
from noust.core.runner import CommandRunner
from noust.deployers.base import BaseDeployer
from noust.deployers.compiled import has_container_file, install_suggestion_for, walk_project
from noust.deployers.helpers.unit_launch import systemd_command
from noust.deployers.registry import DeployerRegistry

#: Project file extensions, and the solution files that group them.
PROJECT_SUFFIXES: tuple[str, ...] = (".csproj", ".fsproj", ".vbproj")
SOLUTION_SUFFIXES: tuple[str, ...] = (".sln", ".slnx")

#: Where ``dotnet publish`` writes, relative to the project. Hidden, because MSBuild's default
#: globs skip directories that start with a dot and would otherwise take the output for source.
PUBLISH_DIR = ".publish"

#: Directories that hold build output, tests or other people's code.
NOT_THE_APPLICATION = frozenset(
    {
        ".git",
        "bin",
        "obj",
        "node_modules",
        "packages",
        "test",
        "tests",
        "Test",
        "Tests",
        "docs",
        "samples",
        "examples",
        "third_party",
    }
)

#: How deep a web project is looked for: ``src/Web/Web.csproj`` is two levels.
MAX_DEPTH = 3

#: Largest project file read.
MAX_PROJECT_BYTES = 256 * 1024

_WEB_SDK = re.compile(r"Microsoft\.NET\.Sdk\.Web\b|Microsoft\.AspNetCore\.App\b")
_TARGET_FRAMEWORKS = re.compile(r"<TargetFrameworks?>\s*([^<]+?)\s*</TargetFrameworks?>")
_FRAMEWORK_MONIKER = re.compile(r"\bnet(?:coreapp)?(\d+)\.(\d+)\b")
_ASSEMBLY_NAME = re.compile(r"<AssemblyName>\s*([^<$]+?)\s*</AssemblyName>")
_SDK_LINE = re.compile(r"^(\d+)\.(\d+)\.\d+\S*\s+\[", re.MULTILINE)
_RUNTIME_LINE = re.compile(r"^Microsoft\.AspNetCore\.App\s+(\d+)\.", re.MULTILINE)
_TEST_PROJECT = re.compile(r"(?:\.|-)(?:Tests?|IntegrationTests?|UnitTests?|Specs?)$")


def _read(path: Path) -> str:
    """
    Args:
        path: A file of an untrusted repository.

    Returns:
        Its text; empty for a link, a missing file or an unreadable one.
    """
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        with path.open(encoding="utf-8-sig", errors="ignore") as handle:
            return handle.read(MAX_PROJECT_BYTES)
    except OSError:
        return ""


def web_projects(root: Path) -> list[str]:
    """
    Find the projects of a tree that serve HTTP.

    Args:
        root: The project's directory or the repository's root.

    Returns:
        The paths of their project files relative to ``root``, shallowest first, then by name.
        Test projects (by name or directory) and anything under ``bin`` or ``obj`` are left
        out.
    """
    found: list[str] = []
    for directory, files in walk_project(root, MAX_DEPTH, NOT_THE_APPLICATION):
        for name in files:
            stem = PurePosixPath(name).stem
            if not name.endswith(PROJECT_SUFFIXES):
                continue
            if _TEST_PROJECT.search(stem) or _TEST_PROJECT.search(directory.name):
                continue
            if _WEB_SDK.search(_read(directory / name)):
                found.append((directory / name).relative_to(root).as_posix())
    return sorted(found, key=lambda path: (path.count("/"), path))


def choose_project(projects: Sequence[str], *, names: Sequence[str]) -> str:
    """
    Pick the project a .NET application is published from.

    Args:
        projects: What :func:`web_projects` found.
        names: Names that point at the right one among several: the application's.

    Returns:
        The project file, relative to the tree.

    Raises:
        DeploymentError: There is none, or several and nothing says which.
    """
    if not projects:
        raise DeploymentError(
            "This repository has no ASP.NET Core project",
            details='A deployable project applies the web SDK (Sdk="Microsoft.NET.Sdk.Web"). '
            "If it is deeper than three directories, give the build command: "
            "build.build: [dotnet, publish, path/to/App.csproj, -c, Release, -o, .publish].",
        )
    if len(projects) == 1:
        return projects[0]
    wanted = {name.lower() for name in names if name}
    for project in projects:
        path = PurePosixPath(project)
        if path.stem.lower() in wanted or path.parent.name.lower() in wanted:
            return project
    shallowest = [p for p in projects if p.count("/") == projects[0].count("/")]
    if len(shallowest) == 1:
        return shallowest[0]
    raise DeploymentError(
        f"This repository has {len(projects)} web projects: {', '.join(projects)}",
        details="Say which one to publish in the build block, for example "
        f"build.build: [dotnet, publish, {projects[0]}, -c, Release, -o, .publish] and how "
        "to start it with build.start.",
    )


def target_major(project_text: str) -> int | None:
    """
    Read the release of .NET a project targets.

    Args:
        project_text: The text of its project file.

    Returns:
        The newest major among its target frameworks (``8`` for ``net8.0``); None when it names
        none (or an old-style ``netstandard`` one).
    """
    declared = _TARGET_FRAMEWORKS.search(project_text)
    if not declared:
        return None
    majors = [int(m.group(1)) for m in _FRAMEWORK_MONIKER.finditer(declared.group(1))]
    return max(majors) if majors else None


def sdk_majors(output: str) -> list[int]:
    """
    Read the SDKs this server has.

    Args:
        output: What ``dotnet --list-sdks`` printed.

    Returns:
        Each SDK's major, newest last.
    """
    return sorted(int(match.group(1)) for match in _SDK_LINE.finditer(output))


def runtime_majors(output: str) -> list[int]:
    """
    Read the ASP.NET Core runtimes this server has.

    Args:
        output: What ``dotnet --list-runtimes`` printed.

    Returns:
        The major of each ``Microsoft.AspNetCore.App`` line, newest last.
    """
    return sorted(int(match.group(1)) for match in _RUNTIME_LINE.finditer(output))


def published_assemblies(publish: Path) -> list[str]:
    """
    Name the programs a publish directory holds.

    Args:
        publish: The directory ``dotnet publish`` wrote.

    Returns:
        The names that have a ``<name>.runtimeconfig.json`` (a library has none) and a
        ``<name>.dll``.
    """
    try:
        entries = sorted(publish.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return []
    return [
        entry.name.removesuffix(".runtimeconfig.json")
        for entry in entries
        if entry.name.endswith(".runtimeconfig.json")
        and not entry.is_symlink()
        and (publish / (entry.name.removesuffix(".runtimeconfig.json") + ".dll")).is_file()
    ]


def runtime_framework_major(runtime_config: str) -> int | None:
    """
    Read the ASP.NET Core release a published program runs on.

    Args:
        runtime_config: The text of its ``runtimeconfig.json``.

    Returns:
        The major of the ``Microsoft.AspNetCore.App`` framework it names; None for another
        framework or an unreadable file.
    """
    try:
        document = json.loads(runtime_config)
        frameworks = document["runtimeOptions"].get("frameworks") or [
            document["runtimeOptions"]["framework"]
        ]
        for framework in frameworks:
            if framework.get("name") == "Microsoft.AspNetCore.App":
                return int(str(framework["version"]).split(".", 1)[0])
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return None


class DotnetDeployer(BaseDeployer):
    """
    Deployer for ASP.NET Core applications: ``dotnet publish`` in the sandbox.

    The web project is found in the tree; the published directory is the release's, and the
    unit runs the program in it with ``dotnet``.
    """

    APP_TYPE = "dotnet"
    DISPLAY_NAME = ".NET (ASP.NET Core)"
    DETECTION_FILES: ClassVar[list[str]] = [f"*{suffix}" for suffix in PROJECT_SUFFIXES] + [
        f"*{suffix}" for suffix in SOLUTION_SUFFIXES
    ]
    #: A solution keeps its projects in directories below it (``src/Web/Web.csproj``), as deep
    #: as :data:`MAX_DEPTH` looks.
    FRAMEWORK_CONFIG_FILES: ClassVar[list[str]] = [
        f"{where}/*{suffix}" for where in ("*", "*/*", "*/*/*") for suffix in PROJECT_SUFFIXES
    ] + ["global.json"]
    DEFAULT_PORT = 5000

    #: With the other native stacks: below Python, above Node.
    DETECTION_PRIORITY = 45

    SYSTEM_DEPS: ClassVar[list[str]] = ["dotnet"]

    #: ``runtime`` is for the languages Noust installs per application; .NET is the server's.
    BUILD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"base_directory", "install", "build", "start"}
    )

    TOOLCHAIN_PACKAGES: ClassVar[dict[PackageFamily, str]] = {
        PackageFamily.APT: "dotnet-sdk-8.0",
        PackageFamily.DNF: "dotnet-sdk-8.0",
        PackageFamily.ZYPPER: "dotnet-sdk-8.0",
    }

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ):
        """
        Args:
            verbose: Enable verbose logging.
            runner: Command runner used for installs and builds. Defaults to the
                process-wide runner.
            fs: Filesystem every change goes through. Defaults to the process-wide one.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        #: The program the publish wrote, once it has run.
        self.assembly: str | None = None

    # Detection -------------------------------------------------------------------------

    def detect(self, path: Path) -> bool:
        """
        Detect a .NET repository that has a web project.

        Args:
            path: Path to check.

        Returns:
            True when the tree has a project applying the web SDK, and no Dockerfile: a library
            or a console tool has nothing to serve, and a Dockerfile is how the repository says
            it runs.
        """
        if has_container_file(path):
            return False
        return bool(web_projects(path))

    def _project(self) -> str:
        """
        Returns:
            The project file to publish, relative to the build directory.

        Raises:
            DeploymentError: None, or several and nothing says which.
        """
        return choose_project(web_projects(self.build_path), names=(self.app_name or "",))

    # Commands --------------------------------------------------------------------------

    def _project_or_dot(self) -> str:
        """
        Returns:
            The project, or the working directory when the choice is ambiguous: describing the
            build (``noust inspect``) never fails, the deployment itself refuses in
            :meth:`pre_build`.
        """
        try:
            return self._project()
        except DeploymentError:
            return "."

    def get_install_command(self) -> list[str]:
        """
        Get the command that restores the packages into the application's cache.

        Returns:
            ``dotnet restore``, held to the lock file when the project commits one.
        """
        project = self._project_or_dot()
        locked = (self.build_path / project).parent / "packages.lock.json"
        return ["dotnet", "restore", project, *(["--locked-mode"] if locked.is_file() else [])]

    def get_build_command(self) -> list[str]:
        """
        Get the command that publishes the program.

        Returns:
            ``dotnet publish`` in Release into ``.publish``, without restoring (the install did),
            without the native host and without the compiler servers that would outlive the
            sandbox's unit.
        """
        return [
            "dotnet",
            "publish",
            self._project_or_dot(),
            "-c",
            "Release",
            "-o",
            PUBLISH_DIR,
            "--no-restore",
            "--nologo",
            "-p:UseAppHost=false",
            "-p:UseSharedCompilation=false",
            "-nodeReuse:false",
        ]

    def _publish_directory(self) -> Path:
        """
        Returns:
            Where the publish leaves the program, in the tree being built.
        """
        return self.build_path / PUBLISH_DIR

    def get_start_command(self) -> str:
        """
        Get the command the unit runs.

        Returns:
            ``dotnet <publish>/<assembly>.dll`` through ``current`` on releases; before a
            publish (``noust inspect``) the assembly is the project's file name.
        """
        name = self.assembly or self._expected_assembly()
        dll = self._at_runtime(self._publish_directory() / f"{name}.dll")
        return systemd_command(["dotnet", str(dll)])

    def _expected_assembly(self) -> str:
        """
        Returns:
            The program's name before it is published: ``<AssemblyName>`` of the project, else
            the project file's own name.
        """
        project = self._project_or_dot()
        if project == ".":
            return self.app_name or "app"
        declared = _ASSEMBLY_NAME.search(_read(self.build_path / project))
        return declared.group(1) if declared else PurePosixPath(project).stem

    def get_health_check(self) -> str:
        """
        Get the health check endpoint.

        Returns:
            ``/``: any answer but a server error means the program is up, so a service with no
            route there is not rolled back for it.
        """
        return "/"

    def _unit_environment(self) -> dict[str, str]:
        """
        Say what the unit sets: the address it listens on and where its files are.

        Returns:
            ``ASPNETCORE_URLS`` on the loopback address and the application's port,
            ``ASPNETCORE_CONTENTROOT`` at the publish directory through ``current``, and the
            switches that keep the runtime from writing to a home it does not have.
        """
        return {
            "ASPNETCORE_URLS": f"http://127.0.0.1:{self.port}",
            "ASPNETCORE_CONTENTROOT": str(self._at_runtime(self._publish_directory())),
            "DOTNET_NOLOGO": "1",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        }

    # Steps -----------------------------------------------------------------------------

    def pre_build(self) -> bool:
        """
        Refuse a repository whose project cannot be told, before compiling anything.

        Returns:
            True.

        Raises:
            DeploymentError: There is no web project, or several and nothing says which.
        """
        if not self.build_config().build:
            self._project()
        return True

    def pre_install(self) -> bool:
        """
        Check that this server's SDK can build the project's target framework.

        Returns:
            True.

        Raises:
            DeploymentError: The project is ambiguous, or targets a newer .NET than any SDK
                on this server.
        """
        if self.build_config().install or self.build_config().build:
            return True
        project = self._project()
        wanted = target_major(_read(self.build_path / project))
        if wanted is None:
            return True
        probe = self._run(["dotnet", "--list-sdks"], timeout=60)
        have = sdk_majors(probe.stdout or "")
        if have and max(have) < wanted:
            raise DeploymentError(
                f"{project} targets .NET {wanted} and this server's newest SDK is {max(have)}",
                details="Install the SDK system-wide "
                f"({self._install_details(wanted)}), or lower the TargetFramework.",
            )
        return True

    def _install_details(self, major: int | None = None) -> str:
        """
        Args:
            major: The .NET release wanted; the hint names the package for it.

        Returns:
            How to install the SDK on this server.
        """
        packages = {
            family: f"dotnet-sdk-{major}.0" if major else package
            for family, package in self.TOOLCHAIN_PACKAGES.items()
        }
        hint = install_suggestion_for(packages, self.runner, sentence=False)
        return hint or "see https://learn.microsoft.com/dotnet/core/install/linux"

    def post_build(self) -> bool:
        """
        Find the program the publish wrote and check the runtime that will run it.

        Returns:
            True.

        Raises:
            DeploymentError: The publish wrote no program, or several.
        """
        if self.build_config().build:
            return True
        publish = self._publish_directory()
        if not publish.is_dir():
            # A dry run publishes nothing.
            return True
        found = published_assemblies(publish)
        if not found:
            raise DeploymentError(
                "dotnet publish wrote no program",
                details=f"Look for a .runtimeconfig.json in {PUBLISH_DIR}: a library has none. "
                "Publish an ASP.NET Core project, or give the build and start commands.",
            )
        if len(found) > 1:
            expected = self._expected_assembly()
            if expected not in found:
                raise DeploymentError(
                    f"dotnet publish wrote {len(found)} programs: {', '.join(found)}",
                    details="Say which one runs in the build block: build.start: "
                    f"[dotnet, {PUBLISH_DIR}/NAME.dll].",
                )
            found = [expected]
        self.assembly = found[0]
        self.logger.substep(f"Program: {self.assembly}.dll")
        self._warn_about_the_runtime(publish / f"{self.assembly}.runtimeconfig.json")
        return True

    def _warn_about_the_runtime(self, runtime_config: Path) -> None:
        """
        Say when the ASP.NET Core runtime the program asks for is not on this server.

        Args:
            runtime_config: The published program's ``runtimeconfig.json``.
        """
        wanted = runtime_framework_major(_read(runtime_config))
        if wanted is None:
            return
        probe = self._run(["dotnet", "--list-runtimes"], timeout=60)
        have = runtime_majors(probe.stdout or "")
        if have and wanted not in have:
            self.logger.warning(
                f"The program runs on ASP.NET Core {wanted} and this server has "
                f"{', '.join(str(major) for major in have)}: install the runtime "
                f"({self._install_details(wanted).replace('sdk', 'aspnetcore-runtime')}) or set "
                "DOTNET_ROLL_FORWARD=Major in .env."
            )

    # Reporting -------------------------------------------------------------------------

    @classmethod
    def install_suggestion(cls, runner: CommandRunner) -> str | None:
        """
        Say how to get the toolchain, for ``noust inspect``.

        Args:
            runner: Used to tell which package manager this server has.

        Returns:
            The command, with the next step; None when no package is known for this server.
        """
        return install_suggestion_for(cls.TOOLCHAIN_PACKAGES, runner)

    def _missing_dependencies_issue(self, missing: list[str]) -> str:
        """
        Say which programs are missing and how to install them.

        Args:
            missing: The programs of :attr:`SYSTEM_DEPS` this server lacks.

        Returns:
            The pre-flight line, with the install command for this server's package manager.
        """
        line = super()._missing_dependencies_issue(missing)
        hint = install_suggestion_for(self.TOOLCHAIN_PACKAGES, self.runner, sentence=False)
        return f"{line}. Install it with: {hint}" if hint else line

    def get_template_context(self) -> dict[str, Any]:
        """
        Get the template context.

        Returns:
            The generic one, marked as a .NET application.
        """
        context = super().get_template_context()
        context["is_dotnet"] = True
        return context


DeployerRegistry.register(DotnetDeployer)
