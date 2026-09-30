# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/integrations`` (:mod:`noust.web.api.integrations`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/integrations/github"): Permission.APPS_READ,
    ("POST", "/api/integrations/github/manifest"): Permission.APPS_MANAGE,
    ("POST", "/api/integrations/github/manifest/conversions"): Permission.APPS_MANAGE,
    ("POST", "/api/integrations/github/installations"): Permission.APPS_MANAGE,
    ("POST", "/api/integrations/github/installations/sync"): Permission.APPS_MANAGE,
    ("GET", "/api/integrations/github/repositories"): Permission.APPS_READ,
    ("GET", "/api/integrations/github/repositories/{owner}/{repo}/branches"): Permission.APPS_READ,
    ("DELETE", "/api/integrations/github"): Permission.APPS_MANAGE,
}
