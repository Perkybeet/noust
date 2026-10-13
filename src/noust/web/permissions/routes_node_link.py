# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/fleet/link`` (:mod:`noust.web.api.node_link`).

Inviting a server to dial in and accepting its code add a server to this
central, which is ``fleet.manage``, exactly as ``POST /api/nodes`` does; the
status says which servers dial in and what this central's sshd lacks, which is
an administrator's to read as well.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/fleet/link"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/link/invites"): Permission.FLEET_MANAGE,
    ("DELETE", "/api/fleet/link/invites/{name}"): Permission.FLEET_MANAGE,
    ("POST", "/api/fleet/link/accept"): Permission.FLEET_MANAGE,
}
