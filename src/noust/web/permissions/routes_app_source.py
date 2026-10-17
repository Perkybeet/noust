# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's source options (:mod:`noust.web.api.app_source`).

Changing how a repository is fetched or which pushes deploy it is configuring
the application, not running anything of the repository's: ``apps.manage``,
without sudo mode. Reading them is ``apps.read``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/source-options"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/source-options"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/source-options"): Permission.APPS_MANAGE,
    # Changing where the code comes from is configuring the application, like pinning a branch:
    # the plan asks the remote and writes nothing, the change needs sudo mode.
    ("POST", "/api/apps/{domain}/source/plan"): Permission.APPS_MANAGE,
    ("PUT", "/api/apps/{domain}/source"): Permission.APPS_MANAGE,
}
