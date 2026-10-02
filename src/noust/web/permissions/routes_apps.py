# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/apps`` (:mod:`noust.web.api.apps`).

Reading an application's ``.env`` is ``apps.read`` because the values come
back redacted; asking for them in clear also needs an admin credential and
sudo mode, checked in the handler that produces them.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps"): Permission.APPS_READ,
    ("POST", "/api/apps"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/inspect"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/types"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/start"): Permission.APPS_OPERATE,
    ("POST", "/api/apps/{domain}/stop"): Permission.APPS_OPERATE,
    ("POST", "/api/apps/{domain}/restart"): Permission.APPS_OPERATE,
    ("GET", "/api/apps/{domain}/logs"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/env"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/env"): Permission.APPS_MANAGE,
    ("PUT", "/api/apps/{domain}/env/marks"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/rollback-points"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/releases"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/releases/{release_id}/activate"): Permission.APPS_DEPLOY,
    ("GET", "/api/apps/{domain}/migrate/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/migrate"): Permission.APPS_MANAGE,
    ("PATCH", "/api/apps/{domain}/limits"): Permission.APPS_MANAGE,
    ("PATCH", "/api/apps/{domain}/health"): Permission.APPS_MANAGE,
    ("PATCH", "/api/apps/{domain}/branch"): Permission.APPS_MANAGE,
    ("PATCH", "/api/apps/{domain}/follow-tags"): Permission.APPS_MANAGE,
    ("PATCH", "/api/apps/{domain}/releases/retention"): Permission.APPS_MANAGE,
}
