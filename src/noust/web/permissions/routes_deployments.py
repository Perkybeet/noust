# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of deployment history and its actions (:mod:`noust.web.api.deployments`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/deployments"): Permission.APPS_READ,
    ("GET", "/api/deployments/{deployment_id}"): Permission.APPS_READ,
    ("GET", "/api/deployments/{deployment_id}/log"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/deployments/{deployment_id}/rebuild"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/deployments/{deployment_id}/rollback"): Permission.APPS_DEPLOY,
}
