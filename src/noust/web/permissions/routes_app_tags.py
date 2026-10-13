# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of tags on applications (:mod:`noust.web.api.app_tags`).

Reading tags is ``apps.read``; setting them is ``apps.manage`` (they decide which
applications a ``deploy`` by tag reaches); deploying a tag is ``apps.deploy``,
which is what a pipeline's token holds to call it.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/tags"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/tags"): Permission.APPS_MANAGE,
    ("GET", "/api/tags"): Permission.APPS_READ,
    ("POST", "/api/tags/{tag}/deploy"): Permission.APPS_DEPLOY,
}
