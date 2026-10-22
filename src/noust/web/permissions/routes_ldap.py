# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/auth/ldap``: signing in against a directory
(:mod:`noust.web.api.ldap`).

Signing in is public, the directory's answer to the person's name and password being the
credential: the route is in the lockout (``noust.web.server`` ``AUTH_PATHS``, counted per address
and name behind a shared address). Which directories there are, and testing one, is the security
officer's (``accounts.read`` and ``accounts.manage``, every change in sudo mode).
"""

from __future__ import annotations

from noust.web.permissions import PUBLIC, Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/ldap/login"): PUBLIC,
    ("GET", "/api/auth/ldap/directories"): Permission.ACCOUNTS_READ,
    ("PUT", "/api/auth/ldap/directories/{name}"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/auth/ldap/directories/{name}"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/auth/ldap/directories/{name}/test"): Permission.ACCOUNTS_MANAGE,
}
