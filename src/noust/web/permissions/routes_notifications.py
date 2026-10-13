# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/notifications`` (:mod:`noust.web.api.notifications`).

The channels are settings of the server (they hold the keys to the places its
alerts go), so reading them is ``settings.read`` and changing them
``settings.manage``; the route also needs sudo mode to change one. Trying a
channel is ``POST /api/config/notifications/{channel}/test``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/notifications/channels"): Permission.SETTINGS_READ,
    ("PUT", "/api/notifications/channels/{name}"): Permission.SETTINGS_MANAGE,
    ("DELETE", "/api/notifications/channels/{name}"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/notifications/events"): Permission.SETTINGS_READ,
    ("GET", "/api/notifications/deliveries"): Permission.SETTINGS_READ,
    ("POST", "/api/notifications/deliveries/sweep"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/notifications/deliveries/{delivery_id}/retry"): Permission.SETTINGS_MANAGE,
}
