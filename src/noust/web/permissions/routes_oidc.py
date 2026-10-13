# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/auth/oidc``: single sign-in through an identity
provider (:mod:`noust.web.api.oidc`).

Signing in is public, the provider's answer being the credential: the three
routes of the flow are in the lockout (``/callback`` and ``/complete`` in
``noust.web.server`` ``AUTH_GET_PATHS`` and ``AUTH_PATHS``). Which providers
there are, and who is linked to whom, is the security officer's
(``accounts.read`` and ``accounts.manage``, every change in sudo mode).
"""

from __future__ import annotations

from noust.web.permissions import PUBLIC, Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/auth/oidc/providers"): PUBLIC,
    ("GET", "/api/auth/oidc/start"): PUBLIC,
    ("GET", "/api/auth/oidc/callback"): PUBLIC,
    ("POST", "/api/auth/oidc/complete"): PUBLIC,
    ("GET", "/api/auth/oidc/config"): Permission.ACCOUNTS_READ,
    ("PUT", "/api/auth/oidc/config/{provider}"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/auth/oidc/config/{provider}"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/auth/oidc/config/{provider}/test"): Permission.ACCOUNTS_MANAGE,
    ("GET", "/api/auth/oidc/local-login"): Permission.ACCOUNTS_READ,
    ("PUT", "/api/auth/oidc/local-login"): Permission.ACCOUNTS_MANAGE,
    ("GET", "/api/auth/oidc/identities"): Permission.ACCOUNTS_READ,
    ("POST", "/api/auth/oidc/identities"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/auth/oidc/identities/{identity_id}"): Permission.ACCOUNTS_MANAGE,
}
