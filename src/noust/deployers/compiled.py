# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Go and Rust applications: compile in the sandbox, serve the binary as a unit (checklist A-38).

Until 3.4 a repository with a ``go.mod`` or a ``Cargo.toml`` was only ever a container
(Railpack, or its own Dockerfile). A compiled program needs no runtime on the server and no
image: the toolchain builds it as the unprivileged build account, the binary is copied into the
release as ``bin/<app>`` and systemd runs it, which is the whole of a "native" deployment and
the reason a rollback is a symlink.

What is specific to each language is small and lives in :class:`GoDeployer` and
:class:`RustDeployer`; what is shared (where the binary goes, the unit's environment, the
toolchain's hint, refusing a repository that brings a Dockerfile) is :class:`CompiledDeployer`.

Three decisions worth knowing:

- **A Dockerfile means the container.** A repository that commits one has said how it runs, and
  deploying it natively instead would change a working application's behaviour just because this
  server grew a Go deployer. ``--type go`` still builds it natively.
- **The module cache is the application's, not the release's.** Go's modules and Cargo's registry
  and compiled dependencies live in ``/var/cache/noust/build/<app>`` (the sandbox points
  ``GOPATH``, ``GOMODCACHE``, ``CARGO_HOME`` and ``CARGO_TARGET_DIR`` there), so a deploy
  downloads and compiles only what changed. "Rebuild without cache" empties it.
- **The build phase may have no network** (the strict profile), so the install phase fetches
  everything (``go mod download``, ``cargo fetch``) and the build runs offline.

The toolchain is the server's own, from its distribution (``apt install golang-go cargo``):
Go 1.21 and later fetches the toolchain a ``go.mod`` asks for by itself, and Cargo's own message
says when a crate needs a newer compiler.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, ClassVar

from noust.core.exceptions import DeploymentError
from noust.core.fs import FileSystem
from noust.core.package_family import PackageFamily, install_hint
from noust.core.runner import CommandRunner
from noust.deployers.base import BaseDeployer
from noust.deployers.helpers.container_image import DOCKERFILE_NAMES
from noust.deployers.registry import DeployerRegistry

#: Where the compiled program is put, relative to the project.
BINARY_DIR = "bin"

#: Directories that hold other people's code, or none of the application's.
_NOT_THE_APPLICATION = frozenset(
    {".git", "vendor", "node_modules", "testdata", "target", "third_party", "examples", "docs"}
)

#: How deep a ``package main`` is looked for: ``cmd/<name>/main.go`` is three levels.
_MAX_DEPTH = 3

_PACKAGE_MAIN = re.compile(r"^package\s+main\b", re.MULTILINE)
_GO_DIRECTIVE = re.compile(r"^go\s+(\d+)\.(\d+)(?:\.(\d+))?\s*$", re.MULTILINE)
_TOOLCHAIN_DIRECTIVE = re.compile(r"^toolchain\s+go(\d+)\.(\d+)(?:\.(\d+))?\S*\s*$", re.MULTILINE)
_GO_VERSION = re.compile(r"\bgo(\d+)\.(\d+)(?:\.(\d+))?")
_MODULE_DIRECTIVE = re.compile(r"^module\s+(\S+)\s*$", re.MULTILINE)

#: Go started fetching the toolchain a ``go.mod`` asks for in 1.21.
_GO_SWITCHES_TOOLCHAINS = (1, 21)


def install_suggestion_for(
    packages: Mapping[PackageFamily, str], runner: CommandRunner, *, sentence: bool = True
) -> str | None:
    """
    Say how to install a toolchain on this server, and only on this server.

    Args:
        packages: The packages that provide it, by package manager.
        runner: Used to tell which package manager this server has.
        sentence: Wrap the command in the sentence ``noust inspect`` shows; the bare command
            otherwise.

    Returns:
        The text; None when no package is known for this server.
    """
    hint = install_hint(packages, runner=runner) if packages else ""
    if not hint:
        return None
    return f"Install the toolchain with: {hint}. Then deploy it." if sentence else hint


def has_container_file(path: Path) -> bool:
    """
    Tell whether a repository says how to run itself as a container.

    Args:
        path: The project.

    Returns:
        True for a Dockerfile or a Containerfile.
    """
    return any((path / name).is_file() for name in DOCKERFILE_NAMES)


def _walk(top: Path, depth: int = _MAX_DEPTH) -> Iterator[tuple[Path, list[str]]]:
    """
    Walk a project's own directories, shallowest first, never through a link.

    Args:
        top: Where to start.
        depth: How many directories below ``top`` to look in.

    Yields:
        ``(directory, file names)`` of each directory that is not a dependency tree, a build
        output, a hidden directory or a test fixture.
    """
    pending = [(top, 0)]
    while pending:
        directory, level = pending.pop(0)
        try:
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
        except OSError:
            continue
        files = [entry.name for entry in entries if entry.is_file() and not entry.is_symlink()]
        yield directory, files
        if level >= depth:
            continue
        pending.extend(
            (entry, level + 1)
            for entry in entries
            if entry.is_dir()
            and not entry.is_symlink()
            and entry.name not in _NOT_THE_APPLICATION
            and not entry.name.startswith((".", "_"))
        )


def go_main_packages(root: Path) -> list[str]:
    """
    Find the packages of a Go module that build to a program.

    Args:
        root: The directory with the ``go.mod``.

    Returns:
        Each directory whose non-test ``.go`` files declare ``package main``, as the package
        argument ``go build`` takes (``.`` or ``./cmd/server``), shallowest first.
    """
    found: list[str] = []
    for directory, files in _walk(root):
        for name in files:
            if not name.endswith(".go") or name.endswith("_test.go"):
                continue
            try:
                with (directory / name).open(encoding="utf-8", errors="ignore") as handle:
                    head = handle.read(8192)
            except OSError:
                continue
            if _PACKAGE_MAIN.search(head):
                relative = directory.relative_to(root).as_posix()
                found.append("." if relative == "." else f"./{relative}")
                break
    return found


def choose_go_package(packages: list[str], *, names: tuple[str, ...]) -> str:
    """
    Pick the package a Go application is built from.

    Args:
        packages: What :func:`go_main_packages` found.
        names: Names that point at the right one when there are several: the module's last
            element, the application's.

    Returns:
        The package argument.

    Raises:
        DeploymentError: There is none, or several and nothing says which.
    """
    if not packages:
        raise DeploymentError(
            "This Go module has no package main",
            details="A program needs a main package. If it is deeper than three directories, "
            "give the build command: build.build: [go, build, -o, bin/app, ./path/to/main].",
        )
    if "." in packages:
        return "."
    if len(packages) == 1:
        return packages[0]
    wanted = {name for name in names if name}
    for package in packages:
        if package.rsplit("/", 1)[-1] in wanted:
            return package
    raise DeploymentError(
        f"This Go module has {len(packages)} programs: {', '.join(packages)}",
        details="Say which one to build in the build block, for example "
        f"build.build: [go, build, -o, bin/app, {packages[0]}], and how to start it with "
        "build.start.",
    )


def _version(match: re.Match[str] | None) -> tuple[int, int] | None:
    """
    Args:
        match: A match of one of the version patterns.

    Returns:
        ``(major, minor)``; None when there was no match.
    """
    return (int(match.group(1)), int(match.group(2))) if match else None


def required_go_version(go_mod: str) -> tuple[int, int] | None:
    """
    Read the Go release a module asks for.

    Args:
        go_mod: The text of ``go.mod``.

    Returns:
        ``(major, minor)`` of the ``toolchain`` line when there is one, else of the ``go`` line;
        None when it names neither.
    """
    return _version(_TOOLCHAIN_DIRECTIVE.search(go_mod)) or _version(_GO_DIRECTIVE.search(go_mod))


def installed_go_version(output: str) -> tuple[int, int] | None:
    """
    Read the release of the Go this server has.

    Args:
        output: What ``go version`` printed.

    Returns:
        ``(major, minor)``; None when the output is not recognisable.
    """
    return _version(_GO_VERSION.search(output))


class CompiledDeployer(BaseDeployer):
    """
    A program compiled on the server and started as the unit's process.

    Subclasses say how their language builds (``get_install_command``, ``get_build_command``,
    ``post_build``) and where its toolchain comes from; the binary is ``bin/<app name>`` in the
    project, run through ``current`` on releases.
    """

    DEFAULT_PORT = 8080

    #: Last among the types that know their language, above only the container.
    DETECTION_PRIORITY = 45

    #: ``runtime`` is for the languages Noust installs per application, and it installs none of
    #: these: the toolchain is the server's.
    BUILD_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"base_directory", "install", "build", "start"}
    )

    #: The packages that provide the toolchain, for the message that says how to install it.
    TOOLCHAIN_PACKAGES: ClassVar[dict[PackageFamily, str]] = {}

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

    def binary_name(self) -> str:
        """
        Name the program the build produces.

        Returns:
            The application's name, which is also the unit's.
        """
        return self.app_name

    def binary_path(self) -> Path:
        """
        Say where the build puts the program.

        Returns:
            ``bin/<name>`` in the project being built.
        """
        return self.build_path / BINARY_DIR / self.binary_name()

    def get_start_command(self) -> str:
        """
        Get the command the unit runs.

        Returns:
            The program, by its path through ``current``.
        """
        return str(self._at_runtime(self.binary_path()))

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
        Say what the unit sets: the port, and to listen on the loopback address.

        Returns:
            ``PORT`` and ``HOST``. The proxy reaches the program on the loopback address, and a
            program that reads ``HOST`` stays off the network that way; one that binds every
            interface regardless is the firewall's to close, as any application's is.
        """
        return {"PORT": str(self.port), "HOST": "127.0.0.1"}

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
        Say which programs are missing and how to install the toolchain.

        Args:
            missing: The programs of :attr:`SYSTEM_DEPS` this server lacks.

        Returns:
            The pre-flight line, with the install command for this server's package manager.
        """
        line = super()._missing_dependencies_issue(missing)
        hint = install_suggestion_for(self.TOOLCHAIN_PACKAGES, self.runner, sentence=False)
        return f"{line}. Install it with: {hint}" if hint else line


class GoDeployer(CompiledDeployer):
    """
    Deployer for Go modules: ``go build`` in the sandbox, the binary as the unit.

    The package built is the module's ``main`` (the root, or the one program under ``cmd/``);
    a module with several says which in the build block. ``-trimpath`` drops the build
    machine's paths from the binary and ``-buildvcs=false`` stops Go asking git about a tree
    that belongs to another account.
    """

    APP_TYPE = "go"
    DISPLAY_NAME = "Go"
    DETECTION_FILES: ClassVar[list[str]] = ["go.mod"]
    SYSTEM_DEPS: ClassVar[list[str]] = ["go"]
    TOOLCHAIN_PACKAGES: ClassVar[dict[PackageFamily, str]] = {
        PackageFamily.APT: "golang-go",
        PackageFamily.DNF: "golang",
        PackageFamily.ZYPPER: "go",
    }

    def detect(self, path: Path) -> bool:
        """
        Detect a Go module that builds to a program.

        Args:
            path: Path to check.

        Returns:
            True for a ``go.mod`` with a ``package main`` and no Dockerfile: a library is not
            deployable, and a Dockerfile is how the repository says it runs.
        """
        if not (path / "go.mod").is_file() or has_container_file(path):
            return False
        return bool(go_main_packages(path))

    def _module_name(self) -> str:
        """
        Read the module's own name, the last element of its path.

        Returns:
            ``server`` for ``module github.com/acme/server``; empty when unreadable.
        """
        try:
            text = (self.build_path / "go.mod").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        found = _MODULE_DIRECTIVE.search(text)
        return found.group(1).rstrip("/").rsplit("/", 1)[-1] if found else ""

    def _main_package(self) -> str:
        """
        Find the package this application is built from.

        Returns:
            The argument for ``go build``.

        Raises:
            DeploymentError: None, or several and nothing says which.
        """
        return choose_go_package(
            go_main_packages(self.build_path), names=(self._module_name(), self.app_name)
        )

    def pre_build(self) -> bool:
        """
        Refuse a module whose program cannot be told, before compiling anything.

        Returns:
            True.

        Raises:
            DeploymentError: The module has no ``main``, or several and nothing says which.
        """
        if not self.build_config().build:
            self._main_package()
        return True

    def get_install_command(self) -> list[str]:
        """
        Get the command that downloads the modules into the application's cache.

        Returns:
            ``go mod download``; nothing for a module that vendors its dependencies.
        """
        if (self.build_path / "vendor" / "modules.txt").is_file():
            return []
        return ["go", "mod", "download"]

    def get_build_command(self) -> list[str]:
        """
        Get the command that compiles the program.

        Returns:
            ``go build`` into ``bin/<app>``. A module whose program is ambiguous gets ``.``
            here, so that describing the build (``noust inspect``) never fails; the deployment
            itself refuses it in :meth:`pre_build`.
        """
        try:
            package = self._main_package()
        except DeploymentError:
            package = "."
        return [
            "go",
            "build",
            "-trimpath",
            "-buildvcs=false",
            "-o",
            f"{BINARY_DIR}/{self.binary_name()}",
            package,
        ]

    def pre_install(self) -> bool:
        """
        Check that this server's Go can build what ``go.mod`` asks for.

        Go 1.21 and later fetches the toolchain a module names, so only an older Go is a
        problem, and the error says what to install.

        Returns:
            True.

        Raises:
            DeploymentError: ``go.mod`` needs a newer Go than this server has and this one
                cannot fetch it.
        """
        try:
            go_mod = (self.build_path / "go.mod").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return True
        wanted = required_go_version(go_mod)
        if wanted is None:
            return True
        probe = self._run(["go", "version"], timeout=60)
        have = installed_go_version(probe.stdout or "")
        if have is None or have >= wanted:
            return True
        if have >= _GO_SWITCHES_TOOLCHAINS:
            self.logger.substep(
                f"go.mod asks for Go {wanted[0]}.{wanted[1]}; this server's Go "
                f"{have[0]}.{have[1]} will fetch that toolchain on the first build"
            )
            return True
        raise DeploymentError(
            f"go.mod asks for Go {wanted[0]}.{wanted[1]} and this server has {have[0]}.{have[1]}",
            details="Go older than 1.21 cannot fetch a newer toolchain. Install a current Go "
            "system-wide (the build account cannot use one under /root), or lower the go "
            "line of go.mod.",
        )

    def get_template_context(self) -> dict[str, Any]:
        """
        Get the template context.

        Returns:
            The generic one, marked as a Go application.
        """
        context = super().get_template_context()
        context["is_go"] = True
        return context


class RustDeployer(CompiledDeployer):
    """
    Deployer for Rust crates and workspaces: ``cargo build --release`` in the sandbox.

    Which program is built comes from ``cargo metadata`` (never from reading the manifest
    here: ``[[bin]]`` tables, ``default-run`` and workspaces are Cargo's to interpret). The
    compiled dependencies live in the application's cache across releases, so only the crate
    itself is compiled again.
    """

    APP_TYPE = "rust"
    DISPLAY_NAME = "Rust"
    DETECTION_FILES: ClassVar[list[str]] = ["Cargo.toml"]
    SYSTEM_DEPS: ClassVar[list[str]] = ["cargo"]
    TOOLCHAIN_PACKAGES: ClassVar[dict[PackageFamily, str]] = {
        PackageFamily.APT: "cargo",
        PackageFamily.DNF: "cargo",
        PackageFamily.ZYPPER: "cargo",
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
        #: What ``cargo metadata`` said the program is: ``(package, program)``.
        self.target: tuple[str, str] | None = None
        #: Where Cargo puts what it compiles, as ``cargo metadata`` says.
        self.target_directory: Path | None = None

    def detect(self, path: Path) -> bool:
        """
        Detect a Rust crate or workspace.

        Args:
            path: Path to check.

        Returns:
            True for a crate with a ``src/main.rs``, a ``src/bin/`` or a ``[[bin]]`` table, or
            a workspace, and no Dockerfile. Which program a workspace builds is asked of Cargo
            once the deployment begins, and a workspace of libraries fails there with Cargo's
            own words.
        """
        manifest = path / "Cargo.toml"
        if not manifest.is_file() or has_container_file(path):
            return False
        if (path / "src" / "main.rs").is_file() or (path / "src" / "bin").is_dir():
            return True
        try:
            text = manifest.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return "[[bin]]" in text or "[workspace]" in text

    def _locked(self) -> bool:
        """
        Tell whether the build is held to the committed ``Cargo.lock``.

        Returns:
            True when the project or its tree has one: the build then uses exactly what was
            committed and fails rather than resolve something else.
        """
        return any(
            (directory / "Cargo.lock").is_file() for directory in (self.build_path, self.tree_root)
        )

    def get_install_command(self) -> list[str]:
        """
        Get the command that fetches the crates into the application's cache.

        Returns:
            ``cargo fetch``, held to the lock file when there is one.
        """
        return ["cargo", "fetch", *(["--locked"] if self._locked() else [])]

    def get_build_command(self) -> list[str]:
        """
        Get the command that compiles the program.

        Returns:
            ``cargo build --release``, offline (the install phase fetched everything, and the
            build may have no network), for the program ``cargo metadata`` named once known.
        """
        command = ["cargo", "build", "--release", "--offline"]
        if self._locked():
            command.append("--locked")
        if self.target is not None:
            package, program = self.target
            command += ["-p", package, "--bin", program]
        return command

    def post_install(self) -> bool:
        """
        Ask Cargo which program this project builds.

        Run after the crates are fetched, as the build account, with the same cache
        variables the build will have: the target directory it reports is the one the build
        writes to.

        Returns:
            True.

        Raises:
            DeploymentError: Cargo cannot read the project, or it has no program, or several
                and nothing says which.
        """
        if self.build_config().build and self.build_config().start:
            return True
        result = self._run(["cargo", "metadata", "--format-version", "1", "--no-deps"], timeout=300)
        if not result.success:
            raise DeploymentError(
                "cargo metadata could not read the project",
                details=(result.stderr or result.stdout or "No output.").strip(),
            )
        if not (result.stdout or "").strip():
            # A dry run answers nothing: there is nothing to choose from.
            return True
        self.target, self.target_directory = choose_rust_binary(
            result.stdout, project=self.build_path, names=(self.app_name,)
        )
        self.logger.substep(f"Program: {self.target[1]} (package {self.target[0]})")
        return True

    def post_build(self) -> bool:
        """
        Copy the program into the release.

        The compiled output lives in the application's cache so the next deploy can reuse it;
        the release gets its own copy as ``bin/<app>``, which is what ``current`` runs and what
        a rollback goes back to.

        Returns:
            True.

        Raises:
            DeploymentError: Cargo reported success and there is no program where it said.
        """
        if self.build_config().build or self.target is None or self.target_directory is None:
            return True
        source = self.target_directory / "release" / self.target[1]
        result = self._run(
            ["install", "-D", "-m", "0755", str(source), str(self.binary_path())], timeout=300
        )
        if not result.success:
            raise DeploymentError(
                f"The compiled program is not where Cargo said: {source}",
                details=(result.stderr or result.stdout or "No output.").strip(),
            )
        return True

    def get_template_context(self) -> dict[str, Any]:
        """
        Get the template context.

        Returns:
            The generic one, marked as a Rust application.
        """
        context = super().get_template_context()
        context["is_rust"] = True
        return context


def choose_rust_binary(
    metadata: str, *, project: Path, names: tuple[str, ...]
) -> tuple[tuple[str, str], Path]:
    """
    Pick the program a Cargo project builds from ``cargo metadata``.

    Args:
        metadata: The JSON ``cargo metadata --format-version 1 --no-deps`` printed.
        project: The directory it was run in.
        names: Names that point at the right program among several: the application's.

    Returns:
        ``((package, program), target directory)``.

    Raises:
        DeploymentError: The output is not metadata, the project builds no program, or builds
            several and nothing (``default-run``, a name, the root package) says which.
    """
    try:
        document = json.loads(metadata)
        packages = document["packages"]
        target_directory = Path(document["target_directory"])
    except (ValueError, KeyError, TypeError) as exc:
        raise DeploymentError(
            "cargo metadata printed something that is not the project's description",
            details=str(exc),
        ) from exc
    members = set(
        document.get("workspace_default_members") or document.get("workspace_members", [])
    )
    root_manifest = str(project / "Cargo.toml")
    candidates: list[tuple[dict[str, Any], str]] = []
    for package in packages:
        if members and package.get("id") not in members:
            continue
        for target in package.get("targets", []):
            if "bin" in target.get("kind", []):
                candidates.append((package, str(target["name"])))
    if not candidates:
        raise DeploymentError(
            "This Cargo project builds no program",
            details="A deployable crate has a src/main.rs, a src/bin/ directory or a [[bin]] "
            "table. A library cannot be run as a service.",
        )
    if len(candidates) == 1:
        package, program = candidates[0]
        return (str(package["name"]), program), target_directory
    for package, program in candidates:
        if package.get("manifest_path") == root_manifest and package.get("default_run") == program:
            return (str(package["name"]), program), target_directory
    wanted = {name for name in names if name}
    for package, program in candidates:
        if program in wanted or str(package["name"]) in wanted:
            return (str(package["name"]), program), target_directory
    in_root = [(p, n) for p, n in candidates if p.get("manifest_path") == root_manifest]
    if len(in_root) == 1:
        package, program = in_root[0]
        return (str(package["name"]), program), target_directory
    listed = ", ".join(f"{package['name']}:{program}" for package, program in candidates)
    raise DeploymentError(
        f"This Cargo project builds {len(candidates)} programs: {listed}",
        details="Name the one to run with default-run in the root Cargo.toml, or give the build "
        "block: build.build: [cargo, build, --release, --bin, NAME] and build.start.",
    )


DeployerRegistry.register(GoDeployer)
DeployerRegistry.register(RustDeployer)
