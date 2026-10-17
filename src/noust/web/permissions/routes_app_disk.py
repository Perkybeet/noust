# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of what an application takes of the disk (:mod:`noust.web.api.app_disk`).

Seeing it is reading an application. Measuring walks the application's trees on the machine's
spare time, which is the same standing as a restart, and the limit holds a deploy back, so
setting it is managing the application.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/disk"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/disk/measure"): Permission.APPS_OPERATE,
    ("PUT", "/api/apps/{domain}/disk"): Permission.APPS_MANAGE,
    # Writes a project id on every file of the tree and a limit on the filesystem: the manager's,
    # and the handler asks for sudo mode.
    ("PUT", "/api/apps/{domain}/disk/kernel"): Permission.APPS_MANAGE,
}
