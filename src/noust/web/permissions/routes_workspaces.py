# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the workspaces of a monorepo (:mod:`noust.web.api.workspaces`).

Reading a repository's workspaces fetches a few files of it, as inspecting a repository does; creating
an application per workspace is creating applications, as importing one is.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/workspaces/inspect"): Permission.APPS_MANAGE,
    ("POST", "/api/workspaces/deploy"): Permission.APPS_MANAGE,
}
