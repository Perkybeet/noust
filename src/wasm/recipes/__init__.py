# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Recipes: well-known applications deployed from a declarative file, without Docker.

A recipe is data shipped with the package (``src/wasm/recipes/<name>.yaml``),
never code: which deployer builds it, where its source comes from (a git
repository at a fixed tag, an archive with its checksum, or files rendered
from the recipe's own assets), the variables it needs with their generated
secrets, its database, what must survive a release, how its health is asked
and what to tell the operator afterwards. :mod:`wasm.recipes.deploy` turns one
into the arguments of an ordinary deployment; the deployers do the rest.

Every file is validated strictly when it is loaded - an unknown key is an
error, not something ignored - and every shipped recipe is loaded by the
tests, so a typo cannot reach an operator.

The format::

    name: wordpress                  # the file's own name
    title: WordPress
    description: One sentence.
    homepage: https://wordpress.org
    available: true                  # false: listed with unavailable_reason
    app_type: php-fpm                # a registered deployer
    source:                          # exactly one of:
      git: https://github.com/o/r    #   a repository, with a fixed ref (tag)
      ref: 1.2.3
      archive: https://h/app.tar.gz  #   an archive, with sha256 or checksum_url
      checksum_url: https://h/app.tar.gz.sha1
      template:                      #   files rendered from assets/<name>/
        package.json: package.json
    layout: releases                 # or inplace; releases by default
    port: 3001                       # default port, when the type runs one
    requires: [Node.js 20+]          # shown to the operator, not enforced
    database:
      engine: mysql                  # mysql (MariaDB), postgresql
    env:                             # values are templates, see deploy.py
      APP_SECRET: "{{ secret(48) }}"
      DATABASE_URL: "{{ database.url }}"
    persistent_paths: [data]
    health: {path: /, expect: 200-399}
    php:                             # php-fpm only
      webroot: "."
      deny: [wp-config.php]
      max_upload: 64m
      shared_from_release: [wp-content]
      files: {wp-config.php: wp-config.php}   # asset written into shared/
    notes:
      - "Open {{ url }}/wp-admin/install.php to finish the installation."
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml
from jinja2 import TemplateSyntaxError

from wasm.core.exceptions import ValidationError
from wasm.validators.environment import is_valid_env_name

#: What a recipe is named: its file name, and what ``--recipe`` takes.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

#: Source kinds a recipe may use.
SOURCE_KINDS = ("git", "archive", "template")

#: Database engines a recipe may ask for, as the database helper spells them.
DATABASE_ENGINES = ("mysql", "mariadb", "postgresql")

#: Layouts a recipe may ask for.
LAYOUTS = ("releases", "inplace")

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_CHECKSUM_SUFFIXES = (".sha1", ".sha256", ".sha512")
_ASSET_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")

_TOP_LEVEL = frozenset(
    {
        "name",
        "title",
        "description",
        "homepage",
        "available",
        "unavailable_reason",
        "app_type",
        "source",
        "layout",
        "port",
        "requires",
        "database",
        "env",
        "persistent_paths",
        "health",
        "php",
        "notes",
    }
)
_DESCRIPTIVE = frozenset(
    {"name", "title", "description", "homepage", "available", "unavailable_reason", "requires"}
)
_SOURCE_KEYS = frozenset({"git", "ref", "archive", "sha256", "checksum_url", "template"})
_PHP_KEYS = frozenset({"webroot", "deny", "max_upload", "shared_from_release", "files"})


class RecipeError(ValidationError):
    """A recipe file is not valid, or names something that does not exist."""


class RecipeNotFoundError(RecipeError):
    """No recipe has that name."""


@dataclass(frozen=True)
class RecipeSource:
    """
    Where a recipe's code comes from.

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
        :meth:`~wasm.managers.source_manager.SourceManager.download_archive`
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
    """

    path: str = "/"
    expect: str | None = None


@dataclass(frozen=True)
class Recipe:
    """
    One recipe, validated.

    Attributes:
        name: The recipe's name.
        title: What it is called.
        description: One sentence.
        homepage: The project's site.
        available: Whether it can be deployed with this release.
        unavailable_reason: Why not, when it cannot.
        app_type: The deployer that builds it.
        source: Where its code comes from.
        layout: ``releases`` or ``inplace``.
        port: Default port, for types that run a process.
        requires: What the server needs, in words, for the operator.
        database_engine: The database it gets, or None.
        env: Variable name to value template.
        persistent_paths: Paths kept across releases in ``shared/``.
        health: Its health check.
        php: Settings of the PHP-FPM deployer.
        notes: Templates of what to tell the operator after the deploy.
    """

    name: str
    title: str
    description: str
    homepage: str
    available: bool = True
    unavailable_reason: str | None = None
    app_type: str = ""
    source: RecipeSource | None = None
    layout: str = "releases"
    port: int | None = None
    requires: tuple[str, ...] = ()
    database_engine: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    persistent_paths: tuple[str, ...] = ()
    health: RecipeHealth | None = None
    php: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        """
        Describe the recipe in a list.

        Returns:
            Name, title, description, homepage, availability and type.
        """
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "homepage": self.homepage,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "app_type": self.app_type or None,
            "database": self.database_engine,
            "requires": list(self.requires),
        }

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the recipe in full, as ``wasm recipe show`` and the API do.

        The variables are listed by name with whether WASM generates them;
        their templates are not secrets but are an implementation detail.

        Returns:
            The summary plus source, layout, port, variables, persistent
            paths, health check and notes.
        """
        return {
            **self.summary(),
            "source": self.source.to_dict() if self.source else None,
            "layout": self.layout,
            "port": self.port,
            "env": [
                {"name": name, "generated": "{{" in value}
                for name, value in sorted(self.env.items())
            ],
            "persistent_paths": list(self.persistent_paths),
            "health": (
                {"path": self.health.path, "expect": self.health.expect} if self.health else None
            ),
            "notes": list(self.notes),
        }


def _fail(name: str, message: str) -> RecipeError:
    """
    Build the error for an invalid recipe.

    Args:
        name: The recipe.
        message: What is wrong.

    Returns:
        The error, naming the file.
    """
    return RecipeError(
        f"Recipe {name!r} is not valid: {message}",
        details=f"Fix src/wasm/recipes/{name}.yaml; see wasm.recipes for the format.",
    )


def _string(name: str, data: dict[str, Any], key: str) -> str:
    """
    Read a required string field.

    Args:
        name: The recipe, for the message.
        data: The mapping.
        key: The field.

    Returns:
        The string.

    Raises:
        RecipeError: When it is missing, empty or not a string.
    """
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(name, f"{key} must be a non-empty string")
    return value


def _strings(name: str, value: Any, key: str) -> tuple[str, ...]:
    """
    Read a list of strings.

    Args:
        name: The recipe, for the message.
        value: The raw value.
        key: The field, for the message.

    Returns:
        The strings.

    Raises:
        RecipeError: When it is not a list of strings.
    """
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _fail(name, f"{key} must be a list of strings")
    return tuple(value)


def _https(name: str, value: Any, key: str) -> str:
    """
    Read a URL that must be https.

    Args:
        name: The recipe, for the message.
        value: The raw value.
        key: The field, for the message.

    Returns:
        The URL.

    Raises:
        RecipeError: When it is not an https URL.
    """
    if not isinstance(value, str) or not re.match(r"^https://[A-Za-z0-9.-]+(?:/\S*)?$", value):
        raise _fail(name, f"{key} must be an https:// URL")
    return value


def _check_template(name: str, key: str, value: str) -> None:
    """
    Make sure a value template at least parses.

    Args:
        name: The recipe, for the message.
        key: Where the template is, for the message.
        value: The template.

    Raises:
        RecipeError: When it is not valid template syntax.
    """
    from wasm.recipes.render import template_environment

    try:
        template_environment().parse(value)
    except TemplateSyntaxError as exc:
        raise _fail(name, f"{key} is not a valid template: {exc.message}") from exc


def _parse_source(name: str, raw: Any) -> RecipeSource:
    """
    Validate a recipe's source.

    Args:
        name: The recipe.
        raw: The ``source`` mapping.

    Returns:
        The source.

    Raises:
        RecipeError: When it is not exactly one valid kind.
    """
    if not isinstance(raw, dict):
        raise _fail(name, "source must be a mapping")
    unknown = set(raw) - _SOURCE_KEYS
    if unknown:
        raise _fail(name, f"unknown source keys: {', '.join(sorted(unknown))}")
    kinds = [kind for kind in SOURCE_KINDS if kind in raw]
    if len(kinds) != 1:
        raise _fail(name, "source must be exactly one of git, archive or template")
    kind = kinds[0]
    if kind == "git":
        ref = raw.get("ref")
        if not isinstance(ref, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", ref):
            raise _fail(name, "a git source needs a fixed ref (a tag)")
        if set(raw) - {"git", "ref"}:
            raise _fail(name, "a git source takes only git and ref")
        return RecipeSource(kind="git", url=_https(name, raw["git"], "source.git"), ref=ref)
    if kind == "archive":
        url = _https(name, raw["archive"], "source.archive")
        sha256, checksum_url = raw.get("sha256"), raw.get("checksum_url")
        if (sha256 is None) == (checksum_url is None):
            raise _fail(name, "an archive needs exactly one of sha256 or checksum_url")
        if sha256 is not None and not (isinstance(sha256, str) and _HEX64.match(sha256)):
            raise _fail(name, "source.sha256 must be 64 hexadecimal characters")
        if checksum_url is not None:
            _https(name, checksum_url, "source.checksum_url")
            if not str(checksum_url).endswith(_CHECKSUM_SUFFIXES):
                raise _fail(name, "source.checksum_url must end in .sha1, .sha256 or .sha512")
        if set(raw) - {"archive", "sha256", "checksum_url"}:
            raise _fail(name, "an archive source takes only archive, sha256 and checksum_url")
        return RecipeSource(kind="archive", url=url, sha256=sha256, checksum_url=checksum_url)
    files = raw.get("template")
    if set(raw) - {"template"}:
        raise _fail(name, "a template source takes only template")
    if not isinstance(files, dict) or not files:
        raise _fail(name, "source.template must map file paths to assets")
    checked: dict[str, str] = {}
    for path, asset in files.items():
        if not (isinstance(path, str) and _ASSET_PATH.match(path) and ".." not in path.split("/")):
            raise _fail(name, f"template path {path!r} is not a plain relative path")
        checked[path] = _asset_name(name, asset)
    return RecipeSource(kind="template", files=checked)


def _asset_name(name: str, asset: Any) -> str:
    """
    Check that an asset a recipe names is shipped.

    Args:
        name: The recipe.
        asset: The asset's path under ``assets/<recipe>/``.

    Returns:
        The asset path.

    Raises:
        RecipeError: When it is not a plain relative path or not shipped.
    """
    if not (isinstance(asset, str) and _ASSET_PATH.match(asset) and ".." not in asset.split("/")):
        raise _fail(name, f"asset {asset!r} is not a plain relative path")
    if not _asset_resource(name, asset).is_file():
        raise _fail(name, f"asset {asset!r} is not in assets/{name}/")
    return asset


def _asset_resource(name: str, asset: str) -> Any:
    """
    Locate a recipe asset inside the package.

    Args:
        name: The recipe.
        asset: The asset's path under ``assets/<recipe>/``.

    Returns:
        The resource.
    """
    resource = resources.files(__name__).joinpath("assets").joinpath(name)
    for part in asset.split("/"):
        resource = resource.joinpath(part)
    return resource


def read_asset(name: str, asset: str) -> str:
    """
    Read a recipe asset.

    Args:
        name: The recipe.
        asset: The asset's path under ``assets/<recipe>/``.

    Returns:
        Its text.

    Raises:
        RecipeError: When it is not shipped.
    """
    resource = _asset_resource(name, _asset_name(name, asset))
    return str(resource.read_text(encoding="utf-8"))


def parse_recipe(name: str, data: Any) -> Recipe:
    """
    Validate a recipe's contents.

    Args:
        name: The recipe's file name, without ``.yaml``.
        data: What the YAML file holds.

    Returns:
        The recipe.

    Raises:
        RecipeError: When anything in it is not valid.
    """
    if not isinstance(data, dict):
        raise _fail(name, "the file must hold a mapping")
    unknown = set(data) - _TOP_LEVEL
    if unknown:
        raise _fail(name, f"unknown keys: {', '.join(sorted(unknown))}")
    if data.get("name") != name:
        raise _fail(name, "name must be the file's name")
    available = data.get("available", True)
    if not isinstance(available, bool):
        raise _fail(name, "available must be true or false")
    title = _string(name, data, "title")
    description = _string(name, data, "description")
    homepage = _https(name, data.get("homepage"), "homepage")
    requires = _strings(name, data.get("requires"), "requires")

    if not available:
        extra = set(data) - _DESCRIPTIVE
        if extra:
            raise _fail(name, f"an unavailable recipe has no {', '.join(sorted(extra))}")
        return Recipe(
            name=name,
            title=title,
            description=description,
            homepage=homepage,
            available=False,
            unavailable_reason=_string(name, data, "unavailable_reason"),
            requires=requires,
        )
    if "unavailable_reason" in data:
        raise _fail(name, "unavailable_reason is only for an unavailable recipe")

    app_type = _string(name, data, "app_type")
    from wasm.deployers.registry import available_types

    known = {entry["type"] for entry in available_types()} - {"auto"}
    if app_type not in known:
        raise _fail(name, f"app_type {app_type!r} is not one of {', '.join(sorted(known))}")
    source = _parse_source(name, data.get("source"))
    layout = data.get("layout", "releases")
    if layout not in LAYOUTS:
        raise _fail(name, f"layout must be one of {', '.join(LAYOUTS)}")
    port = data.get("port")
    if port is not None and not (isinstance(port, int) and 1024 <= port <= 65535):
        raise _fail(name, "port must be a number from 1024 to 65535")

    engine = None
    database = data.get("database")
    if database is not None:
        if not isinstance(database, dict) or set(database) != {"engine"}:
            raise _fail(name, "database takes exactly one key, engine")
        engine = database["engine"]
        if engine not in DATABASE_ENGINES:
            raise _fail(name, f"database.engine must be one of {', '.join(DATABASE_ENGINES)}")

    env = data.get("env") or {}
    if not isinstance(env, dict):
        raise _fail(name, "env must be a mapping")
    checked_env: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not is_valid_env_name(key):
            raise _fail(name, f"env name {key!r} is not a valid variable name")
        if not isinstance(value, str):
            raise _fail(name, f"env {key} must be a string (quote numbers and booleans)")
        _check_template(name, f"env {key}", value)
        if "database." in value and engine is None:
            raise _fail(name, f"env {key} uses the database, which the recipe does not ask for")
        checked_env[key] = value

    persistent = _strings(name, data.get("persistent_paths"), "persistent_paths")
    from wasm.deployers.releases import persistent_path

    for path in persistent:
        persistent_path(path)

    health = None
    raw_health = data.get("health")
    if raw_health is not None:
        if not isinstance(raw_health, dict) or set(raw_health) - {"path", "expect"}:
            raise _fail(name, "health takes path and expect")
        path = raw_health.get("path", "/")
        expect = raw_health.get("expect")
        from wasm.validators.health import parse_health_expect

        if not isinstance(path, str) or not path.startswith("/") or " " in path:
            raise _fail(name, "health.path must start with /")
        if expect is not None:
            parse_health_expect(str(expect))
        health = RecipeHealth(path=path, expect=None if expect is None else str(expect))

    php = data.get("php") or {}
    if php:
        if app_type != "php-fpm":
            raise _fail(name, "php settings are for app_type php-fpm")
        if not isinstance(php, dict) or set(php) - _PHP_KEYS:
            raise _fail(name, f"php takes {', '.join(sorted(_PHP_KEYS))}")
        from wasm.deployers.php_fpm import PhpSettings

        PhpSettings.from_mapping({key: value for key, value in php.items() if key != "files"})
        files = php.get("files") or {}
        if not isinstance(files, dict):
            raise _fail(name, "php.files must map paths to assets")
        for path, asset in files.items():
            if not (isinstance(path, str) and _ASSET_PATH.match(path)):
                raise _fail(name, f"php.files path {path!r} is not a plain relative path")
            _asset_name(name, asset)

    notes = _strings(name, data.get("notes"), "notes")
    for index, note in enumerate(notes):
        _check_template(name, f"note {index + 1}", note)

    return Recipe(
        name=name,
        title=title,
        description=description,
        homepage=homepage,
        available=True,
        app_type=app_type,
        source=source,
        layout=layout,
        port=port,
        requires=requires,
        database_engine=engine,
        env=checked_env,
        persistent_paths=persistent,
        health=health,
        php=dict(php),
        notes=notes,
    )


def _recipe_files() -> dict[str, Any]:
    """
    List the recipe files the package ships.

    Returns:
        Recipe name to resource.
    """
    return {
        entry.name.removesuffix(".yaml"): entry
        for entry in resources.files(__name__).iterdir()
        if entry.name.endswith(".yaml") and entry.is_file()
    }


def get_recipe(name: str) -> Recipe:
    """
    Load one recipe.

    Args:
        name: The recipe's name.

    Returns:
        The recipe, validated.

    Raises:
        RecipeNotFoundError: When no recipe has that name.
        RecipeError: When its file is not valid.
    """
    files = _recipe_files()
    if not NAME_PATTERN.match(name or "") or name not in files:
        raise RecipeNotFoundError(
            f"No recipe named {name!r}",
            details=f"Available recipes: {', '.join(sorted(files))}. See: wasm recipe list",
        )
    try:
        data = yaml.safe_load(files[name].read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise _fail(name, f"the YAML does not parse: {exc}") from exc
    return parse_recipe(name, data)


def list_recipes() -> list[Recipe]:
    """
    Load every recipe the package ships.

    Returns:
        The recipes, the available ones first, each group by title.

    Raises:
        RecipeError: When a file is not valid.
    """
    recipes = [get_recipe(name) for name in _recipe_files()]
    return sorted(recipes, key=lambda recipe: (not recipe.available, recipe.title.lower()))
