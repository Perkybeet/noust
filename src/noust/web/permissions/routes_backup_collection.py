# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/backup-collection`` (:mod:`noust.web.api.backup_collection`).

What a central takes away from a server is every application's ``.env`` and databases, so it is
not ``backups.read``, which a viewer holds: it is ``backups.collect``, which reads the list and the
files and says which copies the central holds, and nothing else. Which centrals collect from this
server is only a thing to see, so it is ``backups.read``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/backup-collection"): Permission.BACKUPS_COLLECT,
    ("GET", "/api/backup-collection/files"): Permission.BACKUPS_COLLECT,
    ("PUT", "/api/backup-collection/held"): Permission.BACKUPS_COLLECT,
    ("GET", "/api/backup-collection/held"): Permission.BACKUPS_READ,
}
