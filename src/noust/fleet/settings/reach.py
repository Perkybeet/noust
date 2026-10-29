# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a channel of the fleet can be heard: its route, and the servers the route can match on.

A channel of the fleet is pushed to servers, and each server delivers its own notifications
(``docs/CENTRAL.md``), so the route of a channel is what decides what it says on each of them.
A fleet route filters by what means the same on every server: the application's **domain** (with
``*`` and ``?``), its **tags**, the **severity**, the **event** and the **server**. The first four
are the server's own route (:class:`noust.core.notifications.routing.Route`), judged by the one
validator a server judges its own with. The server is the one thing the fleet adds: a list of
names and patterns of the central's registry, which only the central reads. It is taken out
before a channel is pushed (:func:`node_value`), because a server does not know its name in
somebody else's registry, and a server of an older Noust would refuse a route with a list it has
never heard of. Projects and environments are not on the list: their slugs and ids are each
server's own, and a channel for them is a team's channel on that server.

A channel is pushed only to a server where its route can match something (:func:`explain`): one
that names other servers, or domains and tags no application of that server has, would get a
credential it can never use. The decision is pure; the applications of a server are given.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.notifications.routing import Route, entries
from noust.core.server_text import say

__all__ = [
    "LOCAL_LISTS",
    "REASONS",
    "Reach",
    "check_route",
    "explain",
    "needs_apps",
    "node_value",
    "servers_of",
]

#: Lists of a route whose names are each server's own (slugs and ids of its teams).
LOCAL_LISTS: tuple[str, ...] = ("projects", "environments")

#: Why a channel does not reach a server.
REASONS: tuple[str, ...] = ("server", "domain", "tag")

#: The one list of a fleet route that is not the server's own.
_SERVERS = "servers"


@dataclass(frozen=True)
class Reach:
    """
    Whether a channel can match something on a server.

    Attributes:
        reaches: True when it can; False when it cannot; None when that depends on the server's
            applications and they could not be read.
        why: For False, what cannot match: ``server`` (the server is not one the route names),
            ``domain`` (no application of the server has a domain it names) or ``tag`` (none
            carries a tag it names).
    """

    reaches: bool | None
    why: str | None = None


def _route_of(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Args:
        value: A channel's value.

    Returns:
        Its route block; empty when it has none.
    """
    route = value.get("route")
    return route if isinstance(route, Mapping) else {}


def _server_route(value: Mapping[str, Any]) -> Route:
    """
    Args:
        value: A channel's value, whose route was checked.

    Returns:
        The part of its route a server reads.
    """
    return Route.from_mapping({k: v for k, v in _route_of(value).items() if k != _SERVERS})


def check_route(value: Mapping[str, Any]) -> None:
    """
    Judge a fleet channel's route when it is saved.

    Args:
        value: The channel's value.

    Raises:
        ValidationError: The route is not an object, names projects or environments, has a list
            or a setting a route does not, or a list of servers that is not one; each says which
            field, as a sentence with a code.
    """
    route = value.get("route")
    if route in (None, ""):
        return
    if not isinstance(route, Mapping):
        raise ValidationError.from_text(
            say("fleet_settings.route_invalid", problem="route must be a mapping"), field="route"
        )
    for name in LOCAL_LISTS:
        if route.get(name) not in (None, "", [], ()):
            raise ValidationError.from_text(
                say("fleet_settings.route_local", list=name), field=f"route.{name}"
            )
    try:
        Route.from_mapping({k: v for k, v in route.items() if k != _SERVERS})
    except ValueError as exc:
        raise ValidationError.from_text(
            say("fleet_settings.route_invalid", problem=str(exc)), field="route"
        ) from exc
    try:
        entries(route.get(_SERVERS), _SERVERS)
    except ValueError as exc:
        raise ValidationError.from_text(
            say("fleet_settings.route_invalid", problem=str(exc)), field=f"route.{_SERVERS}"
        ) from exc


def servers_of(value: Mapping[str, Any]) -> tuple[str, ...]:
    """
    Args:
        value: A channel's value, whose route was checked.

    Returns:
        The servers its route names (names and patterns), without repeats; none for every server.
    """
    return entries(_route_of(value).get(_SERVERS), _SERVERS)


def node_value(value: Mapping[str, Any]) -> dict[str, Any]:
    """
    The value as a server's own route takes it: without the list of servers.

    Args:
        value: A channel's value. Not changed.

    Returns:
        A copy, whose route has no ``servers``, and none at all when nothing else is left in it.
    """
    sent = dict(value)
    route = _route_of(value)
    if _SERVERS not in route:
        return sent
    rest = {k: v for k, v in route.items() if k != _SERVERS}
    if rest:
        sent["route"] = rest
    else:
        del sent["route"]
    return sent


def needs_apps(resource: str, value: Mapping[str, Any]) -> bool:
    """
    Args:
        resource: The kind of setting.
        value: Its value.

    Returns:
        Whether deciding where it reaches takes the servers' applications: only a channel whose
        route narrows to domains or tags, and which does not also ask for what is about the
        server itself, which any server can have.
    """
    if resource != "channel":
        return False
    route = _server_route(value)
    return not route.server_events and bool(route.apps or route.tags)


def explain(
    resource: str,
    value: Mapping[str, Any],
    node: str,
    apps: Sequence[Mapping[str, Any]] | None,
) -> Reach:
    """
    Say whether a setting can match something on a server.

    Args:
        resource: The kind of setting; only a channel has a route.
        value: Its value.
        node: The server, as the central's registry names it.
        apps: The server's applications (``domain`` and ``tags`` each); None when they could not
            be read, which is only asked of a route that depends on them.

    Returns:
        :class:`Reach`.
    """
    if resource != "channel":
        return Reach(True)
    servers = servers_of(value)
    if servers and not any(fnmatch.fnmatchcase(node, pattern) for pattern in servers):
        return Reach(False, "server")
    if not needs_apps(resource, value):
        return Reach(True)
    if apps is None:
        return Reach(None)
    route = _server_route(value)
    domain_matched = False
    for app in apps:
        domain = str(app.get("domain") or "")
        if route.apps and not any(fnmatch.fnmatchcase(domain, pattern) for pattern in route.apps):
            continue
        domain_matched = True
        tags = [str(tag) for tag in app.get("tags") or ()]
        if route.tags and not any(
            fnmatch.fnmatchcase(tag, pattern) for tag in tags for pattern in route.tags
        ):
            continue
        return Reach(True)
    return Reach(False, "tag" if domain_matched or not route.apps else "domain")
