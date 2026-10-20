# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of a team's notification channels (:mod:`noust.web.api.team_notifications`).

A team's channels are configuration of how it hears about its own applications, so they need
what an application's own settings need, in that team: ``apps.read`` to look (credentials are
never shown), ``apps.manage`` to change one, ``apps.operate`` to send a test through it. The
server's own channels stay ``settings.*`` (:mod:`noust.web.permissions.routes_notifications`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

_CHANNELS = "/api/teams/{team}/notifications/channels"

ROUTES: dict[tuple[str, str], str] = {
    ("GET", _CHANNELS): Permission.APPS_READ,
    ("PUT", _CHANNELS + "/{name}"): Permission.APPS_MANAGE,
    ("DELETE", _CHANNELS + "/{name}"): Permission.APPS_MANAGE,
    ("POST", _CHANNELS + "/{name}/test"): Permission.APPS_OPERATE,
}
