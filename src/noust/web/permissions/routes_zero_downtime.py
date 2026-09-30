# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's zero-downtime setting (:mod:`noust.web.api.zero_downtime`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/zero-downtime"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/zero-downtime"): Permission.APPS_MANAGE,
}
