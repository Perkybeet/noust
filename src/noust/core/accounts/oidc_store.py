# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where single sign-in keeps what it knows: providers, identities, flows, tickets.

The one reader and writer of the four tables of the SSO fragment of
:mod:`noust.core.schema_v14`, through the store's own connection and
transaction (the way :class:`~noust.core.accounts.manager.AccountManager` reaches
the accounts) so ``--dry-run`` rehearses a provider change as it rehearses any
other write. Rules about *who may sign in* are not here: this module stores and
finds, :mod:`noust.core.accounts.oidc` decides.

A provider's client secret is not in the database. It is a file of the secret
store, ``oidc/<name>``: 0600, in a 0700 directory, sealed with the rest on a
sealed central, never printed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.accounts.oidc_model import (
    PROTOCOL_OIDC,
    GroupMapping,
    OidcError,
    OidcProvider,
    parse_mappings,
)
from noust.core.exceptions import ValidationError
from noust.core.secrets import SecretStore

if TYPE_CHECKING:
    from noust.core.store import NoustStore

logger = logging.getLogger(__name__)

#: How long a person has between the redirect to the provider and its answer.
FLOW_SECONDS = 600
#: How long the verified answer waits for the console to collect it.
TICKET_SECONDS = 120
#: Sign-ins in flight at most. Starting one needs no credential, so the table
#: that remembers them is bounded rather than trusted.
MAX_PENDING_FLOWS = 2000

_PROVIDER_COLUMNS = (
    "name, label, issuer, client_id, scopes, enabled, require_email_verified, "
    "assume_email_verified, clock_skew_seconds, provisioning, default_role, default_team, default_team_role, "
    "link_by_email, allowed_domains, require_amr, groups_claim, group_sync, group_mappings, "
    "redirect_uri, created_at, created_by, updated_at, updated_by"
)
_IDENTITY_COLUMNS = (
    "id, account_id, protocol, provider, issuer, subject, email, mapped_role, created_at, "
    "created_by, last_login_at, last_login_ip"
)


def digest(value: str) -> str:
    """
    Args:
        value: A random value handed to a browser: a state, a ticket, a binding.

    Returns:
        Its SHA-256, the only form stored. 256 bits of randomness, so a plain
        digest is as good as a slow one.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def secret_name(provider: str) -> str:
    """
    Args:
        provider: A provider's name.

    Returns:
        The secret store's name for its client secret.
    """
    return f"oidc/{provider}"


def _json_list(raw: Any) -> list[Any]:
    """
    Args:
        raw: A JSON list as stored.

    Returns:
        The list; empty for anything that is not one.
    """
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _provider(row: sqlite3.Row) -> OidcProvider:
    """
    Args:
        row: A row selected with :data:`_PROVIDER_COLUMNS`.

    Returns:
        The provider.
    """
    try:
        mappings: list[GroupMapping] = parse_mappings(_json_list(row["group_mappings"]))
    except ValidationError as exc:
        # Fail closed: a hand-edited row must not stop every sign-in, and no
        # mapping means no group gives anything.
        logger.warning("The group mapping of provider %s is not valid: %s", row["name"], exc)
        mappings = []
    return OidcProvider(
        name=str(row["name"]),
        label=str(row["label"]),
        issuer=str(row["issuer"]),
        client_id=str(row["client_id"]),
        scopes=str(row["scopes"]),
        enabled=bool(row["enabled"]),
        require_email_verified=bool(row["require_email_verified"]),
        assume_email_verified=bool(row["assume_email_verified"]),
        clock_skew_seconds=int(row["clock_skew_seconds"]),
        provisioning=str(row["provisioning"]),
        default_role=str(row["default_role"]),
        default_team=row["default_team"],
        default_team_role=row["default_team_role"],
        link_by_email=bool(row["link_by_email"]),
        allowed_domains=tuple(str(item) for item in _json_list(row["allowed_domains"])),
        require_amr=tuple(str(item) for item in _json_list(row["require_amr"])),
        groups_claim=str(row["groups_claim"]),
        group_sync=bool(row["group_sync"]),
        group_mappings=tuple(mappings),
        redirect_uri=row["redirect_uri"],
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
        updated_at=float(row["updated_at"]),
        updated_by=row["updated_by"],
    )


@dataclass(frozen=True)
class Identity:
    """
    Who an account is at an outside provider.

    Attributes:
        id: Store id.
        account_id: The account it signs in as.
        protocol: ``oidc``.
        provider: The configured provider it was linked through.
        issuer: The provider's issuer.
        subject: The provider's stable id for the person; with the issuer, the
            only thing that identifies them.
        email: The address the provider last gave, for display. Never a key.
        mapped_role: The instance role a group mapping last gave.
        created_at: When it was linked, UNIX seconds.
        created_by: Who or what linked it.
        last_login_at: Its last sign-in.
        last_login_ip: From where.
    """

    id: int
    account_id: int
    protocol: str
    provider: str
    issuer: str
    subject: str
    email: str | None
    mapped_role: str | None
    created_at: float
    created_by: str | None
    last_login_at: float | None
    last_login_ip: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The identity as the API and ``--json`` show it.
        """
        return {
            "id": self.id,
            "account_id": self.account_id,
            "protocol": self.protocol,
            "provider": self.provider,
            "issuer": self.issuer,
            "subject": self.subject,
            "email": self.email,
            "mapped_role": self.mapped_role,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "last_login_at": self.last_login_at,
            "last_login_ip": self.last_login_ip,
        }


def _identity(row: sqlite3.Row) -> Identity:
    """
    Args:
        row: A row selected with :data:`_IDENTITY_COLUMNS`.

    Returns:
        The identity.
    """
    return Identity(
        id=int(row["id"]),
        account_id=int(row["account_id"]),
        protocol=str(row["protocol"]),
        provider=str(row["provider"]),
        issuer=str(row["issuer"]),
        subject=str(row["subject"]),
        email=row["email"],
        mapped_role=row["mapped_role"],
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
        last_login_at=row["last_login_at"],
        last_login_ip=row["last_login_ip"],
    )


@dataclass(frozen=True)
class PendingFlow:
    """
    A sign-in between the redirect to the provider and its answer.

    Attributes:
        state_hash: The digest of the ``state`` the provider will echo.
        provider: The provider it was started with.
        nonce: The nonce the ID token must carry.
        code_verifier: The PKCE verifier the token request proves.
        binding_hash: The digest of the secret in the browser's cookie.
        redirect_uri: The callback address the authorization used; the token
            request repeats it exactly.
        next_path: Where the console goes once signed in.
        client_ip: Where it was started from.
        expires_at: When it is no longer answered, UNIX seconds.
    """

    state_hash: str
    provider: str
    nonce: str
    code_verifier: str
    binding_hash: str
    redirect_uri: str
    next_path: str
    client_ip: str
    expires_at: float


@dataclass(frozen=True)
class Ticket:
    """
    A verified answer waiting to be collected by the console.

    Attributes:
        ticket_hash: The digest of the single-use handle.
        account_id: The account the provider's person is.
        provider: Through which provider.
        binding_hash: The digest of the browser's cookie secret.
        client_ip: The address that received the answer.
        next_path: Where the console goes once signed in.
        amr: How the provider authenticated the person.
        detail: One sentence for the audit record of the sign-in.
        expires_at: When it is no longer collected, UNIX seconds.
    """

    ticket_hash: str
    account_id: int
    provider: str
    binding_hash: str
    client_ip: str
    next_path: str
    amr: tuple[str, ...]
    detail: str
    expires_at: float


class OidcStore:
    """
    Read and write the providers, identities, flows and tickets.

    Args:
        store: The store; the process-wide one by default.
        secrets: The secret store; the process-wide one by default.
        clock: The time source, replaceable in tests.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._clock = clock or time.time

    @property
    def store(self) -> NoustStore:
        """The store the tables live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    @property
    def secrets(self) -> SecretStore:
        """Where client secrets are kept."""
        return self._secrets or SecretStore()

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """
        Args:
            sql: A SELECT with fixed clauses.
            params: Its parameters.

        Returns:
            The rows.
        """
        return list(self.store._get_connection().execute(sql, params).fetchall())

    # ----------------------------------------------------------- providers

    def list_providers(self) -> list[OidcProvider]:
        """
        Returns:
            Every configured provider, by name.
        """
        rows = self._rows(f"SELECT {_PROVIDER_COLUMNS} FROM oidc_providers ORDER BY name")  # noqa: S608 - fixed columns
        return [_provider(row) for row in rows]

    def get_provider(self, name: str) -> OidcProvider | None:
        """
        Args:
            name: The provider's name.

        Returns:
            The provider, or None.
        """
        rows = self._rows(
            f"SELECT {_PROVIDER_COLUMNS} FROM oidc_providers WHERE name = ?",  # noqa: S608 - fixed columns
            ((name or "").strip().lower(),),
        )
        return _provider(rows[0]) if rows else None

    def save_provider(self, provider: OidcProvider) -> None:
        """
        Write a provider, replacing the one of the same name.

        Args:
            provider: The validated provider; its ``created_*`` fields are kept
                from the stored row when there is one.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                f"INSERT INTO oidc_providers ({_PROVIDER_COLUMNS}) "  # noqa: S608 - fixed columns
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET label = excluded.label, "
                "issuer = excluded.issuer, client_id = excluded.client_id, "
                "scopes = excluded.scopes, enabled = excluded.enabled, "
                "require_email_verified = excluded.require_email_verified, "
                "assume_email_verified = excluded.assume_email_verified, "
                "clock_skew_seconds = excluded.clock_skew_seconds, "
                "provisioning = excluded.provisioning, default_role = excluded.default_role, "
                "default_team = excluded.default_team, "
                "default_team_role = excluded.default_team_role, "
                "link_by_email = excluded.link_by_email, "
                "allowed_domains = excluded.allowed_domains, require_amr = excluded.require_amr, "
                "groups_claim = excluded.groups_claim, group_sync = excluded.group_sync, "
                "group_mappings = excluded.group_mappings, redirect_uri = excluded.redirect_uri, "
                "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (
                    provider.name,
                    provider.label,
                    provider.issuer,
                    provider.client_id,
                    provider.scopes,
                    int(provider.enabled),
                    int(provider.require_email_verified),
                    int(provider.assume_email_verified),
                    provider.clock_skew_seconds,
                    provider.provisioning,
                    provider.default_role,
                    provider.default_team,
                    provider.default_team_role,
                    int(provider.link_by_email),
                    json.dumps(list(provider.allowed_domains)),
                    json.dumps(list(provider.require_amr)),
                    provider.groups_claim,
                    int(provider.group_sync),
                    json.dumps([mapping.to_dict() for mapping in provider.group_mappings]),
                    provider.redirect_uri,
                    provider.created_at,
                    provider.created_by,
                    provider.updated_at,
                    provider.updated_by,
                ),
            )

    def delete_provider(self, name: str) -> bool:
        """
        Remove a provider, its client secret and the sign-ins in flight with it.

        Identities stay: they are keyed by issuer, and a provider configured
        again for the same issuer finds them. Without a provider they sign no
        one in.

        Args:
            name: The provider's name.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM oidc_flows WHERE provider = ?", (name,))
            cursor.execute("DELETE FROM oidc_tickets WHERE provider = ?", (name,))
            cursor.execute("DELETE FROM oidc_providers WHERE name = ?", (name,))
            removed = cursor.rowcount == 1
        self.secrets.delete(secret_name(name))
        return removed

    def has_secret(self, name: str) -> bool:
        """
        Args:
            name: The provider's name.

        Returns:
            Whether a client secret is stored, without reading it (a sealed,
            locked central can still say).
        """
        return self.secrets.path(secret_name(name)).is_file()

    def read_secret(self, name: str) -> str | None:
        """
        Args:
            name: The provider's name.

        Returns:
            The client secret, or None for a public client.

        Raises:
            SecretsLockedError: The store is sealed and this process is locked.
        """
        return self.secrets.read(secret_name(name))

    def write_secret(self, name: str, value: str) -> None:
        """
        Args:
            name: The provider's name.
            value: The client secret.
        """
        self.secrets.write(secret_name(name), value)

    def delete_secret(self, name: str) -> None:
        """
        Args:
            name: The provider's name.
        """
        self.secrets.delete(secret_name(name))

    # ---------------------------------------------------------- identities

    def find_identity(
        self, issuer: str, subject: str, protocol: str = PROTOCOL_OIDC
    ) -> Identity | None:
        """
        Args:
            issuer: The provider's issuer.
            subject: The provider's id for the person.
            protocol: ``oidc``.

        Returns:
            The identity, or None when nobody was linked to that pair.
        """
        rows = self._rows(
            f"SELECT {_IDENTITY_COLUMNS} FROM account_identities "  # noqa: S608 - fixed columns
            "WHERE protocol = ? AND issuer = ? AND subject = ?",
            (protocol, issuer, subject),
        )
        return _identity(rows[0]) if rows else None

    def get_identity(self, identity_id: int) -> Identity | None:
        """
        Args:
            identity_id: The identity's id.

        Returns:
            The identity, or None.
        """
        rows = self._rows(
            f"SELECT {_IDENTITY_COLUMNS} FROM account_identities WHERE id = ?",  # noqa: S608 - fixed columns
            (int(identity_id),),
        )
        return _identity(rows[0]) if rows else None

    def list_identities(self, account_id: int | None = None) -> list[Identity]:
        """
        Args:
            account_id: Only this account's; every account's when None.

        Returns:
            The identities, oldest first.
        """
        if account_id is None:
            rows = self._rows(
                f"SELECT {_IDENTITY_COLUMNS} FROM account_identities ORDER BY id"  # noqa: S608 - fixed columns
            )
        else:
            rows = self._rows(
                f"SELECT {_IDENTITY_COLUMNS} FROM account_identities "  # noqa: S608 - fixed columns
                "WHERE account_id = ? ORDER BY id",
                (int(account_id),),
            )
        return [_identity(row) for row in rows]

    def link_identity(
        self,
        account_id: int,
        *,
        provider: str,
        issuer: str,
        subject: str,
        email: str | None,
        created_by: str | None,
        protocol: str = PROTOCOL_OIDC,
    ) -> Identity:
        """
        Link a provider's person to an account.

        Args:
            account_id: The account.
            provider: The configured provider it is linked through.
            issuer: The provider's issuer.
            subject: The provider's id for the person.
            email: The address the provider gave, for display.
            created_by: Who or what links it.
            protocol: ``oidc``.

        Returns:
            The identity.

        Raises:
            OidcError: ``already_linked`` when that person is already linked,
                to this account or to another.
        """
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO account_identities (account_id, protocol, provider, issuer, "
                    "subject, email, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        account_id,
                        protocol,
                        provider,
                        issuer,
                        subject,
                        email,
                        self.now(),
                        created_by,
                    ),
                )
                identity_id = int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            raise OidcError(
                "already_linked",
                "That person at the provider is already linked to an account",
                details="List the links with 'noust auth oidc identities'.",
            ) from exc
        found = self.get_identity(identity_id)
        if found is None:
            # Under --dry-run the insert was rolled back; describe what it would have been.
            return Identity(
                identity_id,
                account_id,
                protocol,
                provider,
                issuer,
                subject,
                email,
                None,
                self.now(),
                created_by,
                None,
                None,
            )
        return found

    def unlink_identity(self, identity_id: int) -> Identity | None:
        """
        Args:
            identity_id: The identity's id.

        Returns:
            The identity as it was, or None when there is no such link.
        """
        found = self.get_identity(identity_id)
        if found is not None:
            with self.store._transaction() as cursor:
                cursor.execute("DELETE FROM account_identities WHERE id = ?", (identity_id,))
        return found

    def touch_identity(
        self,
        identity_id: int,
        *,
        email: str | None,
        client_ip: str,
        mapped_role: str | bool | None = False,
    ) -> None:
        """
        Record a sign-in on an identity.

        Args:
            identity_id: The identity.
            email: The address the provider gave this time, for display.
            client_ip: From where.
            mapped_role: The role a group mapping gave, None to say it no longer
                gives one; left alone when False.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE account_identities SET last_login_at = ?, last_login_ip = ?, "
                "email = COALESCE(?, email) WHERE id = ?",
                (self.now(), client_ip, email, identity_id),
            )
            if mapped_role is not False:
                cursor.execute(
                    "UPDATE account_identities SET mapped_role = ? WHERE id = ?",
                    (mapped_role, identity_id),
                )

    def has_identity(self, account_id: int) -> bool:
        """
        Args:
            account_id: An account.

        Returns:
            Whether any provider's person is linked to it.
        """
        return bool(
            self._rows(
                "SELECT 1 FROM account_identities WHERE account_id = ? LIMIT 1", (int(account_id),)
            )
        )

    def accounts_linked_to(self, issuer: str) -> set[int]:
        """
        Args:
            issuer: A provider's issuer.

        Returns:
            The accounts that have an identity at it.
        """
        rows = self._rows(
            "SELECT DISTINCT account_id FROM account_identities WHERE issuer = ?", (issuer,)
        )
        return {int(row["account_id"]) for row in rows}

    # --------------------------------------------------------------- flows

    def begin_flow(self, flow: PendingFlow) -> None:
        """
        Remember a sign-in that is about to leave for the provider.

        Args:
            flow: The sign-in.

        Raises:
            OidcError: ``too_many_flows`` when this many are already in flight.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM oidc_flows WHERE expires_at <= ?", (self.now(),))
            cursor.execute("DELETE FROM oidc_tickets WHERE expires_at <= ?", (self.now(),))
            pending = cursor.execute("SELECT COUNT(*) FROM oidc_flows").fetchone()[0]
            if int(pending) >= MAX_PENDING_FLOWS:
                raise OidcError(
                    "too_many_flows",
                    "Too many single sign-ins are waiting for their provider",
                    public="unavailable",
                )
            cursor.execute(
                "INSERT INTO oidc_flows (state_hash, provider, nonce, code_verifier, "
                "binding_hash, redirect_uri, next_path, client_ip, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    flow.state_hash,
                    flow.provider,
                    flow.nonce,
                    flow.code_verifier,
                    flow.binding_hash,
                    flow.redirect_uri,
                    flow.next_path,
                    flow.client_ip,
                    self.now(),
                    flow.expires_at,
                ),
            )

    def get_flow(self, state_hash: str) -> PendingFlow | None:
        """
        Read a sign-in in flight without taking it out, so that an answer that
        turns out not to be the browser's own cannot use it up.

        Args:
            state_hash: The digest of the state the provider echoed.

        Returns:
            The flow, or None when it is unknown or expired.
        """
        rows = self._rows(
            "SELECT * FROM oidc_flows WHERE state_hash = ? AND expires_at > ?",
            (state_hash, self.now()),
        )
        if not rows:
            return None
        row = rows[0]
        return PendingFlow(
            state_hash=str(row["state_hash"]),
            provider=str(row["provider"]),
            nonce=str(row["nonce"]),
            code_verifier=str(row["code_verifier"]),
            binding_hash=str(row["binding_hash"]),
            redirect_uri=str(row["redirect_uri"]),
            next_path=str(row["next_path"]),
            client_ip=str(row["client_ip"]),
            expires_at=float(row["expires_at"]),
        )

    def spend_flow(self, state_hash: str) -> PendingFlow | None:
        """
        Take a sign-in out of flight, once.

        Args:
            state_hash: The digest of the state the provider echoed.

        Returns:
            The flow, or None when it is unknown, expired or already spent
            (a replayed answer is the last of those). Of two requests spending
            it at once, one gets it.
        """
        flow = self.get_flow(state_hash)
        if flow is None:
            return None
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM oidc_flows WHERE state_hash = ?", (state_hash,))
            if cursor.rowcount != 1:
                return None
        return flow

    # ------------------------------------------------------------- tickets

    def issue_ticket(self, ticket: Ticket) -> None:
        """
        Args:
            ticket: A verified answer, keyed by the digest of its handle.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO oidc_tickets (ticket_hash, account_id, provider, binding_hash, "
                "client_ip, next_path, amr, detail, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticket.ticket_hash,
                    ticket.account_id,
                    ticket.provider,
                    ticket.binding_hash,
                    ticket.client_ip,
                    ticket.next_path,
                    json.dumps(list(ticket.amr)),
                    ticket.detail,
                    self.now(),
                    ticket.expires_at,
                ),
            )

    def get_ticket(self, ticket_hash: str) -> Ticket | None:
        """
        Read a verified answer without collecting it, so that whoever presents
        it from another browser cannot use it up.

        Args:
            ticket_hash: The digest of the handle presented.

        Returns:
            The ticket, or None when it is unknown or expired.
        """
        rows = self._rows(
            "SELECT * FROM oidc_tickets WHERE ticket_hash = ? AND expires_at > ?",
            (ticket_hash, self.now()),
        )
        if not rows:
            return None
        row = rows[0]
        return Ticket(
            ticket_hash=str(row["ticket_hash"]),
            account_id=int(row["account_id"]),
            provider=str(row["provider"]),
            binding_hash=str(row["binding_hash"]),
            client_ip=str(row["client_ip"]),
            next_path=str(row["next_path"]),
            amr=tuple(str(item) for item in _json_list(row["amr"])),
            detail=str(row["detail"]),
            expires_at=float(row["expires_at"]),
        )

    def spend_ticket(self, ticket_hash: str) -> Ticket | None:
        """
        Collect a verified answer, once.

        Args:
            ticket_hash: The digest of the handle presented.

        Returns:
            The ticket, or None when it is unknown, expired or spent. Of two
            requests collecting it at once, one gets it.
        """
        ticket = self.get_ticket(ticket_hash)
        if ticket is None:
            return None
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM oidc_tickets WHERE ticket_hash = ?", (ticket_hash,))
            if cursor.rowcount != 1:
                return None
        return ticket

    # --------------------------------------------------------------- teams

    def team_memberships(self, account_id: int) -> dict[str, tuple[str, str | None]]:
        """
        Args:
            account_id: An account.

        Returns:
            Each team slug it belongs to, with its role there and who added it.
        """
        rows = self._rows(
            "SELECT t.slug AS slug, m.role AS role, m.created_by AS created_by "
            "FROM team_members m JOIN teams t ON t.id = m.team_id WHERE m.account_id = ?",
            (account_id,),
        )
        return {str(row["slug"]): (str(row["role"]), row["created_by"]) for row in rows}
