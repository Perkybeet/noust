# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/backup-schedules`` (:mod:`noust.web.api.backup_schedules`).

A schedule carries pre- and post-backup hooks, commands run as root: writing
one is root-equivalent.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/backup-schedules"): Permission.BACKUPS_READ,
    ("POST", "/api/backup-schedules"): Permission.ROOT_EQUIVALENT,
    ("PUT", "/api/backup-schedules/{domain}"): Permission.ROOT_EQUIVALENT,
    ("DELETE", "/api/backup-schedules/{domain}"): Permission.BACKUPS_MANAGE,
}
