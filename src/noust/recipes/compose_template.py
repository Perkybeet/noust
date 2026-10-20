# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the converters of another platform's service templates have in common.

Coolify's catalogue (:mod:`noust.recipes.coolify`) and Dokploy's
(:mod:`noust.recipes.dokploy`) are both directories of Docker Compose files with
conventions of their own on top, and both are turned into the one Compose document
Noust deploys. The conventions differ (Coolify writes magic variable names, Dokploy a
``template.toml``); what is done to the Compose underneath does not, so it is done here,
once (rule 3):

- the named volumes a service mounts are declared at the top level when the template left
  them out, because Compose refuses a project that mounts one nobody declared;
- a service with no ``restart`` key gets ``unless-stopped``, which is what both platforms
  give it and what their templates rely on (an application that starts before its database
  is ready exits, and has to be started again);
- ``$NAME`` is spelled ``${NAME}``, the one spelling Noust reads;
- the web service's port is published on the loopback address, which is how Noust's web
  server reaches a stack (``127.0.0.1:${PORT:-8080}:8080``);
- a default one service gives a variable is the value another service that reads it plainly
  gets, as both platforms' variables work.

Nothing is run, fetched or written: text comes in and text goes out.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import ValidationError
from noust.deployers.compose_paste import plain_uses_with_a_default
from noust.deployers.helpers.compose_ports import parse_ports

#: The variable a Compose stack publishes its web port through (reserved in a recipe).
PORT_VARIABLE = "PORT"

_BARE_DOLLAR = re.compile(r"(?<!\$)\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class ConvertedVariable:
    """
    A variable the converted document reads.

    Attributes:
        name: The variable.
        origin: Where its value comes from: ``generated`` (made here), ``domain`` (the address
            of the domain), ``port``, ``template`` (the template gave a default) or ``ask``
            (the operator must give it).
        secret: The value must not be shown or logged in clear.
        note: A sentence for the operator, empty when there is nothing to add.
    """

    name: str
    origin: str
    secret: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready (never the value).
        """
        return {"name": self.name, "origin": self.origin, "secret": self.secret, "note": self.note}


@dataclass
class ConvertedTemplate:
    """
    What converting a template made.

    Attributes:
        document: The Compose document.
        values: The values of the variables made here, by name. Secrets among them: the caller
            keeps them out of logs and writes them with the file's mode.
        variables: Every variable the document reads that the template defined, how each gets
            its value.
        routed: ``(service, container port)`` the domain is sent to; None when the template
            names no web service.
        addressed: The services that ask for the stack's address, in the template's order.
        metadata: The template's header (``documentation``, ``slogan``, ``tags``...).
        adjustments: What was changed to make it a Noust document, one sentence each.
        warnings: What was not translated and why.
    """

    document: str
    values: dict[str, str] = field(default_factory=dict)
    variables: list[ConvertedVariable] = field(default_factory=list)
    routed: tuple[str, int] | None = None
    addressed: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    adjustments: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def default_host_port() -> int | None:
    """
    The port a converted stack is published on when the operator names none.

    The container's own port is the wrong default: 80 and 443 are the web server's, and 3000 or
    8080 are as likely to be another application's. This is the port ``noust create`` gives an
    application next (the first free one from 3000 that no application owns), so the number the
    document is converted with is the number its site will proxy to in the usual order of things.

    Returns:
        The port, or None when none is free (the container's own is used then).
    """
    from noust.core.store import StoreError
    from noust.managers.port_claims import PortClaims

    try:
        return PortClaims().choose(preferred=3000)
    except StoreError:
        # no store yet (a rehearsal on a fresh machine): no application owns any
        return 3000


#: What a named volume is called: Docker's own rule for a volume name.
_VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def named_volume(entry: Any) -> str | None:
    """
    Args:
        entry: One item of a service's ``volumes``, in either syntax.

    Returns:
        The name when it mounts a named volume; None for a bind mount (a path), an anonymous
        volume, a tmpfs and a source that is a variable.
    """
    if isinstance(entry, str):
        source, separator, _ = entry.partition(":")
        return source if separator and _VOLUME_NAME.fullmatch(source) else None
    if isinstance(entry, dict) and entry.get("type", "volume") == "volume":
        named = entry.get("source")
        return named if isinstance(named, str) and _VOLUME_NAME.fullmatch(named) else None
    return None


def declare_volumes(document: dict[str, Any], result: ConvertedTemplate) -> None:
    """
    Declare at the top level the named volumes the services mount and the template did not.

    Args:
        document: The template, changed in place.
        result: Where adjustments go.
    """
    declared = document.get("volumes")
    declared = declared if isinstance(declared, dict) else {}
    used: dict[str, None] = {}
    for spec in document["services"].values():
        if not isinstance(spec, dict):
            continue
        for entry in spec.get("volumes") or []:
            name = named_volume(entry)
            if name is not None:
                used.setdefault(name)
    missing = [name for name in used if name not in declared]
    if not missing:
        return
    document["volumes"] = {**declared, **{name: {} for name in missing}}
    result.adjustments.append(
        f"declared {len(missing)} named volume(s) the template mounts without declaring: "
        f"{', '.join(missing)} (Coolify declares them itself, Compose does not)"
    )


#: What Coolify gives a service that does not say (``RESTART_MODE`` in its helpers).
RESTART_DEFAULT = "unless-stopped"


def restart_policy(document: dict[str, Any], result: ConvertedTemplate) -> None:
    """
    Give the services that do not say how they restart what Coolify gives them.

    Args:
        document: The template, changed in place.
        result: Where adjustments go.
    """
    given = 0
    for spec in document["services"].values():
        if isinstance(spec, dict) and "restart" not in spec:
            spec["restart"] = RESTART_DEFAULT
            given += 1
    if given:
        result.adjustments.append(
            f"restart: {RESTART_DEFAULT} was given to {given} service(s) that did not say "
            "(Coolify does the same, and the templates rely on it)"
        )


def normalise(node: Any) -> None:
    """
    Spell every ``$NAME`` as ``${NAME}``, in place.

    Args:
        node: A part of the document.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            node[key] = normalise_value(value)
            normalise(node[key])
    elif isinstance(node, list):
        for index, value in enumerate(node):
            node[index] = normalise_value(value)
            normalise(node[index])


def normalise_value(value: Any) -> Any:
    """
    Args:
        value: A scalar of the document.

    Returns:
        The same, with a bare ``$NAME`` written ``${NAME}``.
    """
    if isinstance(value, str) and "$" in value:
        return _BARE_DOLLAR.sub(lambda m: "${" + m.group(1) + "}", value)
    return value


def publish(
    services: dict[str, Any],
    routing: dict[str, tuple[str, int | None]],
    port: int | None,
    result: ConvertedTemplate,
    guess: Callable[[dict[str, Any], ConvertedTemplate], tuple[str, int] | None] | None = None,
) -> None:
    """
    Publish the web service's port on the loopback address.

    Which service answers the domain is what the template declared (Coolify's
    ``SERVICE_URL_<ID>_<PORT>``, Dokploy's ``[[config.domains]]``); without a declaration
    ``guess`` may name one, and the conversion says that it was a guess.

    Args:
        services: The services.
        routing: The services that declared a route.
        port: The host port asked for.
        result: Where the route and adjustments go.
        guess: Names the web service when the template declared none; None for no guessing.
    """
    declared = {name: value[1] for name, value in routing.items() if value[1] is not None}
    if not declared:
        guessed = guess(services, result) if guess is not None else None
        if guessed is None:
            result.warnings.append(
                "The template names no web service, so nothing is published: it deploys as a "
                "worker (add a ports: entry to serve it)"
            )
            return
        declared = {guessed[0]: guessed[1]}
    ordered = [name for name in services if name in declared]
    if len(ordered) > 1:
        result.warnings.append(
            f"The template routes more than one service; {ordered[0]} gets the domain and "
            f"{', '.join(ordered[1:])} stays inside the stack (route more with noust.nginx.yaml)"
        )
    service = ordered[0]
    container_port = declared[service]
    spec = services[service]
    existing = spec.get("ports") or []
    if any(m.container_port == container_port for m in parse_ports(existing)):
        result.warnings.append(
            f"{service} already publishes port {container_port}; it was left as it is"
        )
    else:
        entry = f"127.0.0.1:${{{PORT_VARIABLE}:-{port or container_port}}}:{container_port}"
        spec["ports"] = [*existing, entry]
        result.adjustments.append(f"{service} is published on {entry} for the web server")
    result.routed = (service, container_port)


#: A value an ``.env`` line carries as it is (``values_file`` refuses what it would have to quote).
_PLAIN_VALUE = re.compile(r"[^\s\"'\\$`]+")


def shared_defaults(body: str, magic: set[str], result: ConvertedTemplate) -> None:
    """
    Give a variable the default another service declares for it.

    Coolify makes a variable's value from the default a template gives it anywhere
    (``${POSTGRES_DB:-app}``), so the service that reads it plainly (``$POSTGRES_DB`` in a
    connection string) sees that value too; Compose would give it a blank.

    Args:
        body: The converted document.
        magic: The magic variables, whose values are made or asked for already.
        result: Where the values and the variables go.
    """
    for name, default in plain_uses_with_a_default(body).items():
        if name in magic or name in result.values or name == PORT_VARIABLE:
            continue
        if _PLAIN_VALUE.fullmatch(default):
            result.values[name] = default
            result.variables.append(
                ConvertedVariable(
                    name,
                    "template",
                    note=f"The template gives it the default {default}, which another service "
                    "reads with none",
                )
            )
        else:
            result.variables.append(
                ConvertedVariable(
                    name,
                    "ask",
                    note=f"One service reads it with no default and another gives it "
                    f"{default!r}: write the value in .env",
                )
            )
            result.warnings.append(
                f"{name} has the default {default!r} in one service and none in another; "
                "give it a value in .env"
            )


def values_file(values: Mapping[str, str]) -> str:
    """
    Args:
        values: The values made by :func:`convert`.

    Returns:
        A Compose ``.env`` file holding them, one ``NAME=value`` per line. A value is a hex or
        base64 string, a name or a URL, so none needs quoting; one that does is refused.

    Raises:
        ValidationError: A value has a character an ``.env`` line cannot carry.
    """
    lines = []
    for name, value in values.items():
        if any(character in value for character in "\n\r\"'\\$` "):
            raise ValidationError(f"The value of {name} cannot be written to an .env file")
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


#: Environment variable names Compose reads.
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: What makes a bare value in a Compose ``.env`` read as something else than its text: an
#: interpolation, an escape, a comment, a quote.
_NEEDS_QUOTES = re.compile(r"[$\\#\"`]")


def env_file(values: Mapping[str, str]) -> str:
    """
    Write a Compose ``.env`` for values somebody else's template chose.

    :func:`values_file` is for the values this module makes itself (hex, base64 and names) and
    refuses anything that would need quoting. A template's own values are text: a sentence with
    spaces, a price with a dollar sign. They are written bare when that reads back as they are,
    and in single quotes (literal to Compose, to the shell and to systemd alike) when it would
    not.

    Args:
        values: The variables.

    Returns:
        One ``NAME=value`` per line.

    Raises:
        ValidationError: A name Compose does not read, a value of several lines, or one with a
            single quote and a ``$`` or a backslash, which no quoting writes for every reader.
    """
    lines = []
    for name, value in values.items():
        if not _ENV_NAME.fullmatch(name):
            raise ValidationError(f"{name!r} is not a variable name an .env file holds")
        if any(character in value for character in "\n\r\x00"):
            raise ValidationError(
                f"The value of {name} has several lines",
                details="An .env value is one line: put the text in a file and mount it.",
            )
        needs = bool(_NEEDS_QUOTES.search(value)) or value != value.strip() or value[:1] in "'"
        if needs and "'" in value:
            raise ValidationError(
                f"The value of {name} cannot be written to an .env file",
                details="It holds a single quote and a character that needs the value quoted.",
            )
        lines.append(f"{name}='{value}'" if needs else f"{name}={value}")
    return "\n".join(lines) + "\n"
