# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/backups`` (:mod:`noust.web.api.backups`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/backups"): Permission.BACKUPS_READ,
    ("GET", "/api/backups/storage"): Permission.BACKUPS_READ,
    ("POST", "/api/backups"): Permission.BACKUPS_RUN,
    ("GET", "/api/backups/{backup_id}"): Permission.BACKUPS_READ,
    ("POST", "/api/backups/{backup_id}/verify"): Permission.BACKUPS_RUN,
    ("POST", "/api/backups/{backup_id}/restore"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/backups/{backup_id}/push"): Permission.BACKUPS_MANAGE,
    ("DELETE", "/api/backups/{backup_id}"): Permission.BACKUPS_MANAGE,
}
