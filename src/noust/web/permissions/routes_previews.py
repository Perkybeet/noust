# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's previews (:mod:`noust.web.api.previews`).

Reading previews, their pull requests and their logs is ``apps.read``. Turning
them on or off, defining the variables they are given and removing one is
``apps.manage``. Making one by hand and building one again deploy code, so they
are ``apps.deploy``; stopping and starting one is ``apps.operate``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/previews"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/previews"): Permission.APPS_DEPLOY,
    ("GET", "/api/apps/{domain}/previews/dns"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/previews/settings"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/previews/settings"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/previews/pull-requests"): Permission.APPS_READ,
    ("GET", "/api/apps/{domain}/previews/variables"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/previews/variables/{name}"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/previews/variables/{name}"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/previews/{number}"): Permission.APPS_READ,
    ("DELETE", "/api/apps/{domain}/previews/{number}"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/{domain}/previews/{number}/redeploy"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/previews/{number}/approve"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/previews/{number}/reject"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/previews/{number}/stop"): Permission.APPS_OPERATE,
    ("POST", "/api/apps/{domain}/previews/{number}/start"): Permission.APPS_OPERATE,
    ("GET", "/api/apps/{domain}/previews/{number}/logs"): Permission.APPS_READ,
}
