# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's domains (:mod:`noust.web.api.domains`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/domains"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/domains"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/domains/{name}"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/domains/{name}/dns"): Permission.APPS_READ,
    ("GET", "/api/domains/dns"): Permission.APPS_READ,
}
