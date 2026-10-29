# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/webserver`` and ``/api/apps/{domain}/webserver``
(:mod:`noust.web.api.webserver_move`).

Looking at which web server serves what, and what moving a site would do, writes nothing. Moving is
the one that writes: it puts a site, translated, where a web server reads it as root, and hands
ports 80 and 443 from one server to the other, so it is root-equivalent like saving a site's raw
configuration.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/webserver/status"): Permission.SETTINGS_READ,
    ("GET", "/api/webserver/migration/plan"): Permission.APPS_READ,
    ("POST", "/api/webserver/migrate"): Permission.ROOT_EQUIVALENT,
    ("GET", "/api/apps/{domain}/webserver/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/webserver/migrate"): Permission.ROOT_EQUIVALENT,
}
