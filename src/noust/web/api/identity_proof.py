# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A recent proof of who is asking, before an account sets up its first second factor (item 132).

An account without a second factor may enrol one from the session it signed in with: until it has
one it can do nothing else. That was a way in for the cookie of an old session, which could bind an
authenticator of its own and then confirm sudo mode with it. The first factor is the one thing a
session cannot vouch for by being a session, so it asks for a proof of its own: the sign-in is at
most :data:`FRESH_SIGN_IN_SECONDS` old (by password, or through an identity provider, either way
somebody proved who they are), or the account's password is given with the request. An account
with no password signs in again.

Both ways of setting a factor up pass through :func:`ensure_first_factor_proof` (rule 4: the guard
is at the point they share, not in the dialog that asks for it): the authenticator's enrolment
and the options of a first passkey's registration. An account that already has a factor is not
asked: the sign-in that got it here asked for that one.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from noust.core.accounts import Account, AuthenticationFailed
from noust.web.auth import audit_event, get_client_ip, record_auth_failure, signed_in_within
from noust.web.server import get_token_manager

#: How recent a sign-in must be to stand for a proof of identity: ten minutes.
FRESH_SIGN_IN_SECONDS = 10 * 60


def _refusal(error: str, detail: str, hint: str) -> HTTPException:
    """
    Args:
        error: The code the console branches on.
        detail: What is wrong.
        hint: What to do about it.

    Returns:
        The 403 in the API's one error shape.
    """
    return HTTPException(
        status_code=403,
        detail={"error": error, "detail": detail, "hint": hint, "fields": None},
    )


def ensure_first_factor_proof(
    request: Request, session: dict[str, Any], account: Account, password: str | None
) -> None:
    """
    Refuse to set up an account's first second factor without a recent proof of who is asking.

    Args:
        request: The request, for the address a wrong password is counted against.
        session: The authenticated payload.
        account: The account the factor is for.
        password: The account's password, when the caller gave it with the request.

    Raises:
        HTTPException: 403 ``identity_proof_required`` when the sign-in is old and the account has
            a password to confirm with; 403 ``fresh_sign_in_required`` when it has none.
        AuthenticationFailed: The password given was wrong: counted by the account's lockout and
            the address's, as a wrong password at sign-in is.
    """
    if account.has_mfa:
        return
    # A browser session whose sign-in is recent. A token that acts for the account never is: it
    # presents no sign-in at all, and the factor is a person's.
    if signed_in_within(session, FRESH_SIGN_IN_SECONDS):
        return
    if not account.has_password:
        raise _refusal(
            "fresh_sign_in_required",
            "This session signed in too long ago to set up a second factor, and the account "
            "has no password to confirm it with",
            "Sign out and sign in again through your identity provider, then set the factor up "
            "within ten minutes.",
        )
    if not password:
        raise _refusal(
            "identity_proof_required",
            "Confirm your password to set up a second factor",
            "This session signed in more than ten minutes ago. Give the account's password with "
            "the request, or sign in again.",
        )
    client_ip = get_client_ip(request)
    accounts = get_token_manager().accounts
    if not accounts.verify_password(account.id, password, client_ip=client_ip):
        record_auth_failure(client_ip, request.url.path, "password")
        audit_event(
            "auth.2fa.enroll",
            "failure",
            client_ip=client_ip,
            session=session,
            target=request.url.path,
            detail="the password given to set up a second factor did not match",
        )
        raise AuthenticationFailed("bad_password", account.id)
