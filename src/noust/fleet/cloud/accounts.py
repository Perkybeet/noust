# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The cloud accounts a central creates servers with: a name, a provider and a token.

The token is the one secret here and is kept like the rest of a central's: a 0600
file under ``secrets/cloud-accounts/`` (:class:`~noust.core.secrets.SecretStore`),
sealed with them on a sealed central, so a copy of the database alone opens
nothing. The row holds only its last four characters, to tell two tokens apart. No
route, command, log line, audit event or job result carries a token; the only
reader that hands it on is :meth:`CloudAccounts.provider`, which gives it to an
adapter that sends it to the provider and nowhere else.

A token is checked against the provider before it is kept, because a mistyped one
found out during a server's creation is a half-made machine at the operator's
expense. It should be scoped to the one project or account the central creates
servers in (:attr:`~noust.fleet.cloud.providers.base.CloudProvider.token_help`);
the central only ever calls the endpoints that create, read and delete servers
and SSH keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core import audit
from noust.core.exceptions import NodeError, ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore, get_store
from noust.fleet.cloud.models import validate_account_name
from noust.fleet.cloud.providers import CloudProvider, provider_class, provider_for

#: The secret namespace tokens are kept under.
NAMESPACE = "cloud-accounts"

#: Longest token accepted. Providers' are 36 to 100 characters.
MAX_TOKEN = 512

#: Shortest token accepted; shorter is a mistake, not a token.
MIN_TOKEN = 16

#: Characters a token never has: whitespace and controls (a pasted newline would
#: end up in an ``Authorization`` header).
_BAD_TOKEN = re.compile(r"[\s\x00-\x1f\x7f]")

#: How many characters of a token the row remembers.
TAIL = 4


def _now() -> str:
    """Returns: The current time, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CloudAccount:
    """
    A provider account, without its token.

    Attributes:
        name: What the central calls it.
        provider: The provider's key.
        token_tail: The last characters of the token.
        note: A free note (which project the token belongs to).
        created_at: When it was added, ISO 8601 UTC.
        created_by: Who added it.
        verified_at: When the provider last accepted the token.
        last_used_at: When a server was last created with it.
    """

    name: str
    provider: str
    token_tail: str
    note: str | None
    created_at: str
    created_by: str | None
    verified_at: str | None
    last_used_at: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The account as the API and the CLI show it: everything but the token.
        """
        return {
            "name": self.name,
            "provider": self.provider,
            "token_hint": f"...{self.token_tail}",
            "note": self.note,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "verified_at": self.verified_at,
            "last_used_at": self.last_used_at,
        }


def check_token(token: object) -> str:
    """
    Check the shape of a token before anything is sent anywhere with it.

    Args:
        token: What the operator pasted.

    Returns:
        The token without surrounding whitespace.

    Raises:
        ValidationError: When it is empty, too short or too long, or holds a space.
    """
    text = token.strip() if isinstance(token, str) else ""
    if not (MIN_TOKEN <= len(text) <= MAX_TOKEN) or _BAD_TOKEN.search(text):
        raise ValidationError(
            "That does not look like an API token",
            details="Paste the token the provider showed once, with no spaces or line breaks.",
            field="token",
        )
    return text


class CloudAccounts:
    """
    Add, list, replace and remove cloud accounts.

    Args:
        store: The store; the process-wide one by default.
        secrets: Where tokens are kept; the process-wide secret store by default.
        provider_options: Passed to every adapter this builds (tests give an opener).
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        *,
        provider_options: dict[str, Any] | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._options = provider_options or {}

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def secrets(self) -> SecretStore:
        """The secret store tokens are kept in."""
        if self._secrets is None:
            self._secrets = SecretStore()
        return self._secrets

    # -- Reading -----------------------------------------------------------

    def list(self) -> list[CloudAccount]:
        """
        Returns:
            Every account, by name.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM cloud_accounts ORDER BY name")
            .fetchall()
        )
        return [_account(row) for row in rows]

    def get(self, name: str) -> CloudAccount:
        """
        Read one account.

        Args:
            name: Its name.

        Returns:
            The account.

        Raises:
            NodeError: When there is none by that name.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM cloud_accounts WHERE name = ?", (name,))
            .fetchone()
        )
        if row is None:
            raise NodeError(
                f"No cloud account named {name}",
                details="List them with 'noust fleet cloud account list'.",
                field="account",
            )
        return _account(row)

    def provider(self, name: str) -> CloudProvider:
        """
        Build the adapter for an account, holding its token.

        Args:
            name: The account's name.

        Returns:
            The adapter.

        Raises:
            NodeError: When there is no such account, or its token is gone from the
                secret store (the file was removed).
            SecretsLockedError: When this central's secrets are sealed and locked.
        """
        account = self.get(name)
        token = self.secrets.read(f"{NAMESPACE}/{account.name}")
        if not token:
            raise NodeError(
                f"The token of the cloud account {name} is missing",
                details="Save the account again with its token: "
                f"noust fleet cloud account token {name}",
                field="account",
            )
        return provider_for(account.provider, token, **self._options)

    # -- Changing ----------------------------------------------------------

    def add(
        self,
        name: str,
        provider: str,
        token: str,
        *,
        note: str | None = None,
        actor: str | None = None,
        verify: bool = True,
    ) -> CloudAccount:
        """
        Keep a provider token under a name, after the provider accepted it.

        Args:
            name: What to call the account.
            provider: The provider's key.
            token: Its API token.
            note: A free note.
            actor: Who adds it.
            verify: Ask the provider whether it accepts the token first.

        Returns:
            The account.

        Raises:
            ValidationError: When a field is malformed.
            NodeError: When the name is taken.
            CloudProviderError: When the provider refuses the token (nothing is kept).
        """
        validate_account_name(name)
        provider_class(provider)
        token = check_token(token)
        cleaned_note = _note(note)
        if self._exists(name):
            raise NodeError(
                f"A cloud account named {name} already exists",
                details=f"Give the new one another name, or replace its token: "
                f"noust fleet cloud account token {name}",
                field="name",
            )
        verified = None
        if verify:
            provider_for(provider, token, **self._options).verify()
            verified = _now()
        now = _now()
        self.secrets.write(f"{NAMESPACE}/{name}", token)
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO cloud_accounts (name, provider, token_tail, note, created_at, "
                "created_by, verified_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, provider, token[-TAIL:], cleaned_note, now, actor, verified),
            )
        audit.record(
            "fleet.cloud.account.add",
            target=f"cloud-account:{name}",
            details={"provider": provider, "verified": verify},
        )
        return self.get(name)

    def replace_token(
        self, name: str, token: str, *, actor: str | None = None, verify: bool = True
    ) -> CloudAccount:
        """
        Swap an account's token for a new one, after the provider accepted it.

        Args:
            name: The account.
            token: The new token.
            actor: Who does it.
            verify: Ask the provider whether it accepts the token first.

        Returns:
            The account.

        Raises:
            NodeError: When there is no such account.
            ValidationError: When the token is malformed.
            CloudProviderError: When the provider refuses it (the old one is kept).
        """
        account = self.get(name)
        token = check_token(token)
        verified = None
        if verify:
            provider_for(account.provider, token, **self._options).verify()
            verified = _now()
        self.secrets.write(f"{NAMESPACE}/{name}", token)
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE cloud_accounts SET token_tail = ?, verified_at = ? WHERE name = ?",
                (token[-TAIL:], verified, name),
            )
        audit.record(
            "fleet.cloud.account.rotate",
            target=f"cloud-account:{name}",
            details={"provider": account.provider, "by": actor},
        )
        return self.get(name)

    def verify(self, name: str) -> CloudAccount:
        """
        Ask the provider whether it still accepts an account's token.

        Args:
            name: The account.

        Returns:
            The account, with the time it was verified.

        Raises:
            NodeError: When there is no such account.
            CloudProviderError: When the provider refuses the token.
        """
        self.provider(name).verify()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE cloud_accounts SET verified_at = ? WHERE name = ?", (_now(), name)
            )
        return self.get(name)

    def mark_used(self, name: str) -> None:
        """
        Record that a server was just created with an account.

        Args:
            name: The account.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE cloud_accounts SET last_used_at = ? WHERE name = ?", (_now(), name)
            )

    def remove(self, name: str, *, actor: str | None = None, force: bool = False) -> None:
        """
        Forget an account and its token.

        The machines created with it keep running at the provider; without the
        token the central can no longer delete them, so removing an account that
        still has some is refused unless ``force`` says the operator knows.

        Args:
            name: The account.
            actor: Who does it.
            force: Remove it although machines made with it are still recorded.

        Raises:
            NodeError: When there is no such account, or machines still depend on it.
        """
        account = self.get(name)
        live = (
            self.store._get_connection()
            .execute(
                "SELECT node FROM cloud_servers WHERE account = ? AND state != 'deleted' "
                "ORDER BY node",
                (name,),
            )
            .fetchall()
        )
        if live and not force:
            nodes = ", ".join(str(row["node"]) for row in live)
            raise NodeError(
                f"The cloud account {name} still has servers: {nodes}",
                details="Without its token this central cannot delete them or read their "
                "power state. Delete the servers first, or remove the account anyway with "
                "--force.",
                field="account",
            )
        self.secrets.delete(f"{NAMESPACE}/{name}")
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM cloud_accounts WHERE name = ?", (name,))
        audit.record(
            "fleet.cloud.account.remove",
            target=f"cloud-account:{name}",
            details={"provider": account.provider, "forced": bool(live), "by": actor},
        )

    def _exists(self, name: str) -> bool:
        """
        Args:
            name: An account name.

        Returns:
            Whether an account has it.
        """
        return (
            self.store._get_connection()
            .execute("SELECT 1 FROM cloud_accounts WHERE name = ?", (name,))
            .fetchone()
            is not None
        )


def _note(note: str | None) -> str | None:
    """
    Check a free note.

    Args:
        note: What the operator wrote.

    Returns:
        The note, or None when empty.

    Raises:
        ValidationError: When it is too long or holds a control character.
    """
    if note is None or not note.strip():
        return None
    text = note.strip()
    if len(text) > 200 or any(ord(char) < 32 for char in text):
        raise ValidationError("A note is one line of at most 200 characters", field="note")
    return text


def _account(row: Any) -> CloudAccount:
    """
    Build an account out of its row.

    Args:
        row: A ``cloud_accounts`` row.

    Returns:
        The account.
    """
    return CloudAccount(
        name=str(row["name"]),
        provider=str(row["provider"]),
        token_tail=str(row["token_tail"]),
        note=row["note"],
        created_at=str(row["created_at"]),
        created_by=row["created_by"],
        verified_at=row["verified_at"],
        last_used_at=row["last_used_at"],
    )
