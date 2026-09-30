# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's export and import (:mod:`noust.web.api.app_export`).

An export carries the application's configuration and, on request, its
secrets, so reading one is revealing secrets.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/export"): Permission.SECRETS_REVEAL,
    ("POST", "/api/apps/import"): Permission.APPS_MANAGE,
}
