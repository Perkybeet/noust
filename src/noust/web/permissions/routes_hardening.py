# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's unit sandbox and tuning (:mod:`noust.web.api.hardening`,
:mod:`noust.web.api.tuning`).

Changing it restarts the application in another sandbox and widens or narrows
what its process may touch, so it is the application's manager's, and the
handler also asks for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/hardening"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/hardening"): Permission.APPS_MANAGE,
    # What the unit may take and keep beyond its limits (B-57, B-60): the same standing.
    ("GET", "/api/apps/{domain}/tuning"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/tuning"): Permission.APPS_MANAGE,
}
