# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of building a release here and running it elsewhere
(:mod:`noust.web.api.app_release_bundles`).

A bundle is a built tree, which may hold what the build baked in, and receiving one switches an
application to code it did not build: every route that moves one is a deploy, ``apps.deploy``,
reading the list of what waits included. A role that only reads sees applications, not their builds.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/apps/{domain}/releases/bundle"): Permission.APPS_DEPLOY,
    ("GET", "/api/apps/{domain}/releases/bundles"): Permission.APPS_DEPLOY,
    ("GET", "/api/apps/{domain}/releases/bundles/{bundle_id}"): Permission.APPS_DEPLOY,
    ("GET", "/api/apps/{domain}/releases/bundles/{bundle_id}/data"): Permission.APPS_DEPLOY,
    ("DELETE", "/api/apps/{domain}/releases/bundles/{bundle_id}"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/releases/uploads"): Permission.APPS_DEPLOY,
    ("GET", "/api/apps/{domain}/releases/uploads/{upload_id}"): Permission.APPS_DEPLOY,
    ("PUT", "/api/apps/{domain}/releases/uploads/{upload_id}"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/releases/uploads/{upload_id}/complete"): Permission.APPS_DEPLOY,
    ("DELETE", "/api/apps/{domain}/releases/uploads/{upload_id}"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/releases/receive"): Permission.APPS_DEPLOY,
}
