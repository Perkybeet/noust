# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the web application firewall (:mod:`noust.web.api.waf`).

Turning it on for a site is the site's policy, held to the permission of the policy; installing the
packages runs the package manager as root and is the server's.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/waf"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/waf/events"): Permission.APPS_READ,
    ("POST", "/api/waf/install"): Permission.SERVER_MANAGE,
}
