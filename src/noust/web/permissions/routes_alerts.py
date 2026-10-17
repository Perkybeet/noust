# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/alerts`` (:mod:`noust.web.api.alerts`).

Alert rules are what the monitor watches, so they follow the monitor's own
permissions: ``server.read`` to see them, ``server.manage`` to change them. Taking
an alert that is firing is what whoever is on call does, so it asks for what
restarting an application does (``apps.operate``), not for the right to change rules.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/alerts/rules"): Permission.SERVER_READ,
    ("PUT", "/api/alerts/rules/{name}"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/alerts/rules/{name}"): Permission.SERVER_MANAGE,
    ("POST", "/api/alerts/rules/{name}/enable"): Permission.SERVER_MANAGE,
    ("POST", "/api/alerts/rules/{name}/disable"): Permission.SERVER_MANAGE,
    ("POST", "/api/alerts/rules/{name}/acknowledge"): Permission.APPS_OPERATE,
    ("DELETE", "/api/alerts/rules/{name}/acknowledge"): Permission.APPS_OPERATE,
}
