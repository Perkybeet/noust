# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's deploy hooks (:mod:`noust.web.api.app_hooks`).

A hook of the operator's is code that runs with the application's identity
and its secrets on every deployment: writing one is root-equivalent, and the
handler also asks for sudo mode. Clearing it only lets the repository's
``noust.yaml`` apply again, code anyone who can push already controls.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/hooks"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/hooks"): Permission.ROOT_EQUIVALENT,
    ("DELETE", "/api/apps/{domain}/hooks"): Permission.APPS_MANAGE,
}
