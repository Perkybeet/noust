# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's build block (:mod:`noust.web.api.app_build`).

The commands of the operator's block run in the build sandbox on every
deployment and, for ``start``, as the application's unit with its secrets:
writing one is root-equivalent, and the handler also asks for sudo mode.
Clearing it only lets the repository's ``noust.yaml`` apply again, code anyone
who can push already controls.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/build"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/build"): Permission.ROOT_EQUIVALENT,
    ("DELETE", "/api/apps/{domain}/build"): Permission.APPS_MANAGE,
    # The cache holds nothing the repository did not give a build; emptying it
    # only makes the next build slower, so it is as much as deploying.
    ("GET", "/api/apps/{domain}/build-cache"): Permission.APPS_READ,
    ("DELETE", "/api/apps/{domain}/build-cache"): Permission.APPS_DEPLOY,
}
