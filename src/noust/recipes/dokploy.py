# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Dokploy template, turned into a Compose document Noust deploys (checklist D-26).

Dokploy's catalogue of one-click applications is a directory per application with two files: a
``docker-compose.yml`` and a ``template.toml`` that says what Dokploy fills in
(https://github.com/Dokploy/templates, MIT licence). The Compose file is nearly plain Compose;
the TOML holds the rest:

- ``[variables]``: named values, written with expressions Dokploy expands when it installs the
  template (``${domain}``, ``${password:32}``, ``${hash:8}``, ``${base64:32}``, ``${uuid}``,
  ``${jwt:secret:payload}``...);
- ``[config].env``: the lines of the ``.env`` the Compose file reads (``KEY=${variable}``);
- ``[[config.domains]]``: which service answers the domain, on which port;
- ``[[config.mounts]]``: files written beside the stack (``../files/...``) and mounted by the
  Compose file.

This module expands the expressions the way Dokploy's own processor does
(``packages/server/src/templates/processors.ts``) with Noust's generators, and writes what is left
once the conventions are Noust's: the Compose document, the values for its ``.env``, and the files:

- the domain's service is published on the loopback address, which is how Noust's web server
  reaches a stack (``127.0.0.1:${PORT:-8080}:8080``); more than one routed service, a path and an
  address other than the main one are said, never guessed;
- ``../files/x`` becomes ``./files/x``, and the files are returned for the caller to write beside
  the document (a path that leaves that folder is refused: a template is untrusted input);
- Dokploy's network and Traefik labels are dropped, and each is reported in ``adjustments``;
- the restart policy, the declaration of the named volumes and the spelling of ``${NAME}`` are
  what :mod:`noust.recipes.compose_template` does for Coolify's templates too.

Nothing is run, fetched or written: text comes in and text goes out, and the result goes through
the same analysis as any pasted document (:func:`noust.deployers.compose_paste.analyze_document`),
so a template that mounts the Docker socket is refused as any Compose file is.

Licence. The templates are MIT (Copyright 2024 Dokploy and Carlos Ortiz,
https://github.com/Dokploy/templates); Dokploy's own code is Apache-2.0 and its expansion rules
are read from it, not copied. A template's own authors keep their rights in it. Noust redistributes
none: the operator brings the files, the converted document says where it came from, and the logos
the templates name are not fetched.
"""

from __future__ import annotations

import json
import re
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

import yaml

from noust.core.compose_yaml import load_compose
from noust.core.exceptions import ValidationError
from noust.core.secret_detection import name_looks_secret
from noust.deployers.importers.toml_fallback import load_toml_fallback
from noust.recipes.compose_template import (
    ConvertedTemplate,
    ConvertedVariable,
    declare_volumes,
    default_host_port,
    env_file,
    normalise,
    publish,
    restart_policy,
    shared_defaults,
)
from noust.recipes.variables import (
    generate_value,
    random_alphanumeric,
    random_base64,
    random_hex_digits,
    signed_jwt,
)

__all__ = [
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TEMPLATE_BYTES",
    "MAX_TOML_BYTES",
    "DokployImport",
    "convert",
    "default_host_port",
    "env_file",
]

#: The largest compose file accepted: the biggest in Dokploy's catalogue is 86 KiB.
MAX_TEMPLATE_BYTES = 256 * 1024

#: The largest template.toml accepted: it carries the content of the files it mounts, and the
#: biggest in Dokploy's catalogue (ChirpStack's configuration) is 283 KiB.
MAX_TOML_BYTES = 1024 * 1024

#: How many files a template may mount, and how large each may be: the biggest templates mount a
#: handful of configuration files.
MAX_FILES = 64
MAX_FILE_BYTES = 256 * 1024

#: A Dokploy expression: ``${name}`` or ``${function:argument}``.
_EXPRESSION = re.compile(r"\$\{([^}]+)\}")

#: Environment variable names Compose reads.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Labels that only mean something to Dokploy's proxy.
_PROXY_LABEL = re.compile(r"^(traefik\.|dokploy\.)", re.I)

#: Where Dokploy keeps the files a template mounts, as the Compose file spells them.
_FILES_PREFIX = re.compile(r"(?<![\w./-])\.\./files/")

_LICENCE_LINES = (
    "# Converted by Noust from a Dokploy template.",
    "# Dokploy's templates are MIT (https://github.com/Dokploy/templates);",
    "# the template's own authors keep their rights in it.",
)

#: The functions whose value is a secret.
_SECRET_FUNCTIONS = frozenset({"password", "base64", "hash", "jwt"})


@dataclass
class DokployImport(ConvertedTemplate):
    """
    What converting a Dokploy template made.

    Attributes:
        files: The files the template mounts, by path relative to the folder the document is
            written in (``files/nginx/nginx.conf``), with their content.
        secret_files: The paths among ``files`` that hold a generated secret: the caller writes
            them readable by their owner only, like the ``.env``.
    """

    files: dict[str, str] = field(default_factory=dict)
    secret_files: set[str] = field(default_factory=set)


def convert(
    compose_text: str,
    template_text: str | None = None,
    *,
    domain: str | None = None,
    port: int | None = None,
) -> DokployImport:
    """
    Convert a Dokploy template to a Noust Compose document.

    Args:
        compose_text: The template's ``docker-compose.yml``.
        template_text: Its ``template.toml``; None converts the Compose file alone (nothing is
            filled in, no service is routed, and it is said).
        domain: The name the stack is served on.
        port: The host port the web service is published on (``127.0.0.1``); the container's own
            port by default (:func:`default_host_port` is what a command passes instead, because
            that one is rarely free).

    Returns:
        The document, the values made, the files, and what was changed or left.

    Raises:
        ValidationError: A file is too large or not what it should be, a mounted file's path
            leaves its folder, or the template builds addresses and no domain was given.
    """
    document = _load_compose(compose_text)
    toml = _load_template(template_text)
    result = DokployImport(document="", metadata=_metadata(toml))
    raw_config = toml.get("config")
    config: Mapping[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    if domain is None and "${domain}" in (template_text or ""):
        raise ValidationError(
            "This template builds its addresses from the domain",
            details="Give the name the stack will be served on (noust compose import --domain "
            "app.example.com).",
            field="domain",
        )

    expander = _Expander(_variables(toml), domain, result)
    expander.resolve_variables()

    _drop_dokploy(document, result)
    routing = _routing(config, document["services"], expander, domain, result)
    result.files, result.secret_files = _files(config, expander)
    _point_at_files(document, result)
    if config.get("isolated"):
        result.warnings.append(
            "The template asks for an isolated deployment (a network of its own with a random "
            "suffix). A Compose project already has a network of its own here, so nothing is added."
        )

    _env(config, expander, result)
    declare_volumes(document, result)
    restart_policy(document, result)
    normalise(document)
    publish(document["services"], routing, port, result)

    body = yaml.safe_dump(
        document, sort_keys=False, default_flow_style=False, width=100, allow_unicode=True
    )
    shared_defaults(body, set(), result)
    result.document = "\n".join(_LICENCE_LINES) + _origin(result) + "\n\n" + body
    return result


# -- reading ------------------------------------------------------------------------------


def _load_compose(text: str) -> dict[str, Any]:
    """
    Args:
        text: A template's ``docker-compose.yml``.

    Returns:
        The document.

    Raises:
        ValidationError: Too large, not YAML, or without services.
    """
    if len(text.encode("utf-8", "replace")) > MAX_TEMPLATE_BYTES:
        raise ValidationError(
            "That Compose file is far too large",
            field="document",
            details="A template's Compose file is a few KiB.",
        )
    try:
        document = load_compose(text)
    except yaml.YAMLError as exc:
        raise ValidationError(
            "That Compose file is not YAML", details=str(exc)[:300], field="document"
        ) from exc
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise ValidationError(
            "That is not a Compose file: it has no services",
            details="A Dokploy template's docker-compose.yml has services: at the top.",
            field="document",
        )
    return document


def _load_template(text: str | None) -> dict[str, Any]:
    """
    Args:
        text: A template's ``template.toml``, or None.

    Returns:
        What it holds; empty for None.

    Raises:
        ValidationError: Too large, or not TOML.
    """
    if text is None:
        return {}
    if len(text.encode("utf-8", "replace")) > MAX_TOML_BYTES:
        raise ValidationError(
            "That template.toml is far too large",
            field="template",
            details=f"A template.toml is a few KiB; the limit is {MAX_TOML_BYTES // 1024} KiB.",
        )
    try:
        return _toml(text)
    except RecursionError as exc:
        raise ValidationError(
            "That template.toml nests too deeply to be read", field="template"
        ) from exc


def _toml(text: str) -> dict[str, Any]:
    """
    Args:
        text: TOML.

    Returns:
        The document, read by ``tomllib`` where it exists and by Noust's own reader on Python 3.10.

    Raises:
        ValidationError: The text is not TOML.
    """
    try:
        import tomllib
    except ModuleNotFoundError:
        return load_toml_fallback(text, name="template.toml")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(
            "That template.toml is not valid TOML", details=str(exc)[:300], field="template"
        ) from exc


def _metadata(toml: Mapping[str, Any]) -> dict[str, str]:
    """
    Args:
        toml: The template.

    Returns:
        The name, description and version the template carries, if any (Dokploy keeps them in a
        ``meta.json`` beside it; some templates repeat them here).
    """
    found: dict[str, str] = {}
    meta = toml.get("metadata")
    if isinstance(meta, dict):
        for key in ("name", "description", "version"):
            if isinstance(meta.get(key), str):
                found[key] = meta[key][:300]
    return found


def _origin(result: DokployImport) -> str:
    """
    Args:
        result: What conversion found.

    Returns:
        The comment lines that name the template, each starting with a newline.
    """
    return "".join(
        f"\n# {key}: {result.metadata[key]}"
        for key in ("name", "description", "version")
        if result.metadata.get(key)
    )


def _variables(toml: Mapping[str, Any]) -> dict[str, str]:
    """
    Args:
        toml: The template.

    Returns:
        ``[variables]`` as strings, in the order the template wrote them.
    """
    table = toml.get("variables")
    if not isinstance(table, dict):
        return {}
    return {str(key): value for key, value in table.items() if isinstance(value, str)}


# -- the expressions ---------------------------------------------------------------------------


class _Expander:
    """
    Expand Dokploy's expressions the way its processor does.

    Dokploy runs two passes over ``[variables]``: the ones that need no other variable first,
    then every one in the order the template wrote them, each seeing the values of those before
    it. A name that is not a function is a variable; one nobody defines stays as it is.
    """

    def __init__(self, raw: dict[str, str], domain: str | None, result: DokployImport) -> None:
        self.variables = dict(raw)
        self.domain = domain
        self.result = result
        #: What each variable's value was made from (the functions it used, directly or through
        #: the variables it reads), for the report of where each value of the ``.env`` came from.
        self.kinds: dict[str, frozenset[str]] = {}
        self.unknown: list[str] = []
        self._used: set[str] = set()

    def resolve_variables(self) -> None:
        """Make the value of every variable of the template."""
        for name, value in list(self.variables.items()):
            self.variables[name], self.kinds[name] = self.expand_with_kinds(value)

    def expand(self, text: str) -> str:
        """
        Expand every expression in a text.

        Args:
            text: A value of the template.

        Returns:
            The text with each expression replaced; one that names nothing known is left as it is.
        """
        return self.expand_with_kinds(text)[0]

    def expand_with_kinds(self, text: str) -> tuple[str, frozenset[str]]:
        """
        Expand every expression in a text and say what it was made from.

        Args:
            text: A value of the template.

        Returns:
            The expanded text, and the names of the functions (``password``, ``domain``...) that
            made it, directly or through the variables it reads.
        """
        self._used = set()
        text = _EXPRESSION.sub(lambda match: self._expression(match.group(1), match.group(0)), text)
        text = _EXPRESSION.sub(self._variable, text)
        return text, frozenset(self._used)

    def _variable(self, match: re.Match[str]) -> str:
        """
        Args:
            match: One ``${name}``.

        Returns:
            The variable's value, or the expression as written when nothing defines it.
        """
        name = match.group(1)
        if name in self.variables:
            self._used |= self.kinds.get(name, frozenset())
            return self.variables[name]
        return match.group(0)

    def _expression(self, body: str, whole: str) -> str:
        """
        Args:
            body: What is inside ``${...}``.
            whole: The expression as written, kept for an unknown name.

        Returns:
            Its value.
        """
        name, _, argument = body.partition(":")
        value = self._function(name, argument)
        if value is not None:
            self._used.add(name)
            return value
        if body not in self.variables:
            self.unknown.append(body)
        return whole

    def _function(self, name: str, argument: str) -> str | None:
        """
        Args:
            name: The function's name.
            argument: What follows the colon.

        Returns:
            Its value, or None when ``name`` is not a function Dokploy has.
        """
        if name == "domain" and not argument:
            return self.domain or ""
        if name == "password":
            return random_alphanumeric(_length(argument, 16), lowercase=True)
        if name == "base64":
            return random_base64(_length(argument, 32))
        if name == "hash":
            return random_hex_digits(_length(argument, 8))
        if name == "uuid" and not argument:
            return str(uuid.uuid4())
        if name in ("timestamp", "timestampms"):
            return str(_instant(argument, 1000))
        if name == "timestamps":
            return str(_instant(argument, 1))
        if name == "randomPort" and not argument:
            return str(secrets.randbelow(40001) + 20000)
        if name == "jwt":
            return self._jwt(argument)
        if name == "username" and not argument:
            return generate_value("user")
        if name == "email" and not argument:
            return f"admin@{self.domain or 'example.com'}"
        return None

    def _jwt(self, argument: str) -> str:
        """
        Make a token the way Dokploy does.

        Args:
            argument: Nothing (a token with a random secret), a count of random bytes to write in
                hexadecimal, or ``secret[:payload]``, each the name of a variable or the text
                itself; the payload is JSON.

        Returns:
            The token.
        """
        if not argument:
            return _token(secrets.token_hex(32), {})
        if re.fullmatch(r"\d{1,3}", argument):
            return secrets.token_hex(int(argument))
        parts = argument.split(":")
        secret = self.variables.get(parts[0]) or parts[0]
        payload = parts[1] if len(parts) > 1 else ""
        payload = self.variables.get(payload, payload)
        claims: dict[str, Any] = {}
        if payload.strip().startswith("{") and payload.strip().endswith("}"):
            try:
                loaded = json.loads(payload)
            except json.JSONDecodeError:
                self.result.warnings.append(
                    "A JWT payload in the template is not valid JSON: the token carries no claims"
                )
            else:
                if isinstance(loaded, dict):
                    claims = loaded
        return _token(secret, claims)


def _token(secret: str, claims: dict[str, Any]) -> str:
    """
    Args:
        secret: The signing secret.
        claims: The payload the template gave.

    Returns:
        An HS256 token. Dokploy fills ``iss`` (itself), ``iat`` and ``exp`` (2030-01-01) when the
        template does not.
    """
    full: dict[str, Any] = {
        "iat": int(time.time()),
        "exp": int(datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()),
        **claims,
    }
    full.setdefault("iss", "dokploy")
    return signed_jwt(secret, full)


def _length(argument: str, default: int) -> int:
    """
    Args:
        argument: What follows the colon of ``${function:N}``.
        default: The length when there is none or it is not a positive number.

    Returns:
        How many characters or bytes to draw, at most 4096.
    """
    return min(int(argument), 4096) if argument.isdigit() and int(argument) > 0 else default


def _instant(argument: str, per_second: int) -> int:
    """
    Args:
        argument: Empty for now, or an ISO date.
        per_second: 1 for seconds, 1000 for milliseconds.

    Returns:
        That instant as a whole number of those units; 0 for a date that is not one.
    """
    if not argument:
        return int(time.time() * per_second)
    try:
        moment = datetime.fromisoformat(argument.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * per_second)


# -- the document ---------------------------------------------------------------------------


def _drop_dokploy(document: dict[str, Any], result: DokployImport) -> None:
    """
    Drop what only Dokploy's proxy reads.

    Args:
        document: The template, changed in place.
        result: Where adjustments go.
    """
    dropped = 0
    for spec in document["services"].values():
        if not isinstance(spec, dict):
            continue
        labels = spec.get("labels")
        if isinstance(labels, list):
            kept = [e for e in labels if not _PROXY_LABEL.match(str(e).split("=", 1)[0])]
            dropped += len(labels) - len(kept)
            spec["labels"] = kept
        elif isinstance(labels, dict):
            kept_dict = {k: v for k, v in labels.items() if not _PROXY_LABEL.match(str(k))}
            dropped += len(labels) - len(kept_dict)
            spec["labels"] = kept_dict
        if not spec.get("labels"):
            spec.pop("labels", None)
        networks = spec.get("networks")
        if isinstance(networks, list) and "dokploy-network" in networks:
            spec["networks"] = [n for n in networks if n != "dokploy-network"]
            result.adjustments.append("The dokploy-network is Dokploy's and was dropped")
        elif isinstance(networks, dict) and "dokploy-network" in networks:
            del networks["dokploy-network"]
            result.adjustments.append("The dokploy-network is Dokploy's and was dropped")
        if not spec.get("networks"):
            spec.pop("networks", None)
    if dropped:
        result.adjustments.append(
            f"{dropped} Traefik/Dokploy label(s) were dropped: the web server routes here"
        )
    networks = document.get("networks")
    if isinstance(networks, dict) and "dokploy-network" in networks:
        del networks["dokploy-network"]
    if not document.get("networks"):
        document.pop("networks", None)


def _routing(
    config: Mapping[str, Any],
    services: Mapping[str, Any],
    expander: _Expander,
    domain: str | None,
    result: DokployImport,
) -> dict[str, tuple[str, int | None]]:
    """
    Read ``[[config.domains]]``: which service answers the domain.

    Args:
        config: The template's ``[config]`` table.
        services: The Compose file's services.
        expander: Expands the host.
        domain: The name the stack is served on.
        result: Where warnings go.

    Returns:
        ``{service: (id, container port)}`` for the service that gets the domain.
    """
    routing: dict[str, tuple[str, int | None]] = {}
    declared = config.get("domains")
    if not isinstance(declared, list):
        if not config:
            result.warnings.append(
                "There is no template.toml, so no service is routed and no variable is filled in"
            )
        return routing
    for entry in declared:
        if not isinstance(entry, dict):
            continue
        service = str(entry.get("serviceName") or "")
        number = entry.get("port")
        if service not in services:
            result.warnings.append(
                f"The template routes the domain to {service or 'a service with no name'}, which "
                "the Compose file does not have"
            )
            continue
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 65535:
            result.warnings.append(f"The port the template gives {service} is not a port")
            continue
        host = expander.expand(str(entry["host"])) if entry.get("host") else ""
        path = str(entry.get("path") or "")
        if host and domain and host != domain:
            result.warnings.append(
                f"The template also answers on {host}; Noust serves one name per stack, add it "
                "with 'noust domain add' once it points here"
            )
        if path and path != "/":
            result.warnings.append(
                f"The template routes only {path} of the domain to {service}; Noust sends the "
                "whole domain (route a path with noust.nginx.yaml)"
            )
        routing.setdefault(service, ("domain", number))
    ordered = list(routing)
    if len(ordered) > 1:
        result.warnings.append(
            f"The template routes more than one service; {ordered[0]} gets the domain and "
            f"{', '.join(ordered[1:])} stays inside the stack (route more with noust.nginx.yaml)"
        )
        for extra in ordered[1:]:
            routing.pop(extra)
    if routing:
        result.adjustments.append("The domain of the template became a published port on 127.0.0.1")
    return routing


def _files(config: Mapping[str, Any], expander: _Expander) -> tuple[dict[str, str], set[str]]:
    """
    Read ``[[config.mounts]]``: the files the template writes beside the stack.

    Args:
        config: The template's ``[config]`` table.
        expander: Expands the content.

    Returns:
        ``{path relative to the folder: content}``, and the paths whose content holds a secret
        the template generated.

    Raises:
        ValidationError: A path leaves the folder, there are too many files, or one is too large.
    """
    mounts = config.get("mounts")
    files: dict[str, str] = {}
    secret: set[str] = set()
    if not isinstance(mounts, list):
        return files, secret
    entries = [m for m in mounts if isinstance(m, dict) and (m.get("filePath") or m.get("content"))]
    if len(entries) > MAX_FILES:
        raise ValidationError(
            f"The template mounts {len(entries)} files",
            details=f"A template mounts a handful; the limit is {MAX_FILES} files.",
            field="template",
        )
    for entry in entries:
        path = _safe_path(expander.expand(str(entry.get("filePath") or "")))
        content, kinds = expander.expand_with_kinds(str(entry.get("content") or ""))
        if len(content.encode("utf-8", "replace")) > MAX_FILE_BYTES:
            raise ValidationError(
                f"The file {path} is too large",
                details=f"A mounted file is a few KiB; the limit is {MAX_FILE_BYTES // 1024} KiB.",
                field="template",
            )
        files[f"files/{path}"] = content
        if kinds & _SECRET_FUNCTIONS:
            secret.add(f"files/{path}")
    return files, secret


def _safe_path(raw: str) -> str:
    """
    Check the path of a file the template mounts.

    Dokploy writes it under the stack's ``files`` folder and reads a leading slash as that
    folder's root; it is relative to that folder here too, and it may not leave it.

    Args:
        raw: ``filePath`` as the template writes it.

    Returns:
        The path relative to the folder, with no leading slash and no ``.`` part (``./Caddyfile``
        is the Caddyfile).

    Raises:
        ValidationError: The path is empty, holds a NUL or a backslash, or has a ``..`` part.
    """
    stripped = raw.strip()
    segments = [part for part in stripped.split("/") if part and part != "."]
    if not segments or "\x00" in raw or "\\" in raw or ".." in segments:
        raise ValidationError(
            f"The path of a mounted file is not one Noust writes: {raw!r}",
            details="It must name a file inside the stack's folder: no '..', no backslash.",
            field="template",
        )
    return str(PurePosixPath(*segments))


def _point_at_files(document: dict[str, Any], result: DokployImport) -> None:
    """
    Make the Compose file find the mounted files where they were written.

    Dokploy keeps them in a ``files`` folder beside the folder the Compose file is in
    (``../files/x``); here they sit beside the document (``./files/x``).

    Args:
        document: The Compose file, changed in place.
        result: Where the files are and where warnings go.
    """
    wanted: set[str] = set()

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            for found in re.findall(r"(?<![\w./-])\.\./files/([^\s:'\"]+)", value):
                wanted.add(f"files/{found}")
            return _FILES_PREFIX.sub("./files/", value)
        return value

    def each(entry: Any) -> Any:
        if isinstance(entry, dict):
            return {key: rewrite(item) for key, item in entry.items()}
        return rewrite(entry)

    for spec in document["services"].values():
        if not isinstance(spec, dict):
            continue
        for key in ("volumes", "env_file"):
            entries = spec.get(key)
            if isinstance(entries, list):
                spec[key] = [each(entry) for entry in entries]
            elif isinstance(entries, str):
                spec[key] = rewrite(entries)
    for missing in sorted(wanted - set(result.files)):
        result.warnings.append(
            f"The Compose file mounts {missing}, which the template does not provide: put the "
            "file there before deploying"
        )


def _env(config: Mapping[str, Any], expander: _Expander, result: DokployImport) -> None:
    """
    Read ``[config].env``: the lines of the stack's ``.env``.

    Args:
        config: The template's ``[config]`` table.
        expander: Expands each value.
        result: Where the values, the variables and the warnings go.
    """
    entries = config.get("env")
    lines: list[str] = []
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, str):
                lines.append(entry)
            elif isinstance(entry, dict):
                lines.extend(f"{key}={value}" for key, value in entry.items())
    elif isinstance(entries, dict):
        lines.extend(f"{key}={value}" for key, value in entries.items())
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        name, separator, value = text.partition("=")
        name = name.strip()
        if not separator:
            result.warnings.append(f"The env line {text!r} has no '=' and was skipped")
            continue
        if not _NAME.fullmatch(name):
            result.warnings.append(f"The variable name {name!r} is not one Compose reads: skipped")
            continue
        made, kinds = expander.expand_with_kinds(value.strip())
        result.values[name] = made
        result.variables = [v for v in result.variables if v.name != name]
        result.variables.append(_describe(name, kinds))
    for token in dict.fromkeys(expander.unknown):
        result.warnings.append(
            f"${{{token}}} is neither a variable nor a function of the template, and is written "
            "as it is"
        )


def _describe(name: str, kinds: frozenset[str]) -> ConvertedVariable:
    """
    Say where a value of the ``.env`` came from.

    Args:
        name: The variable.
        kinds: The functions that made its value.

    Returns:
        ``domain`` for the stack's address, ``generated`` for a random value, ``template`` for
        what the template wrote; secret when a random secret went into it or its name says so.
    """
    secret = bool(kinds & _SECRET_FUNCTIONS) or name_looks_secret(name)
    if kinds == {"domain"}:
        return ConvertedVariable(name, "domain", secret=secret)
    if "email" in kinds:
        return ConvertedVariable(
            name,
            "template",
            secret=secret,
            note="Dokploy invents a random address here; Noust writes one on the domain. "
            "Change it to an address you read",
        )
    generating = kinds - {"domain", "timestamp", "timestampms", "timestamps"}
    return ConvertedVariable(name, "generated" if generating else "template", secret=secret)
