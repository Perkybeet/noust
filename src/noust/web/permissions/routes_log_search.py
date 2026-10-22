# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permission of ``/api/logs/search`` (:mod:`noust.web.api.log_search`).

It reads application logs, so it is ``apps.read`` like reading one application's
logs; which applications it reads is the caller's own (their teams), and a system
unit can be asked for only by a caller who sees the whole server. How long the journal
keeps them is the server's, so it is ``server.read`` and ``server.manage``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/logs/search"): Permission.APPS_READ,
    ("GET", "/api/logs/retention"): Permission.SERVER_READ,
    ("PUT", "/api/logs/retention"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/logs/retention"): Permission.SERVER_MANAGE,
}
