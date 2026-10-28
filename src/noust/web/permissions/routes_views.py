# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/views`` (:mod:`noust.web.api.views`).

A view is the page of a resource across the servers, so it asks for what that page asks for on one
server: reading the applications, the databases, the audit trail. Asking the other servers takes
the fleet permission too, but that is not what decides whether the page is shown: a credential
without it gets the rows of this central, and each server still applies its own map to the role
the central forwards.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/views/apps"): Permission.APPS_READ,
    ("GET", "/api/views/databases"): Permission.DATABASES_READ,
    ("GET", "/api/views/sites"): Permission.APPS_READ,
    ("GET", "/api/views/certificates"): Permission.APPS_READ,
    ("GET", "/api/views/backups"): Permission.BACKUPS_READ,
    ("GET", "/api/views/cron"): Permission.APPS_READ,
    ("GET", "/api/views/logs"): Permission.APPS_READ,
    ("GET", "/api/views/alerts"): Permission.SERVER_READ,
    ("GET", "/api/views/activity"): Permission.AUDIT_READ,
    ("GET", "/api/views/search"): Permission.APPS_READ,
}
