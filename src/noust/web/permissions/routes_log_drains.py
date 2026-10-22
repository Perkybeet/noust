# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the destinations of logs (:mod:`noust.web.api.log_drains`).

A destination is where an application's logs, which may hold what its users did,
leave the server for: the server's own setting, managed by whoever manages settings,
with sudo mode. Seeing which ones there are (never their token) is reading settings.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/log-drains"): Permission.SETTINGS_READ,
    ("PUT", "/api/log-drains/{name}"): Permission.SETTINGS_MANAGE,
    ("DELETE", "/api/log-drains/{name}"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/log-drains/{name}/test"): Permission.SETTINGS_MANAGE,
}
