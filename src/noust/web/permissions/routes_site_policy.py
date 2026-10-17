# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of a site's policy (:mod:`noust.web.api.site_policy`).

A policy is configuration of one application's site, held to the same
permission as its domains. The ranges Cloudflare publishes are the server's.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/policy"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/policy"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/policy"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/http3"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/policy/users/{username}"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/policy/users/{username}"): Permission.APPS_MANAGE,
    ("GET", "/api/policy/cloudflare-ranges"): Permission.APPS_READ,
    ("GET", "/api/policy/forward-auth-presets"): Permission.APPS_READ,
    ("POST", "/api/policy/cloudflare-ranges/refresh"): Permission.APPS_MANAGE,
}
