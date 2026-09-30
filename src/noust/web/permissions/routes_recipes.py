# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/recipes`` (:mod:`noust.web.api.recipes`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/recipes"): Permission.APPS_READ,
    ("GET", "/api/recipes/{name}"): Permission.APPS_READ,
}
