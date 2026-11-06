# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/auth/dashboards`` (:mod:`noust.web.api.dashboards`).

The panels of the console's Home are one's own, like the team one works in: ``self``, and the
handlers act on the caller's account only. They are not the server's, so no role gains or loses
them, and a credential with no account (the master token) is answered 400 by the handler.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/auth/dashboards"): Permission.SELF,
    ("POST", "/api/auth/dashboards/order"): Permission.SELF,
    ("PUT", "/api/auth/dashboards/{dashboard_id}"): Permission.SELF,
    ("DELETE", "/api/auth/dashboards/{dashboard_id}"): Permission.SELF,
}
