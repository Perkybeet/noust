# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/map`` (:mod:`noust.web.api.map`).

The map is applications, their names, databases and scheduled work: what ``GET /api/apps`` and
``GET /api/cron`` already show to whoever reads applications, drawn as one picture. Applications and
databases are read through the caller's teams.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/map"): Permission.APPS_READ,
}
