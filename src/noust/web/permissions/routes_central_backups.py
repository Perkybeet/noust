# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/central-backups`` (:mod:`noust.web.api.central_backups`).

Seeing what the central holds and how collecting is going is ``backups.read``; asking for a pass is
``backups.run`` (taking copies, like taking a backup); who is followed, the knobs, letting a copy
go and giving one back (a restore) are ``backups.manage``, and each of those asks for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/central-backups"): Permission.BACKUPS_READ,
    ("GET", "/api/central-backups/status"): Permission.BACKUPS_READ,
    ("PUT", "/api/central-backups/settings"): Permission.BACKUPS_MANAGE,
    ("PUT", "/api/central-backups/nodes/{node}"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/central-backups/collect"): Permission.BACKUPS_RUN,
    ("DELETE", "/api/central-backups/{node}/{kind}"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/central-backups/{node}/{kind}/restore"): Permission.BACKUPS_MANAGE,
}
