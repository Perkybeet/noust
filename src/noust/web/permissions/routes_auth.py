# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/auth``: sign-in, sessions, second factor, tokens and accounts
(:mod:`noust.web.api.auth`, :mod:`noust.web.api.auth_accounts`).

What is one's own - session, factor, password, tokens, notice - needs only
``self``; the handlers narrow a list or a revocation to the caller's own unless
they also hold ``accounts.manage``.
"""

from __future__ import annotations

from noust.web.permissions import PUBLIC, Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/login"): PUBLIC,
    ("POST", "/api/auth/login/second-factor"): PUBLIC,
    ("GET", "/api/auth/session"): PUBLIC,
    ("POST", "/api/auth/invitations/open"): PUBLIC,
    ("POST", "/api/auth/invitations/accept"): PUBLIC,
    ("POST", "/api/auth/elevate"): Permission.SELF,
    ("POST", "/api/auth/logout"): Permission.SELF,
    ("GET", "/api/auth/verify"): Permission.SELF,
    ("POST", "/api/auth/ws-ticket"): Permission.SELF,
    ("GET", "/api/auth/sessions"): Permission.SELF,
    ("POST", "/api/auth/sessions/revoke-all"): Permission.SELF,
    ("POST", "/api/auth/sessions/revoke-others"): Permission.SELF,
    ("DELETE", "/api/auth/sessions/{sid_prefix}"): Permission.SELF,
    ("GET", "/api/auth/2fa"): Permission.SELF,
    ("POST", "/api/auth/2fa/enroll"): Permission.SELF,
    ("POST", "/api/auth/2fa/confirm"): Permission.SELF,
    ("POST", "/api/auth/2fa/disable"): Permission.SELF,
    ("POST", "/api/auth/2fa/backup-codes"): Permission.SELF,
    ("GET", "/api/auth/tokens"): Permission.SELF,
    ("POST", "/api/auth/tokens"): Permission.SELF,
    ("DELETE", "/api/auth/tokens/{token_id}"): Permission.SELF,
    ("POST", "/api/auth/fleet/revoke"): Permission.SELF,
    ("POST", "/api/auth/password"): Permission.SELF,
    ("POST", "/api/auth/notice/accept"): Permission.SELF,
    ("GET", "/api/auth/roles"): Permission.SELF,
    ("GET", "/api/auth/accounts"): Permission.ACCOUNTS_READ,
    ("POST", "/api/auth/accounts"): Permission.ACCOUNTS_MANAGE,
    ("GET", "/api/auth/accounts/{username}"): Permission.ACCOUNTS_READ,
    ("PATCH", "/api/auth/accounts/{username}"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/auth/accounts/{username}"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/auth/accounts/{username}/disable"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/auth/accounts/{username}/enable"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/auth/accounts/{username}/unlock"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/auth/accounts/{username}/reset-mfa"): Permission.ACCOUNTS_MANAGE,
    ("POST", "/api/auth/invitations"): Permission.ACCOUNTS_MANAGE,
    ("GET", "/api/auth/exceptions"): Permission.ACCOUNTS_READ,
    ("POST", "/api/auth/exceptions"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/auth/exceptions/{exception_id}"): Permission.ACCOUNTS_MANAGE,
}
