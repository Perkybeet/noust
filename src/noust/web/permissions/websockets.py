# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every WebSocket's permission: a handshake is a route like any other.

The streams are held to the permission of the HTTP route that reads the same
thing (a job's log, an application's journal, the event feed), checked with
:func:`noust.web.permissions.enforce.check_permission` in the security
middleware, the one place every handshake passes - so the second-factor and
usage-notice gates apply to a stream exactly as to a call, and a WebSocket
route missing from this map is refused rather than served.

A central's relay of a node's stream (``/ws/nodes/{node}/...``) needs
``fleet.read`` and what the node's own stream needs, the way the HTTP proxy
needs what the node's own path does.
"""

from __future__ import annotations

import re

from noust.web.permissions import Permission

#: The node relay's template, as FastAPI reports it without the converter.
NODE_STREAM_TEMPLATE = "/ws/nodes/{node}/{path}"
_NODE_STREAM_PATH = re.compile(r"^/ws/nodes/[^/]+(/.*)$")

#: Template to permission, for this server's own streams.
WEBSOCKETS: dict[str, str] = {
    # The console's live feed, like GET /events.
    "/ws/events": Permission.APPS_READ,
    # Every job's progress, like GET /api/jobs.
    "/ws/jobs": Permission.APPS_READ,
    # One job's log, like GET /api/jobs/{job_id}/log.
    "/ws/jobs/{job_id}": Permission.APPS_READ,
    # An application's journal, like GET /api/apps/{domain}/logs. The
    # console's own journal is refused below admin by the handler as well.
    "/ws/logs/{domain}": Permission.APPS_READ,
    NODE_STREAM_TEMPLATE: Permission.FLEET_READ,
}

#: What a node's stream this server does not know needs: the 3.0 rule for an
#: unknown path, anything that is not a read is ``admin``.
UNKNOWN_NODE_STREAM = Permission.APPS_MANAGE

_PARAMETER = re.compile(r"\{[^/{}]+\}")


def _compiled() -> list[tuple[re.Pattern[str], str]]:
    """
    Returns:
        Each of this server's own stream templates as a pattern, literal
        templates first.
    """
    entries = []
    for template, permission in WEBSOCKETS.items():
        if template == NODE_STREAM_TEMPLATE:
            continue
        parts = _PARAMETER.split(template)
        pattern = re.compile("^" + "[^/]+".join(re.escape(part) for part in parts) + "$")
        entries.append((len(parts), pattern, permission))
    entries.sort(key=lambda entry: entry[0])
    return [(pattern, permission) for _count, pattern, permission in entries]


def websocket_permission_for_template(template: str) -> str | None:
    """
    Args:
        template: A WebSocket route's template, such as ``/ws/jobs/{job_id}``.

    Returns:
        Its permission, or None when no entry names it.
    """
    return WEBSOCKETS.get(template)


def websocket_permissions(path: str) -> list[str] | None:
    """
    Everything a handshake to a concrete path needs.

    Args:
        path: The handshake's path, such as ``/ws/logs/shop.example.com``.

    Returns:
        The permissions, all of which the principal must hold; None for a
        path no entry covers, which the caller refuses.
    """
    relayed = _NODE_STREAM_PATH.match(path)
    if relayed is not None:
        inner = "/ws" + relayed.group(1)
        return [Permission.FLEET_READ, _own_permission(inner) or UNKNOWN_NODE_STREAM]
    own = _own_permission(path)
    return [own] if own is not None else None


def _own_permission(path: str) -> str | None:
    """
    Args:
        path: A path of one of this server's own streams.

    Returns:
        Its permission, or None.
    """
    for pattern, permission in _compiled():
        if pattern.match(path):
            return permission
    return None
