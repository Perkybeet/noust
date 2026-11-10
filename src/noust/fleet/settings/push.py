# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The typed calls that write one setting of the fleet on a node.

Each kind goes through the node's own route for it (a channel by ``PUT
/api/notifications/channels/{name}``, a registry login by ``POST /api/registries`` or ``PATCH
/api/registries/{name}``...) and never through ``PATCH /api/config`` of loose keys: the node
validates the value with its own validators, answers with its own words, and audits the change
as it audits any other. A route that creates and a route that changes are different for some
kinds, so :func:`find` asks the node whether it has the setting before :func:`push` writes it.

Nothing here knows how to call a node: it is given a :class:`PushTarget`, which the reconciler
builds around a request signed with the person and the change.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import quote

from noust.core.exceptions import ValidationError
from noust.core.server_text import say
from noust.fleet.settings.reach import node_value

__all__ = ["PushTarget", "find", "push"]


class PushTarget(Protocol):
    """A node, as far as writing a setting goes."""

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """
        Send one request and read its JSON.

        Args:
            method: The method.
            path: The node's path.
            body: The JSON body.
            params: Query parameters.

        Returns:
            The decoded answer.
        """


def _listed(node: PushTarget, path: str, key: str) -> list[Mapping[str, Any]]:
    """
    Args:
        node: The node.
        path: Its list route.
        key: The field of the answer that holds the list.

    Returns:
        The items; none when the answer has no such list.
    """
    answer = node.call("GET", path)
    items = answer.get(key) if isinstance(answer, Mapping) else None
    return [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []


#: Where each kind is listed, and under which field of the answer.
_LISTS: dict[str, tuple[str, str]] = {
    "channel": ("/api/notifications/channels", "items"),
    "alert_rule": ("/api/alerts/rules", "items"),
    "backup_destination": ("/api/backup-destinations", "destinations"),
    "registry": ("/api/registries", "logins"),
    "variable": ("/api/variables/server", "variables"),
}


#: The route that says where a node's notification links open.
CONSOLE_URL_PATH = "/api/notifications/console-url"

#: The route a node's policy is read and written by.
POLICY_PATH = "/api/config/policy"


def find(node: PushTarget, resource: str, name: str) -> dict[str, Any] | None:
    """
    Ask a node whether it has a setting.

    Args:
        node: The node.
        resource: The kind.
        name: The setting.

    Returns:
        The node's own listing of it; None when it has none. The empty default of a channel
        type is not a channel its operator has, and a console address that is empty is not one.
    """
    if resource == "console_link":
        answer = node.call("GET", CONSOLE_URL_PATH)
        url = answer.get("url") if isinstance(answer, Mapping) else None
        return {"name": name, "url": url} if isinstance(url, str) and url else None
    if resource == "policy":
        # A server always has a policy: it only has its own when its operator chose one, and
        # only that is not the fleet's to overwrite on a first look.
        answer = node.call("GET", POLICY_PATH)
        chosen = isinstance(answer, Mapping) and answer.get("customized") is True
        return (
            {"name": name, **{k: v for k, v in answer.items() if k != "customized"}}
            if chosen
            else None
        )
    path, key = _LISTS[resource]
    for item in _listed(node, path, key):
        if item.get("name") != name:
            continue
        if resource == "channel" and item.get("default") and not item.get("configured"):
            return None
        return dict(item)
    return None


def _put(path: str) -> Callable[[PushTarget, str, dict[str, Any], dict[str, Any] | None], str]:
    """
    Args:
        path: The route, with ``{name}`` for the setting.

    Returns:
        A pusher that writes the value there whether or not the node has it.
    """

    def push_one(
        node: PushTarget, name: str, value: dict[str, Any], found: dict[str, Any] | None
    ) -> str:
        node.call("PUT", path.format(name=quote(name, safe="")), body=value)
        return "updated" if found is not None else "created"

    return push_one


#: The blocks of a channel that its route replaces when they are given and keeps when they are
#: left out.
_CHANNEL_BLOCKS = ("events", "route", "quiet", "flood", "summary")


def _channel(
    node: PushTarget, name: str, value: dict[str, Any], found: dict[str, Any] | None
) -> str:
    """
    Args:
        node: The node.
        name: The channel.
        value: Its type, settings and routing, with the central's own list of servers in the route.
        found: The node's listing of it, when it has it.

    Returns:
        ``created`` or ``updated``.
    """
    body = node_value(value)
    if found is not None:
        # The node keeps a block that is not mentioned, so a filter the fleet took away would stay
        # on the node for ever: say that the block is empty.
        for block in _CHANNEL_BLOCKS:
            if block not in body and found.get(block):
                body[block] = {}
    node.call("PUT", f"/api/notifications/channels/{quote(name, safe='')}", body=body)
    return "updated" if found is not None else "created"


def _console_link(
    node: PushTarget, name: str, value: dict[str, Any], found: dict[str, Any] | None
) -> str:
    """
    Args:
        node: The node.
        name: The console link.
        value: The address this node is sent.
        found: The node's own listing of it, when it has one.

    Returns:
        ``created`` or ``updated``.
    """
    node.call("PUT", CONSOLE_URL_PATH, body={"url": value["url"]})
    return "updated" if found is not None else "created"


def _policy(
    node: PushTarget, name: str, value: dict[str, Any], found: dict[str, Any] | None
) -> str:
    """
    Args:
        node: The node.
        name: The policy.
        value: Its four fields.
        found: The node's own policy, when its operator chose one.

    Returns:
        ``created`` or ``updated``.
    """
    node.call("PUT", POLICY_PATH, body=value)
    return "updated" if found is not None else "created"


def _registry(
    node: PushTarget, name: str, value: dict[str, Any], found: dict[str, Any] | None
) -> str:
    """
    Args:
        node: The node.
        name: The login.
        value: Its kind, user, password and host.
        found: The node's listing of it, when it has it.

    Returns:
        ``created`` or ``updated``.
    """
    if found is None:
        node.call("POST", "/api/registries", body={"name": name, **value})
        return "created"
    node.call("PATCH", f"/api/registries/{quote(name, safe='')}", body=value)
    return "updated"


def _destination(
    node: PushTarget, name: str, value: dict[str, Any], found: dict[str, Any] | None
) -> str:
    """
    Args:
        node: The node.
        name: The destination.
        value: Its backend, fields and encryption.
        found: The node's listing of it, when it has it.

    Returns:
        ``created`` or ``updated``.

    Raises:
        ValidationError: The node has it with another backend, which no route changes.
    """
    if found is None:
        node.call("POST", "/api/backup-destinations", body={"name": name, **value})
        return "created"
    if found.get("backend") != value.get("backend"):
        raise ValidationError.from_text(
            say(
                "fleet_settings.backend_changed",
                name=name,
                have=str(found.get("backend")),
                want=str(value.get("backend")),
            ),
            field="backend",
        )
    body = {key: value[key] for key in ("fields", "encrypted", "verify") if key in value}
    node.call("PUT", f"/api/backup-destinations/{quote(name, safe='')}", body=body)
    return "updated"


_WRITERS: dict[str, Callable[[PushTarget, str, dict[str, Any], dict[str, Any] | None], str]] = {
    "channel": _channel,
    "alert_rule": _put("/api/alerts/rules/{name}"),
    "backup_destination": _destination,
    "registry": _registry,
    "variable": _put("/api/variables/server/{name}"),
    "console_link": _console_link,
    "policy": _policy,
}


def push(
    node: PushTarget,
    resource: str,
    name: str,
    value: Mapping[str, Any],
    found: dict[str, Any] | None,
) -> str:
    """
    Write a setting on a node, by the node's own route for it.

    Args:
        node: The node, speaking as the push.
        resource: The kind.
        name: The setting.
        value: The whole value, credentials in it. Not changed.
        found: What :func:`find` said of it.

    Returns:
        ``created`` or ``updated``.

    Raises:
        ValidationError: A destination of another backend.
        Failure: The node refused or did not answer; its words are in the failure.
    """
    return _WRITERS[resource](node, name, dict(value), found)
