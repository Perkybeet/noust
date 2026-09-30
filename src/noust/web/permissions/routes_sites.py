# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/sites`` (:mod:`noust.web.api.sites`).

Raw site configuration is root-equivalent: an nginx or Apache directive can
read any file on the machine or proxy to any local socket.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/sites"): Permission.APPS_READ,
    ("GET", "/api/sites/templates"): Permission.APPS_READ,
    ("POST", "/api/sites"): Permission.APPS_MANAGE,
    ("POST", "/api/sites/reload"): Permission.APPS_OPERATE,
    ("GET", "/api/sites/{domain}"): Permission.APPS_READ,
    ("GET", "/api/sites/{domain}/config"): Permission.APPS_READ,
    ("POST", "/api/sites/{domain}/config/test"): Permission.APPS_OPERATE,
    ("PUT", "/api/sites/{domain}/config"): Permission.ROOT_EQUIVALENT,
    ("POST", "/api/sites/{domain}/enable"): Permission.APPS_MANAGE,
    ("POST", "/api/sites/{domain}/disable"): Permission.APPS_MANAGE,
    ("DELETE", "/api/sites/{domain}"): Permission.APPS_MANAGE,
}
