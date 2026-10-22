# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's client certificates (:mod:`noust.web.api.client_certs`).

Who may open a site is its policy, held to the permission of the policy; making a certificate hands
out a private key and revoking one renders the site again, so both ask for sudo mode in the handler.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/client-certs"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/client-certs"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/client-certs/{serial}"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/client-certs/ca"): Permission.APPS_READ,
}
