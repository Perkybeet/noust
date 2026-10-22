# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/volume-backups`` (:mod:`noust.web.api.volume_backups`).

Reading what is backed up needs ``backups.read``; taking, checking and
queueing a policy's run needs ``backups.run``; what changes what is kept or
replaces data (a policy, a restore, a deletion, sending a copy away) needs
``backups.manage`` and, for a policy, a restore and a deletion, sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/volume-backups"): Permission.BACKUPS_READ,
    ("POST", "/api/volume-backups"): Permission.BACKUPS_RUN,
    ("GET", "/api/volume-backups/policies"): Permission.BACKUPS_READ,
    ("GET", "/api/volume-backups/policies/{domain}"): Permission.BACKUPS_READ,
    ("PUT", "/api/volume-backups/policies/{domain}"): Permission.BACKUPS_MANAGE,
    ("DELETE", "/api/volume-backups/policies/{domain}"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/volume-backups/policies/{domain}/run"): Permission.BACKUPS_RUN,
    ("GET", "/api/volume-backups/targets/{domain}"): Permission.BACKUPS_READ,
    ("GET", "/api/volume-backups/remote/{domain}"): Permission.BACKUPS_READ,
    ("POST", "/api/volume-backups/restore-remote"): Permission.BACKUPS_MANAGE,
    ("GET", "/api/volume-backups/{backup_id}"): Permission.BACKUPS_READ,
    ("POST", "/api/volume-backups/{backup_id}/verify"): Permission.BACKUPS_RUN,
    ("POST", "/api/volume-backups/{backup_id}/push"): Permission.BACKUPS_MANAGE,
    ("POST", "/api/volume-backups/{backup_id}/restore"): Permission.BACKUPS_MANAGE,
    ("DELETE", "/api/volume-backups/{backup_id}"): Permission.BACKUPS_MANAGE,
}
