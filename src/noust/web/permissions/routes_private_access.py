# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the private-network view of a site (:mod:`noust.web.api.private_access`).

Making an application private is its site's policy, held to the permission of the policy; the
certificate Tailscale issues joins the server's uploaded ones, so it is held to theirs.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/private-networks"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/private"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/private"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/private"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/{domain}/private/certificate"): Permission.APPS_MANAGE,
}
