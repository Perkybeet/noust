# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permission of an application's address (:mod:`noust.web.api.app_address`).

Where an application is reached is what seeing the application is: ``apps.read``. It carries no secret.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/address"): Permission.APPS_READ,
}
