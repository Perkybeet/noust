# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's mounts (:mod:`noust.web.api.mounts`).

Listing them shows where things are, never what is in them. Opening a file to edit
it reveals what may be a secret (a token, a certificate), so it is the permission to
reveal secrets and sudo mode, like the environment's. Writing and removing are the
application's manager's with sudo mode; binding a path of the server into a
container is root-equivalent, which the handler asks for when that is what is
requested.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/mounts"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/mounts/content"): Permission.SECRETS_REVEAL,
    ("PUT", "/api/apps/{domain}/mounts"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/mounts"): Permission.APPS_MANAGE,
}
