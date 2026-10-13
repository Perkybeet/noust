# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the deploy calls a pipeline makes (:mod:`noust.web.api.app_deploy`).

Both are deployments, so ``apps.deploy``, the permission a ``deploy``-scope token
holds. The batch route is the instance's in :mod:`noust.web.permissions.scopes`:
it names several applications in its body, which a team-scoped token cannot be
held to one at a time.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/apps/{domain}/deploy"): Permission.APPS_DEPLOY,
    ("POST", "/api/deploy"): Permission.APPS_DEPLOY,
}
