# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/metrics`` and of an application's metrics status
(:mod:`noust.web.api.metrics`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/metrics"): Permission.SERVER_READ,
    ("GET", "/api/metrics/query"): Permission.SERVER_READ,
    ("GET", "/api/metrics/{metric}"): Permission.SERVER_READ,
    ("GET", "/api/apps/{domain}/metrics"): Permission.APPS_READ,
}
