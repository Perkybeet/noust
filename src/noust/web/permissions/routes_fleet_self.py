# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permission of ``GET /api/auth/fleet/self`` (:mod:`noust.web.api.fleet_self`).

It says how far a central may go on this server, to the central itself and
to this server's own users: something about the caller's own access, like its
session, so ``self``. It only reads; the ceiling is set on the command line.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/auth/fleet/self"): Permission.SELF,
}
