# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a recipe is, once it has been validated: the closed lists and the records.

Nothing here reads a file or runs anything. :mod:`noust.recipes.schema` builds
these records from a YAML document, :mod:`noust.recipes.catalog` finds them,
and everything else (the deploy plan, the API, the command line) reads them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from noust.core.exceptions import ValidationError

#: The recipe format this release writes. A file without ``schema`` is the
#: 2.3 format (version 1) and is read as a native recipe.
SCHEMA_VERSION = 2

#: What a recipe is named: its file name, and what ``--recipe`` takes.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

#: Words the recipes API uses as a path under ``/api/recipes``: no recipe is
#: called one, or its page could not be reached.
RESERVED_NAMES = frozenset({"installed", "categories", "private", "catalog"})

#: How a recipe is deployed. ``native``: a pinned source built by one of the
#: deployers, a systemd unit and nginx. ``compose``: an inline Docker Compose
#: document of pinned images, deployed by the Compose deployer behind nginx.
KINDS = ("native", "compose")

#: Where a native recipe's code comes from.
SOURCE_KINDS = ("git", "archive", "template")

#: Database engines a v1 recipe asked for, as the database helper spells them.
DATABASE_ENGINES = ("mysql", "mariadb", "postgresql")

#: Layouts a recipe may ask for.
LAYOUTS = ("releases", "inplace")

#: The catalogue's categories. A closed list on purpose: Coolify's free text
#: ended up with 38 spellings ("Networking" and "networking" among them), which
#: makes a filter useless.
CATEGORIES = (
    "ai",
    "analytics",
    "automation",
    "backend",
    "business",
    "cms",
    "communication",
    "data",
    "development",
    "files",
    "media",
    "monitoring",
    "productivity",
    "search",
    "security",
    "smart-home",
    "utilities",
    "other",
)

#: The architectures a recipe may name, as Docker spells them.
ARCHITECTURES = ("amd64", "arm64", "arm", "386", "riscv64")

#: What a recipe may ask the server to provide (``requires``). The databases and
#: the keyed stores are provisioned through the existing database managers; S3 is
#: an external service the operator names, because Noust manages no object store.
REQUIREMENTS = ("postgres", "mysql", "mariadb", "redis", "valkey", "mongodb", "s3")

#: The requirements that are a SQL database and an account, as the v1 helper
#: provisions them.
SQL_REQUIREMENTS = ("postgres", "mysql", "mariadb")

#: The requirements the database service provisions with a connection string.
KEYED_REQUIREMENTS = ("redis", "valkey", "mongodb")

#: A requirement's name to the engine the database managers know it as.
ENGINE_OF = {
    "postgres": "postgresql",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "redis": "redis",
    "valkey": "valkey",
    "mongodb": "mongodb",
}

#: What a variable may take from a requirement (``from: postgres.url``).
REQUIREMENT_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "postgres": ("url", "host", "port", "name", "user", "password"),
    "mysql": ("url", "host", "port", "name", "user", "password"),
    "mariadb": ("url", "host", "port", "name", "user", "password"),
    "redis": ("url", "host", "port", "password"),
    "valkey": ("url", "host", "port", "password"),
    "mongodb": ("url", "host", "port", "name", "user", "password"),
    "s3": ("endpoint", "bucket", "region", "access_key", "secret_key"),
}

#: The random forms a variable may be generated in. The names follow what the
#: catalogues call them: a password of 32 or 64 letters and digits, 32 or 64
#: hexadecimal characters, the base64 of 32 or 64 random bytes, a user name,
#: and a signed JWT.
GENERATORS = (
    "user",
    "password32",
    "password64",
    "hex32",
    "hex64",
    "base64_32",
    "base64_64",
    "jwt",
)

#: Generated forms whose value is a secret.
SECRET_GENERATORS = frozenset(GENERATORS) - {"user"}

#: What a variable may take from the deployment itself (``from: url``).
CONTEXT_SOURCES = ("url", "fqdn", "scheme", "https", "port", "app_name", "app_path")

#: The variable Noust always provides and no recipe may declare: the port the
#: web server proxies to. A Compose document publishes it as
#: ``127.0.0.1:${PORT}:<container port>``.
RESERVED_VARIABLES = frozenset({"PORT"})

#: Image tags that name no release: they move.
FLOATING_TAGS = frozenset(
    {
        "latest",
        "stable",
        "edge",
        "nightly",
        "main",
        "master",
        "dev",
        "develop",
        "devel",
        "canary",
        "next",
        "beta",
        "alpha",
    }
)


class RecipeError(ValidationError):
    """A recipe file is not valid, or names something that does not exist."""


class RecipeNotFoundError(RecipeError):
    """No recipe has that name."""


#: Where a recipe's file is, by name, for the error that names it. A recipe
#: from the package is in the source tree; the catalogue records where it found
#: each of the others (an operator's folder, a catalogue package).
_LOCATIONS: dict[str, str] = {}

#: Where a recipe comes from: the package, a signed catalogue package that is
#: newer than it, or the operator's own folder.
ORIGINS = ("shipped", "catalog", "operator")


def set_location(name: str, location: str | None) -> None:
    """
    Remember where a recipe's file is, so an error about it can say.

    Args:
        name: The recipe.
        location: The file's path, or None to forget it (the package's own).
    """
    if location is None:
        _LOCATIONS.pop(name, None)
    else:
        _LOCATIONS[name] = location


def recipe_error(name: str, message: str, *, field: str | None = None) -> RecipeError:
    """
    Build the error for an invalid recipe.

    Args:
        name: The recipe.
        message: What is wrong.
        field: The part of the file it is about.

    Returns:
        The error, naming the file.
    """
    where = _LOCATIONS.get(name, f"src/noust/recipes/{name}.yaml")
    return RecipeError(
        f"Recipe {name!r} is not valid: {message}",
        details=f"Fix {where}; see noust.recipes for the format.",
        field=field,
    )


@dataclass(frozen=True)
class RecipeSource:
    """
    Where a native recipe's code comes from.

    Attributes:
        kind: ``git``, ``archive`` or ``template``.
        url: The repository or archive URL; empty for a template.
        ref: The git tag or branch deployed.
        sha256: The archive's pinned SHA-256.
        checksum_url: Where the archive's checksum is published.
        files: For a template: path in the source to asset name.
    """

    kind: str
    url: str = ""
    ref: str | None = None
    sha256: str | None = None
    checksum_url: str | None = None
    files: dict[str, str] = field(default_factory=dict)

    def deploy_source(self) -> str:
        """
        Spell the source as a deployment takes it.

        An archive carries its checksum in the URL fragment, which
        :meth:`~noust.managers.source_manager.SourceManager.download_archive`
        verifies on every download, updates included.

        Returns:
            The URL; for a template, an empty string (the caller renders it).
        """
        if self.kind == "archive" and self.sha256:
            return f"{self.url}#sha256={self.sha256.lower()}"
        if self.kind == "archive" and self.checksum_url:
            return f"{self.url}#checksum={self.checksum_url}"
        return self.url

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the source for the CLI and the API.

        Returns:
            ``kind`` plus whichever of the fields apply.
        """
        described: dict[str, Any] = {"kind": self.kind}
        if self.url:
            described["url"] = self.url
        if self.ref:
            described["ref"] = self.ref
        if self.sha256:
            described["sha256"] = self.sha256
        if self.checksum_url:
            described["checksum_url"] = self.checksum_url
        if self.files:
            described["files"] = sorted(self.files)
        return described


@dataclass(frozen=True)
class RecipeHealth:
    """
    What the health gate asks of a recipe's application.

    Attributes:
        path: The path probed.
        expect: The statuses that mean up, or None for below 500.
        timeout: Seconds the application gets to come up, or None for the
            gate's own wait; a JVM or a model server needs more.
    """

    path: str = "/"
    expect: str | None = None
    timeout: int | None = None


@dataclass(frozen=True)
class RecipeBuild:
    """
    How a native recipe is installed, built and started.

    The same block, with the same names, as the ``build:`` of a repository's
    ``noust.yaml`` (the native build lane), so a recipe and a repository say
    the same thing the same way. Every key is optional: what is left out is
    what the application's type does by default. Commands are split with
    ``shlex`` and run as an argv, never through a shell.

    Attributes:
        base_directory: The directory inside the source that holds the project.
        install: The command that installs dependencies.
        build: The command that builds.
        start: The command the unit runs.
        publish_directory: Where a static build puts what is served.
        runtime: The language runtime and its major version (``node@22``).
    """

    base_directory: str | None = None
    install: str | None = None
    build: str | None = None
    start: str | None = None
    publish_directory: str | None = None
    runtime: str | None = None

    def to_dict(self) -> dict[str, str]:
        """
        Describe the block for the CLI and the API.

        Returns:
            The keys that are set.
        """
        return {key: value for key, value in vars(self).items() if value is not None}


@dataclass(frozen=True)
class RecipeVariable:
    """
    One variable of a recipe, as it ends up in the application's ``.env``.

    Exactly one of ``generate``, ``source``, ``value``, ``default`` or
    ``required`` says where its value comes from; the operator can override
    any of them at install time and edit them afterwards, and a value Noust
    generated is never generated again.

    Attributes:
        name: The environment variable.
        description: What it is for, shown where the operator is asked.
        generate: One of :data:`GENERATORS`.
        source: ``from`` in the file: a deployment value (``url``) or what a
            requirement provides (``postgres.url``).
        value: A fixed template over other variables (``${PUBLIC_URL}/``).
        default: The value when the operator gives none; an empty string is
            a value.
        required: Installation stops until the operator gives it.
        secret: The value must not be shown or logged in clear.
        service: For ``url``/``fqdn``: the Compose service it points at.
        port: For ``url``/``fqdn``: the container port it points at.
        signed_with: For ``jwt``: the variable that holds the signing secret.
        role: For ``jwt``: the ``role`` claim.
    """

    name: str
    description: str = ""
    generate: str | None = None
    source: str | None = None
    value: str | None = None
    default: str | None = None
    required: bool = False
    secret: bool = False
    service: str | None = None
    port: int | None = None
    signed_with: str | None = None
    role: str | None = None

    @property
    def is_input(self) -> bool:
        """Whether the operator is asked for it: it has a default or is required."""
        return self.required or self.default is not None or self.is_external

    @property
    def is_external(self) -> bool:
        """Whether it comes from a service Noust does not run (S3)."""
        return self.source is not None and self.source.startswith("s3.")

    @property
    def is_required(self) -> bool:
        """Whether installation stops until the operator gives it."""
        return self.required or self.is_external

    def origin(self) -> str:
        """
        Say where its value comes from, for the API.

        Returns:
            ``generated``, ``deployment``, ``provisioned``, ``composed``,
            ``default`` or ``required``.
        """
        if self.generate is not None:
            return "generated"
        if self.source is not None:
            if self.is_external:
                return "required"
            return "provisioned" if "." in self.source else "deployment"
        if self.value is not None:
            return "composed"
        if self.required:
            return "required"
        return "default"

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the variable without any value that could be a secret.

        Returns:
            Name, origin, whether the operator is asked, whether it is
            required or secret, its description, and its default when it is
            neither generated nor secret.
        """
        described: dict[str, Any] = {
            "name": self.name,
            "origin": self.origin(),
            "input": self.is_input,
            "required": self.is_required,
            "secret": self.secret,
            "description": self.description,
            "default": None if self.secret else self.default,
        }
        if self.generate is not None:
            described["generate"] = self.generate
        if self.source is not None:
            described["from"] = self.source
        return described


@dataclass(frozen=True)
class RecipeImage:
    """
    One image of a Compose recipe, as the document pins it.

    Attributes:
        service: The Compose service that runs it.
        reference: The image exactly as the document writes it.
        repository: The name without tag or digest.
        tag: The tag.
        digest: The ``sha256:`` digest, when the reference carries one.
    """

    service: str
    reference: str
    repository: str
    tag: str
    digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the image for the API.

        Returns:
            Service, repository, tag and digest.
        """
        return {
            "service": self.service,
            "repository": self.repository,
            "tag": self.tag,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class PublicPort:
    """
    A port a Compose recipe publishes on every address, on purpose.

    Everything else a recipe publishes is bound to ``127.0.0.1`` and reached
    through nginx; a port listed here is open to the network, which the
    operator is told before installing.

    Attributes:
        port: The port, the same on the host and in the container.
        protocol: ``tcp`` or ``udp``.
        purpose: What it is for.
    """

    port: int
    protocol: str
    purpose: str


@dataclass(frozen=True)
class ChangelogEntry:
    """
    What a version of a recipe changed, for the operator deciding to update.

    Attributes:
        version: The version of the application.
        summary: One sentence.
    """

    version: str
    summary: str


#: Where a variable's value comes from when the operator is not asked.
_SUPPLIED = frozenset({"generated", "deployment", "provisioned", "composed"})


@dataclass(frozen=True)
class Recipe:
    """
    One recipe, validated.

    Attributes:
        name: The recipe's name.
        title: What it is called.
        description: One or two sentences.
        homepage: The project's site.
        schema: The format version the file was written in (1 or 2).
        kind: ``native`` or ``compose``.
        slogan: One line for the catalogue card.
        category: One of :data:`CATEGORIES`.
        tags: Lower-case labels for the catalogue's filter.
        logo: The SVG shipped under ``assets/<name>/``.
        version: The version of the application the recipe pins.
        revision: The recipe's own revision of that version.
        updated: When the recipe last changed.
        docs: The project's documentation.
        repository: The project's source code.
        license: The project's licence, as an SPDX identifier.
        min_noust: The oldest Noust that can install it, when it needs one
            that has something an older one lacks.
        architectures: The architectures it runs on (all its images are
            published for them), empty for any. Checked against the server.
        available: Whether it can be deployed with this release.
        unavailable_reason: Why not, when it cannot.
        app_type: The deployer that builds it.
        source: Where a native recipe's code comes from.
        layout: ``releases`` or ``inplace``.
        port: Default port, for types that run a process.
        needs: What the host must have, in words, for the operator.
        requires: What the server must provide (:data:`REQUIREMENTS`).
        database_choices: The SQL engines the operator may choose between at install, the
            recipe's own (its default) first: ``mysql`` and ``mariadb`` speak the same protocol,
            so one recipe can run on either.
        build: A native recipe's install, build and start commands.
        variables: Its variables, in the order the file lists them.
        env: Version 1 only: variable name to Jinja value template.
        persistent_paths: Paths kept across releases in ``shared/``.
        health: Its health check.
        php: Settings of the PHP-FPM deployer.
        compose: A Compose recipe's document, as written.
        files: The files a recipe ships: path to asset name. A Compose
            recipe's go beside its document; a native recipe's are seeded once
            into the application (``shared/`` on releases).
        rendered_files: The paths among them whose ``${NAME}`` references are
            filled with the application's variables when they are written.
        file_modes: Permissions of the files that name one.
        images: A Compose recipe's images, as pinned.
        digest_verified: Every image carries a digest read from its registry.
        public_ports: Ports a Compose recipe opens to the network.
        notes: Templates of what to tell the operator after the deploy.
        changelog: What the latest versions changed.
        origin: One of :data:`ORIGINS`: the package, a catalogue package, or
            the operator's folder.
    """

    name: str
    title: str
    description: str
    homepage: str
    schema: int = SCHEMA_VERSION
    kind: str = "native"
    slogan: str = ""
    category: str = "other"
    tags: tuple[str, ...] = ()
    logo: str | None = None
    version: str = ""
    revision: int = 1
    updated: date | None = None
    docs: str | None = None
    repository: str | None = None
    license: str | None = None
    min_noust: str | None = None
    architectures: tuple[str, ...] = ()
    available: bool = True
    unavailable_reason: str | None = None
    app_type: str = ""
    source: RecipeSource | None = None
    layout: str = "releases"
    port: int | None = None
    needs: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    database_choices: tuple[str, ...] = ()
    build: RecipeBuild | None = None
    variables: tuple[RecipeVariable, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    persistent_paths: tuple[str, ...] = ()
    health: RecipeHealth | None = None
    php: dict[str, Any] = field(default_factory=dict)
    compose: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    rendered_files: frozenset[str] = frozenset()
    file_modes: dict[str, int] = field(default_factory=dict)
    images: tuple[RecipeImage, ...] = ()
    digest_verified: bool = True
    public_ports: tuple[PublicPort, ...] = ()
    notes: tuple[str, ...] = ()
    changelog: tuple[ChangelogEntry, ...] = ()
    origin: str = "shipped"

    @property
    def runs_in_docker(self) -> bool:
        """Whether the application runs in containers, which the catalogue says."""
        return self.kind == "compose"

    @property
    def database_engine(self) -> str | None:
        """
        The SQL database it is given, in the helper's spelling, or None.

        Returns:
            ``postgresql``, ``mysql`` or ``mariadb`` for the first SQL
            requirement, or None.
        """
        for requirement in self.requires:
            if requirement in SQL_REQUIREMENTS:
                return ENGINE_OF[requirement]
        return None

    def variable(self, name: str) -> RecipeVariable | None:
        """
        Find a variable by name.

        Args:
            name: The environment variable.

        Returns:
            The variable, or None when the recipe does not declare it.
        """
        return next((entry for entry in self.variables if entry.name == name), None)

    def _env_view(self) -> list[dict[str, Any]]:
        """
        List the variables the way the console's review step has always read them.

        Returns:
            Name and whether Noust supplies the value (a version 1 template
            that renders something, or a version 2 variable that is not an
            input the operator fills in).
        """
        if self.schema < 2:
            return [
                {"name": name, "generated": "{{" in value}
                for name, value in sorted(self.env.items())
            ]
        return [
            {"name": entry.name, "generated": entry.origin() in _SUPPLIED}
            for entry in self.variables
        ]

    def summary(self) -> dict[str, Any]:
        """
        Describe the recipe in a list.

        ``requires`` is what the host must have, in words, and ``database``
        the SQL engine: both are what the console's gallery has always read.
        ``dependencies`` is the list of services the recipe asks Noust for.

        Returns:
            Everything a catalogue card shows.
        """
        return {
            "name": self.name,
            "title": self.title,
            "slogan": self.slogan,
            "description": self.description,
            "homepage": self.homepage,
            "docs": self.docs,
            "repository": self.repository,
            "license": self.license,
            "min_noust": self.min_noust,
            "architectures": list(self.architectures),
            "category": self.category,
            "tags": list(self.tags),
            "logo": self.logo,
            "version": self.version or None,
            "revision": self.revision,
            "updated": self.updated.isoformat() if self.updated else None,
            "kind": self.kind,
            "runs_in_docker": self.runs_in_docker,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "app_type": self.app_type or None,
            "database": self.database_engine,
            "database_choices": [ENGINE_OF[choice] for choice in self.database_choices],
            "dependencies": list(self.requires),
            "requires": list(self.needs),
            "digest_verified": self.digest_verified if self.kind == "compose" else None,
            "schema": self.schema,
            "origin": self.origin,
        }

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the recipe in full, as ``noust recipe show`` and the API do.

        Variables are listed by name with where their value comes from; no
        value is ever included.

        Returns:
            The summary plus source, layout, port, build block, variables,
            images, public ports, persistent paths, health check, notes and
            changelog.
        """
        return {
            **self.summary(),
            "source": self.source.to_dict() if self.source else None,
            "layout": self.layout,
            "port": self.port,
            "build": self.build.to_dict() if self.build else None,
            "variables": [entry.to_dict() for entry in self.variables],
            "env": self._env_view(),
            "images": [image.to_dict() for image in self.images],
            "public_ports": [
                {"port": entry.port, "protocol": entry.protocol, "purpose": entry.purpose}
                for entry in self.public_ports
            ],
            "persistent_paths": list(self.persistent_paths),
            "health": (
                {
                    "path": self.health.path,
                    "expect": self.health.expect,
                    "timeout": self.health.timeout,
                }
                if self.health
                else None
            ),
            "notes": list(self.notes),
            "changelog": [
                {"version": entry.version, "summary": entry.summary} for entry in self.changelog
            ],
        }
