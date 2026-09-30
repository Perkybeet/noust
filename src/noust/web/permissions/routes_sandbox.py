# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's build sandbox (:mod:`noust.web.api.sandbox`).

Turning the sandbox off and allowing a compose stack privileged containers
both hand root to code from the repository: they are root-equivalent, and the
handlers also ask for sudo mode. A trial build runs the repository's code, in
the sandbox, like a deployment does.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/sandbox"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/sandbox/test"): Permission.APPS_DEPLOY,
    ("POST", "/api/apps/{domain}/sandbox/enable"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/{domain}/sandbox/disable"): Permission.ROOT_EQUIVALENT,
    ("PUT", "/api/apps/{domain}/sandbox/compose-exception"): Permission.ROOT_EQUIVALENT,
    ("DELETE", "/api/apps/{domain}/sandbox/compose-exception"): Permission.APPS_MANAGE,
}
