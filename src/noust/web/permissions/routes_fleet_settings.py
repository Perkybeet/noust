# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/fleet/settings`` (:mod:`noust.web.api.fleet_settings`).

Looking at what the fleet wants, and where each server stands, is ``fleet.read``. Setting,
removing, leaving a server out, applying and putting a setting back are ``fleet.manage``; the ones
that write also ask sudo mode, and setting or removing a thing asks, besides, the permission its
own route on a single server asks (checked by the manager, which knows the kind): a person who
manages the fleet does not thereby manage the registry logins of every server.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/fleet/settings"): Permission.FLEET_READ,
    ("GET", "/api/fleet/settings/status"): Permission.FLEET_READ,
    ("GET", "/api/fleet/settings/{resource}"): Permission.FLEET_READ,
    ("GET", "/api/fleet/settings/{resource}/{name}"): Permission.FLEET_READ,
    ("PUT", "/api/fleet/settings/{resource}/{name}"): Permission.FLEET_MANAGE,
    ("DELETE", "/api/fleet/settings/{resource}/{name}"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/settings/apply"): Permission.FLEET_MANAGE,
    ("PUT", "/api/fleet/servers/{node}/settings-mode"): Permission.FLEET_MANAGE,
    (
        "POST",
        "/api/fleet/servers/{node}/settings/{resource}/{name}/restore",
    ): Permission.FLEET_MANAGE,
}
