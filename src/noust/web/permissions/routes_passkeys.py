# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/auth/passkeys`` (:mod:`noust.web.api.passkeys`).

A passkey is one's own, like a session or a second factor: ``self``, and the
handlers act on the caller's passkeys only. Signing in with one is public,
the assertion being the credential; both routes that verify one are in the
lockout's ``AUTH_PATHS``. Another person's passkeys are removed by a security
officer through ``reset-mfa`` (``accounts.manage``), never here.
"""

from __future__ import annotations

from noust.web.permissions import PUBLIC, Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/auth/passkeys"): Permission.SELF,
    ("POST", "/api/auth/passkeys/registration/options"): Permission.SELF,
    ("POST", "/api/auth/passkeys/registration"): Permission.SELF,
    ("PATCH", "/api/auth/passkeys/{passkey_id}"): Permission.SELF,
    ("DELETE", "/api/auth/passkeys/{passkey_id}"): Permission.SELF,
    ("POST", "/api/auth/passkeys/login/options"): PUBLIC,
    ("POST", "/api/auth/passkeys/login"): PUBLIC,
    ("POST", "/api/auth/passkeys/elevate/options"): Permission.SELF,
    ("POST", "/api/auth/passkeys/elevate"): Permission.SELF,
}
