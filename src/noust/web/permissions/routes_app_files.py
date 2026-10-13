# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's file browser (:mod:`noust.web.api.app_files`).

It acts as the application's own account on files that may hold its secrets: what
a shell as the application is, so it asks for the same permission, an
administrator's, for every route. Anything that reads or changes a file's content
also asks for sudo mode, in the handler.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/files"): Permission.APPS_TERMINAL,
    ("GET", "/api/apps/{domain}/files/content"): Permission.APPS_TERMINAL,
    ("GET", "/api/apps/{domain}/files/download"): Permission.APPS_TERMINAL,
    ("PUT", "/api/apps/{domain}/files/content"): Permission.APPS_TERMINAL,
    ("PUT", "/api/apps/{domain}/files/upload"): Permission.APPS_TERMINAL,
    ("POST", "/api/apps/{domain}/files/directory"): Permission.APPS_TERMINAL,
    ("POST", "/api/apps/{domain}/files/move"): Permission.APPS_TERMINAL,
    ("DELETE", "/api/apps/{domain}/files"): Permission.APPS_TERMINAL,
}
