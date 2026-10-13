# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of where certificates come from (:mod:`noust.web.api.cert_settings`).

Which authority orders is configuration of the server; an uploaded certificate is managed
like the certificates Noust orders, and its key travels in under sudo mode. An
application's own choice is managing the application.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/certs/acme"): Permission.APPS_READ,
    ("PUT", "/api/certs/acme"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/certs/acme/register"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/certs/uploaded"): Permission.APPS_READ,
    ("POST", "/api/certs/uploaded"): Permission.APPS_MANAGE,
    ("DELETE", "/api/certs/uploaded/{name}"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/certificate"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/certificate"): Permission.APPS_MANAGE,
}
