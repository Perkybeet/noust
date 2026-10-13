# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Compose document of a ``kind: compose`` recipe, validated.

A recipe's document is package data, but it is held to what an untrusted
``docker-compose.yml`` is held to, and a little more: it is the thing that
runs as root on an operator's server, through the Docker daemon, once they
press install. So the document is read through a closed schema:

- Every image is pinned: a tag that names a release (never ``latest``, never
  none, never a tag that moves), and, when the recipe says its digests were
  verified, the ``sha256`` digest the registry served for that tag. Coolify's
  catalogue has no digest anywhere and 54% of its templates use a floating tag;
  this is where that stops.
- Nothing the Compose guard counts as root on the host: no ``privileged``,
  no Docker socket, no host namespaces, no devices, no added capabilities, no
  disabled confinement. A recipe that needs one of them is not a recipe.
- One web port, published as ``127.0.0.1:${PORT}:<container port>``: nginx is
  the only way in. Anything else a stack opens to the network is declared in
  the recipe's ``public_ports``, so the operator is told before installing.
- Named volumes, declared and used; or paths under ``./`` that are files the
  recipe ships. No path of the host, no ``..``.
- ``restart: unless-stopped`` on every service, because the unit that owns the
  stack only runs ``docker compose up -d`` once.
- Every ``${VAR}`` is a variable the recipe declares, or carries its own
  default (``${VAR:-x}``) or its own demand (``${VAR:?message}``).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from noust.recipes.model import (
    FLOATING_TAGS,
    PublicPort,
    RecipeError,
    RecipeImage,
    recipe_error,
)
from noust.recipes.variables import Reference, ReferenceSyntaxError, scan_references

#: The longest document a recipe may carry.
MAX_COMPOSE_BYTES = 48 * 1024

_TOP_LEVEL = frozenset({"services", "volumes"})

#: What a service of a recipe may say. Anything else (``build``, ``privileged``,
#: ``network_mode``, ``cap_add``, ``devices``, ``env_file``, ``extends``,
#: ``volumes_from``, ``secrets``, ``configs``) is refused by name.
_SERVICE_KEYS = frozenset(
    {
        "image",
        "command",
        "entrypoint",
        "environment",
        "ports",
        "volumes",
        "depends_on",
        "healthcheck",
        "restart",
        "user",
        "working_dir",
        "expose",
        "tmpfs",
        "stop_grace_period",
        "stop_signal",
        "init",
        "read_only",
        "shm_size",
        "ulimits",
        "mem_limit",
        "mem_reservation",
        "cpus",
        "pids_limit",
        "deploy",
        "cap_drop",
        "security_opt",
        "logging",
        "hostname",
        "extra_hosts",
        "pull_policy",
        "tty",
        "stdin_open",
    }
)

_IMAGE = re.compile(
    r"^(?P<repository>[a-z0-9]+(?:[._-]+[a-z0-9]+)*(?:/[a-z0-9]+(?:[._-]+[a-z0-9]+)*)*)"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?"
    r"(?:@(?P<digest>sha256:[0-9a-f]{64}))?$"
)

_WEB_PORT = re.compile(r"^127\.0\.0\.1:\$\{PORT\}:(?P<container>[0-9]{1,5})(?:/tcp)?$")
_LOCAL_PORT = re.compile(
    r"^127\.0\.0\.1:\$\{(?P<variable>[A-Z][A-Z0-9_]*)\}:(?P<container>[0-9]{1,5})(?:/(?P<protocol>tcp|udp))?$"
)
_PUBLIC_PORT = re.compile(
    r"^(?P<host>[0-9]{1,5}):(?P<container>[0-9]{1,5})(?:/(?P<protocol>tcp|udp))?$"
)
_NAMED_VOLUME = re.compile(r"^(?P<name>[a-z][a-z0-9_-]*):(?P<target>/[^:]*)(?::(?:ro|rw))?$")
_FILE_VOLUME = re.compile(r"^(?P<source>\./[A-Za-z0-9._/-]+):(?P<target>/[^:]*)(?::(?:ro|rw))?$")
_PATH_VOLUME = re.compile(
    r"^\$\{(?P<variable>[A-Z][A-Z0-9_]*)\}:(?P<target>/[^:]*)(?::(?:ro|rw))?$"
)
_SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_NO_NEW_PRIVILEGES = ("no-new-privileges:true", "no-new-privileges=true")


@dataclass(frozen=True)
class ComposeInfo:
    """
    What a recipe's Compose document says, once it has been accepted.

    Attributes:
        images: Every service's image, as pinned.
        services: The services, in file order.
        web_service: The service nginx proxies to.
        web_port: The container port of that service.
        references: Every variable reference in the document.
        local_port_variables: Variables that name a loopback host port.
        path_variables: Variables that name a directory of the host mounted
            into a service (a media library): the operator's choice.
    """

    images: tuple[RecipeImage, ...]
    services: tuple[str, ...]
    web_service: str
    web_port: int
    references: tuple[Reference, ...]
    local_port_variables: frozenset[str]
    path_variables: frozenset[str] = frozenset()


def parse_image(name: str, service: str, reference: Any, *, digest_verified: bool) -> RecipeImage:
    """
    Read an image reference and check that it is pinned.

    Args:
        name: The recipe, for the message.
        service: The service the image belongs to.
        reference: What ``image:`` holds.
        digest_verified: Whether the recipe claims every image carries a
            digest read from its registry.

    Returns:
        The image.

    Raises:
        RecipeError: It is not a string, has no tag, has a floating tag, has
            no digest when one is promised, or has a malformed digest.
    """
    where = f"service {service}"
    if not isinstance(reference, str) or "${" in reference:
        raise recipe_error(name, f"{where}: image must be a fixed reference, not a variable")
    match = _IMAGE.fullmatch(reference)
    if match is None:
        raise recipe_error(name, f"{where}: {reference!r} is not an image reference")
    tag, digest = match.group("tag"), match.group("digest")
    if not tag:
        raise recipe_error(
            name,
            f"{where}: {reference!r} has no tag",
            field="compose",
        )
    if tag.lower() in FLOATING_TAGS:
        raise recipe_error(
            name,
            f"{where}: tag {tag!r} moves; pin a release",
            field="compose",
        )
    if digest_verified and digest is None:
        raise recipe_error(
            name,
            f"{where}: {reference!r} has no digest; add @sha256:... read from the registry, "
            "or set digest_verified: false",
            field="compose",
        )
    return RecipeImage(
        service=service,
        reference=reference,
        repository=match.group("repository"),
        tag=tag,
        digest=digest,
    )


def validate_compose(
    name: str,
    text: Any,
    *,
    digest_verified: bool,
    public_ports: Collection[PublicPort],
    files: Collection[str],
    declared: Collection[str],
) -> ComposeInfo:
    """
    Check a recipe's Compose document against the closed schema.

    Args:
        name: The recipe, for the message.
        text: The document as the recipe holds it.
        digest_verified: Whether every image must carry a digest.
        public_ports: The ports the recipe declares open to the network.
        files: The paths of the files the recipe ships beside the document.
        declared: The variables the recipe declares.

    Returns:
        What the document says.

    Raises:
        RecipeError: Anything in it is outside the schema; the message names
            the service and what is wrong.
    """
    if not isinstance(text, str) or not text.strip():
        raise recipe_error(name, "compose must hold a Docker Compose document", field="compose")
    if len(text.encode()) > MAX_COMPOSE_BYTES:
        raise recipe_error(name, f"compose is larger than {MAX_COMPOSE_BYTES // 1024} KiB")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise recipe_error(name, f"compose is not valid YAML: {exc}", field="compose") from exc
    if not isinstance(document, dict):
        raise recipe_error(name, "compose must be a mapping with services", field="compose")
    unknown = set(document) - _TOP_LEVEL
    if unknown:
        raise recipe_error(
            name,
            f"compose has top-level keys {', '.join(sorted(map(str, unknown)))}; "
            "only services and volumes are accepted",
            field="compose",
        )
    services = document.get("services")
    if not isinstance(services, dict) or not services:
        raise recipe_error(name, "compose has no services", field="compose")
    volumes = _top_level_volumes(name, document.get("volumes"))

    try:
        references = tuple(scan_references(text))
    except ReferenceSyntaxError as exc:
        raise recipe_error(name, f"compose: {exc}", field="compose") from exc

    images: list[RecipeImage] = []
    web: list[tuple[str, int]] = []
    local_variables: set[str] = set()
    path_variables: set[str] = set()
    used_volumes: set[str] = set()
    allowed_public = {(entry.port, entry.protocol) for entry in public_ports}
    opened: set[tuple[int, str]] = set()
    for service_name, service in services.items():
        service_name = str(service_name)
        if not _SERVICE_NAME.fullmatch(service_name):
            raise recipe_error(name, f"service name {service_name!r} is not a plain name")
        if not isinstance(service, dict):
            raise recipe_error(name, f"service {service_name} must be a mapping")
        unknown = set(service) - _SERVICE_KEYS
        if unknown:
            raise recipe_error(
                name,
                f"service {service_name} uses {', '.join(sorted(map(str, unknown)))}, which a "
                "recipe may not",
                field="compose",
            )
        images.append(
            parse_image(name, service_name, service.get("image"), digest_verified=digest_verified)
        )
        if service.get("restart") != "unless-stopped":
            raise recipe_error(
                name,
                f"service {service_name} needs restart: unless-stopped",
                field="compose",
            )
        _check_security(name, service_name, service)
        used_volumes |= _check_volumes(name, service_name, service, volumes, files, path_variables)
        _check_ports(
            name,
            service_name,
            service,
            web=web,
            local_variables=local_variables,
            allowed_public=allowed_public,
            opened=opened,
        )
        _check_depends_on(name, service_name, service, services)

    if len(web) != 1:
        raise recipe_error(
            name,
            "exactly one service must publish 127.0.0.1:${PORT}:<port>, the one nginx proxies to"
            + (f" (found {len(web)})" if web else ""),
            field="compose",
        )
    never_opened = allowed_public - opened
    if never_opened:
        raise recipe_error(
            name,
            "public_ports declares "
            + ", ".join(f"{port}/{protocol}" for port, protocol in sorted(never_opened))
            + ", which no service publishes",
        )
    unused = set(volumes) - used_volumes
    if unused:
        raise recipe_error(name, f"volumes {', '.join(sorted(unused))} are declared and not used")

    # Imported here: the deployers package loads every deployer, which a
    # catalogue listing has no reason to pay for.
    from noust.deployers.helpers.compose_guard import inspect_compose

    # A mount chosen by the operator through a variable is checked above and
    # is exactly what the guard would warn about; it is not a finding here.
    judged = {
        **document,
        "services": {
            key: {
                **service,
                "volumes": [
                    volume
                    for volume in service.get("volumes") or ()
                    if not _PATH_VOLUME.fullmatch(str(volume))
                ],
            }
            for key, service in services.items()
        },
    }
    findings = inspect_compose(judged, Path("/var/www/apps/recipe"))
    if findings.refused or findings.warned:
        raise recipe_error(
            name,
            "compose asks for something of the host: "
            + "; ".join(findings.refused + findings.warned),
            field="compose",
        )
    _check_references(name, references, declared, local_variables | path_variables)
    return ComposeInfo(
        images=tuple(images),
        services=tuple(str(key) for key in services),
        web_service=web[0][0],
        web_port=web[0][1],
        references=references,
        local_port_variables=frozenset(local_variables),
        path_variables=frozenset(path_variables),
    )


def _top_level_volumes(name: str, raw: Any) -> set[str]:
    """
    Read the named volumes a document declares.

    Args:
        name: The recipe.
        raw: The top-level ``volumes`` key.

    Returns:
        The names.

    Raises:
        RecipeError: A volume is configured (driver, external, name), which
            would point it somewhere Noust does not know.
    """
    if raw is None:
        return set()
    if not isinstance(raw, dict):
        raise recipe_error(name, "volumes must be a mapping of names")
    for volume, config in raw.items():
        if not re.fullmatch(r"^[a-z][a-z0-9_-]*$", str(volume)):
            raise recipe_error(name, f"volume {volume!r} is not a plain name")
        if config not in (None, {}):
            raise recipe_error(
                name,
                f"volume {volume} must be declared with no options: a recipe's data stays in Docker's own volumes",
            )
    return {str(volume) for volume in raw}


def _check_security(name: str, service: str, data: Mapping[str, Any]) -> None:
    """
    Refuse what lowers a container's confinement.

    Args:
        name: The recipe.
        service: The service.
        data: Its definition.

    Raises:
        RecipeError: ``security_opt`` holds anything but no-new-privileges.
    """
    for option in data.get("security_opt") or ():
        if str(option).replace(" ", "") not in _NO_NEW_PRIVILEGES:
            raise recipe_error(
                name,
                f"service {service}: security_opt {option!r} is not allowed; only no-new-privileges:true",
            )


def _check_volumes(
    name: str,
    service: str,
    data: Mapping[str, Any],
    declared: set[str],
    files: Collection[str],
    path_variables: set[str],
) -> set[str]:
    """
    Check a service's mounts.

    Args:
        name: The recipe.
        service: The service.
        data: Its definition.
        declared: The named volumes the document declares.
        files: The files the recipe ships.
        path_variables: Collects the variables that name a directory of the
            host (``${MEDIA_PATH}:/media``): the one place a host path may
            enter, and only because the operator is the one who sets it.

    Returns:
        The named volumes it uses.

    Raises:
        RecipeError: A mount is a path of the host written in the document,
            climbs, or names a volume or a file the recipe does not have.
    """
    used: set[str] = set()
    for entry in data.get("volumes") or ():
        if not isinstance(entry, str):
            raise recipe_error(name, f"service {service}: write volumes as 'name:/path'")
        from_variable = _PATH_VOLUME.fullmatch(entry)
        if from_variable is not None:
            path_variables.add(from_variable.group("variable"))
            continue
        named = _NAMED_VOLUME.fullmatch(entry)
        if named is not None:
            if named.group("name") not in declared:
                raise recipe_error(
                    name,
                    f"service {service}: volume {named.group('name')} is not declared under volumes",
                )
            used.add(named.group("name"))
            continue
        shipped = _FILE_VOLUME.fullmatch(entry)
        if shipped is not None:
            source = PurePosixPath(shipped.group("source")).as_posix().removeprefix("./")
            if ".." in source.split("/") or not any(
                path == source or path.startswith(source.rstrip("/") + "/") for path in files
            ):
                raise recipe_error(
                    name, f"service {service}: {entry!r} mounts a file the recipe does not ship"
                )
            continue
        raise recipe_error(
            name,
            f"service {service}: volume {entry!r} must be a declared named volume, a ./file "
            "the recipe ships, or ${VARIABLE}:/path with the directory chosen by the operator",
        )
    return used


def _check_ports(
    name: str,
    service: str,
    data: Mapping[str, Any],
    *,
    web: list[tuple[str, int]],
    local_variables: set[str],
    allowed_public: set[tuple[int, str]],
    opened: set[tuple[int, str]],
) -> None:
    """
    Check what a service publishes.

    Args:
        name: The recipe.
        service: The service.
        data: Its definition.
        web: Collects the ``127.0.0.1:${PORT}`` mappings found.
        local_variables: Collects variables used as a loopback host port.
        allowed_public: ``(port, protocol)`` the recipe declares open.
        opened: Collects the declared ports a service does publish.

    Raises:
        RecipeError: A port is published on an address or in a way the
            recipe did not declare.
    """
    for entry in data.get("ports") or ():
        if not isinstance(entry, str):
            raise recipe_error(name, f"service {service}: write ports as 'host:container' strings")
        web_match = _WEB_PORT.fullmatch(entry)
        if web_match is not None:
            web.append((service, int(web_match.group("container"))))
            continue
        local_match = _LOCAL_PORT.fullmatch(entry)
        if local_match is not None and local_match.group("variable") != "PORT":
            local_variables.add(local_match.group("variable"))
            continue
        public_match = _PUBLIC_PORT.fullmatch(entry)
        if public_match is not None and public_match.group("host") == public_match.group(
            "container"
        ):
            key = (int(public_match.group("host")), public_match.group("protocol") or "tcp")
            if key in allowed_public:
                opened.add(key)
                continue
        raise recipe_error(
            name,
            f"service {service}: port {entry!r} is not allowed; publish the web port as "
            "127.0.0.1:${PORT}:<port>, and declare anything open to the network under public_ports",
            field="compose",
        )


def _check_depends_on(
    name: str, service: str, data: Mapping[str, Any], services: Mapping[str, Any]
) -> None:
    """
    Check that what a service waits for is in the document.

    Args:
        name: The recipe.
        service: The service.
        data: Its definition.
        services: Every service of the document.

    Raises:
        RecipeError: It depends on a service that does not exist.
    """
    depends = data.get("depends_on") or ()
    for other in depends:
        if str(other) not in {str(key) for key in services}:
            raise recipe_error(name, f"service {service} depends on {other}, which is not defined")


def _check_references(
    name: str,
    references: tuple[Reference, ...],
    declared: Collection[str],
    local_variables: Collection[str],
) -> None:
    """
    Check that every variable the document names is the recipe's.

    A reference is fine when the recipe declares the variable, when it is the
    reserved ``PORT``, or when the reference itself carries a default or a
    demand. A plain ``${NAME}`` of something undeclared is a typo, and Compose
    would fill it with an empty string without a word.

    Args:
        name: The recipe.
        references: Everything the document references.
        declared: The recipe's declared variables.
        local_variables: Variables used as a loopback host port.

    Raises:
        RecipeError: A plain reference names an undeclared variable, or a
            host-port variable is not declared with a numeric default.
    """
    for reference in references:
        if reference.name == "PORT" or reference.name in declared:
            continue
        if reference.operator is None:
            raise recipe_error(
                name,
                f"compose uses ${{{reference.name}}}, which the recipe does not declare under "
                f"variables (or write ${{{reference.name}:-default}})",
                field="compose",
            )
    missing = sorted(set(local_variables) - set(declared))
    if missing:
        raise recipe_error(
            name,
            f"{', '.join(missing)} publish a loopback port and must be declared under variables "
            "with a numeric default",
        )


def implicit_variables(
    references: tuple[Reference, ...], declared: Collection[str]
) -> dict[str, Reference]:
    """
    Find the variables a document declares by how it uses them.

    ``${NAME:-x}`` is an optional setting that starts at ``x``; ``${NAME:?msg}``
    is one the operator must give. Neither needs a line under ``variables``.

    Args:
        references: Everything the document references.
        declared: The variables the recipe declares.

    Returns:
        Name to the first reference that implies it, in order of appearance.

    Raises:
        RecipeError: One name is implied twice in different ways.
    """
    implied: dict[str, Reference] = {}
    for reference in references:
        if reference.name == "PORT" or reference.name in declared or reference.operator is None:
            continue
        before = implied.get(reference.name)
        if before is None:
            implied[reference.name] = reference
        elif (before.operator, before.argument) != (reference.operator, reference.argument):
            raise RecipeError(
                f"Compose uses ${{{reference.name}}} with two different defaults or demands",
                details="Declare it once under variables instead.",
            )
    return implied
