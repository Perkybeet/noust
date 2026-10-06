# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/fleet/enroll`` (:mod:`noust.web.api.node_enroll`).

Enrolling a server adds it to this central, which is ``fleet.manage``, exactly
as ``POST /api/nodes`` does; asking an address for its host key is the first step
of it and needs the same.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/fleet/enroll/host-key"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/enroll"): Permission.FLEET_MANAGE,
}
