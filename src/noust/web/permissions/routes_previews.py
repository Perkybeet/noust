# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's previews (:mod:`noust.web.api.previews`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/previews"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/previews/settings"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/previews/settings"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/previews/{number}"): Permission.APPS_MANAGE,
}
