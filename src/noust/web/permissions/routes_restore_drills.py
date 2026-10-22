# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's restore drills (:mod:`noust.web.api.restore_drills`).

A drill touches nothing of the application, but it loads the application's data
into temporary databases for a while, so running one is what running a backup is.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/restore-drills"): Permission.BACKUPS_READ,
    ("POST", "/api/apps/{domain}/restore-drills"): Permission.BACKUPS_RUN,
}
