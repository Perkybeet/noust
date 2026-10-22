# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/server/restore`` (:mod:`noust.web.api.server_restore`).

Reading what a restore would do is ``server.read``. Doing it installs software, creates database
roles and restores every application over what is on the machine, so it is ``backups.manage``, the
permission of putting a backup back, and asks sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/server/restore/plan"): Permission.SERVER_READ,
    ("POST", "/api/server/restore"): Permission.BACKUPS_MANAGE,
}
