# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of DNS accounts and records (:mod:`noust.web.api.dns`).

An account is a credential that edits DNS and belongs to the server, so adding, changing
and removing one is managing the server (and the handlers ask for sudo mode when a
credential goes in or out). What an application's names point at, and making them point
here, is managing the application.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/dns/providers"): Permission.APPS_READ,
    ("POST", "/api/dns/providers"): Permission.SERVER_MANAGE,
    ("PATCH", "/api/dns/providers/{name}"): Permission.SERVER_MANAGE,
    ("POST", "/api/dns/providers/{name}/verify"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/dns/providers/{name}"): Permission.SERVER_MANAGE,
    ("GET", "/api/dns/records"): Permission.APPS_READ,
    ("POST", "/api/dns/sweep"): Permission.SERVER_MANAGE,
    ("GET", "/api/apps/{domain}/dns"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/dns"): Permission.APPS_MANAGE,
}
