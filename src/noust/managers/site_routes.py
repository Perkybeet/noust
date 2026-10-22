# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What happens at a prefix of an application's site: who may reach it, where it goes.

A route is a path prefix of the site with one of three meanings:

- ``site``: the prefix is served as the rest of the site is, with its own answer to
  "does it ask for the password" (``open`` lifts the site's, ``password`` asks for it
  where the site does not) and its own response headers. ``/admin`` behind a password,
  ``/hooks`` open to a service that cannot sign in, ``/api`` allowing a cross-origin
  caller.
- ``proxy``: the prefix is handed to another service on this machine, by its port, with
  or without the prefix taken off the path. ``/api`` served by another process.
- ``redirect``: the prefix answers with a redirect to an address or to another path.

Routes are part of the site's policy (:mod:`noust.managers.site_policy`): kept in the
store, rendered by whoever writes the site, and so still there after a deploy, an alias
or a certificate order. Everything here ends up in a file the web server loads as root,
so a path is a closed alphabet, a target is a URL of a closed alphabet, and no value can
carry a quote, ``$`` (except the variables Noust writes itself), ``;`` or a space.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import ValidationError
from noust.managers.site_headers import check_headers

#: What a prefix can do.
ROUTE_KINDS = ("site", "proxy", "redirect")

#: A route's answer to the password: the site's own, always, or never.
ROUTE_AUTH = ("inherit", "password", "open")

#: The statuses a redirect may answer with.
REDIRECT_CODES = (301, 302, 307, 308)

#: The most routes one site may have.
MAX_ROUTES = 40

#: The most headers one route may set.
MAX_ROUTE_HEADERS = 10

#: The longest path a route may name.
MAX_PATH = 200

_FIELDS = frozenset({"path", "kind", "auth", "port", "strip", "to", "code", "keep_path", "headers"})

_SEGMENT = re.compile(r"[A-Za-z0-9._~-]{1,100}")
_HOST = r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"
_TARGET_PATH = r"/[A-Za-z0-9._~%+@:,/-]*"
# A path with no host must not start with two slashes: "//host" is an address.
_LOCAL_PATH = r"/(?!/)[A-Za-z0-9._~%+@:,/-]*|/"
_TARGET_QUERY = r"\?[A-Za-z0-9._~%+@:,/=&-]*"
_TARGET = re.compile(
    rf"(?:https?://{_HOST}(?::[0-9]{{1,5}})?(?:{_TARGET_PATH})?|{_LOCAL_PATH})(?:{_TARGET_QUERY})?"
)

#: The path the certificate authority validates on: no route may shadow it.
_RESERVED = "/.well-known"


@dataclass(frozen=True)
class SiteRoute:
    """
    One prefix of a site and what happens there. Immutable.

    Attributes:
        path: The prefix, ``/admin``: it matches ``/admin`` and everything under it,
            never ``/administrator``.
        kind: ``site``, ``proxy`` or ``redirect``.
        auth: ``inherit``, ``password`` or ``open`` (``site`` and ``proxy``).
        port: The local port a ``proxy`` route hands the prefix to.
        strip: Whether a ``proxy`` takes the prefix off the path the service sees.
        to: Where a ``redirect`` goes: an address or an absolute path.
        code: A ``redirect``'s status.
        keep_path: Whether a ``redirect`` carries what follows the prefix to the target.
        headers: Response headers sent at this prefix, over the site's.
    """

    path: str
    kind: str = "site"
    auth: str = "inherit"
    port: int = 0
    strip: bool = False
    to: str = ""
    code: int = 301
    keep_path: bool = True
    headers: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the route for the API, the CLI and the stored document.

        Returns:
            Every field, JSON-ready.
        """
        return {
            "path": self.path,
            "kind": self.kind,
            "auth": self.auth,
            "port": self.port,
            "strip": self.strip,
            "to": self.to,
            "code": self.code,
            "keep_path": self.keep_path,
            "headers": dict(self.headers),
        }


def normal_path(value: Any, *, field_name: str = "routes") -> str:
    """
    Check a prefix and give its canonical spelling.

    Args:
        value: What was given.
        field_name: The field to name in an error.

    Returns:
        The path without a trailing slash.

    Raises:
        ValidationError: It is not a path of the closed alphabet, is the whole site,
            or lies in the certificate authority's own path.
    """
    text = str(value).strip() if isinstance(value, str) else ""
    text = text.removesuffix("/") if len(text) > 1 else text
    segments = text.split("/")[1:] if text.startswith("/") else []
    if (
        not text
        or len(text) > MAX_PATH
        or not segments
        or any(not _SEGMENT.fullmatch(segment) or segment in (".", "..") for segment in segments)
    ):
        raise ValidationError(
            f"{value!r} is not a path to route",
            details="Use a path such as /admin or /api/v2: letters, digits, dots, dashes, "
            "underscores and tildes between slashes. / alone is the site itself.",
            field=field_name,
        )
    if text == _RESERVED or text.startswith(_RESERVED + "/"):
        raise ValidationError(
            f"{text} is where certificates are validated",
            details="Nothing may take over /.well-known: the certificate authority's checks "
            "would stop reaching the site.",
            field=field_name,
        )
    return text


def redirect_target(value: Any, *, keep_path: bool = False, absolute_only: bool = False) -> str:
    """
    Check where a redirect goes.

    The one judge of a redirect's address, for a route and for the answer to an unknown name.

    Args:
        value: What was given.
        keep_path: Whether the rest of the path is appended to it.
        absolute_only: Whether only an ``http`` or ``https`` address is accepted, not a path.

    Returns:
        The target, as given.

    Raises:
        ValidationError: It is not an address or a path of the closed alphabet.
    """
    text = value if isinstance(value, str) else ""
    if not text or len(text) > 2048 or not _TARGET.fullmatch(text):
        raise ValidationError(
            f"{value!r} cannot be a redirect target",
            details="Use an address such as https://docs.example.com/guide or an absolute path "
            "such as /new. Spaces, quotes, $, ; and # are not accepted.",
            field="routes",
        )
    if absolute_only and not text.startswith(("http://", "https://")):
        raise ValidationError(
            f"{text!r} is not an address",
            details="Use an address that starts with http:// or https://.",
            field="redirect_to",
        )
    if keep_path and "?" in text:
        raise ValidationError(
            "A redirect that keeps the rest of the path cannot carry a query of its own",
            details="Drop the ?query, or turn off keep_path.",
            field="routes",
        )
    return text


def _flag(value: Any, name: str) -> bool:
    """
    Args:
        value: What was given.
        name: The field.

    Returns:
        The boolean.

    Raises:
        ValidationError: It is not a boolean.
    """
    if not isinstance(value, bool):
        raise ValidationError(f"{name} is true or false", field="routes")
    return value


def route_from(data: Any) -> SiteRoute:
    """
    Check one route and build it.

    Args:
        data: A mapping with ``path`` and the fields of its kind.

    Returns:
        The route.

    Raises:
        ValidationError: A field is unknown, does not belong to the route's kind, or
            holds a value the web server's file cannot safely carry.
    """
    if not isinstance(data, Mapping):
        raise ValidationError("A route is an object with a path", field="routes")
    unknown = sorted(set(data) - _FIELDS)
    if unknown:
        raise ValidationError(
            f"Unknown route field {unknown[0]!r}",
            details="The fields are: " + ", ".join(sorted(_FIELDS)) + ".",
            field="routes",
        )
    path = normal_path(data.get("path"))
    kind = data.get("kind", "site")
    if kind not in ROUTE_KINDS:
        raise ValidationError(f"{path}: kind is one of {', '.join(ROUTE_KINDS)}", field="routes")
    auth = data.get("auth", "inherit")
    if auth not in ROUTE_AUTH:
        raise ValidationError(f"{path}: auth is one of {', '.join(ROUTE_AUTH)}", field="routes")
    strip = _flag(data.get("strip", False), "strip")
    keep_path = _flag(data.get("keep_path", True), "keep_path")
    headers = check_headers(data.get("headers", {}), allow_removal=False)
    if len(headers) > MAX_ROUTE_HEADERS:
        raise ValidationError(
            f"{path}: a route may set at most {MAX_ROUTE_HEADERS} headers", field="routes"
        )
    clean_headers = {name: str(value) for name, value in headers.items() if value is not None}

    if kind == "redirect":
        code = data.get("code", 301)
        if isinstance(code, bool) or code not in REDIRECT_CODES:
            raise ValidationError(
                f"{path}: a redirect answers with one of {', '.join(map(str, REDIRECT_CODES))}",
                field="routes",
            )
        if data.get("port") or strip:
            raise ValidationError(f"{path}: a redirect has no port to proxy to", field="routes")
        if auth != "inherit" or clean_headers:
            raise ValidationError(
                f"{path}: a redirect answers before the password or the headers matter",
                details="Set them on a route of kind site, or leave them out.",
                field="routes",
            )
        return SiteRoute(
            path=path,
            kind="redirect",
            to=redirect_target(data.get("to"), keep_path=keep_path),
            code=int(code),
            keep_path=keep_path,
        )

    if data.get("to"):
        raise ValidationError(f"{path}: only a redirect has a target", field="routes")
    if kind == "proxy":
        port = data.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValidationError(
                f"{path}: a proxy route needs the local port of the service, 1 to 65535",
                field="routes",
            )
        return SiteRoute(
            path=path, kind="proxy", auth=auth, port=port, strip=strip, headers=clean_headers
        )

    if data.get("port") or strip:
        raise ValidationError(
            f"{path}: a route of kind site has no port to proxy to",
            details="Use kind proxy to hand the prefix to another service.",
            field="routes",
        )
    if auth == "inherit" and not clean_headers:
        raise ValidationError(
            f"{path}: a route of kind site must change something",
            details="Give it auth (password or open) or response headers.",
            field="routes",
        )
    return SiteRoute(path=path, kind="site", auth=auth, headers=clean_headers)


def routes_from(value: Any) -> tuple[SiteRoute, ...]:
    """
    Check a list of routes as a whole.

    Args:
        value: A list of mappings (or of routes already checked).

    Returns:
        The routes, longest path first.

    Raises:
        ValidationError: One is not valid, a path is named twice, or a proxy or a
            redirect lies inside (or around) another route.
    """
    if not isinstance(value, (list, tuple)):
        raise ValidationError("routes is a list", field="routes")
    if len(value) > MAX_ROUTES:
        raise ValidationError(
            f"A site may have at most {MAX_ROUTES} routes, not {len(value)}", field="routes"
        )
    routes = [item if isinstance(item, SiteRoute) else route_from(item) for item in value]
    paths = [route.path for route in routes]
    for path in set(paths):
        if paths.count(path) > 1:
            raise ValidationError(f"{path} is routed twice", field="routes")
    for outer in routes:
        for inner in routes:
            if outer is inner or not inner.path.startswith(outer.path + "/"):
                continue
            if outer.kind != "site" or inner.kind != "site":
                raise ValidationError(
                    f"{inner.path} lies inside {outer.path}",
                    details="A route that proxies or redirects cannot contain another route "
                    "nor be inside one; the web servers would not agree on which wins. "
                    "Routes that only change the password or the headers can be nested.",
                    field="routes",
                )
    return tuple(sorted(routes, key=lambda route: (-len(route.path), route.path)))


def needs_password(routes: Sequence[SiteRoute]) -> bool:
    """
    Args:
        routes: A site's routes.

    Returns:
        Whether any route asks for the password, so the accounts file is needed
        even where the site as a whole asks for none.
    """
    return any(route.auth == "password" for route in routes)


def regex_of(path: str) -> str:
    """
    Turn a prefix into the regular expression nginx and Apache both read.

    Args:
        path: A checked path.

    Returns:
        ``^/admin(?:/|$)``-style text; dots are written as ``[.]`` so no backslash
        reaches the configuration file.
    """
    return "^" + path.replace(".", "[.]") + "(?:/|$)"
