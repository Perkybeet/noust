# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/central`` (:mod:`noust.web.api.central`).

Unlocking a sealed central is custody of its passphrase, a security duty.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/central/unlock"): Permission.SECURITY_MANAGE,
}
