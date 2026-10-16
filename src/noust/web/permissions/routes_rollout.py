# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's delivery settings and of freezing deploys
(:mod:`noust.web.api.app_rollout`, :mod:`noust.web.api.deploy_freezes`).

Reading them is ``apps.read``. Changing how long an application keeps its history
and how its unit is stopped is configuring it: ``apps.manage``. Holding and
releasing what deploys it by itself, and freezing deploys, are decisions about
when code goes to production, which an operator takes in an incident: ``apps.deploy``.
Freezing or lifting the whole server's is also held to the instance's scope by the
handler. Building a release without serving it, and making it serve
(:mod:`noust.web.api.app_staged`), are deploys: ``apps.deploy``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/rollout"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/rollout"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/{domain}/rollout/pause"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/rollout/resume"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/releases/stage"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/releases/{release_id}/promote"): Permission.APPS_DEPLOY,
    ("GET", "/api/deploy-freezes"): Permission.APPS_READ,
    ("POST", "/api/deploy-freezes"): Permission.APPS_DEPLOY,
    ("DELETE", "/api/deploy-freezes/{freeze_id}"): Permission.APPS_DEPLOY,
}
