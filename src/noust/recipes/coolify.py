# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Coolify service template, turned into a Compose document Noust deploys.

Coolify's catalogue of one-click services is a directory of Docker Compose files with a few
conventions of their own: a header of ``# documentation:``/``# slogan:``/``# tags:``/``# port:``
comments, environment variables named ``SERVICE_URL_<ID>``, ``SERVICE_FQDN_<ID>``,
``SERVICE_USER_<ID>``, ``SERVICE_PASSWORD_<ID>`` and a few more that Coolify fills in (the
public address, a random user, a random password), a bare ``SERVICE_URL_<ID>_<PORT>`` entry that
says which service the proxy sends the domain to, and labels and networks for Traefik. Everything
else is Compose. This module reads one of those files and writes the Compose document that is
left once the conventions are Noust's:

- the variables keep their names, so the converted file reads like the original and a template
  updated upstream can be converted again; their **values** are made here (``values``), from the
  same generators the recipes use (:func:`noust.recipes.variables.generate_value`), and the
  addresses from the domain the operator names;
- the bare routing entry becomes a published port on the loopback address, which is how Noust's
  web server reaches a stack (``127.0.0.1:${PORT:-8080}:8080``), and goes away as an entry;
- Traefik and Coolify labels, the ``coolify`` network and ``exclude_from_hc`` are dropped: they
  mean nothing here, and each is reported in ``adjustments``;
- what Compose needs and Coolify did for the template (a restart policy, the declaration of the
  named volumes a service mounts, ``${NAME}`` for ``$NAME``) is done by
  :mod:`noust.recipes.compose_template`, which Dokploy's converter uses too (340 of the 345
  templates of the catalogue that mount a volume leave its declaration out);
- what cannot be translated is said in ``warnings`` and never guessed (a file mounted from
  inline ``content``, a magic variable this module does not know).

Nothing is run, fetched or written: the text comes in and the text goes out, and the result goes
through the same analysis as any pasted document (:func:`noust.deployers.compose_paste.analyze_document`),
so a template that mounts the Docker socket is refused as any Compose file is.

Licence. Coolify and its templates are Apache-2.0 (https://github.com/coollabsio/coolify); a
template's own authors keep their rights in it. Noust redistributes none: the operator brings the
file, the converted document says where it came from, and the logos the templates name are not
fetched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from noust.core.compose_yaml import load_compose
from noust.core.exceptions import ValidationError
from noust.recipes.compose_template import (
    PORT_VARIABLE,
    ConvertedTemplate,
    ConvertedVariable,
    declare_volumes,
    default_host_port,
    normalise,
    publish,
    restart_policy,
    shared_defaults,
    values_file,
)
from noust.recipes.variables import generate_value

__all__ = [
    "MAX_TEMPLATE_BYTES",
    "PORT_VARIABLE",
    "ConvertedVariable",
    "CoolifyImport",
    "Magic",
    "convert",
    "default_host_port",
    "read_header",
    "read_magic",
    "values_file",
]

#: The largest template accepted: the biggest in Coolify's catalogue is tens of KiB.
MAX_TEMPLATE_BYTES = 256 * 1024

#: Magic variables and what makes their value: ``(prefix, generator)``, the longest first so that
#: ``PASSWORD_64`` is not read as ``PASSWORD`` with an id of ``64_...``.
_GENERATED: tuple[tuple[str, str], ...] = (
    ("PASSWORDWITHSYMBOLS", "password32"),
    ("PASSWORD_64", "password64"),
    ("PASSWORD", "password32"),
    ("REALBASE64_64", "base64_64"),
    ("REALBASE64", "base64_32"),
    ("BASE64_128", "base64_64"),
    ("BASE64_64", "base64_64"),
    ("BASE64_32", "base64_32"),
    ("BASE64", "base64_32"),
    ("HEX_64", "hex64"),
    ("HEX_32", "hex32"),
    ("HEX", "hex32"),
    ("USER", "user"),
)

_ADDRESSES = ("url", "fqdn")
_JWT = {"SUPABASEANON": "anon", "SUPABASESERVICE": "service_role"}

#: A magic variable the document *reads*: ``${SERVICE_...}``. Matching the bare words would also
#: catch ``TERMS_OF_SERVICE_URL`` and an application's own ``SERVICE_URL=`` key.
_MAGIC = re.compile(r"\$\{(SERVICE_[A-Z0-9_]+)")

_TRAILING_PORT = re.compile(r"(?P<id>.+)_(?P<port>\d{2,5})")
_HEADER = re.compile(r"#\s*(documentation|slogan|tags|logo|port|env_file|ignore)\s*:\s*(.*)", re.I)

#: Labels that only mean something to Coolify's proxy.
_PROXY_LABEL = re.compile(r"^(traefik\.|coolify\.|caddy)", re.I)


@dataclass(frozen=True)
class Magic:
    """
    A Coolify magic variable, read from its name.

    Attributes:
        name: The variable as the template writes it.
        kind: ``generated``, ``url``, ``fqdn`` or ``jwt``.
        generator: For ``generated``: the generator that makes its value.
        id: The service identifier in the name (``GHOST``).
        port: For an address with a port: the container port the proxy is told to reach.
        role: For ``jwt``: the claim.
    """

    name: str
    kind: str
    generator: str = ""
    id: str = ""
    port: int | None = None
    role: str = ""


def read_magic(name: str) -> Magic | None:
    """
    Read a variable name as a Coolify magic variable.

    Args:
        name: A variable name.

    Returns:
        What it is, or None when it is not one this module knows.
    """
    if not name.startswith("SERVICE_"):
        return None
    rest = name.removeprefix("SERVICE_")
    for kind in _ADDRESSES:
        prefix = kind.upper()
        if rest.startswith(prefix + "_") and len(rest) > len(prefix) + 1:
            tail = rest[len(prefix) + 1 :]
            matched = _TRAILING_PORT.fullmatch(tail)
            port = int(matched["port"]) if matched and 1 <= int(matched["port"]) <= 65535 else None
            ident = matched["id"] if matched and port is not None else tail
            return Magic(name, kind, id=ident, port=port)
    for prefix, role in _JWT.items():
        if rest.startswith(prefix + "_") and len(rest) > len(prefix) + 1:
            return Magic(name, "jwt", id=rest[len(prefix) + 1 :], role=role)
    for prefix, generator in _GENERATED:
        if rest.startswith(prefix + "_") and len(rest) > len(prefix) + 1:
            return Magic(name, "generated", generator=generator, id=rest[len(prefix) + 1 :])
    return None


class CoolifyImport(ConvertedTemplate):
    """What converting a Coolify template made (see :class:`ConvertedTemplate`)."""


def read_header(text: str) -> dict[str, str]:
    """
    Read the comment header of a template.

    Args:
        text: The template.

    Returns:
        ``documentation``, ``slogan``, ``tags``, ``logo`` and ``port``, those the header has.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            if stripped:
                break
            continue
        matched = _HEADER.fullmatch(stripped)
        if matched:
            found[matched.group(1).lower()] = matched.group(2).strip()[:300]
    return found


def convert(text: str, *, domain: str | None = None, port: int | None = None) -> CoolifyImport:
    """
    Convert a Coolify template to a Noust Compose document.

    Args:
        text: The template.
        domain: The name the stack is served on; without one the addresses are left for the
            operator to give.
        port: The host port the web service is published on (``127.0.0.1``); the container's own
            port by default (:func:`default_host_port` is what a command or an endpoint passes
            instead, because that one is rarely free).

    Returns:
        The document, the values made, and what was changed or left.

    Raises:
        ValidationError: The text is too large, is not YAML, or is not a Compose document with
            services.
    """
    if len(text.encode("utf-8", "replace")) > MAX_TEMPLATE_BYTES:
        raise ValidationError(
            "That template is far too large", field="document", details="A template is a few KiB."
        )
    try:
        document = load_compose(text)
    except yaml.YAMLError as exc:
        raise ValidationError(
            "That template is not YAML", details=str(exc)[:300], field="document"
        ) from exc
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise ValidationError(
            "That is not a Compose template: it has no services",
            details="A Coolify template is a Compose file: services: at the top.",
            field="document",
        )

    result = CoolifyImport(document="", metadata=read_header(text))
    routing = _strip_declarations(document["services"], result)
    _clean(document, result)
    declare_volumes(document, result)
    restart_policy(document, result)
    normalise(document)
    publish(document["services"], routing, port, result, _guess)
    body = yaml.safe_dump(
        document, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True
    )
    # What the converted document reads is what needs a value: a routing entry that was only
    # a declaration is gone, and a name the template never used does not matter.
    read = list(dict.fromkeys(_MAGIC.findall(body)))
    magic = {name: item for name in read if (item := read_magic(name)) is not None}
    for name in read:
        if name not in magic:
            result.warnings.append(
                f"{name} is named like a Coolify variable but is not one Noust fills in: give it"
            )
    _values(magic, domain, result)
    shared_defaults(body, set(magic), result)
    result.document = _header(result) + body
    return result


# -- reading ------------------------------------------------------------------------------


def _declaration(entry: Any) -> str | None:
    """
    Args:
        entry: An item of a service's ``environment``.

    Returns:
        The variable name when the item only declares it (a bare name, or one with no value),
        else None.
    """
    if isinstance(entry, str):
        return entry.strip() if "=" not in entry else None
    return None


#: Images of what a stack keeps beside the application: never the service the domain goes to.
_BACKING = re.compile(
    r"(^|/)(postgres|postgis|mysql|mariadb|mongo|redis|valkey|keydb|dragonfly|clickhouse|"
    r"memcached|rabbitmq|nats|minio|elasticsearch|opensearch|meilisearch|typesense|"
    r"cassandra|couchdb|influxdb|timescaledb|zookeeper|kafka|mailpit|mailhog)\b",
    re.I,
)


def _strip_declarations(
    services: dict[str, Any], result: CoolifyImport
) -> dict[str, tuple[str, int | None]]:
    """
    Take the bare ``SERVICE_URL_X_PORT`` declarations out of the environments.

    The declaration is how a template tells Coolify's proxy which service answers the domain and
    on which port; the value reaches the container as a variable of the same name without the
    port. Here the routing is the published port, and the base variables stay in the environment
    so the application keeps reading them.

    Args:
        services: The services.
        result: Where adjustments go.

    Returns:
        ``{service: (id, port)}`` for the services that declared a route.
    """
    routing: dict[str, tuple[str, int | None]] = {}
    result.addressed = []
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        environment = spec.get("environment")
        if isinstance(environment, list):
            kept: list[Any] = []
            for entry in environment:
                declared = _declaration(entry)
                magic = read_magic(declared) if declared else None
                if magic is None or magic.kind not in _ADDRESSES:
                    kept.append(entry)
                    continue
                if str(name) not in result.addressed:
                    result.addressed.append(str(name))
                if magic.port is not None:
                    routing.setdefault(str(name), (magic.id, magic.port))
                base = f"SERVICE_{magic.kind.upper()}_{magic.id}"
                line = f"{base}=${{{base}}}"
                if line not in kept:
                    kept.append(line)
            spec["environment"] = kept
        elif isinstance(environment, dict):
            for key in [k for k, v in environment.items() if v is None]:
                magic = read_magic(str(key))
                if magic is None or magic.kind not in _ADDRESSES:
                    continue
                del environment[key]
                if str(name) not in result.addressed:
                    result.addressed.append(str(name))
                if magic.port is not None:
                    routing.setdefault(str(name), (magic.id, magic.port))
                base = f"SERVICE_{magic.kind.upper()}_{magic.id}"
                environment.setdefault(base, f"${{{base}}}")
    if routing:
        result.adjustments.append(
            "The routing entries (SERVICE_URL_<ID>_<PORT>) became a published port on 127.0.0.1"
        )
    return routing


def _clean(document: dict[str, Any], result: CoolifyImport) -> None:
    """
    Drop what only Coolify's proxy reads.

    Args:
        document: The template, changed in place.
        result: Where adjustments go.
    """
    dropped_labels = 0
    for spec in document["services"].values():
        if not isinstance(spec, dict):
            continue
        labels = spec.get("labels")
        if isinstance(labels, list):
            kept = [e for e in labels if not _PROXY_LABEL.match(str(e).split("=", 1)[0])]
            dropped_labels += len(labels) - len(kept)
            spec["labels"] = kept
        elif isinstance(labels, dict):
            kept_dict = {k: v for k, v in labels.items() if not _PROXY_LABEL.match(str(k))}
            dropped_labels += len(labels) - len(kept_dict)
            spec["labels"] = kept_dict
        if not spec.get("labels"):
            spec.pop("labels", None)
        if spec.pop("exclude_from_hc", None) is not None:
            result.adjustments.append("exclude_from_hc is Coolify's and was dropped")
        networks = spec.get("networks")
        if isinstance(networks, list) and "coolify" in networks:
            spec["networks"] = [n for n in networks if n != "coolify"]
            result.adjustments.append("The coolify network is Coolify's and was dropped")
        elif isinstance(networks, dict) and "coolify" in networks:
            del networks["coolify"]
            result.adjustments.append("The coolify network is Coolify's and was dropped")
        if not spec.get("networks"):
            spec.pop("networks", None)
        for volume in spec.get("volumes") or []:
            if isinstance(volume, dict) and "content" in volume:
                result.warnings.append(
                    f"A volume of {volume.get('target', 'a service')} carries inline file content "
                    "(a Coolify extension); put the file next to the document and mount it"
                )
    if dropped_labels:
        result.adjustments.append(
            f"{dropped_labels} Traefik/Coolify label(s) were dropped: the web server routes here"
        )
    networks = document.get("networks")
    if isinstance(networks, dict) and "coolify" in networks:
        del networks["coolify"]
    if not document.get("networks"):
        document.pop("networks", None)


# -- the published port ------------------------------------------------------------------


def _guess(services: dict[str, Any], result: CoolifyImport) -> tuple[str, int] | None:
    """
    Guess the service that serves the web, when the template does not say.

    Args:
        services: The services.
        result: What conversion found so far (the header, the services that ask for an address).

    Returns:
        The service and its port, or None when the header names no port or no service is fit.
    """
    header = result.metadata.get("port", "")
    if not header.isdigit() or not 1 <= int(header) <= 65535:
        return None
    candidates = [name for name in result.addressed if name in services] or [
        name
        for name, spec in services.items()
        if isinstance(spec, dict) and "image" in spec and not _BACKING.search(str(spec["image"]))
    ]
    if not candidates:
        return None
    result.warnings.append(
        f"The template names no routing entry; {candidates[0]} was taken as the web service on "
        f"port {header} because of its header"
    )
    return candidates[0], int(header)


# -- the values -------------------------------------------------------------------------------


def _values(magic: dict[str, Magic], domain: str | None, result: CoolifyImport) -> None:
    """
    Make the value of every magic variable the document reads.

    Args:
        magic: The magic variables the converted document reads.
        domain: The name the stack is served on, when given.
        result: Where values and variables go.
    """
    for name, item in magic.items():
        if item.kind == "generated":
            result.values[name] = generate_value(item.generator)
            result.variables.append(
                ConvertedVariable(name, "generated", secret=item.generator != "user")
            )
    signing = next(
        (
            n
            for n, m in magic.items()
            if m.kind == "generated" and m.id == "JWT" and n in result.values
        ),
        None,
    )
    for name, item in magic.items():
        if item.kind in _ADDRESSES:
            if domain is None:
                result.variables.append(
                    ConvertedVariable(
                        name,
                        "ask",
                        note=f"The address of the stack: give the domain, or set {name}",
                    )
                )
                continue
            result.values[name] = f"https://{domain}" if item.kind == "url" else domain
            result.variables.append(ConvertedVariable(name, "domain"))
        elif item.kind == "jwt":
            if signing is None:
                result.variables.append(
                    ConvertedVariable(
                        name,
                        "ask",
                        secret=True,
                        note="A token signed with the stack's JWT secret, which this template "
                        "does not define: give it",
                    )
                )
                continue
            result.values[name] = generate_value(
                "jwt", signing_secret=result.values[signing], role=item.role
            )
            result.variables.append(ConvertedVariable(name, "generated", secret=True))


def _header(result: CoolifyImport) -> str:
    """
    Args:
        result: What conversion found.

    Returns:
        The comment that says where the document came from, ending in a blank line.
    """
    lines = [
        "# Converted by Noust from a Coolify service template.",
        "# Coolify and its templates are Apache-2.0 (https://github.com/coollabsio/coolify);",
        "# the template's own authors keep their rights in it.",
    ]
    for key in ("documentation", "slogan", "tags"):
        if result.metadata.get(key):
            lines.append(f"# {key}: {result.metadata[key]}")
    return "\n".join(lines) + "\n\n"
