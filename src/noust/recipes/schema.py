# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading a recipe: the closed schema, version 2, and the 2.3 format it replaces.

A recipe is data, never code, and it is untrusted in the way a repository's
``noust.yaml`` is: an unknown key is an error naming it, a value of the wrong
shape is an error naming the field, and nothing is ignored. Every shipped recipe
is loaded by the tests, so a typo reaches no operator.

:func:`parse_recipe` reads both formats. A document without ``schema`` is the
2.3 format (version 1: one native recipe, ``database.engine`` and Jinja
``env`` values) and still loads, as a native recipe of category ``other``; a
document with ``schema: 2`` is the format :mod:`noust.recipes` describes.
"""

from __future__ import annotations

import dataclasses
import re
import shlex
from datetime import date
from typing import Any

from jinja2 import TemplateSyntaxError

from noust.recipes.assets import checked_asset, is_plain_path, read_asset
from noust.recipes.composefile import ComposeInfo, implicit_variables, validate_compose
from noust.recipes.model import (
    ARCHITECTURES,
    CATEGORIES,
    CONTEXT_SOURCES,
    DATABASE_ENGINES,
    GENERATORS,
    KINDS,
    LAYOUTS,
    REQUIREMENT_ATTRIBUTES,
    REQUIREMENTS,
    RESERVED_VARIABLES,
    SCHEMA_VERSION,
    SECRET_GENERATORS,
    SOURCE_KINDS,
    SQL_REQUIREMENTS,
    ChangelogEntry,
    Entrypoint,
    PublicPort,
    Recipe,
    RecipeBuild,
    RecipeError,
    RecipeHealth,
    RecipeSource,
    RecipeVariable,
    recipe_error,
)
from noust.recipes.variables import (
    ReferenceSyntaxError,
    references_in_order,
    scan_references,
)
from noust.validators.environment import is_valid_env_name

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_CHECKSUM_SUFFIXES = (".sha1", ".sha256", ".sha512")
_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,39}$")
_RUNTIME = re.compile(r"^[a-z][a-z0-9-]*@[0-9][0-9A-Za-z.]*$")
_LICENSE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9.+-]*(?: (?:OR|AND|WITH) [A-Za-z0-9][A-Za-z0-9.+-]*)*$"
)

#: The most tags a card shows.
MAX_TAGS = 8
#: The longest slogan: one line on a card.
MAX_SLOGAN = 100
#: The longest description.
MAX_DESCRIPTION = 400
#: The most changelog entries a recipe carries.
MAX_CHANGELOG = 5

_V1_TOP_LEVEL = frozenset(
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
_V1_DESCRIPTIVE = frozenset(
    {"name", "title", "description", "homepage", "available", "unavailable_reason", "requires"}
)
_V2_COMMON = frozenset(
    {
        "schema",
        "name",
        "kind",
        "title",
        "slogan",
        "description",
        "category",
        "tags",
        "logo",
        "version",
        "revision",
        "updated",
        "homepage",
        "docs",
        "repository",
        "license",
        "min_noust",
        "architectures",
        "available",
        "unavailable_reason",
        "changelog",
        "notes",
        "needs",
        "requires",
        "variables",
        "health",
        "port",
    }
)
_V2_NATIVE = frozenset(
    {
        "app_type",
        "source",
        "layout",
        "build",
        "persistent_paths",
        "php",
        "files",
        "database_choices",
    }
)
_V2_COMPOSE = frozenset({"compose", "files", "digest_verified", "public_ports", "entrypoints"})
_ENTRYPOINT_KEYS = frozenset({"title", "description", "domain_variable", "port_variable"})
#: The extra public addresses a recipe may have: each needs a DNS record and a place in the
#: certificate, so a recipe that wants more is a different shape of application.
_MAX_ENTRYPOINTS = 4
_ENTRYPOINT_NAME = re.compile(r"[a-z][a-z0-9-]{0,19}")
_FILE_KEYS = frozenset({"asset", "render", "mode"})
#: The permissions a recipe's file may ask for: a rendered file may hold a secret.
_FILE_MODES = {"0600": 0o600, "0640": 0o640, "0644": 0o644}
_MAX_FILES = 40
_V2_UNAVAILABLE = frozenset(
    {
        "schema",
        "name",
        "kind",
        "title",
        "slogan",
        "description",
        "category",
        "tags",
        "logo",
        "version",
        "updated",
        "homepage",
        "docs",
        "repository",
        "license",
        "available",
        "unavailable_reason",
        "needs",
        "changelog",
    }
)
_SOURCE_KEYS = frozenset({"git", "ref", "archive", "sha256", "checksum_url", "template"})
_PHP_KEYS = frozenset({"webroot", "deny", "max_upload", "shared_from_release", "files"})
_BUILD_KEYS = frozenset(
    {"base_directory", "install", "build", "start", "publish_directory", "runtime"}
)
_VARIABLE_KEYS = frozenset(
    {
        "description",
        "generate",
        "from",
        "value",
        "default",
        "required",
        "secret",
        "service",
        "port",
        "signed_with",
        "role",
    }
)
#: What a variable may be made from: exactly one.
_VARIABLE_ORIGINS = ("generate", "from", "value", "default", "required")


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
        raise recipe_error(name, f"{key} must be a non-empty string")
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
        raise recipe_error(name, f"{key} must be a list of strings")
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
    if not isinstance(value, str) or not re.fullmatch(r"^https://[A-Za-z0-9.-]+(?:/\S*)?$", value):
        raise recipe_error(name, f"{key} must be an https:// URL")
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
    from noust.recipes.render import template_environment

    try:
        template_environment().parse(value)
    except TemplateSyntaxError as exc:
        raise recipe_error(name, f"{key} is not a valid template: {exc.message}") from exc


def _parse_source(name: str, raw: Any) -> RecipeSource:
    """
    Validate a native recipe's source.

    Args:
        name: The recipe.
        raw: The ``source`` mapping.

    Returns:
        The source.

    Raises:
        RecipeError: When it is not exactly one valid kind.
    """
    if not isinstance(raw, dict):
        raise recipe_error(name, "source must be a mapping")
    unknown = set(raw) - _SOURCE_KEYS
    if unknown:
        raise recipe_error(name, f"unknown source keys: {', '.join(sorted(unknown))}")
    kinds = [kind for kind in SOURCE_KINDS if kind in raw]
    if len(kinds) != 1:
        raise recipe_error(name, "source must be exactly one of git, archive or template")
    kind = kinds[0]
    if kind == "git":
        ref = raw.get("ref")
        if not isinstance(ref, str) or not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", ref):
            raise recipe_error(name, "a git source needs a fixed ref (a tag)")
        if set(raw) - {"git", "ref"}:
            raise recipe_error(name, "a git source takes only git and ref")
        return RecipeSource(kind="git", url=_https(name, raw["git"], "source.git"), ref=ref)
    if kind == "archive":
        url = _https(name, raw["archive"], "source.archive")
        sha256, checksum_url = raw.get("sha256"), raw.get("checksum_url")
        if (sha256 is None) == (checksum_url is None):
            raise recipe_error(name, "an archive needs exactly one of sha256 or checksum_url")
        if sha256 is not None and not (isinstance(sha256, str) and _HEX64.fullmatch(sha256)):
            raise recipe_error(name, "source.sha256 must be 64 hexadecimal characters")
        if checksum_url is not None:
            _https(name, checksum_url, "source.checksum_url")
            if not str(checksum_url).endswith(_CHECKSUM_SUFFIXES):
                raise recipe_error(
                    name, "source.checksum_url must end in .sha1, .sha256 or .sha512"
                )
        if set(raw) - {"archive", "sha256", "checksum_url"}:
            raise recipe_error(
                name, "an archive source takes only archive, sha256 and checksum_url"
            )
        return RecipeSource(kind="archive", url=url, sha256=sha256, checksum_url=checksum_url)
    files = raw.get("template")
    if set(raw) - {"template"}:
        raise recipe_error(name, "a template source takes only template")
    if not isinstance(files, dict) or not files:
        raise recipe_error(name, "source.template must map file paths to assets")
    checked: dict[str, str] = {}
    for path, asset in files.items():
        if not is_plain_path(path):
            raise recipe_error(name, f"template path {path!r} is not a plain relative path")
        checked[path] = checked_asset(name, asset)
    return RecipeSource(kind="template", files=checked)


def _parse_health(name: str, raw: Any) -> RecipeHealth | None:
    """
    Validate a health check.

    Args:
        name: The recipe.
        raw: The ``health`` mapping, or None.

    Returns:
        The check, or None when the recipe has none.

    Raises:
        RecipeError: It is not a mapping of ``path`` and ``expect``, or one of
            them is not usable by the health gate.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"path", "expect", "timeout"}:
        raise recipe_error(name, "health takes path, expect and timeout")
    path = raw.get("path", "/")
    expect = raw.get("expect")
    timeout = raw.get("timeout")
    from noust.core.exceptions import ValidationError
    from noust.validators.health import check_health_timeout, parse_health_expect

    if not isinstance(path, str) or not path.startswith("/") or " " in path:
        raise recipe_error(name, "health.path must start with /")
    try:
        if expect is not None:
            parse_health_expect(str(expect))
        if timeout is not None:
            check_health_timeout(timeout)
    except ValidationError as exc:
        raise recipe_error(name, f"health: {exc.message}") from exc
    return RecipeHealth(path=path, expect=None if expect is None else str(expect), timeout=timeout)


def _parse_php(name: str, app_type: str, raw: Any) -> dict[str, Any]:
    """
    Validate the settings of the PHP-FPM deployer.

    Args:
        name: The recipe.
        app_type: The recipe's type.
        raw: The ``php`` mapping, or None.

    Returns:
        The settings; empty when there are none.

    Raises:
        RecipeError: They are for another type, or not valid.
    """
    php = raw or {}
    if not php:
        return {}
    if app_type != "php-fpm":
        raise recipe_error(name, "php settings are for app_type php-fpm")
    if not isinstance(php, dict) or set(php) - _PHP_KEYS:
        raise recipe_error(name, f"php takes {', '.join(sorted(_PHP_KEYS))}")
    from noust.deployers.php_fpm import PhpSettings

    PhpSettings.from_mapping({key: value for key, value in php.items() if key != "files"})
    files = php.get("files") or {}
    if not isinstance(files, dict):
        raise recipe_error(name, "php.files must map paths to assets")
    for path, asset in files.items():
        if not is_plain_path(path):
            raise recipe_error(name, f"php.files path {path!r} is not a plain relative path")
        checked_asset(name, asset)
    return dict(php)


def _parse_notes(name: str, raw: Any) -> tuple[str, ...]:
    """
    Validate the notes shown after the deploy.

    Args:
        name: The recipe.
        raw: The ``notes`` list, or None.

    Returns:
        The notes.

    Raises:
        RecipeError: One is not a valid template.
    """
    notes = _strings(name, raw, "notes")
    for index, note in enumerate(notes):
        _check_template(name, f"note {index + 1}", note)
    return notes


def _known_app_type(name: str, app_type: str) -> None:
    """
    Check that a deployer of that name is registered.

    Args:
        name: The recipe.
        app_type: The type.

    Raises:
        RecipeError: It is not one of the registered types.
    """
    from noust.deployers.registry import available_types

    known = {entry["type"] for entry in available_types()} - {"auto"}
    if app_type not in known:
        raise recipe_error(name, f"app_type {app_type!r} is not one of {', '.join(sorted(known))}")


def _persistent(name: str, raw: Any) -> tuple[str, ...]:
    """
    Validate the paths kept across releases.

    Args:
        name: The recipe.
        raw: The ``persistent_paths`` list, or None.

    Returns:
        The paths.

    Raises:
        RecipeError: One is not a path a release may share.
    """
    persistent = _strings(name, raw, "persistent_paths")
    from noust.deployers.releases import persistent_path

    for path in persistent:
        persistent_path(path)
    return persistent


def _port(name: str, raw: Any) -> int | None:
    """
    Validate a default port.

    Args:
        name: The recipe.
        raw: The ``port`` value, or None.

    Returns:
        The port.

    Raises:
        RecipeError: It is not an integer from 1024 to 65535.
    """
    if raw is not None and not (
        isinstance(raw, int) and not isinstance(raw, bool) and 1024 <= raw <= 65535
    ):
        raise recipe_error(name, "port must be a number from 1024 to 65535")
    return raw


# ---------------------------------------------------------------------------
# Version 1: the 2.3 format
# ---------------------------------------------------------------------------


def _parse_v1(name: str, data: dict[str, Any]) -> Recipe:
    """
    Read a recipe written in the 2.3 format.

    Args:
        name: The recipe's file name.
        data: What the file holds.

    Returns:
        The recipe, as a native one of category ``other``.

    Raises:
        RecipeError: When anything in it is not valid.
    """
    unknown = set(data) - _V1_TOP_LEVEL
    if unknown:
        raise recipe_error(name, f"unknown keys: {', '.join(sorted(unknown))}")
    if data.get("name") != name:
        raise recipe_error(name, "name must be the file's name")
    available = data.get("available", True)
    if not isinstance(available, bool):
        raise recipe_error(name, "available must be true or false")
    title = _string(name, data, "title")
    description = _string(name, data, "description")
    homepage = _https(name, data.get("homepage"), "homepage")
    needs = _strings(name, data.get("requires"), "requires")

    if not available:
        extra = set(data) - _V1_DESCRIPTIVE
        if extra:
            raise recipe_error(name, f"an unavailable recipe has no {', '.join(sorted(extra))}")
        return Recipe(
            name=name,
            title=title,
            description=description,
            homepage=homepage,
            schema=1,
            available=False,
            unavailable_reason=_string(name, data, "unavailable_reason"),
            needs=needs,
        )
    if "unavailable_reason" in data:
        raise recipe_error(name, "unavailable_reason is only for an unavailable recipe")

    app_type = _string(name, data, "app_type")
    _known_app_type(name, app_type)
    source = _parse_source(name, data.get("source"))
    layout = data.get("layout", "releases")
    if layout not in LAYOUTS:
        raise recipe_error(name, f"layout must be one of {', '.join(LAYOUTS)}")
    port = _port(name, data.get("port"))

    engine = None
    database = data.get("database")
    if database is not None:
        if not isinstance(database, dict) or set(database) != {"engine"}:
            raise recipe_error(name, "database takes exactly one key, engine")
        engine = database["engine"]
        if engine not in DATABASE_ENGINES:
            raise recipe_error(
                name, f"database.engine must be one of {', '.join(DATABASE_ENGINES)}"
            )

    env = data.get("env") or {}
    if not isinstance(env, dict):
        raise recipe_error(name, "env must be a mapping")
    checked_env: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not is_valid_env_name(key):
            raise recipe_error(name, f"env name {key!r} is not a valid variable name")
        if not isinstance(value, str):
            raise recipe_error(name, f"env {key} must be a string (quote numbers and booleans)")
        _check_template(name, f"env {key}", value)
        if "database." in value and engine is None:
            raise recipe_error(
                name, f"env {key} uses the database, which the recipe does not ask for"
            )
        checked_env[key] = value

    return Recipe(
        name=name,
        title=title,
        description=description,
        homepage=homepage,
        schema=1,
        kind="native",
        available=True,
        app_type=app_type,
        source=source,
        layout=layout,
        port=port,
        needs=needs,
        requires=(("postgres" if engine == "postgresql" else engine),) if engine else (),
        env=checked_env,
        persistent_paths=_persistent(name, data.get("persistent_paths")),
        health=_parse_health(name, data.get("health")),
        php=_parse_php(name, app_type, data.get("php")),
        notes=_parse_notes(name, data.get("notes")),
    )


# ---------------------------------------------------------------------------
# Version 2
# ---------------------------------------------------------------------------


def _parse_date(name: str, raw: Any) -> date:
    """
    Read the date a recipe last changed.

    Args:
        name: The recipe.
        raw: A YAML date, or an ISO ``YYYY-MM-DD`` string.

    Returns:
        The date.

    Raises:
        RecipeError: It is neither.
    """
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    raise recipe_error(name, "updated must be a date, YYYY-MM-DD")


def _parse_metadata(
    name: str, data: dict[str, Any], *, listed_only: bool = False
) -> dict[str, Any]:
    """
    Read what a catalogue card shows.

    Args:
        name: The recipe.
        data: What the file holds.
        listed_only: The recipe is listed but cannot be deployed: it has no
            version to pin, no date, and no notes or variables to read.

    Returns:
        Keyword arguments for :class:`~noust.recipes.model.Recipe`.

    Raises:
        RecipeError: A field is missing or not valid.
    """
    slogan = _string(name, data, "slogan")
    if len(slogan) > MAX_SLOGAN or "\n" in slogan:
        raise recipe_error(name, f"slogan must be one line of at most {MAX_SLOGAN} characters")
    description = _string(name, data, "description")
    if len(description) > MAX_DESCRIPTION:
        raise recipe_error(name, f"description is longer than {MAX_DESCRIPTION} characters")
    category = data.get("category")
    if category not in CATEGORIES:
        raise recipe_error(name, f"category must be one of {', '.join(CATEGORIES)}")
    tags = _strings(name, data.get("tags"), "tags")
    if len(tags) > MAX_TAGS or len(set(tags)) != len(tags):
        raise recipe_error(name, f"tags are at most {MAX_TAGS}, each different")
    for tag in tags:
        if not _TAG.fullmatch(tag):
            raise recipe_error(name, f"tag {tag!r} must be lower-case letters, digits and dashes")
    version = data.get("version", "" if listed_only else None)
    if not isinstance(version, str) or not (
        _VERSION.fullmatch(version) or (listed_only and not version)
    ):
        raise recipe_error(name, "version must be a string such as '1.2.3' (quote it)")
    revision = data.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise recipe_error(name, "revision must be a whole number from 1")
    license_id = data.get("license")
    if license_id is not None and not (
        isinstance(license_id, str) and _LICENSE.fullmatch(license_id) and len(license_id) <= 80
    ):
        raise recipe_error(name, "license must be an SPDX expression such as AGPL-3.0-only")
    logo = data.get("logo")
    if logo is not None:
        from noust.recipes.assets import read_asset
        from noust.recipes.logos import validate_svg

        validate_svg(name, read_asset(name, checked_asset(name, logo)))
    return {
        "title": _string(name, data, "title"),
        "slogan": slogan,
        "description": description,
        "homepage": _https(name, data.get("homepage"), "homepage"),
        "docs": _https(name, data["docs"], "docs") if data.get("docs") is not None else None,
        "repository": _https(name, data["repository"], "repository")
        if data.get("repository") is not None
        else None,
        "license": license_id,
        "category": category,
        "tags": tags,
        "logo": logo,
        "version": version,
        "revision": revision,
        "updated": None
        if listed_only and "updated" not in data
        else _parse_date(name, data.get("updated")),
        "needs": _strings(name, data.get("needs"), "needs"),
        "changelog": _parse_changelog(name, data.get("changelog")),
        "notes": () if listed_only else _parse_notes(name, data.get("notes")),
    }


def _parse_min_noust(name: str, raw: Any) -> str | None:
    """
    Read the oldest Noust a recipe can be installed by.

    Args:
        name: The recipe.
        raw: The ``min_noust`` value, or None.

    Returns:
        The version, or None.

    Raises:
        RecipeError: It is not ``major.minor.patch``.
    """
    if raw is None:
        return None
    if not isinstance(raw, str) or not re.fullmatch(r"^\d+\.\d+\.\d+$", raw):
        raise recipe_error(name, "min_noust must be a version such as '3.4.0' (quote it)")
    return raw


def _parse_architectures(name: str, kind: str, raw: Any) -> tuple[str, ...]:
    """
    Read the architectures a recipe runs on.

    A Compose recipe must say, because its images are published for some
    platforms and not others, and ``scripts/recipe_pins.py check`` compares
    what it says with what the registry serves; a native recipe may leave it
    out, meaning any.

    Args:
        name: The recipe.
        kind: ``native`` or ``compose``.
        raw: The ``architectures`` list, or None.

    Returns:
        The architectures.

    Raises:
        RecipeError: One is not in the closed list, one is repeated, or a
            Compose recipe names none.
    """
    architectures = _strings(name, raw, "architectures")
    unknown = [item for item in architectures if item not in ARCHITECTURES]
    if unknown or len(set(architectures)) != len(architectures):
        raise recipe_error(
            name,
            f"architectures must be different ones of {', '.join(ARCHITECTURES)}"
            + (f" (not {', '.join(unknown)})" if unknown else ""),
        )
    if kind == "compose" and not architectures:
        raise recipe_error(
            name, "a compose recipe says which architectures its images are published for"
        )
    return architectures


def _parse_changelog(name: str, raw: Any) -> tuple[ChangelogEntry, ...]:
    """
    Read what the latest versions of a recipe changed.

    Args:
        name: The recipe.
        raw: The ``changelog`` list, or None.

    Returns:
        The entries, newest first as written.

    Raises:
        RecipeError: An entry is not ``{version, summary}`` of strings.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_CHANGELOG:
        raise recipe_error(name, f"changelog is a list of at most {MAX_CHANGELOG} entries")
    entries: list[ChangelogEntry] = []
    for index, item in enumerate(raw):
        if (
            not isinstance(item, dict)
            or set(item) != {"version", "summary"}
            or not all(isinstance(value, str) and value.strip() for value in item.values())
        ):
            raise recipe_error(name, f"changelog entry {index + 1} needs a version and a summary")
        entries.append(ChangelogEntry(version=item["version"], summary=item["summary"]))
    return tuple(entries)


def _parse_requires(name: str, kind: str, raw: Any) -> tuple[str, ...]:
    """
    Read what the recipe asks the server to provide.

    Args:
        name: The recipe.
        kind: ``native`` or ``compose``.
        raw: The ``requires`` list, or None.

    Returns:
        The requirement names.

    Raises:
        RecipeError: One is unknown, repeated, a second SQL database, or a
            database or store a Compose recipe cannot reach.
    """
    requires = _strings(name, raw, "requires")
    unknown = [item for item in requires if item not in REQUIREMENTS]
    if unknown:
        raise recipe_error(
            name, f"requires {', '.join(unknown)}: it can be {', '.join(REQUIREMENTS)}"
        )
    if len(set(requires)) != len(requires):
        raise recipe_error(name, "requires lists the same service twice")
    if sum(item in SQL_REQUIREMENTS for item in requires) > 1:
        raise recipe_error(
            name, "a recipe gets one SQL database; list one of postgres, mysql, mariadb"
        )
    if kind == "compose" and any(item != "s3" for item in requires):
        raise recipe_error(
            name,
            "a compose recipe cannot use the server's databases (its containers cannot reach "
            "127.0.0.1); put the database in the stack, and keep requires for s3",
        )
    return requires


#: Engines that speak one protocol, so one recipe runs on any of them.
_SAME_PROTOCOL = frozenset({"mysql", "mariadb"})


def _parse_database_choices(name: str, raw: Any, requires: tuple[str, ...]) -> tuple[str, ...]:
    """
    Read the SQL engines a native recipe may run on, the operator choosing at install.

    Args:
        name: The recipe.
        raw: The ``database_choices`` list, or None.
        requires: The recipe's requirements, whose SQL one is the default.

    Returns:
        The choices, the default first; empty when the recipe offers none.

    Raises:
        RecipeError: A choice is not an engine that speaks the default's protocol, repeats, or
            the recipe asks for no SQL database or lists a default other than its own first.
    """
    choices = _strings(name, raw, "database_choices")
    if not choices:
        return ()
    default = next((item for item in requires if item in SQL_REQUIREMENTS), None)
    if default is None:
        raise recipe_error(name, "database_choices needs a SQL database in requires")
    if len(set(choices)) != len(choices):
        raise recipe_error(name, "database_choices lists the same engine twice")
    if choices[0] != default:
        raise recipe_error(
            name, f"database_choices starts with the default, {default} (the one in requires)"
        )
    if any(item not in _SAME_PROTOCOL for item in choices):
        raise recipe_error(
            name,
            "database_choices can only offer engines that speak the same protocol: "
            "mysql and mariadb",
        )
    if len(choices) < 2:
        raise recipe_error(name, "database_choices offers a choice: list at least two engines")
    return choices


def _parse_build(name: str, raw: Any) -> RecipeBuild | None:
    """
    Read a native recipe's build block.

    The block has the names of the ``build:`` of a repository's
    ``noust.yaml``, so one description serves both. Commands are split with
    ``shlex`` and run as an argv; none of them goes through a shell.

    Args:
        name: The recipe.
        raw: The ``build`` mapping, or None.

    Returns:
        The block, or None when the recipe has none.

    Raises:
        RecipeError: An unknown key, a command that is not a plain command
            line, a directory that leaves the source, or a malformed runtime.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw:
        raise recipe_error(name, "build must be a mapping with at least one key")
    unknown = set(raw) - _BUILD_KEYS
    if unknown:
        raise recipe_error(name, f"unknown build keys: {', '.join(sorted(unknown))}")
    checked: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value.strip():
            raise recipe_error(name, f"build.{key} must be a non-empty string")
        if key in ("install", "build", "start"):
            try:
                words = shlex.split(value)
            except ValueError as exc:
                raise recipe_error(name, f"build.{key} is not a command line: {exc}") from exc
            if not words or any(
                token in {"|", "||", "&&", ";", "&", ">", ">>", "<"} for token in words
            ):
                raise recipe_error(
                    name,
                    f"build.{key} is run as an argv, never through a shell: no pipes, "
                    "chaining or redirects",
                )
            try:
                scan_references(value)
            except ReferenceSyntaxError as exc:
                raise recipe_error(name, f"build.{key}: {exc}") from exc
        elif key in ("base_directory", "publish_directory"):
            if not is_plain_path(value):
                raise recipe_error(name, f"build.{key} must be a plain relative path")
        elif not _RUNTIME.fullmatch(value):
            raise recipe_error(name, "build.runtime must look like node@22 or python@3.12")
        checked[key] = value
    return RecipeBuild(**checked)


def _variable_entry(name: str, variable: str, raw: Any) -> RecipeVariable:
    """
    Read one variable's declaration.

    Args:
        name: The recipe.
        variable: The variable's name.
        raw: What the file says under it.

    Returns:
        The variable, before it is checked against the others.

    Raises:
        RecipeError: Unknown key, not exactly one origin, or a value of the
            wrong type.
    """
    where = f"variable {variable}"
    if not is_valid_env_name(variable) or variable in RESERVED_VARIABLES:
        raise recipe_error(
            name,
            f"{where}: not a usable name"
            + (" (Noust provides PORT itself)" if variable in RESERVED_VARIABLES else ""),
        )
    if not isinstance(raw, dict):
        raise recipe_error(name, f"{where} must be a mapping")
    unknown = set(raw) - _VARIABLE_KEYS
    if unknown:
        raise recipe_error(name, f"{where} has unknown keys: {', '.join(sorted(unknown))}")
    origins = [key for key in _VARIABLE_ORIGINS if key in raw]
    if len(origins) != 1:
        raise recipe_error(
            name, f"{where} needs exactly one of generate, from, value, default or required"
        )
    origin = origins[0]
    description = raw.get("description", "")
    if not isinstance(description, str) or len(description) > 200 or "\n" in description:
        raise recipe_error(name, f"{where}: description is one line of at most 200 characters")
    secret = raw.get("secret")
    if secret is not None and not isinstance(secret, bool):
        raise recipe_error(name, f"{where}: secret must be true or false")
    fields: dict[str, Any] = {"name": variable, "description": description}
    if origin == "generate":
        form = raw["generate"]
        if form not in GENERATORS:
            raise recipe_error(name, f"{where}: generate must be one of {', '.join(GENERATORS)}")
        fields["generate"] = form
        if form == "jwt":
            if not isinstance(raw.get("signed_with"), str) or not isinstance(raw.get("role"), str):
                raise recipe_error(name, f"{where}: a jwt needs signed_with and role")
            fields["signed_with"], fields["role"] = raw["signed_with"], raw["role"]
        elif "signed_with" in raw or "role" in raw:
            raise recipe_error(name, f"{where}: signed_with and role are for generate: jwt")
        if secret is None:
            secret = form in SECRET_GENERATORS
    elif origin == "from":
        source = raw["from"]
        if not isinstance(source, str) or not (
            source in CONTEXT_SOURCES or _requirement_source(source)
        ):
            raise recipe_error(
                name,
                f"{where}: from must be one of {', '.join(CONTEXT_SOURCES)}, or a requirement "
                "attribute such as postgres.url",
            )
        fields["source"] = source
        if secret is None:
            secret = _source_is_secret(source)
    elif origin == "value":
        text = raw["value"]
        if not isinstance(text, str):
            raise recipe_error(name, f"{where}: value must be a string (quote numbers)")
        try:
            scan_references(text)
        except ReferenceSyntaxError as exc:
            raise recipe_error(name, f"{where}: {exc}") from exc
        fields["value"] = text
    elif origin == "default":
        if not isinstance(raw["default"], str):
            raise recipe_error(name, f"{where}: default must be a string (quote numbers)")
        fields["default"] = raw["default"]
    else:
        if raw["required"] is not True:
            raise recipe_error(name, f"{where}: required is true, or leave it out")
        fields["required"] = True
    if "service" in raw or "port" in raw:
        if fields.get("source") not in ("url", "fqdn"):
            raise recipe_error(name, f"{where}: service and port are for from: url or fqdn")
        if "service" in raw:
            if not isinstance(raw["service"], str):
                raise recipe_error(name, f"{where}: service must be a service name")
            fields["service"] = raw["service"]
        if "port" in raw:
            if not isinstance(raw["port"], int) or isinstance(raw["port"], bool):
                raise recipe_error(name, f"{where}: port must be a container port number")
            fields["port"] = raw["port"]
    fields["secret"] = bool(secret)
    return RecipeVariable(**fields)


def _source_is_secret(source: str) -> bool:
    """
    Tell whether what a requirement provides is a secret.

    A provisioned connection string embeds the password, so it is one too.

    Args:
        source: A ``from`` value.

    Returns:
        True for ``postgres.password``, ``redis.url``, ``s3.secret_key``;
        False for ``postgres.host``, ``s3.bucket`` and the deployment's own values.
    """
    requirement, _, attribute = source.partition(".")
    if requirement == "s3":
        return attribute in ("access_key", "secret_key")
    return attribute in ("password", "url")


def _requirement_source(source: str) -> bool:
    """
    Tell whether ``source`` is ``<requirement>.<attribute>`` of a known requirement.

    Args:
        source: A ``from`` value.

    Returns:
        True for ``postgres.url``; False otherwise.
    """
    requirement, _, attribute = source.partition(".")
    return attribute in REQUIREMENT_ATTRIBUTES.get(requirement, ())


def _parse_variables(
    name: str,
    raw: Any,
    requires: tuple[str, ...],
    info: ComposeInfo | None,
    extra_used: frozenset[str] = frozenset(),
) -> tuple[RecipeVariable, ...]:
    """
    Read and cross-check a recipe's variables.

    Args:
        name: The recipe.
        raw: The ``variables`` mapping, or None.
        requires: The recipe's requirements.
        info: The Compose document, for a Compose recipe.
        extra_used: Names the recipe's rendered files use, which count as used.

    Returns:
        The variables in file order, then the ones a Compose document implies
        (``${X:-default}`` and ``${X:?message}``).

    Raises:
        RecipeError: A variable uses a requirement the recipe did not ask
            for, a jwt is not signed with a declared variable, ``url`` points
            at a service the stack does not route, or the Compose document
            does not use a variable that is declared.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise recipe_error(name, "variables must be a mapping")
    declared = [_variable_entry(name, str(key), value) for key, value in raw.items()]
    names = {variable.name for variable in declared}
    for variable in declared:
        if variable.source and "." in variable.source:
            requirement = variable.source.split(".", 1)[0]
            if requirement not in requires:
                raise recipe_error(
                    name,
                    f"variable {variable.name} is {variable.source}, but the recipe does not "
                    f"require {requirement}",
                )
        if variable.signed_with is not None and variable.signed_with not in names:
            raise recipe_error(
                name,
                f"variable {variable.name} is signed with {variable.signed_with}, not declared",
            )
        if info is not None and (variable.service or variable.port):
            if variable.service not in (None, info.web_service) or variable.port not in (
                None,
                info.web_port,
            ):
                raise recipe_error(
                    name,
                    f"variable {variable.name}: this release routes one web endpoint "
                    f"({info.web_service}:{info.web_port}); service and port must name it",
                )
    try:
        ordered = references_in_order(declared)
    except RecipeError as exc:
        raise recipe_error(name, exc.message) from exc
    secret_names = {variable.name for variable in ordered if variable.secret}
    result: dict[str, RecipeVariable] = {}
    for variable in ordered:
        if variable.value is not None and any(
            reference.name in secret_names for reference in scan_references(variable.value)
        ):
            variable = dataclasses.replace(variable, secret=True)
            secret_names.add(variable.name)
        result[variable.name] = variable
    variables = [result[variable.name] for variable in declared]

    if info is not None:
        used = {reference.name for reference in info.references} | set(extra_used)
        for variable in declared:
            used |= {reference.name for reference in scan_references(variable.value or "")}
            used.add(variable.signed_with or "")
        unused = sorted(names - used)
        if unused:
            raise recipe_error(
                name,
                f"variables {', '.join(unused)} are declared and the compose document never uses them",
            )
        try:
            implied = implicit_variables(info.references, names)
        except RecipeError as exc:
            raise recipe_error(name, exc.message) from exc
        for implicit_name, reference in implied.items():
            variables.append(
                RecipeVariable(
                    name=implicit_name,
                    default=reference.default if reference.default is not None else None,
                    required=reference.requires_value,
                    description=reference.argument if reference.requires_value else "",
                )
            )
    return tuple(variables)


def _parse_public_ports(name: str, raw: Any) -> tuple[PublicPort, ...]:
    """
    Read the ports a Compose recipe opens to the network.

    Args:
        name: The recipe.
        raw: The ``public_ports`` list, or None.

    Returns:
        The ports.

    Raises:
        RecipeError: An entry is not ``{port, protocol, purpose}``.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise recipe_error(name, "public_ports must be a list")
    ports: list[PublicPort] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"port", "protocol", "purpose"}:
            raise recipe_error(name, "each public port needs port, protocol and purpose")
        port, protocol, purpose = item["port"], item["protocol"], item["purpose"]
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
            or protocol not in ("tcp", "udp")
            or not isinstance(purpose, str)
            or not purpose.strip()
        ):
            raise recipe_error(
                name, f"public port {item!r} is not a port, tcp or udp, and a purpose"
            )
        ports.append(PublicPort(port=port, protocol=protocol, purpose=purpose))
    return tuple(ports)


def _parse_entrypoints(
    name: str, raw: Any, variables: tuple[RecipeVariable, ...], info: ComposeInfo
) -> tuple[Entrypoint, ...]:
    """
    Read the extra public addresses of a Compose recipe.

    Args:
        name: The recipe.
        raw: The ``entrypoints`` mapping, or None.
        variables: The recipe's variables.
        info: What its Compose document says.

    Returns:
        The entrypoints, in the order the file lists them.

    Raises:
        RecipeError: An entrypoint is malformed, names a variable that is not what it needs to
            be, or shares a variable with another.
    """
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise recipe_error(name, "entrypoints must be a mapping")
    if not raw:
        raise recipe_error(name, "entrypoints needs at least one entry")
    if len(raw) > _MAX_ENTRYPOINTS:
        raise recipe_error(name, f"a recipe has at most {_MAX_ENTRYPOINTS} entrypoints")
    by_name = {variable.name: variable for variable in variables}
    found: list[Entrypoint] = []
    used: dict[tuple[str, str], str] = {}
    for key, item in raw.items():
        entry = str(key)
        if not _ENTRYPOINT_NAME.fullmatch(entry):
            raise recipe_error(
                name, f"entrypoint name {entry!r} must be lower-case letters, digits and dashes"
            )
        if not isinstance(item, dict):
            raise recipe_error(name, f"entrypoint {entry} must be a mapping")
        unknown = set(item) - _ENTRYPOINT_KEYS
        if unknown:
            raise recipe_error(
                name, f"entrypoint {entry} has unknown keys: {', '.join(sorted(map(str, unknown)))}"
            )
        domain, port = (str(item.get("domain_variable", "")), str(item.get("port_variable", "")))
        for label, variable in (("domain_variable", domain), ("port_variable", port)):
            if variable not in by_name:
                raise recipe_error(
                    name, f"entrypoint {entry}: {label} {variable} is not a variable of the recipe"
                )
            if (label, variable) in used:
                role = "address" if label == "domain_variable" else "port"
                raise recipe_error(
                    name, f"entrypoint {entry}: {variable} is the {role} of two entrypoints"
                )
            used[(label, variable)] = entry
        chosen = by_name[domain]
        if not chosen.required or chosen.generate or chosen.default is not None or chosen.secret:
            raise recipe_error(
                name,
                f"entrypoint {entry}: {domain} must be a required variable the operator gives "
                "(no default, nothing generated, not secret)",
            )
        if port not in info.local_port_variables:
            raise recipe_error(
                name,
                f"entrypoint {entry}: {port} is not a loopback port the document publishes "
                "(127.0.0.1:${NAME}:<container port>)",
            )
        found.append(
            Entrypoint(
                name=entry,
                title=_string(name, item, "title"),
                description=str(item.get("description", "")).strip(),
                domain_variable=domain,
                port_variable=port,
            )
        )
    return tuple(found)


def _parse_files(
    name: str, raw: Any, declared: set[str], *, kind: str
) -> tuple[dict[str, str], frozenset[str], dict[str, int], frozenset[str]]:
    """
    Read the files a recipe ships.

    An entry is ``path: asset`` (copied as it is) or ``path: {asset, render,
    mode}``. A rendered file has its ``${NAME}`` references filled with the
    application's variables when it is written, so it may carry a secret and
    is written ``0600`` (``0640`` for a native recipe, whose account reads
    its group's files) unless the recipe asks for more; every name it uses must
    be one the recipe declares (or ``PORT`` in a Compose recipe), which is
    checked here, when the recipe loads.

    Args:
        name: The recipe.
        raw: The ``files`` mapping, or None.
        declared: The variables the recipe declares.
        kind: ``native`` or ``compose``.

    Returns:
        Path to asset name, the paths that are rendered, the permissions
        asked for, and the variable names the rendered files use.

    Raises:
        RecipeError: A path is not plain, an asset is not shipped, an entry
            has a key outside the closed list, a mode is not one of
            ``0600``, ``0640`` and ``0644``, or a rendered file uses a
            reference that is not declared.
    """
    if raw is None:
        return {}, frozenset(), {}, frozenset()
    if not isinstance(raw, dict):
        raise recipe_error(name, "files must map paths to assets")
    if len(raw) > _MAX_FILES:
        raise recipe_error(name, f"a recipe ships at most {_MAX_FILES} files")
    reserved = ("docker-compose.yml", ".env") if kind == "compose" else (".env",)
    known = declared | ({"PORT"} if kind == "compose" else set())
    files: dict[str, str] = {}
    rendered: set[str] = set()
    modes: dict[str, int] = {}
    used: set[str] = set()
    for path, entry in raw.items():
        if not is_plain_path(path) or path in reserved:
            raise recipe_error(name, f"file path {path!r} is not a plain relative path")
        if isinstance(entry, dict):
            extra = set(entry) - _FILE_KEYS
            if extra:
                raise recipe_error(
                    name, f"file {path!r} has unknown keys: {', '.join(sorted(map(str, extra)))}"
                )
            asset = entry.get("asset")
            render = entry.get("render", False)
            if not isinstance(render, bool):
                raise recipe_error(name, f"file {path!r}: render must be true or false")
            mode = entry.get("mode")
            if mode is not None:
                if str(mode) not in _FILE_MODES:
                    raise recipe_error(
                        name,
                        f"file {path!r}: mode must be one of {', '.join(_FILE_MODES)}, as a string",
                    )
                modes[path] = _FILE_MODES[str(mode)]
        else:
            asset, render = entry, False
        files[path] = checked_asset(name, asset)
        if render:
            rendered.add(path)
            # A native application's account reads its group's files; a container
            # reads what root owns only when the recipe says so.
            modes.setdefault(path, 0o600 if kind == "compose" else 0o640)
            used |= _check_references(name, path, read_asset(name, files[path]), known)
    return files, frozenset(rendered), modes, frozenset(used)


def _check_references(name: str, path: str, text: str, known: set[str]) -> set[str]:
    """
    Check that a rendered file only uses variables the recipe declares.

    Args:
        name: The recipe.
        path: The file, for the message.
        text: Its asset.
        known: The names it may use.

    Returns:
        The names the file uses.

    Raises:
        RecipeError: A reference is malformed or names a variable nobody declares.
    """
    try:
        used = {reference.name for reference in scan_references(text)}
    except ReferenceSyntaxError as exc:
        raise recipe_error(name, f"file {path!r}: {exc}") from exc
    unknown = sorted(used - known)
    if unknown:
        raise recipe_error(
            name,
            f"file {path!r} uses {', '.join(unknown)}, which the recipe does not declare",
            field="files",
        )
    return used


def _parse_v2(name: str, data: dict[str, Any]) -> Recipe:
    """
    Read a recipe in the current format.

    Args:
        name: The recipe's file name.
        data: What the file holds.

    Returns:
        The recipe.

    Raises:
        RecipeError: When anything in it is not valid.
    """
    if data.get("schema") != SCHEMA_VERSION:
        raise recipe_error(name, f"schema must be {SCHEMA_VERSION}, or absent for the 2.3 format")
    available = data.get("available", True)
    if not isinstance(available, bool):
        raise recipe_error(name, "available must be true or false")
    kind = data.get("kind", "native")
    if kind not in KINDS:
        raise recipe_error(name, f"kind must be one of {', '.join(KINDS)}")

    if not available:
        unknown = set(data) - _V2_UNAVAILABLE
        if unknown:
            raise recipe_error(name, f"an unavailable recipe has no {', '.join(sorted(unknown))}")
        if data.get("name") != name:
            raise recipe_error(name, "name must be the file's name")
        return Recipe(
            name=name,
            schema=SCHEMA_VERSION,
            kind=kind,
            available=False,
            unavailable_reason=_string(name, data, "unavailable_reason"),
            **_parse_metadata(name, data, listed_only=True),
        )
    if "unavailable_reason" in data:
        raise recipe_error(name, "unavailable_reason is only for an unavailable recipe")

    allowed = _V2_COMMON | (_V2_NATIVE if kind == "native" else _V2_COMPOSE)
    unknown = set(data) - allowed
    if unknown:
        raise recipe_error(name, f"unknown keys: {', '.join(sorted(unknown))}")
    if data.get("name") != name:
        raise recipe_error(name, "name must be the file's name")
    metadata = _parse_metadata(name, data)
    if metadata["logo"] is None:
        raise recipe_error(name, "logo is required: an SVG under assets/<recipe>/")
    metadata["min_noust"] = _parse_min_noust(name, data.get("min_noust"))
    metadata["architectures"] = _parse_architectures(name, kind, data.get("architectures"))
    requires = _parse_requires(name, kind, data.get("requires"))
    port = _port(name, data.get("port"))
    health = _parse_health(name, data.get("health"))

    if kind == "compose":
        digest_verified = data.get("digest_verified", True)
        if not isinstance(digest_verified, bool):
            raise recipe_error(name, "digest_verified must be true or false")
        public_ports = _parse_public_ports(name, data.get("public_ports"))
        declared_names = _declared_names(name, data.get("variables"))
        files, rendered_files, file_modes, file_uses = _parse_files(
            name, data.get("files"), declared_names, kind="compose"
        )
        info = validate_compose(
            name,
            data.get("compose"),
            digest_verified=digest_verified,
            public_ports=public_ports,
            files=files,
            declared=declared_names,
        )
        variables = _parse_variables(name, data.get("variables"), requires, info, file_uses)
        _check_host_variables(name, variables, info)
        entrypoints = _parse_entrypoints(name, data.get("entrypoints"), variables, info)
        return Recipe(
            name=name,
            schema=SCHEMA_VERSION,
            kind="compose",
            app_type="docker-compose",
            layout="inplace",
            port=port,
            requires=requires,
            variables=variables,
            health=health,
            compose=str(data["compose"]),
            files=files,
            rendered_files=rendered_files,
            file_modes=file_modes,
            images=info.images,
            digest_verified=digest_verified,
            public_ports=public_ports,
            entrypoints=entrypoints,
            **metadata,
        )

    app_type = _string(name, data, "app_type")
    _known_app_type(name, app_type)
    if app_type in ("docker-compose", "monorepo"):
        raise recipe_error(name, f"a {app_type} recipe is kind: compose, not native")
    layout = data.get("layout", "releases")
    if layout not in LAYOUTS:
        raise recipe_error(name, f"layout must be one of {', '.join(LAYOUTS)}")
    variables = _parse_variables(name, data.get("variables"), requires, None)
    files, rendered_files, file_modes, _used = _parse_files(
        name, data.get("files"), {variable.name for variable in variables}, kind="native"
    )
    return Recipe(
        name=name,
        schema=SCHEMA_VERSION,
        kind="native",
        app_type=app_type,
        source=_parse_source(name, data.get("source")),
        layout=layout,
        port=port,
        requires=requires,
        database_choices=_parse_database_choices(name, data.get("database_choices"), requires),
        build=_parse_build(name, data.get("build")),
        variables=variables,
        persistent_paths=_persistent(name, data.get("persistent_paths")),
        health=health,
        php=_parse_php(name, app_type, data.get("php")),
        files=files,
        rendered_files=rendered_files,
        file_modes=file_modes,
        **metadata,
    )


def _declared_names(name: str, raw: Any) -> set[str]:
    """
    List the names a ``variables`` mapping declares, before it is parsed.

    Args:
        name: The recipe.
        raw: The ``variables`` mapping, or None.

    Returns:
        The names.

    Raises:
        RecipeError: It is not a mapping.
    """
    if raw is None:
        return set()
    if not isinstance(raw, dict):
        raise recipe_error(name, "variables must be a mapping")
    return {str(key) for key in raw}


def _check_host_variables(
    name: str, variables: tuple[RecipeVariable, ...], info: ComposeInfo
) -> None:
    """
    Check the variables a Compose document uses to reach the host.

    A host port needs a default that is a port, and a mounted directory a
    default that is a directory inside the application (``./media``), so a
    recipe installed without a word from the operator touches nothing outside
    its own directory.

    Args:
        name: The recipe.
        variables: The recipe's variables.
        info: The Compose document.

    Raises:
        RecipeError: A host port or a mounted directory has no usable default.
    """
    for variable in variables:
        default = variable.default
        if variable.name in info.local_port_variables and (
            default is None or not default.isdigit() or not 1024 <= int(default) <= 65535
        ):
            raise recipe_error(
                name,
                f"variable {variable.name} is a host port and needs a default from 1024 to 65535",
            )
        if variable.name in info.path_variables and (
            default is None or not default.startswith("./") or not is_plain_path(default[2:])
        ):
            raise recipe_error(
                name,
                f"variable {variable.name} is a mounted directory and needs a default "
                "inside the application, such as ./media",
            )


def parse_recipe(name: str, data: Any) -> Recipe:
    """
    Validate a recipe's contents, in either format.

    Args:
        name: The recipe's file name, without ``.yaml``.
        data: What the YAML file holds.

    Returns:
        The recipe.

    Raises:
        RecipeError: When anything in it is not valid.
    """
    if not isinstance(data, dict):
        raise recipe_error(name, "the file must hold a mapping")
    if "schema" in data:
        return _parse_v2(name, data)
    return _parse_v1(name, data)
