# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's process types (:mod:`noust.web.api.app_processes`).

Reading what the code declares and which of it a deployment uses is ``apps.read``; nothing here
changes anything.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/process-types"): Permission.APPS_READ,
}
