# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of a Compose stack's settings (:mod:`noust.web.api.app_compose`).

Reading says which file, profiles and arguments a stack runs with. Changing
them is managing the application and, because the arguments run as root
(allowlisted, never a shell line) and a new file is written into the unit, the
handlers also ask for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/compose"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/compose/variables"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/compose/document"): Permission.SECRETS_REVEAL,
    ("POST", "/api/apps/{domain}/compose/document/check"): Permission.APPS_MANAGE,
    ("PUT", "/api/apps/{domain}/compose/document"): Permission.APPS_MANAGE,
    ("PUT", "/api/apps/{domain}/compose"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/compose"): Permission.APPS_MANAGE,
}
