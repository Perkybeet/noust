# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/backup-destinations`` (:mod:`noust.web.api.backup_destinations`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/backup-destinations/backends"): Permission.BACKUPS_READ,
    ("GET", "/api/backup-destinations"): Permission.BACKUPS_READ,
    ("POST", "/api/backup-destinations"): Permission.BACKUPS_MANAGE,
    ("PUT", "/api/backup-destinations/{name}"): Permission.BACKUPS_MANAGE,
    ("DELETE", "/api/backup-destinations/{name}"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/backup-destinations/{name}/test"): Permission.BACKUPS_RUN,
    ("POST", "/api/backup-destinations/{name}/show-key"): Permission.SECRETS_REVEAL,
    ("GET", "/api/backup-destinations/{name}/backups"): Permission.BACKUPS_READ,
    (
        "POST",
        "/api/backup-destinations/{name}/backups/{backup_id}/restore",
    ): Permission.BACKUPS_MANAGE,
}
