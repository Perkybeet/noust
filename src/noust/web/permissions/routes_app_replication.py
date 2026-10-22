# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permission of asking whether an application could run on several servers
(:mod:`noust.web.api.app_replication`): reading it. It names the variables and paths that stop it
and never a value.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/replication"): Permission.APPS_READ,
}
