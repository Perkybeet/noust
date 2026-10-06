# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of a container application's settings (:mod:`noust.web.api.app_container`).

Reading them is ``apps.read``: the registry's password and the build secrets
never leave the secret store. Changing them is ``apps.manage``, like the
environment and the limits: they decide what image is built or pulled the next
time the application is deployed, and the change is audited.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/container"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/container"): Permission.APPS_MANAGE,
}
