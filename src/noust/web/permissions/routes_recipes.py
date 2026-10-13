# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/recipes`` (:mod:`noust.web.api.recipes`).

Reading the catalogue needs what reading an application needs. Installing a
recipe is deploying an application, so it needs what ``POST /api/apps`` needs;
updating one is an update, which is a deployment.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/recipes"): Permission.APPS_READ,
    ("GET", "/api/recipes/categories"): Permission.APPS_READ,
    ("GET", "/api/recipes/installed"): Permission.APPS_READ,
    ("GET", "/api/recipes/installed/{domain}"): Permission.APPS_READ,
    ("POST", "/api/recipes/installed/{domain}/update"): Permission.APPS_DEPLOY,
    # A package of recipes decides what can be deployed from this server.
    ("GET", "/api/recipes/catalog"): Permission.APPS_READ,
    ("POST", "/api/recipes/catalog/fetch"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/recipes/catalog"): Permission.SERVER_MANAGE,
    # The server's own recipes decide what can be deployed from this server.
    ("POST", "/api/recipes/private/check"): Permission.SERVER_MANAGE,
    ("POST", "/api/recipes/private"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/recipes/private/{name}"): Permission.SERVER_MANAGE,
    ("GET", "/api/recipes/{name}"): Permission.APPS_READ,
    ("GET", "/api/recipes/{name}/logo"): Permission.APPS_READ,
    ("GET", "/api/recipes/{name}/requirements"): Permission.APPS_READ,
    ("POST", "/api/recipes/{name}/install"): Permission.APPS_MANAGE,
}
