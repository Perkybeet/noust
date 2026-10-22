# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/server/migration`` (:mod:`noust.web.api.server_migration`).

Reading what a move would take and what the new server found is ``server.read``. Copying
everything to a destination takes a backup of every application and sends Noust's own state, which
holds every secret of the server (sealed under a passphrase), so it is ``backups.manage``, the
permission of sending a bundle away, and asks sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/server/migration/plan"): Permission.SERVER_READ,
    ("POST", "/api/server/migration/prepare"): Permission.BACKUPS_MANAGE,
    ("GET", "/api/server/migration/check"): Permission.SERVER_READ,
}
