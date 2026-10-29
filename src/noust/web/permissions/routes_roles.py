# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the server's roles (:mod:`noust.web.api.roles`).

Reading the list, and what the frameworks make of it, is ``accounts.read``: the screen of who may
do what. Defining a role, changing one, putting one back and taking or leaving what a newer
release offers it are the security role's decision (``accounts.manage``), in sudo mode, as giving a
role to an account is.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/roles"): Permission.ACCOUNTS_READ,
    ("POST", "/api/roles"): Permission.ACCOUNTS_MANAGE,
    ("GET", "/api/roles/review"): Permission.ACCOUNTS_READ,
    ("GET", "/api/roles/{name}"): Permission.ACCOUNTS_READ,
    ("PATCH", "/api/roles/{name}"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/roles/{name}"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/roles/{name}/duplicate"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/roles/{name}/reset"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/roles/{name}/offers"): Permission.ACCOUNTS_MANAGE,
}
