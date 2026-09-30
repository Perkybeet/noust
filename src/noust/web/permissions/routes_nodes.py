# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/nodes`` (:mod:`noust.web.api.nodes`, :mod:`noust.web.api.node_proxy`).

A request through the proxy needs ``fleet.read`` here and, when this
server's own map knows the node's path, the permission that path needs:
:mod:`noust.web.permissions.enforce` asks for both. The node then applies its
own map to the role the central forwards.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/nodes"): Permission.FLEET_READ,
    ("POST", "/api/nodes"): Permission.FLEET_MANAGE,
    ("GET", "/api/nodes/{node}"): Permission.FLEET_READ,
    ("DELETE", "/api/nodes/{node}"): Permission.FLEET_MANAGE,
    ("POST", "/api/nodes/{node}/test"): Permission.FLEET_READ,
    ("GET", "/api/nodes/{node}/key"): Permission.FLEET_MANAGE,
    ("GET", "/api/nodes/{node}/api/{path}"): Permission.FLEET_READ,
    ("HEAD", "/api/nodes/{node}/api/{path}"): Permission.FLEET_READ,
    ("POST", "/api/nodes/{node}/api/{path}"): Permission.FLEET_READ,
    ("PUT", "/api/nodes/{node}/api/{path}"): Permission.FLEET_READ,
    ("PATCH", "/api/nodes/{node}/api/{path}"): Permission.FLEET_READ,
    ("DELETE", "/api/nodes/{node}/api/{path}"): Permission.FLEET_READ,
    ("GET", "/api/nodes/{node}/events"): Permission.FLEET_READ,
}
