# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/auth/saml``: signing in through a SAML 2.0 identity provider
(:mod:`noust.web.api.saml`).

Beginning a sign-in, the provider's answer and this server's metadata are public: the signed
response is the credential, and a guess at it is counted by the lockout
(``noust.web.server`` ``AUTH_PATHS``). Which providers there are, and testing one, is the
security officer's (``accounts.read`` and ``accounts.manage``, every change in sudo mode).
"""

from __future__ import annotations

from noust.web.permissions import PUBLIC, Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/auth/saml/start"): PUBLIC,
    ("POST", "/api/auth/saml/acs"): PUBLIC,
    ("GET", "/api/auth/saml/metadata/{provider}"): PUBLIC,
    ("GET", "/api/auth/saml/config"): Permission.ACCOUNTS_READ,
    ("PUT", "/api/auth/saml/config/{provider}"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/auth/saml/config/{provider}"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/auth/saml/config/{provider}/test"): Permission.ACCOUNTS_MANAGE,
}
