# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's unit sandbox (:mod:`noust.web.api.hardening`).

Changing it restarts the application in another sandbox and widens or narrows
what its process may touch, so it is the application's manager's, and the
handler also asks for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/hardening"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/hardening"): Permission.APPS_MANAGE,
}
