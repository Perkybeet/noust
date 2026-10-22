# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of adopting a site that already runs (:mod:`noust.web.api.app_native_adopt`).

Adopting writes only the store, but it makes a unit Noust may restart, so registering one is
``apps.manage`` (and the handler asks for sudo mode). Listing the candidates reads the whole
server's sites and the units behind them, which is the same permission; saying what an adopted
application is, is reading an application.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/native-sites"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/adopt-site"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/adoption"): Permission.APPS_READ,
}
