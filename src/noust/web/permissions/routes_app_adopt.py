# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permission of adopting a running Docker Compose stack (:mod:`noust.web.api.app_adopt`).

Adopting registers an application and installs its unit, as deploying one
does, so it is ``apps.manage``; the handler also asks for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/apps/adopt"): Permission.APPS_MANAGE,
}
