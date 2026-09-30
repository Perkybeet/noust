# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/fleet`` (:mod:`noust.web.api.fleet`).

Looking at the fleet is ``fleet.read``; acting on several servers at once, and
labelling them, is ``fleet.manage``. Each node still applies its own map to the
role the central forwards for every call a view or an action makes, so these
only decide who may ask the central to ask.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/fleet/summary"): Permission.FLEET_READ,
    ("GET", "/api/fleet/servers"): Permission.FLEET_READ,
    ("GET", "/api/fleet/apps"): Permission.FLEET_READ,
    ("GET", "/api/fleet/certificates"): Permission.FLEET_READ,
    ("GET", "/api/fleet/backups"): Permission.FLEET_READ,
    ("GET", "/api/fleet/updates"): Permission.FLEET_READ,
    ("GET", "/api/fleet/activity"): Permission.FLEET_READ,
    ("GET", "/api/fleet/actions"): Permission.FLEET_READ,
    ("POST", "/api/fleet/actions"): Permission.FLEET_MANAGE,
    ("GET", "/api/fleet/jobs"): Permission.FLEET_READ,
    ("GET", "/api/fleet/jobs/{job_id}"): Permission.FLEET_READ,
    ("POST", "/api/fleet/jobs/{job_id}/retry"): Permission.FLEET_MANAGE,
    ("PUT", "/api/fleet/servers/{node}/labels"): Permission.FLEET_MANAGE,
}
