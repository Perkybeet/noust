# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's SQLite databases (:mod:`noust.web.api.app_sqlite`).

Listing reads file headers, so it is as visible as the application. A check opens the
database and, in the rollback journal mode, holds off its writers while SQLite reads it: it
asks for the permission that manages the application, not the one that only looks at it.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/sqlite"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/sqlite/check"): Permission.APPS_OPERATE,
}
