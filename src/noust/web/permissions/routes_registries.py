# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the registry logins (:mod:`noust.web.api.registries`).

Reading says which accounts exist and as whom, never a password. Adding,
changing, testing and removing one is managing the applications that pull from
it and, because a credential travels in or goes away, the handlers also ask for
sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/registries"): Permission.APPS_READ,
    ("POST", "/api/registries"): Permission.APPS_MANAGE,
    ("GET", "/api/registries/{name}"): Permission.APPS_READ,
    ("PATCH", "/api/registries/{name}"): Permission.APPS_MANAGE,
    ("DELETE", "/api/registries/{name}"): Permission.APPS_MANAGE,
    ("POST", "/api/registries/{name}/test"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/registry-logins"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/registry-logins"): Permission.APPS_MANAGE,
}
