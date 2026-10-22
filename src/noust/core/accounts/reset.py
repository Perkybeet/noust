# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A password reset by mail: a link that is good once, for an hour, with the second factor.

A person who forgot their password asks for a link (:meth:`PasswordResets.request`),
which is mailed to the address of their account and nowhere else. Following it and
choosing a new password (:meth:`PasswordResets.redeem`) needs the link **and** a code
of the account's own second factor, so an inbox alone is never an account: the reset
changes the password and leaves the authenticator as it was, which an invitation does
not (an invitation is how a security officer lets someone in again, and replaces both).

What keeps it from being a way around the rest:

- a link is issued only to an active account that has a password, an address and a
  second factor; any other request is answered like one that worked (the caller
  never learns which accounts exist), and nothing is issued for it;
- only the SHA-256 of the code is stored, a new request spends the ones before it, an
  account is sent at most :data:`MAX_PER_HOUR` links an hour, and a link lives
  :data:`LIFETIME_MINUTES` minutes;
- a wrong second factor counts as a failed sign-in (it can lock the account), and the
  link survives a typo, a password the policy refuses, and nothing else.

The caller (:mod:`noust.web.api.auth_accounts`) decides whether the feature is on at
all: there must be a way to send mail and a public address for the link, local
sign-in must exist, and the ENS profile recovers accounts through a security officer.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from noust.core.accounts.manager import AccountManager
from noust.core.accounts.model import (
    Account,
    AccountError,
    AuthenticationFailed,
    validate_username,
)
from noust.core.exceptions import ValidationError

#: What every reset code starts with, so a stray one is recognised for what it is.
RESET_PREFIX = "noust_reset_"

#: How long a link is good.
LIFETIME_MINUTES = 60

#: The most links one account is sent in an hour.
MAX_PER_HOUR = 3

#: The purpose a second-factor code is spent on, so it is not also good for signing in.
PURPOSE = "reset"


class ResetRefused(AccountError):
    """
    A link was not valid: unknown, spent, expired, or its account can no longer use it.

    One answer for all of them, like an invitation's, so a guesser learns nothing.
    """

    def __init__(self) -> None:
        super().__init__(
            "This link is not valid: it may have expired or been used.",
            details="Ask for a new one from the sign-in page.",
        )


@dataclass(frozen=True)
class IssuedReset:
    """
    A link to mail.

    Attributes:
        account: Whose it is.
        address: Where to send it.
        code: The code the link carries, in clear, exactly once.
        expires_at: When it stops working, UNIX seconds.
    """

    account: Account
    address: str
    code: str
    expires_at: float


def _digest(code: str) -> str:
    """
    Args:
        code: A reset code (256 bits of randomness, so a plain digest is enough).

    Returns:
        What is stored in place of it.
    """
    return hashlib.sha256((code or "").encode()).hexdigest()


class PasswordResets:
    """
    Issue and redeem password reset links.

    Args:
        accounts: The account manager (and its store).
        clock: The time source; the manager's by default.
    """

    def __init__(self, accounts: AccountManager, *, clock: Callable[[], float] | None = None):
        self.accounts = accounts
        self._clock = clock

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock() if self._clock is not None else self.accounts.now()

    def _account_for(self, identifier: str) -> Account | None:
        """
        Args:
            identifier: A username or an address, as typed.

        Returns:
            The one account it names, or None for none or for several (an address two
            accounts share names nobody in particular).
        """
        text = (identifier or "").strip()
        if not text:
            return None
        if "@" in text:
            found = self.accounts.find_by_email(text)
            return found[0] if len(found) == 1 else None
        try:
            return self.accounts.find(validate_username(text))
        except ValidationError:
            return None

    def request(self, identifier: str, *, client_ip: str) -> IssuedReset | None:
        """
        Make a link for the account an identifier names, when it may have one.

        Args:
            identifier: A username or an address.
            client_ip: Where the request came from, kept with the link.

        Returns:
            The link to mail, or None when nothing is to be sent (no such account,
            no address, not active, no password, no second factor, too many links
            already). The caller answers the same either way.
        """
        account = self._account_for(identifier)
        if (
            account is None
            or not account.email
            or not account.can_sign_in(self.now())
            or not account.has_password
            or not account.has_mfa
        ):
            return None
        now = self.now()
        code = f"{RESET_PREFIX}{secrets.token_urlsafe(32)}"
        expires = now + LIFETIME_MINUTES * 60
        with self.accounts.store._transaction() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM account_password_resets "
                "WHERE account_id = ? AND created_at > ?",
                (account.id, now - 3600),
            )
            if int(cursor.fetchone()[0]) >= MAX_PER_HOUR:
                return None
            cursor.execute(
                "UPDATE account_password_resets SET used_at = ? "
                "WHERE account_id = ? AND used_at IS NULL",
                (now, account.id),
            )
            cursor.execute(
                "INSERT INTO account_password_resets "
                "(account_id, code_hash, expires_at, created_at, requested_ip) "
                "VALUES (?, ?, ?, ?, ?)",
                (account.id, _digest(code), expires, now, client_ip),
            )
        return IssuedReset(account, account.email, code, expires)

    def redeem(self, code: str, password: str, second_factor: str, *, client_ip: str) -> Account:
        """
        Finish a reset: check the link and the second factor, and set the password.

        Args:
            code: The code the link carried.
            password: The new password; checked against the policy.
            second_factor: A code of the account's authenticator, or one of its backup codes.
            client_ip: Where the request came from, for the failure record.

        Returns:
            The account.

        Raises:
            ResetRefused: When the link is not valid.
            ValidationError: When the password is refused; the link stays usable.
            AuthenticationFailed: When the second factor is wrong; the link stays
                usable, and the failure counts towards locking the account.
        """
        now = self.now()
        with self.accounts.store._transaction() as cursor:
            cursor.execute(
                "SELECT id, account_id FROM account_password_resets "
                "WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?",
                (_digest(code), now),
            )
            row = cursor.fetchone()
        if row is None:
            raise ResetRefused
        account = self.accounts.get(int(row[1]))
        if (
            account is None
            or not account.can_sign_in(now)
            or not account.has_password
            or not account.has_mfa
        ):
            raise ResetRefused
        self.accounts.check_password(account.username, password)
        if not self.accounts.verify_second_factor(account.id, second_factor, purpose=PURPOSE):
            self.accounts.record_failure(account.id, client_ip)
            raise AuthenticationFailed("bad_second_factor", account.id)
        updated = self.accounts.set_password(account.username, password)
        with self.accounts.store._transaction() as cursor:
            cursor.execute(
                "UPDATE account_password_resets SET used_at = ? "
                "WHERE account_id = ? AND used_at IS NULL",
                (now, account.id),
            )
        return updated
