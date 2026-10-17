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
- ``$NAME`` becomes ``${NAME}``, the one spelling Noust reads;
- Traefik and Coolify labels, the ``coolify`` network and ``exclude_from_hc`` are dropped: they
  mean nothing here, and each is reported in ``adjustments``;
- a service with no ``restart`` key gets ``unless-stopped``, which is what Coolify's own parser
  does and what the templates rely on (an application that starts before its database is set up
  exits, and has to be started again);
- the named volumes a service mounts are declared at the top level when the template did not
  (Coolify declares them itself; Compose refuses a project that refers to a volume nobody
  declared, and 340 of the 345 templates of the catalogue that use one leave the declaration out);
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
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

from noust.core.compose_yaml import load_compose
from noust.core.exceptions import ValidationError
from noust.deployers.compose_paste import plain_uses_with_a_default
from noust.deployers.helpers.compose_ports import parse_ports
from noust.recipes.variables import generate_value

#: The largest template accepted: the biggest in Coolify's catalogue is tens of KiB.
MAX_TEMPLATE_BYTES = 256 * 1024

#: The variable a Compose stack publishes its web port through (reserved in a recipe).
PORT_VARIABLE = "PORT"

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
_BARE_DOLLAR = re.compile(r"(?<!\$)\$([A-Za-z_][A-Za-z0-9_]*)")

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
class CoolifyImport:
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
    from noust.core.store import StoreError, get_store
    from noust.validators.port import find_available_port

    try:
        owned = get_store().ports_owned_by_apps()
    except StoreError:
        owned = set()  # no store yet (a rehearsal on a fresh machine): no application owns any
    return find_available_port(preferred=3000, exclude=owned)


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
    _declare_volumes(document, result)
    _restart_policy(document, result)
    _normalise(document)
    _publish(document["services"], routing, port, result)
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
    _shared_defaults(body, set(magic), result)
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


#: What a named volume is called: Docker's own rule for a volume name.
_VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _named_volume(entry: Any) -> str | None:
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


def _declare_volumes(document: dict[str, Any], result: CoolifyImport) -> None:
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
            name = _named_volume(entry)
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


def _restart_policy(document: dict[str, Any], result: CoolifyImport) -> None:
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


def _normalise(node: Any) -> None:
    """
    Spell every ``$NAME`` as ``${NAME}``, in place.

    Args:
        node: A part of the document.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            node[key] = _normalise_value(value)
            _normalise(node[key])
    elif isinstance(node, list):
        for index, value in enumerate(node):
            node[index] = _normalise_value(value)
            _normalise(node[index])


def _normalise_value(value: Any) -> Any:
    """
    Args:
        value: A scalar of the document.

    Returns:
        The same, with a bare ``$NAME`` written ``${NAME}``.
    """
    if isinstance(value, str) and "$" in value:
        return _BARE_DOLLAR.sub(lambda m: "${" + m.group(1) + "}", value)
    return value


# -- the published port ------------------------------------------------------------------


def _publish(
    services: dict[str, Any],
    routing: dict[str, tuple[str, int | None]],
    port: int | None,
    result: CoolifyImport,
) -> None:
    """
    Publish the web service's port on the loopback address.

    Which service answers the domain is what the template declared (``SERVICE_URL_<ID>_<PORT>``);
    without a declaration it is guessed, in words: the first service that asks for the stack's
    address, or the first that is not a database or a cache, on the port the header names.

    Args:
        services: The services.
        routing: The services that declared a route.
        port: The host port asked for.
        result: Where the route and adjustments go.
    """
    declared = {name: value[1] for name, value in routing.items() if value[1] is not None}
    if not declared:
        guessed = _guess(services, result)
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


#: A value an ``.env`` line carries as it is (``values_file`` refuses what it would have to quote).
_PLAIN_VALUE = re.compile(r"[^\s\"'\\$`]+")


def _shared_defaults(body: str, magic: set[str], result: CoolifyImport) -> None:
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
