# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/fleet/cloud`` (:mod:`noust.web.api.cloud`).

Every route is ``fleet.manage``, an administrator's: a provider token can create
machines at the operator's expense, the catalog is read with it, the cloud-init
library is what a new server runs as root on its first boot, and the machines are
what that costs. None of it is something a viewer or an operator needs.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/fleet/cloud/providers"): Permission.FLEET_MANAGE,
    ("GET", "/api/fleet/cloud/accounts"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/cloud/accounts"): Permission.FLEET_MANAGE,
    ("PUT", "/api/fleet/cloud/accounts/{name}/token"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/cloud/accounts/{name}/verify"): Permission.FLEET_MANAGE,
    ("DELETE", "/api/fleet/cloud/accounts/{name}"): Permission.FLEET_MANAGE,
    ("GET", "/api/fleet/cloud/accounts/{name}/catalog"): Permission.FLEET_MANAGE,
    ("GET", "/api/fleet/cloud/scripts"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/cloud/scripts/preview"): Permission.FLEET_MANAGE,
    ("GET", "/api/fleet/cloud/scripts/{name}"): Permission.FLEET_MANAGE,
    ("PUT", "/api/fleet/cloud/scripts/{name}"): Permission.FLEET_MANAGE,
    ("DELETE", "/api/fleet/cloud/scripts/{name}"): Permission.FLEET_MANAGE,
    ("GET", "/api/fleet/cloud/servers"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/cloud/servers"): Permission.FLEET_MANAGE,
    ("GET", "/api/fleet/cloud/servers/{node}"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/cloud/servers/{node}/link"): Permission.FLEET_MANAGE,
    ("DELETE", "/api/fleet/cloud/servers/{node}/link"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/cloud/servers/{node}/power-on"): Permission.FLEET_MANAGE,
    ("DELETE", "/api/fleet/cloud/servers/{node}"): Permission.FLEET_MANAGE,
}
