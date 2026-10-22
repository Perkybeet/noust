# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's https credential (:mod:`noust.web.api.git_credentials`).

Reading says whether one exists and as whom, never the token. Writing and
removing one is managing the application and, because a secret travels in or a
credential goes away, also asks for sudo mode in the handler.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/git-credential"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/git-credential"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/git-credential"): Permission.APPS_MANAGE,
}
