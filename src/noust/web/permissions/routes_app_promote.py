# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of promoting a release (:mod:`noust.web.api.app_promote`).

Reading where a release can go is ``apps.read``. Promoting one deploys code to
another application, so it is ``apps.deploy``, in the team both applications
belong to. Cloning an application creates one, so it is ``apps.manage``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/promotion"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/promote"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/clone"): Permission.APPS_MANAGE,
}
