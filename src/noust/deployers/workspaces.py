# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A JavaScript monorepo as one application per workspace, on releases (checklist A-53).

The monorepo deployer wants a ``turbo.json``, one workspace configuration and at least two
apps under ``apps/``, and deploys them in place, with no instant rollback and no previews. That
was a deployer of its own for what the rest of Noust already does: a repository can hold several
applications, each with its own ``build.base_directory``, its own watch paths (a push that
touches ``apps/api`` deploys the API and not the web app) and its own releases. This module is
what turns a monorepo into those applications.

:func:`detect_workspaces` reads what the workspace tools declare (npm and Yarn ``workspaces``,
``pnpm-workspace.yaml``, ``lerna.json``, Nx and Turborepo on top of them), finds the workspace
directories, says which are applications and which are libraries, and for each application
proposes the build block and the watch paths: its own directory, the workspaces it depends on,
and the files that change every workspace (the lock file, the root ``package.json``).
:func:`workspace_document` turns one into the export document that ``noust app import`` already
plans and applies, so creating the applications is the one path every import takes: built,
health-gated and recorded like any deployment, on the releases layout.

Detection reads ``package.json`` files and a few configuration files, as data: nothing of the
repository is run, a link is never followed, and every file read is capped.
"""

from __future__ import annotations

import json
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml

from noust.core.exceptions import ValidationError
from noust.core.source_rules import watch_paths_match
from noust.deployers.app_export import proposal_document
from noust.deployers.helpers.build_config import join_argv
from noust.deployers.helpers.package_manager import PackageManagerHelper
from noust.deployers.importers.base import Proposal
from noust.deployers.registry import DeployerRegistry, _import_deployers

#: The workspace tools a repository can use, most specific first.
WorkspaceTool = Literal["turborepo", "nx", "lerna", "pnpm", "yarn", "npm", "none"]

#: The largest configuration file read.
MAX_FILE_BYTES = 256 * 1024

#: How many workspaces one repository lists at most.
MAX_WORKSPACES = 100

#: How deep a workspace directory may be, counting its parts (``apps/web`` is 2).
MAX_DEPTH = 4

#: Directories that never hold a workspace.
_SKIPPED = frozenset({"node_modules", ".git", ".turbo", ".next", "dist", "build", ".yarn"})

#: What changes every workspace's build: a push touching one deploys them all.
ROOT_FILES = (
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "yarn.lock",
    "package-lock.json",
    "bun.lockb",
    "bun.lock",
    "turbo.json",
    "nx.json",
    "lerna.json",
    ".npmrc",
)

#: The patterns a sparse checkout needs to read workspaces: configuration at the root, and the
#: ``package.json`` of every directory two or three levels down.
SPARSE_PATTERNS = (
    *(f"/{name}" for name in ROOT_FILES),
    "/*/*/package.json",
    "/*/*/*/package.json",
)

#: What a Node application in a workspace is when it is not a framework.
_SERVER_DEPENDENCIES = frozenset(
    {"express", "fastify", "koa", "hono", "@nestjs/core", "@hapi/hapi", "restify", "polka"}
)

_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")

_TURBO_COMMAND: dict[str, tuple[str, ...]] = {
    "pnpm": ("pnpm", "exec", "turbo"),
    "yarn": ("yarn", "turbo"),
    "bun": ("bunx", "turbo"),
    "npm": ("npx", "turbo"),
}


@dataclass(frozen=True)
class Workspace:
    """
    One package of the monorepo.

    Attributes:
        name: Its ``package.json`` name; the directory's name when it has none.
        directory: Where it is, relative to the repository, with ``/``.
        app_type: The Noust type that would deploy it; None when it is a library.
        deployable: Whether it is an application.
        reason: Why it is not, in a sentence; empty for an application.
        depends_on: The names of the workspaces it depends on, directly.
        watch_paths: What a push must touch to deploy it: itself, everything it depends on, and
            the files that change every workspace.
        build: The command that builds it together with what it depends on (Turborepo only);
            None leaves the type's own build.
    """

    name: str
    directory: str
    app_type: str | None
    deployable: bool
    reason: str = ""
    depends_on: tuple[str, ...] = ()
    watch_paths: tuple[str, ...] = ()
    build: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``.

        Returns:
            Its fields, commands as lists.
        """
        return {
            "name": self.name,
            "directory": self.directory,
            "app_type": self.app_type,
            "deployable": self.deployable,
            "reason": self.reason,
            "depends_on": list(self.depends_on),
            "watch_paths": list(self.watch_paths),
            "build": list(self.build) if self.build else None,
        }


@dataclass(frozen=True)
class WorkspaceReport:
    """
    What a repository's workspaces are.

    Attributes:
        tool: Which workspace tool configures them; ``none`` when there are none.
        package_manager: ``npm``, ``pnpm``, ``yarn`` or ``bun``.
        workspaces: Every workspace, by directory.
        problems: What could not be read, one sentence each.
    """

    tool: WorkspaceTool = "none"
    package_manager: str = "npm"
    workspaces: tuple[Workspace, ...] = ()
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def deployable(self) -> tuple[Workspace, ...]:
        """The workspaces that are applications."""
        return tuple(workspace for workspace in self.workspaces if workspace.deployable)

    def named(self, key: str) -> Workspace | None:
        """
        Args:
            key: A workspace's name or its directory.

        Returns:
            It, or None.
        """
        for workspace in self.workspaces:
            if key in (workspace.name, workspace.directory):
                return workspace
        return None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the report for the API and ``--json``.

        Returns:
            The tool, the package manager and every workspace.
        """
        return {
            "tool": self.tool,
            "package_manager": self.package_manager,
            "workspaces": [workspace.to_dict() for workspace in self.workspaces],
            "problems": list(self.problems),
        }


# Reading -----------------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    """
    Args:
        path: A JSON file.

    Returns:
        Its object; None when it is missing, a link, too large or not an object.
    """
    if path.is_symlink() or not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _patterns(root: Path) -> tuple[WorkspaceTool, list[str], list[str]]:
    """
    Read the workspace globs the repository declares.

    Args:
        root: The repository.

    Returns:
        The tool, the globs, and what could not be read.
    """
    problems: list[str] = []
    patterns: list[str] = []
    package = _read_json(root / "package.json") or {}
    declared = package.get("workspaces")
    if isinstance(declared, dict):
        declared = declared.get("packages")
    if isinstance(declared, list):
        patterns = [item for item in declared if isinstance(item, str)]
    pnpm = root / "pnpm-workspace.yaml"
    tool: WorkspaceTool = "none"
    if pnpm.is_file() and not pnpm.is_symlink():
        try:
            if pnpm.stat().st_size <= MAX_FILE_BYTES:
                data = yaml.safe_load(pnpm.read_text(encoding="utf-8")) or {}
                listed = data.get("packages") if isinstance(data, dict) else None
                if isinstance(listed, list):
                    patterns = [item for item in listed if isinstance(item, str)]
                tool = "pnpm"
        except (OSError, yaml.YAMLError) as exc:
            problems.append(f"pnpm-workspace.yaml could not be read: {exc}")
    lerna = _read_json(root / "lerna.json")
    if lerna is not None and not patterns:
        listed = lerna.get("packages")
        patterns = (
            [item for item in listed if isinstance(item, str)]
            if isinstance(listed, list)
            else ["packages/*"]
        )
    if tool == "none" and patterns:
        tool = "yarn" if (root / "yarn.lock").is_file() else "npm"
    if lerna is not None and patterns:
        tool = "lerna"
    if (root / "nx.json").is_file() and patterns:
        tool = "nx"
    if (root / "turbo.json").is_file() and patterns:
        tool = "turborepo"
    return tool, patterns, problems


def _matches(patterns: Sequence[str], directory: str) -> bool:
    """
    Decide whether a directory is a workspace the globs list.

    The last pattern that matches decides, and ``!`` excludes, as in a ``package.json``'s
    ``workspaces``. A pattern with no glob character names one directory exactly: unlike a
    watch path, it does not cover the directories beneath it.

    Args:
        patterns: The globs.
        directory: A directory, relative, with ``/``.

    Returns:
        Whether it is listed.
    """
    decided = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        body = (pattern[1:] if negated else pattern).strip().lstrip("./").rstrip("/")
        if not body or body.startswith("/") or ".." in PurePosixPath(body).parts:
            continue
        if any(char in body for char in "*?["):
            hit = watch_paths_match([body], directory)
        else:
            hit = directory == body
        if hit:
            decided = not negated
    return decided


def _directories(root: Path, listing: Sequence[str] | None) -> list[str]:
    """
    List the directories that hold a ``package.json``.

    Args:
        root: The repository.
        listing: Every file of the commit, when the checkout is sparse and the directories
            are not all on disk; None reads the directory tree.

    Returns:
        The directories, relative with ``/``, shallowest first.
    """
    found: set[str] = set()
    if listing is not None:
        for name in listing:
            path = PurePosixPath(name)
            if path.name == "package.json" and len(path.parts) > 1:
                parent = path.parent
                if not _SKIPPED.intersection(parent.parts) and len(parent.parts) <= MAX_DEPTH:
                    found.add(parent.as_posix())
        return sorted(found, key=lambda value: (value.count("/"), value))
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop(0)
        if depth >= MAX_DEPTH:
            continue
        try:
            entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name in _SKIPPED or entry.is_symlink() or not entry.is_dir():
                continue
            if (entry / "package.json").is_file() and not (entry / "package.json").is_symlink():
                found.add(entry.relative_to(root).as_posix())
            pending.append((entry, depth + 1))
    return sorted(found, key=lambda value: (value.count("/"), value))


def _dependencies(package: dict[str, Any]) -> dict[str, str]:
    """
    Args:
        package: A ``package.json``.

    Returns:
        Every dependency name it declares, whichever list it is in, to its range.
    """
    merged: dict[str, str] = {}
    for key in _DEPENDENCY_FIELDS:
        value = package.get(key)
        if isinstance(value, dict):
            merged.update({str(name): str(version) for name, version in value.items()})
    return merged


def _classify(directory: Path, package: dict[str, Any]) -> tuple[str | None, str]:
    """
    Say whether a workspace is an application, and as what.

    Args:
        directory: The workspace on disk.
        package: Its ``package.json``.

    Returns:
        The Noust type and an empty reason for an application; ``(None, why)`` for a library.
    """
    _import_deployers()
    app_type = DeployerRegistry.detect(directory)
    if app_type is None or app_type == "auto":
        return None, "Noust has no type that deploys it"
    if app_type == "nodejs":
        declared = package.get("scripts")
        scripts: dict[str, Any] = declared if isinstance(declared, dict) else {}
        if "start" not in scripts and not _SERVER_DEPENDENCIES & set(_dependencies(package)):
            return None, "a library: it has no start script and no server framework"
    return app_type, ""


def detect_workspaces(root: Path, listing: Sequence[str] | None = None) -> WorkspaceReport:
    """
    Find the workspaces of a repository and which of them are applications.

    Args:
        root: The repository on disk (a sparse checkout is enough: only ``package.json`` files
            and a few configuration files are read).
        listing: Every file of the commit, when the checkout is sparse; None reads the tree.

    Returns:
        The report; ``tool`` is ``none`` for a repository that declares no workspaces.
    """
    tool, patterns, problems = _patterns(root)
    if tool == "none" or not patterns:
        return WorkspaceReport(problems=tuple(problems))
    manager = PackageManagerHelper().declared_manager(root) or "npm"
    root_package = _read_json(root / "package.json") or {}
    uses_turbo = (root / "turbo.json").is_file() and "turbo" in _dependencies(root_package)

    packages: dict[str, tuple[str, dict[str, Any]]] = {}
    for directory in _directories(root, listing):
        if directory == "." or not _matches(patterns, directory):
            continue
        package = _read_json(root / directory / "package.json")
        if package is None:
            problems.append(f"{directory}/package.json could not be read")
            continue
        name = package.get("name") if isinstance(package.get("name"), str) else None
        packages[directory] = (name or PurePosixPath(directory).name, package)
        if len(packages) >= MAX_WORKSPACES:
            problems.append(f"Only the first {MAX_WORKSPACES} workspaces are listed")
            break

    by_name = {name: directory for directory, (name, _package) in packages.items()}
    direct: dict[str, tuple[str, ...]] = {
        directory: tuple(
            sorted(dep for dep in _dependencies(package) if dep in by_name and dep != name)
        )
        for directory, (name, package) in packages.items()
    }
    root_files = tuple(name for name in ROOT_FILES if (root / name).is_file())

    workspaces: list[Workspace] = []
    for directory, (name, package) in packages.items():
        app_type, reason = _classify(root / directory, package)
        closure: list[str] = []
        pending = list(direct[directory])
        while pending:
            dependency = pending.pop()
            # A cycle leads back to the workspace itself, which is already watched.
            if dependency in closure or dependency == name:
                continue
            closure.append(dependency)
            pending.extend(direct[by_name[dependency]])
        watch = (
            tuple(
                f"{place}/**" for place in [directory, *(by_name[dep] for dep in sorted(closure))]
            )
            + root_files
        )
        build: tuple[str, ...] | None = None
        if uses_turbo and app_type is not None:
            build = (
                *_TURBO_COMMAND.get(manager, _TURBO_COMMAND["npm"]),
                "run",
                "build",
                f"--filter={name}...",
            )
        workspaces.append(
            Workspace(
                name=name,
                directory=directory,
                app_type=app_type,
                deployable=app_type is not None,
                reason=reason,
                depends_on=direct[directory],
                watch_paths=watch if app_type is not None else (),
                build=build,
            )
        )
    return WorkspaceReport(tool, manager, tuple(workspaces), tuple(problems))


# Fetching ----------------------------------------------------------------------------------


def inspect_workspaces(
    source: str, *, branch: str | None = None, cancel: threading.Event | None = None
) -> WorkspaceReport:
    """
    Fetch as little of a repository as workspaces need, and say what they are.

    Args:
        source: Git URL, archive URL or local path, as :meth:`SourceManager.fetch` accepts it.
        branch: The branch, for a git source.
        cancel: Set it, from any thread, to stop the fetch.

    Returns:
        The report.

    Raises:
        SourceError: The source is invalid or could not be fetched.
        CommandCancelled: ``cancel`` was set.
    """
    from noust.core.runner import cancellable
    from noust.deployers.inspect import SCRATCH_PREFIX
    from noust.managers.source_manager import SourceManager
    from noust.validators.source import validate_source

    event = cancel if cancel is not None else threading.Event()
    with cancellable(event):
        source_type, normalized = validate_source(source)
        if source_type == "local":
            return detect_workspaces(Path(normalized))
        manager = SourceManager()
        with tempfile.TemporaryDirectory(prefix=SCRATCH_PREFIX) as scratch:
            checkout = Path(scratch) / "source"
            if source_type == "git":
                if manager.sparse_clone(
                    normalized, checkout, branch=branch, patterns=list(SPARSE_PATTERNS)
                ):
                    return detect_workspaces(checkout, manager.list_files(checkout))
                checkout = Path(scratch) / "clone"
                manager.clone_git(
                    normalized, checkout, branch=branch, depth=1, recursive=False, lfs=False
                )
                return detect_workspaces(checkout)
            manager.fetch(normalized, checkout, depth=1)
            return detect_workspaces(checkout)


# Creating ----------------------------------------------------------------------------------


def workspace_document(
    report: WorkspaceReport,
    workspace: Workspace,
    *,
    domain: str,
    source: str,
    branch: str | None = None,
) -> dict[str, Any]:
    """
    Write an application that deploys one workspace, as an export document.

    The document is what ``noust app import`` plans and applies: the application is created by
    the normal deploy path on the releases layout, with the workspace as its base directory,
    its watch paths as source options and, in a Turborepo, the build of the workspace and what
    it depends on.

    Args:
        report: What :func:`detect_workspaces` found.
        workspace: The workspace to deploy.
        domain: The domain to create the application on.
        source: The repository.
        branch: The branch, or None for the default.

    Returns:
        The document.

    Raises:
        ValidationError: The workspace is a library.
    """
    if not workspace.deployable:
        raise ValidationError(
            f"{workspace.name} is not an application: {workspace.reason}",
            details="Deploy a workspace that has a start script or a framework.",
            field="workspace",
        )
    proposal = Proposal(
        platform="workspace",
        app_type=workspace.app_type,
        name=workspace.name,
        base_directory=workspace.directory,
        build_command=join_argv(workspace.build) if workspace.build else None,
        watch_paths=list(workspace.watch_paths),
    )
    document = proposal_document(proposal, domain=domain, source=source, branch=branch)
    document["app"]["layout"] = "releases"
    return document


__all__ = [
    "ROOT_FILES",
    "SPARSE_PATTERNS",
    "Workspace",
    "WorkspaceReport",
    "WorkspaceTool",
    "detect_workspaces",
    "inspect_workspaces",
    "workspace_document",
]
