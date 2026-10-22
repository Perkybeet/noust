# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/backups`` (:mod:`noust.web.api.backups`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/backups"): Permission.BACKUPS_READ,
    ("GET", "/api/backups/storage"): Permission.BACKUPS_READ,
    ("GET", "/api/backups/failures"): Permission.BACKUPS_READ,
    ("GET", "/api/backups/failures/{failure_id}"): Permission.BACKUPS_READ,
    ("GET", "/api/backups/failures/{failure_id}/output"): Permission.BACKUPS_READ,
    ("GET", "/api/backups/encryption"): Permission.BACKUPS_READ,
    ("POST", "/api/backups/encryption/enable"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/backups/encryption/rotate"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/backups/encryption/disable"): Permission.BACKUPS_MANAGE,
    # A key is a secret: reading one is the permission that reads secrets, not the backups'.
    ("GET", "/api/backups/encryption/keys/{key_id}"): Permission.SECRETS_REVEAL,
    ("POST", "/api/backups/encryption/keys"): Permission.BACKUPS_MANAGE,
    ("GET", "/api/backups/encryption/seal-existing"): Permission.BACKUPS_READ,
    ("POST", "/api/backups/encryption/seal-existing"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/backups"): Permission.BACKUPS_RUN,
    ("GET", "/api/backups/{backup_id}"): Permission.BACKUPS_READ,
    ("POST", "/api/backups/{backup_id}/verify"): Permission.BACKUPS_RUN,
    ("POST", "/api/backups/{backup_id}/restore"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/backups/{backup_id}/push"): Permission.BACKUPS_MANAGE,
    ("DELETE", "/api/backups/{backup_id}"): Permission.BACKUPS_MANAGE,
}
