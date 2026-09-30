# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/certs`` (:mod:`noust.web.api.certs`).

Renewing is operating what exists; issuing, revoking and deleting change it.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/certs"): Permission.APPS_READ,
    ("POST", "/api/certs/renew-all"): Permission.APPS_OPERATE,
    ("GET", "/api/certs/{domain}"): Permission.APPS_READ,
    ("POST", "/api/certs/{domain}"): Permission.APPS_MANAGE,
    ("POST", "/api/certs/{domain}/renew"): Permission.APPS_OPERATE,
    ("POST", "/api/certs/{domain}/revoke"): Permission.APPS_MANAGE,
    ("DELETE", "/api/certs/{domain}"): Permission.APPS_MANAGE,
}
