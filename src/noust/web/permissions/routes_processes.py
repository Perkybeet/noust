# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's processes (:mod:`noust.web.api.processes`).

A process runs a command as the application's account, so defining, scaling and
removing one is the application's manager's, and the handler also asks for sudo
mode; reading them and their journal is reading the application.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/processes"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/processes/{name}"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/{domain}/processes/{name}/scale"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/processes/{name}"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/processes/{name}/logs"): Permission.APPS_READ,
}
