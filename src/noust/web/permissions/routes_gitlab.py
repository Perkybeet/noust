# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the GitLab connections (:mod:`noust.web.api.gitlab`).

Connections and OAuth applications are credentials of the server: adding, signing in and removing
them is managing applications and the handlers also ask for sudo mode. Listing what a connection can
read is reading applications, and checking one against GitLab is as much as deploying.
"""

from __future__ import annotations

from noust.web.permissions import Permission

PREFIX = "/api/integrations/gitlab"

ROUTES: dict[tuple[str, str], str] = {
    ("GET", PREFIX): Permission.APPS_READ,
    ("POST", f"{PREFIX}/connections"): Permission.APPS_MANAGE,
    ("POST", f"{PREFIX}/connections/{{name}}/test"): Permission.APPS_DEPLOY,
    ("DELETE", f"{PREFIX}/connections/{{name}}"): Permission.APPS_MANAGE,
    ("GET", f"{PREFIX}/connections/{{name}}/groups"): Permission.APPS_READ,
    ("GET", f"{PREFIX}/connections/{{name}}/projects"): Permission.APPS_READ,
    ("GET", f"{PREFIX}/connections/{{name}}/projects/{{project}}/branches"): Permission.APPS_READ,
    ("PUT", f"{PREFIX}/oauth-apps"): Permission.APPS_MANAGE,
    ("DELETE", f"{PREFIX}/oauth-apps/{{host}}"): Permission.APPS_MANAGE,
    ("POST", f"{PREFIX}/oauth/authorize"): Permission.APPS_MANAGE,
    ("POST", f"{PREFIX}/oauth/callback"): Permission.APPS_MANAGE,
}
