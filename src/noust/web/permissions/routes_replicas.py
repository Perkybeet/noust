# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's copies (:mod:`noust.web.api.replicas`).

Seeing how an application is scaled is reading it. Changing it starts and stops units and
decides what nginx answers with, so it is the application's manager's, and the handler
also asks for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/replicas"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/replicas"): Permission.APPS_MANAGE,
}
