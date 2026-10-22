# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``build:`` block: how an application's code is installed, built and started.

Every deployer builds and starts a project with the commands of its type
(``npm ci``, ``npm run build``, ``gunicorn app:app``), and that is what makes a
redeploy and a rollback behave like the first deploy. A project that needs
something else says so here, in the ``noust.yaml`` at the root of its
repository or, for the operator, in the store::

    build:
      base_directory: apps/web            # the project lives in this subdirectory
      install: pnpm install --frozen-lockfile
      build: pnpm --filter web build
      start: node dist/server.js --port ${PORT}
      publish_directory: dist             # static output the web server serves
      runtime: node@22                    # node@22, python@3.12, php@8.3
      cache: false                        # never reuse the build cache (default true)
      timeout: 3600                       # seconds each of install and build may take

Everything is optional and replaces what the application's type would have
done for that step; what is left out keeps the type's own. The commands are
argument vectors (split like a shell would split them, never given to one), run
as ``noust-build`` in the build sandbox, and ``start`` is the unit's
``ExecStart``, where systemd expands ``${PORT}``.

The precedence is the hooks' (:func:`noust.deployers.helpers.hooks.resolve_hooks`):
the operator's block, when the store holds one, replaces the repository's whole;
otherwise the repository's ``noust.yaml``; otherwise nothing. This module holds
the value (:class:`BuildConfig`), the one resolution of "which block applies"
and the operator's read and write shared by the CLI and the API
(rule 3); the schema is read by :mod:`noust.deployers.helpers.project_file`.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

import yaml

from noust.core.exceptions import NoustError, ValidationError
from noust.core.versions import RuntimeRequest

if TYPE_CHECKING:
    from noust.core.store import App, NoustStore

#: Where a block comes from.
BuildSource = Literal["operator", "repo", "none"]

#: Every key of the block, in the order they are shown.
BUILD_KEYS: tuple[str, ...] = (
    "base_directory",
    "install",
    "build",
    "start",
    "publish_directory",
    "runtime",
    "cache",
    "timeout",
)

#: The keys that are commands (argument vectors).
COMMAND_KEYS: tuple[str, ...] = ("install", "build", "start")


#: The shortest and longest time a build step may be given, in seconds: a minute is less than
#: any install takes, and a day is more than a build that is still going to finish.
MIN_TIMEOUT = 60
MAX_TIMEOUT = 86400


#: What makes an argument need quotes to split back into itself: whitespace,
#: a quote, a backslash, or nothing at all. ``$`` and ``{`` do not: a
#: ``${PORT}`` is what the operator wrote, and what systemd expands.
_NEEDS_QUOTES = re.compile(r"""[\s'"\\]|^$""")


def join_argv(argv: Sequence[str]) -> str:
    """
    Write an argument vector as the text it was parsed from.

    Args:
        argv: The program and its arguments.

    Returns:
        The words separated by spaces, quoted only where splitting them again
        (with :func:`shlex.split`) would otherwise change them.
    """
    return " ".join(shlex.quote(word) if _NEEDS_QUOTES.search(word) else word for word in argv)


@dataclass(frozen=True)
class BuildConfig:
    """
    What a project says about how it is built and started.

    Attributes:
        base_directory: The subdirectory of the repository the project lives
            in, as a normalised POSIX path; None for the root. The build runs
            there and the unit and the site point there.
        install: The install command, replacing the type's.
        build: The build command, replacing the type's.
        start: The command the unit runs, replacing the type's.
        publish_directory: Where a static build leaves the files the web server
            serves, relative to the base directory.
        runtime: The pinned runtime, ``node@22``; None for the server's own.
        cache: False to build every deployment from scratch, without the
            package managers' cache or the dependencies of the release that
            serves; None (or True) keeps them, as always.
        timeout: Seconds each of the install and the build may take before
            they are stopped; None takes the server's (``deploy.install_timeout``
            and ``deploy.build_timeout``).
        source: ``operator``, ``repo`` or ``none``.
    """

    base_directory: str | None = None
    install: tuple[str, ...] | None = None
    build: tuple[str, ...] | None = None
    start: tuple[str, ...] | None = None
    publish_directory: str | None = None
    runtime: RuntimeRequest | None = None
    cache: bool | None = None
    timeout: int | None = None
    source: BuildSource = "none"

    @property
    def declared(self) -> bool:
        """Whether the block says anything at all."""
        return bool(self.declared_keys)

    @property
    def declared_keys(self) -> tuple[str, ...]:
        """The keys it sets, in :data:`BUILD_KEYS` order."""
        return tuple(key for key in BUILD_KEYS if getattr(self, key) is not None)

    def command_text(self, key: str) -> str | None:
        """
        Say a command the way it was written.

        Args:
            key: ``install``, ``build`` or ``start``.

        Returns:
            The argument vector joined with shell quoting, which splits back
            to the same vector; None when the block does not set it.
        """
        argv = getattr(self, key)
        return join_argv(argv) if argv else None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the block for the API and ``--json``.

        Returns:
            Every key (None when unset), commands as text, and the source.
        """
        return {
            "source": self.source,
            "base_directory": self.base_directory,
            "install": self.command_text("install"),
            "build": self.command_text("build"),
            "start": self.command_text("start"),
            "publish_directory": self.publish_directory,
            "runtime": self.runtime.text if self.runtime is not None else None,
            "cache": self.cache,
            "timeout": self.timeout,
        }

    def to_mapping(self) -> dict[str, str | bool | int]:
        """
        Say the block as the mapping under ``build:`` in a document.

        Returns:
            The keys it sets, as the text a document holds.
        """
        values = self.to_dict()
        return {
            key: values[key] if isinstance(values[key], bool | int) else str(values[key])
            for key in BUILD_KEYS
            if values[key] is not None
        }

    def to_document(self) -> str:
        """
        Write the block as the document the store keeps.

        Returns:
            ``build:`` and its keys, as YAML.
        """
        return yaml.safe_dump(
            {"build": self.to_mapping()}, sort_keys=False, default_flow_style=False
        )

    def with_source(self, source: BuildSource) -> BuildConfig:
        """
        Label the block with where it came from.

        Args:
            source: ``operator``, ``repo`` or ``none``.

        Returns:
            The same block, labelled; an empty one stays ``none``
            unless it is the operator's, whose empty block still wins.
        """
        if not self.declared and source != "operator":
            return replace(self, source="none")
        return replace(self, source=source)


def project_dir(root: Path, base_directory: str | None) -> Path:
    """
    Find the project inside a tree, without ever walking through a link.

    A repository is untrusted input: ``apps/web`` committed as a link to ``/``
    would make a build, a unit and a site work on the server's own files, as
    root where Noust writes.

    Args:
        root: The tree being deployed: a release, or the application directory.
        base_directory: The block's ``base_directory``.

    Returns:
        ``root`` for no base directory, else the directory inside it.

    Raises:
        ValidationError: A component is a link, or the directory is not in the
            tree; ``field`` is ``build.base_directory``.
    """
    if not base_directory:
        return root
    current = root
    for part in PurePosixPath(base_directory).parts:
        current = current / part
        if current.is_symlink():
            raise ValidationError(
                f"build.base_directory goes through a link: {part}",
                details="A repository's links are never followed. Commit the directory itself.",
                field="build.base_directory",
            )
    if not current.is_dir():
        raise ValidationError(
            f"build.base_directory {base_directory!r} is not a directory of the repository",
            details="Name the subdirectory the project lives in, relative to the root of the "
            "repository (apps/web).",
            field="build.base_directory",
        )
    return current


def refuse_links(root: Path, relative: str, *, field: str) -> Path:
    """
    Name a directory a build leaves, after checking no part of the way is a link.

    The directory need not exist yet (it is checked again after the build),
    but whatever of it does must be the tree's own: a link there would make the
    web server serve, or the unit run in, a place the repository chose.

    Args:
        root: The project.
        relative: The directory, relative to it (``publish_directory``).
        field: The field it came from, for the error.

    Returns:
        The directory.

    Raises:
        ValidationError: A component of the way is a link.
    """
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValidationError(
                f"{field} goes through a link: {part}",
                details="A repository's links are never followed. Build into a real directory.",
                field=field,
            )
    return current


def resolve_build(app: App | None, root: Path, store: NoustStore) -> BuildConfig:
    """
    Name the build block a deployment of an application uses.

    The operator's, when the store holds one for the application, whole;
    otherwise the repository's ``noust.yaml``; otherwise none.

    Args:
        app: The application's row; None for one not registered yet, which
            can only have the repository's.
        root: The tree being deployed, where ``noust.yaml`` is read.
        store: The store holding the operator's block.

    Returns:
        The block, with where it came from.

    Raises:
        ValidationError: The block that applies is not valid; ``field`` names
            what.
    """
    # Imported here: project_file builds the dataclasses this module defines.
    from noust.core import app_build_store
    from noust.deployers.helpers.project_file import load_project_file, parse_build_document

    if app is not None and app.domain:
        document = app_build_store.get_document(app.domain, store=store)
        if document is not None:
            return parse_build_document(document, source="operator")
    return load_project_file(root).build


@dataclass(frozen=True)
class AppBuild:
    """
    The block an application's next deployment would use, and where it comes from.

    Attributes:
        domain: The application.
        build: What applies.
        document: The operator's document as stored, when there is one.
        repository_error: Why the running code's ``noust.yaml`` cannot be
            used, when it cannot (the next deployment would fail with this).
        applied: What the application's unit was last written from, when it
            was written from a build block.
    """

    domain: str
    build: BuildConfig
    document: str | None = None
    repository_error: str | None = None
    applied: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``.

        Returns:
            The block, the operator's document and what the unit runs.
        """
        return {
            "domain": self.domain,
            **self.build.to_dict(),
            "document": self.document,
            "repository_error": self.repository_error,
            "applied": self.applied,
        }


def _known(domain: str, store: NoustStore) -> App:
    """
    Read an application's row or say it does not exist.

    Args:
        domain: A validated domain.
        store: The store.

    Returns:
        The row.

    Raises:
        NoustError: The application is unknown.
    """
    app = store.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    return app


def describe_build(domain: str, *, store: NoustStore | None = None) -> AppBuild:
    """
    Say which build block an application's next deployment uses: the one read CLI and API share.

    The repository's is read from the code that runs now; the next deploy
    reads it from what it fetches, which may differ.

    Args:
        domain: A validated domain.
        store: The store; the process-wide one when None.

    Returns:
        What applies and where it comes from.

    Raises:
        NoustError: The application is unknown.
    """
    from noust.core import app_build_store
    from noust.core.store import get_store
    from noust.deployers.helpers.layout import code_path_for
    from noust.deployers.helpers.project_file import load_project_file, parse_build_document

    target = store or get_store()
    app = _known(domain, target)
    applied = app_build_store.get_applied(domain, store=target)
    document = app_build_store.get_document(domain, store=target)
    if document is not None:
        return AppBuild(
            domain=domain,
            build=parse_build_document(document, source="operator"),
            document=document,
            applied=applied,
        )
    try:
        build = load_project_file(code_path_for(app)).build
    except ValidationError as exc:
        return AppBuild(
            domain=domain, build=BuildConfig(), repository_error=str(exc), applied=applied
        )
    return AppBuild(domain=domain, build=build, applied=applied)


def refuse_unsupported(
    build: BuildConfig,
    app_type: str,
    allowed: Collection[str] | None = None,
    languages: Collection[str] | None = None,
) -> None:
    """
    Refuse a block that sets what an application's type cannot do.

    Args:
        build: What applies.
        app_type: The application's type.
        allowed: The keys the type honours, when the caller is the deployer
            and has them; read from the registry's class for ``app_type``
            otherwise (the operator's block is checked before any deployer
            exists).
        languages: The runtimes the type can be pinned to, the same way.

    Raises:
        ValidationError: A key the type does not honour, with the way out.
            ``field`` is ``build.<key>``.
    """
    from noust.deployers.registry import DeployerRegistry, _import_deployers

    if not build.declared:
        return
    if allowed is None:
        _import_deployers()
        deployer = DeployerRegistry.get(app_type)
        allowed = getattr(deployer, "BUILD_KEYS", None)
        languages = getattr(deployer, "RUNTIME_LANGUAGES", frozenset())
    if languages is None:
        languages = frozenset()
    if allowed is None:
        raise ValidationError(
            f"A {app_type} application is not built by Noust's build block",
            details="It is built where its own configuration says (a Compose file, a "
            "workspace). Remove the build block, or deploy the project as the type that "
            "runs it.",
            field="build",
        )
    for key in build.declared_keys:
        # Not building from the cache, and how long a build may take, are things
        # every type that is built by Noust can do.
        if key not in allowed and key not in ("cache", "timeout"):
            raise ValidationError(
                f"build.{key} does not apply to a {app_type} application",
                details=_why_not(key, app_type),
                field=f"build.{key}",
            )
    if build.start and build.publish_directory:
        raise ValidationError(
            "build.start and build.publish_directory cannot both be set",
            details="An application is either files the web server serves (publish_directory) "
            "or a process it proxies to (start); name the one it is.",
            field="build.publish_directory",
        )
    if build.runtime is not None and build.runtime.language not in languages:
        raise ValidationError(
            f"build.runtime pins {build.runtime.language}, which a {app_type} application "
            "does not run on",
            details=f"Pin one of: {', '.join(sorted(languages)) or 'none'}.",
            field="build.runtime",
        )


def _why_not(key: str, app_type: str) -> str:
    """
    Say why a key does not apply to a type, and what to do instead.

    Args:
        key: The key.
        app_type: The type.

    Returns:
        A sentence for ``details``.
    """
    if key == "start":
        return (
            f"A {app_type} application runs no process of its own that Noust starts. "
            "Deploy the project as a Node.js or Python application to give it a start command."
        )
    if key == "publish_directory":
        return (
            f"A {app_type} application runs a process; publish_directory is for static "
            "output the web server serves (a static site, or a Vite build). Use start instead."
        )
    if key == "base_directory":
        return (
            "PHP applications serve their web root: set it with webroot in the PHP settings "
            "(for a project in a subdirectory, webroot: backend/public)."
        )
    return f"A {app_type} application has no use for {key}."


def set_operator_build(
    domain: str,
    document: str | None,
    *,
    actor: str | None,
    store: NoustStore | None = None,
) -> BuildConfig:
    """
    Store, or clear, an application's build block: the one write CLI and API share.

    The block is code that runs in the application's sandbox and as its unit on
    every deployment, so the change is recorded in the audit trail here, where
    neither caller can forget it (rule 4).

    Args:
        domain: A validated domain.
        document: The YAML document (``build:`` and its keys), or None to
            clear it and let the repository's apply again.
        actor: Who changed it, for the store's record.
        store: The store; the process-wide one when None.

    Returns:
        The block the document declares; an empty one once cleared.

    Raises:
        NoustError: The application is unknown.
        ValidationError: The document is not valid (naming the field), or sets
            what the application's type cannot do.
    """
    from noust.core import app_build_store, audit
    from noust.core.store import get_store
    from noust.deployers.helpers.project_file import parse_build_document

    target = store or get_store()
    app = _known(domain, target)
    build = BuildConfig()
    stored: str | None = None
    if document is not None:
        build = parse_build_document(document, source="operator")
        refuse_unsupported(build, app.app_type)
        # What is stored is what was validated, written the one way, so a
        # reader never meets a comment or a key order of somebody's editor.
        stored = build.to_document()
    app_build_store.set_document(domain, stored, updated_by=actor, store=target)
    audit.record(
        "apps.build",
        target=f"app:{domain}",
        details={
            "action": "clear" if document is None else "set",
            "keys": list(build.declared_keys),
            "base_directory": build.base_directory,
            "runtime": build.runtime.text if build.runtime else None,
            "start": build.command_text("start"),
            "install": build.command_text("install"),
            "build": build.command_text("build"),
            "timeout": build.timeout,
        },
    )
    return build


__all__ = [
    "BUILD_KEYS",
    "COMMAND_KEYS",
    "AppBuild",
    "BuildConfig",
    "BuildSource",
    "describe_build",
    "join_argv",
    "project_dir",
    "refuse_links",
    "refuse_unsupported",
    "resolve_build",
    "set_operator_build",
]
