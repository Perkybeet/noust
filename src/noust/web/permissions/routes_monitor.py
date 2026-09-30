# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/monitor`` (:mod:`noust.web.api.monitor`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/monitor/status"): Permission.SERVER_READ,
    ("GET", "/api/monitor/config"): Permission.SERVER_READ,
    ("GET", "/api/monitor/metrics"): Permission.SERVER_READ,
    ("GET", "/api/monitor/processes"): Permission.SERVER_READ,
    ("GET", "/api/monitor/observations"): Permission.SERVER_READ,
    ("POST", "/api/monitor/scan"): Permission.APPS_OPERATE,
    ("POST", "/api/monitor/observations/{observation_id}/acknowledge"): Permission.APPS_OPERATE,
    ("POST", "/api/monitor/test-email"): Permission.APPS_OPERATE,
    ("POST", "/api/monitor/install"): Permission.SERVER_MANAGE,
    ("POST", "/api/monitor/uninstall"): Permission.SERVER_MANAGE,
    ("POST", "/api/monitor/enable"): Permission.SERVER_MANAGE,
    ("POST", "/api/monitor/disable"): Permission.SERVER_MANAGE,
    ("POST", "/api/monitor/start"): Permission.SERVER_MANAGE,
    ("POST", "/api/monitor/stop"): Permission.SERVER_MANAGE,
}
