# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of a Docker Compose stack's settings (:mod:`noust.web.api.app_stack`).

Both changes are managing the application, like its branch or its limits;
sudo mode is asked in the handlers.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("PATCH", "/api/apps/{domain}/backup-before-update"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/headless"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/headless"): Permission.APPS_MANAGE,
    # Starting a unit Noust already wrote, like POST .../start: operating
    # the application, not changing how it is set up.
    ("POST", "/api/apps/{domain}/reclaim"): Permission.APPS_OPERATE,
}
