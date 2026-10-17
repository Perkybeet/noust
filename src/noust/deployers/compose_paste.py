# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker Compose document pasted into the console: looked at before it runs.

Coolify's "Docker Compose Empty" runs whatever is typed into it, after a check
for injection. A document typed into a browser is untrusted input in the same
way a repository's ``noust.yaml`` is, and here it gets the same treatment as
the Compose file of any repository: the guard that refuses ``privileged`` and a
mount of the Docker socket (:mod:`noust.deployers.helpers.compose_guard`) runs on
it, and so does the Compose deployer when the stack is deployed.

:func:`analyze_document` is what the console's preview shows, and what refuses a
document that cannot be a deployment before one is queued:

- the services, their images and the ports they publish;
- the variables the document reads (``${NAME}``, ``${NAME:-default}``,
  ``${NAME:?message}``), with which have a default and which are required, so the
  form can ask for them;
- what is refused (root on the host; a ``build:`` or ``env_file:`` that points at
  files a pasted document does not have) and what is only worth knowing (an image
  without a digest, a floating tag, a port published on every address).

:func:`write_document` puts the document in the directory the deployment is made
from, the way a recipe's Compose document is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from noust.core.compose_yaml import load_compose
from noust.core.exceptions import ValidationError
from noust.core.fs import get_fs
from noust.core.store import NoustStore
from noust.deployers.helpers.compose_guard import inspect_compose
from noust.deployers.helpers.compose_ports import parse_ports
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.recipes.model import FLOATING_TAGS

#: The largest document accepted: a stack is a few KiB; this is generous.
MAX_DOCUMENT_BYTES = 256 * 1024

#: At most this many services: a document that needs more is a repository.
MAX_SERVICES = 40

_SERVICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
#: ``${NAME}``, ``${NAME:-x}``, ``${NAME-x}``, ``${NAME:?message}``, ``${NAME?message}``.
_BRACED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])([^}]*))?\}")
#: ``$NAME`` and not ``$$NAME``.
_BARE = re.compile(r"(?<!\$)\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class PastedService:
    """
    One service of a pasted document.

    Attributes:
        name: The service.
        image: Its image as written, or None when it builds one.
        publishes: The host ports it publishes, as ``8080:80/tcp`` text.
        depends_on: The services it waits for.
    """

    name: str
    image: str | None
    publishes: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class PastedVariable:
    """
    A variable the document reads.

    Attributes:
        name: The variable.
        default: Its value when nothing is given (``${NAME:-default}``), or None.
        required: Whether a missing value stops Compose (``${NAME:?message}``).
        message: The text after ``?``, when there is one.
        services: The services that use it.
    """

    name: str
    default: str | None = None
    required: bool = False
    message: str | None = None
    services: tuple[str, ...] = ()


@dataclass
class PastedCompose:
    """
    What a pasted document is, before it runs.

    Attributes:
        services: Its services.
        variables: Its variables.
        web: The services that publish a port, which one nginx can send ``/`` to.
        refused: Why it cannot be deployed as it is; empty when it can.
        warned: What is worth knowing and does not stop it.
    """

    services: list[PastedService] = field(default_factory=list)
    variables: list[PastedVariable] = field(default_factory=list)
    web: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)

    @property
    def deployable(self) -> bool:
        """Whether nothing refuses it."""
        return not self.refused


def analyze_document(document: str) -> PastedCompose:
    """
    Read a pasted Compose document and say what it is, running nothing.

    Args:
        document: The document's text.

    Returns:
        Its services and variables, and what refuses or warns about it.

    Raises:
        ValidationError: It is not a Compose document at all: too large, not
            YAML, no mapping of services, or a service named in a way a
            container name cannot be.
    """
    if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValidationError(
            "The Compose document is larger than 256 KiB",
            details="A stack is a few KiB. Deploy a repository for anything bigger.",
            field="compose_document",
        )
    try:
        parsed = load_compose(document)
    except yaml.YAMLError as exc:
        raise ValidationError(
            f"The Compose document is not valid YAML: {exc}", field="compose_document"
        ) from exc
    services = parsed.get("services") if isinstance(parsed, dict) else None
    if not isinstance(services, dict) or not services:
        raise ValidationError(
            "The Compose document has no services",
            details="It needs a top-level 'services:' mapping.",
            field="compose_document",
        )
    if len(services) > MAX_SERVICES:
        raise ValidationError(
            f"The Compose document has more than {MAX_SERVICES} services",
            details="Deploy a repository for a stack that large.",
            field="compose_document",
        )
    result = PastedCompose()
    for name, spec in services.items():
        if not _SERVICE_NAME.fullmatch(str(name)) or not isinstance(spec, dict):
            raise ValidationError(
                f"Service {name!r} is not a usable service", field="compose_document"
            )
        _read_service(str(name), spec, result)
    result.variables = _variables(document, services)
    findings = inspect_compose(parsed, Path("/var/www/apps/app"))
    result.refused.extend(findings.refused)
    result.warned.extend(findings.warned)
    return result


def _read_service(name: str, spec: dict[str, Any], result: PastedCompose) -> None:
    """
    Add one service to the analysis, with what is wrong with it.

    Args:
        name: The service.
        spec: Its mapping.
        result: The analysis being built.
    """
    image = spec.get("image")
    ports = [
        f"{mapping.host_port or '*'}:{mapping.container_port}/{mapping.protocol}"
        for mapping in parse_ports(spec.get("ports"))
    ]
    depends = spec.get("depends_on") or []
    names = tuple(depends if isinstance(depends, list) else depends.keys())
    result.services.append(
        PastedService(
            name,
            image if isinstance(image, str) else None,
            tuple(ports),
            tuple(str(item) for item in names),
        )
    )
    if ports:
        result.web.append(name)
    if isinstance(image, str):
        _judge_image(name, image, result)
    elif "build" not in spec:
        result.refused.append(f"service {name} has neither an image nor a build")
    build = spec.get("build")
    if build is not None:
        context = build if isinstance(build, str) else (build or {}).get("context", ".")
        if not str(context).startswith(("http://", "https://", "git@", "git://")):
            result.refused.append(
                f"service {name} builds from {context!r}, files a pasted document does not "
                "have: use an image, or deploy a repository"
            )
    for entry in _env_files(spec.get("env_file")):
        if entry not in (".env", "./.env"):
            result.refused.append(
                f"service {name} reads env_file {entry!r}, which a pasted document does not "
                "have: give the values as variables"
            )
    for mapping in parse_ports(spec.get("ports")):
        if mapping.host_ip is None and mapping.host_port is not None:
            result.warned.append(
                f"service {name} publishes {mapping.host_port} on every address of this "
                "server, around nginx and its certificate; bind it to 127.0.0.1 to keep it "
                "private"
            )


def _env_files(value: Any) -> list[str]:
    """
    List the files a service's ``env_file`` names.

    Args:
        value: What ``env_file`` holds: a path, a list of paths or mappings.

    Returns:
        The paths.
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return [str(item.get("path") if isinstance(item, dict) else item) for item in items]


def _judge_image(service: str, image: str, result: PastedCompose) -> None:
    """
    Say what is worth knowing about an image reference.

    Args:
        service: The service.
        image: The reference as written.
        result: The analysis being built.
    """
    if "$" in image:
        return
    reference, _, digest = image.partition("@")
    last = reference.rsplit("/", 1)[-1]
    tag = last.rsplit(":", 1)[1] if ":" in last else ""
    if (
        not reference
        or any(char.isspace() for char in image)
        or (digest and not _DIGEST.fullmatch(digest))
    ):
        result.refused.append(f"service {service}: {image!r} is not an image reference")
        return
    if digest:
        return
    if not tag or tag.lower() in FLOATING_TAGS:
        result.warned.append(
            f"service {service}: {image!r} moves (no tag, or a tag like latest): the same "
            "document can run different software tomorrow"
        )
    else:
        result.warned.append(
            f"service {service}: {image!r} is pinned by tag only; add @sha256:... to pin it "
            "to the exact image"
        )


def _variables(document: str, services: dict[str, Any]) -> list[PastedVariable]:
    """
    List the variables a document reads.

    Args:
        document: The text.
        services: The parsed services, to say which use each variable.

    Returns:
        The variables in the order they first appear. A variable with a
        default somewhere and none elsewhere counts as having none: Compose
        would stop on the plain use.
    """
    found: dict[str, PastedVariable] = {}
    for match in _BRACED.finditer(document.replace("$$", "")):
        name, operator, argument = match.group(1), match.group(2) or "", match.group(3)
        current = found.get(name, PastedVariable(name))
        default = current.default
        required = current.required
        message = current.message
        if operator in (":-", "-"):
            default = argument if default is None else default
        elif operator in (":?", "?"):
            required, message = True, argument or None
        found[name] = PastedVariable(name, default, required, message)
    for match in _BARE.finditer(document):
        found.setdefault(match.group(1), PastedVariable(match.group(1)))
    users: dict[str, list[str]] = {name: [] for name in found}
    for service, spec in services.items():
        text = yaml.safe_dump(spec)
        for name in found:
            if f"${name}" in text or f"${{{name}" in text:
                users[name].append(str(service))
    return [
        PastedVariable(
            item.name,
            item.default,
            item.required or item.default is None,
            item.message,
            tuple(users[item.name]),
        )
        for item in found.values()
    ]


def plain_uses_with_a_default(document: str) -> dict[str, str]:
    """
    List the variables a document reads with no default in one place and with one in another.

    Compose gives the plain use a blank: ``$DB`` in one service and ``${DB:-app}`` in another is
    ``app`` for the second and nothing for the first. Coolify gives both the default, because it
    makes the variable's value from it, so a template converted from there needs it written.

    Args:
        document: The text.

    Returns:
        Each such variable and the first default it is given. An empty default is not one: it is
        what the plain use gets anyway.
    """
    text = document.replace("$$", "")
    plain: set[str] = set()
    defaults: dict[str, str] = {}
    for match in _BRACED.finditer(text):
        name, operator, argument = match.group(1), match.group(2) or "", match.group(3)
        if operator in (":-", "-"):
            if argument:
                defaults.setdefault(name, argument)
        elif not operator:
            plain.add(name)
    plain.update(match.group(1) for match in _BARE.finditer(text))
    return {name: value for name, value in defaults.items() if name in plain}


def write_document(app_name: str, document: str, store: NoustStore, *, fs: Any = None) -> Path:
    """
    Put a pasted document where the deployment is made from.

    Args:
        app_name: The application name.
        document: The text, already analysed and not refused.
        store: The store, beside which the source is kept.
        fs: The filesystem to write through; the process-wide one by default.

    Returns:
        The directory holding ``docker-compose.yml``.
    """
    fs = fs or get_fs()
    directory = recipe_source_dir(app_name, store)
    if directory.exists():
        fs.remove_tree(directory)
    fs.make_dir(directory, parents=True)
    fs.write_text(
        directory / COMPOSE_FILE, document if document.endswith("\n") else document + "\n"
    )
    return directory
