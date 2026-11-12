# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permission of how an application runs (:mod:`noust.web.api.app_runs`).

Reading it is ``apps.read``: it is what the application's own page already says of it (its type and
how it is built), and the registry's password and the build secrets are nowhere in it.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/runs"): Permission.APPS_READ,
}
