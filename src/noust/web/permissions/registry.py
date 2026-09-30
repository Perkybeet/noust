# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every route's permission, collected from the ``routes_<area>.py`` maps.

Each area of the API states its own map in a module of this package named
``routes_<area>.py``, holding ``ROUTES: dict[tuple[str, str], str]`` from
``(METHOD, path template)`` - the template exactly as FastAPI reports it,
such as ``/api/apps/{domain}/restart`` - to a :class:`Permission` value or
:data:`PUBLIC`. The maps are discovered, not listed, so an area adding its
own map touches no shared file; two maps naming the same route is an error at
import, not a silent override.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from functools import lru_cache
from types import MappingProxyType

from noust.web.permissions import ALL_PERMISSIONS, PUBLIC

#: The proxy to a node: whatever follows ``/api`` there is the node's own
#: path, judged by the node's own map, and by this one before it is forwarded.
NODE_PROXY_TEMPLATE = "/api/nodes/{node}/api/{path}"
_NODE_PROXY_PATH = re.compile(r"^/api/nodes/[^/]+(/api/.*)$")

_PARAMETER = re.compile(r"\{[^/{}]+\}")

RouteKey = tuple[str, str]


def _area_modules() -> list[str]:
    """
    Returns:
        The names of every ``routes_*`` module of this package, sorted.
    """
    import noust.web.permissions as package

    return sorted(
        f"{package.__name__}.{info.name}"
        for info in pkgutil.iter_modules(package.__path__)
        if info.name.startswith("routes_")
    )


@lru_cache(maxsize=1)
def route_map() -> MappingProxyType[RouteKey, str]:
    """
    Collect every area's map.

    Returns:
        ``(METHOD, template)`` to permission, read-only.

    Raises:
        ValueError: When two areas map one route, or a map names something
            that is neither a permission nor :data:`PUBLIC`: a typo there
            must fail the import, not guard nothing.
    """
    combined: dict[RouteKey, str] = {}
    owner: dict[RouteKey, str] = {}
    for name in _area_modules():
        module = importlib.import_module(name)
        routes: dict[RouteKey, str] = getattr(module, "ROUTES", {})
        for (method, template), permission in routes.items():
            key = (method.upper(), template)
            if permission != PUBLIC and permission not in ALL_PERMISSIONS:
                raise ValueError(f"{name}: {key} maps to unknown permission {permission!r}")
            if key in combined:
                raise ValueError(f"{key} is mapped by both {owner[key]} and {name}")
            combined[key] = permission
            owner[key] = name
    return MappingProxyType(combined)


def permission_for_route(method: str, template: str) -> str | None:
    """
    The permission a route needs.

    Args:
        method: The HTTP method; ``HEAD`` falls back to ``GET``, which is
            what Starlette serves a HEAD with.
        template: The route's path template.

    Returns:
        The permission, :data:`PUBLIC`, or None when no map names the route.
    """
    routes = route_map()
    verb = method.upper()
    found = routes.get((verb, template))
    if found is None and verb == "HEAD":
        found = routes.get(("GET", template))
    return found


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[str, str, re.Pattern[str], int], ...]:
    """
    Returns:
        Each mapped route as ``(method, template, pattern, specificity)``,
        where a more specific template (fewer parameters, then longer) sorts
        first, the way the router prefers ``/api/apps/types`` over
        ``/api/apps/{domain}``.
    """
    entries = []
    for (method, template), _permission in route_map().items():
        literal_parts = _PARAMETER.split(template)
        pattern = re.compile("^" + "[^/]+".join(re.escape(part) for part in literal_parts) + "$")
        parameters = len(literal_parts) - 1
        entries.append((method, template, pattern, parameters))
    entries.sort(key=lambda entry: (entry[3], -len(entry[1])))
    return tuple(entries)


def match_path(method: str, path: str) -> tuple[str, str] | None:
    """
    Find the mapped route a concrete path belongs to.

    Only for a path no router matched here, such as what follows a node in
    the proxy; a request this server routes itself is judged by its template.

    Args:
        method: The HTTP method.
        path: A concrete path such as ``/api/apps/shop.example.com/restart``.

    Returns:
        ``(template, permission)``, or None when no mapped route matches.
    """
    verb = method.upper()
    for candidate_verb in (verb, "GET") if verb == "HEAD" else (verb,):
        for entry_method, template, pattern, _count in _compiled():
            if entry_method == candidate_verb and pattern.match(path):
                return template, route_map()[(entry_method, template)]
    return None


def proxied_path(path: str) -> str | None:
    """
    Args:
        path: A request path.

    Returns:
        The node's own path when this is a request through the node proxy
        (``/api/nodes/{node}/api/...``), otherwise None.
    """
    matched = _NODE_PROXY_PATH.match(path)
    return matched.group(1) if matched else None
