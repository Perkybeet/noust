# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Passkeys: the one implementation of registering, using and governing them.

The console's endpoints (:mod:`noust.web.api.passkeys`) and ``noust passkey``
call :class:`PasskeyManager`; the protocol itself is
:mod:`noust.core.accounts.webauthn`. Decisions (spec §2.4, research
``passkeys.md`` §5):

- **A passkey is complete.** Discoverable (``residentKey: required``) and
  user-verified, so it signs in on its own and confirms sudo mode, and it
  counts as the second factor of its owner: once an account has one, its
  password alone no longer signs it in; once the master token has one, the
  master token alone no longer does either.
- **Owners.** An account, or the master token itself (``account_id`` None):
  the break-glass credential, and the whole console on a server with no
  account yet, may protect its own sign-in the same way.
- **One ``user.id`` per owner and server, random.** A constant one would make
  an authenticator replace this server's passkey with another server's when
  both are reached as ``localhost`` through SSH tunnels. The handle is made
  for the first passkey of an owner and reused for the next.
- **Bound to a name.** The RP ID is the host the browser used, never an
  address (:func:`resolve_relying_party`), and each credential is stored with
  it: one registered under ``localhost`` is unknown under the public name.
- **Challenges are signed, not stored,** when issued: the anonymous sign-in
  options write nothing. Each carries its purpose, its session, its RP ID and
  an expiry under an HMAC; its nonce is recorded when it is spent, so it
  serves once - and it is spent whether or not the rest of the ceremony
  verifies.
- **Recovery** is another passkey, the account's backup codes (issued with
  its first second factor, whichever it is), a security officer's
  ``reset-mfa``, or root's ``noust passkey reset``. An account may not remove
  its only second factor itself.
"""

from __future__ import annotations

import builtins
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import sqlite3
import struct
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from noust.core.accounts.model import AccountError
from noust.core.accounts.webauthn import (
    ALG_EDDSA,
    ALG_ES256,
    ALG_RS256,
    StoredCredential,
    WebAuthnError,
    WebAuthnUnavailable,
    b64url_encode,
    load_crypto,
    supported_algorithms,
    verify_assertion,
    verify_registration,
)
from noust.core.exceptions import SecurityError, ValidationError

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: How long a ceremony may take, from the options to the answer.
CHALLENGE_SECONDS = 300
#: What the browser is told, in milliseconds: the same five minutes.
CEREMONY_TIMEOUT_MS = CHALLENGE_SECONDS * 1000

#: The ceremonies a challenge may be spent on.
PURPOSES = ("register", "login", "elevate")

#: Most passkeys one owner may hold, so a session cannot fill the store.
MAX_PASSKEYS_PER_OWNER = 20
#: Longest name of a passkey; it is shown, never parsed.
MAX_NAME_LENGTH = 64
#: Bytes of a WebAuthn user handle: random, and nothing about the person.
USER_HANDLE_BYTES = 32

_CHALLENGE_VERSION = 1
_CHALLENGE_DOMAIN = b"noust-webauthn-v1"
_NONCE_BYTES = 16
_MAC_BYTES = 32

#: The key challenges are signed with when none is given: this process's own.
#: A challenge lives five minutes and the console is one process, so a key
#: that dies with it costs at most a ceremony started before a restart.
_PROCESS_KEY = secrets.token_bytes(32)

_ALGORITHM_NAMES = {ALG_ES256: "ES256", ALG_EDDSA: "EdDSA", ALG_RS256: "RS256"}

_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")

_COLUMNS = (
    "p.id, p.account_id, p.credential_id, p.user_handle, p.rp_id, p.public_key, p.alg, "
    "p.sign_count, p.backup_eligible, p.backup_state, p.transports, p.aaguid, p.name, "
    "p.created_at, p.created_by, p.last_used_at, p.last_used_ip, p.clone_warning_at, "
    "a.username AS owner_name"
)
_FROM = "account_passkeys p LEFT JOIN accounts a ON a.id = p.account_id"


# ------------------------------------------------------------ relying party


class PasskeysUnavailable(SecurityError):
    """
    Passkeys cannot work for this request, and why.

    Attributes:
        reason: ``library_missing``, ``ip_address``, ``insecure_context``,
            ``invalid_host`` or ``host_mismatch``: what the console explains.
    """

    def __init__(self, reason: str, message: str, details: str) -> None:
        """
        Args:
            reason: The machine-readable reason.
            message: What is wrong.
            details: How to fix it.
        """
        super().__init__(message, details=details)
        self.reason = reason


@dataclass(frozen=True)
class RelyingParty:
    """
    What this console is to a passkey: a name, and the pages that may use it.

    Attributes:
        id: The RP ID: a host name, never an address, never with a port.
        origins: Every origin a page of this console may have.
        name: What an authenticator shows as the site.
    """

    id: str
    origins: tuple[str, ...]
    name: str = "Noust"


def require_library() -> None:
    """
    Check that signatures can be verified here.

    Raises:
        PasskeysUnavailable: ``library_missing``, with what to install.
    """
    try:
        load_crypto()
    except WebAuthnUnavailable as exc:
        raise PasskeysUnavailable(
            "library_missing", "Passkeys need a library this server does not have", exc.details
        ) from exc


def _split_host(host: str) -> tuple[str, str]:
    """
    Separate a ``Host`` header into its name and what the browser typed.

    Args:
        host: The header, possibly with a port or IPv6 brackets.

    Returns:
        The bare host, lower-cased, and the header lower-cased (with its port).
    """
    header = host.strip().lower()
    if header.startswith("["):
        return header[1:].split("]", 1)[0], header
    name, separator, port = header.rpartition(":")
    if separator and port.isdigit():
        return name, header
    return header, header


def _is_address(value: str) -> bool:
    """
    Args:
        value: A bare host.

    Returns:
        True for an IPv4 or IPv6 address in any spelling.
    """
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_name(value: str) -> bool:
    """
    Args:
        value: A bare, lower-cased host.

    Returns:
        True for a DNS name: dot-separated labels of letters, digits and inner
        hyphens, 253 characters at most.
    """
    if not value or len(value) > 253:
        return False
    return all(_LABEL.fullmatch(label) for label in value.rstrip(".").split("."))


def _within(host: str, rp_id: str) -> bool:
    """
    Args:
        host: The host a page is on.
        rp_id: An RP ID.

    Returns:
        True when the RP ID is the host or a parent of it, which is what a
        browser lets a page claim.
    """
    return host == rp_id or host.endswith("." + rp_id)


def _origin_of(url: str) -> tuple[str, str] | None:
    """
    Args:
        url: An absolute URL, such as ``web.public_url``.

    Returns:
        Its origin and its bare host, or None when it is not an http(s) URL.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return f"{parts.scheme}://{parts.netloc.lower()}", parts.hostname.lower()


def resolve_relying_party(
    host: str | None,
    *,
    secure: bool,
    rp_id: str | None = None,
    origins: Sequence[str] | None = None,
    public_url: str | None = None,
) -> RelyingParty:
    """
    Decide the RP ID and the origins for a request, or say why passkeys cannot work.

    The RP ID is the host the browser used, or ``web.passkeys.rp_id`` when the
    operator set one (behind a proxy whose public name differs, or to share
    passkeys between subdomains). An address is refused: browsers refuse an
    IP as an RP ID, and a certificate for a private address is not one a
    browser trusts. ``localhost`` and its subdomains are allowed over plain
    HTTP, the one place a browser offers WebAuthn without TLS.

    Args:
        host: The request's ``Host`` header.
        secure: Whether the request arrived over TLS (directly or through a
            trusted proxy).
        rp_id: ``web.passkeys.rp_id``, when set.
        origins: ``web.passkeys.origins``, when set: they replace the origin
            derived from the request.
        public_url: ``web.public_url``, an origin of its own host.

    Returns:
        The relying party.

    Raises:
        PasskeysUnavailable: ``ip_address``, ``insecure_context``,
            ``invalid_host`` or ``host_mismatch``.
    """
    bare, header = _split_host(host or "")
    public = _origin_of(public_url) if public_url else None
    if _is_address(bare):
        instead = (
            f"Open {public[0]} instead"
            if public
            else "Give this console a name with a certificate the browser trusts"
        )
        raise PasskeysUnavailable(
            "ip_address",
            f"Passkeys do not work on an address such as {bare}",
            f"Browsers only offer passkeys to a site with a name. {instead}, or reach this "
            "server through an SSH tunnel at http://localhost:<port>.",
        )
    if not _is_name(bare):
        raise PasskeysUnavailable(
            "invalid_host",
            "This request does not name a host passkeys can be bound to",
            "Open the console by its name, such as https://noust.example.com.",
        )
    local = bare == "localhost" or bare.endswith(".localhost")
    if not secure and not local:
        raise PasskeysUnavailable(
            "insecure_context",
            "Passkeys need HTTPS on a name other than localhost",
            (
                f"Browsers only offer passkeys over HTTPS, or on http://localhost. Serve "
                f"{bare} with a certificate the browser trusts, or reach this server through "
                "an SSH tunnel at http://localhost:<port>."
            ),
        )
    chosen = bare.rstrip(".")
    if rp_id:
        chosen = rp_id.strip().lower().rstrip(".")
        if _is_address(chosen) or not _is_name(chosen):
            raise PasskeysUnavailable(
                "invalid_host",
                f"web.passkeys.rp_id is not a host name: {rp_id!r}",
                "Set it to the console's name, such as noust.example.com, or unset it.",
            )
        if not _within(bare, chosen):
            raise PasskeysUnavailable(
                "host_mismatch",
                f"This console's passkeys belong to {chosen}, and it was opened as {bare}",
                f"Open it at {chosen} (or a name under it), or change web.passkeys.rp_id.",
            )
    scheme = "https" if secure else "http"
    allowed = [item.strip().lower().rstrip("/") for item in origins or () if item.strip()]
    if not allowed:
        allowed = [f"{scheme}://{header}"]
        if public is not None and _within(public[1], chosen):
            allowed.append(public[0])
    return RelyingParty(id=chosen, origins=tuple(dict.fromkeys(allowed)))


# ------------------------------------------------------------------ records


@dataclass(frozen=True)
class PasskeyOwner:
    """
    Whose passkeys a ceremony is about.

    Attributes:
        account_id: The account, or None for the master token.
        username: The account's name, or ``master``.
        display_name: How the account is greeted, when it has a display name.
    """

    account_id: int | None
    username: str
    display_name: str = ""


@dataclass(frozen=True)
class Passkey:
    """
    One registered passkey, without its key.

    Attributes:
        id: Store id.
        account_id: The owner, or None for the master token.
        owner_name: The owner's username, or None for the master token.
        name: What the owner called it.
        rp_id: The name it was registered under; it signs in nowhere else.
        alg: Its COSE algorithm.
        sign_count: The last counter it reported.
        backup_eligible: Whether the authenticator said it may be synced.
        backup_state: Whether it was synced at its last use.
        transports: The browser's hints about how it is reached.
        aaguid: The authenticator model it claimed (not attested).
        created_at: When it was registered, UNIX seconds.
        created_by: Who registered it.
        last_used_at: Its last use.
        last_used_ip: Where from.
        clone_warning_at: When its counter last went back, if it ever did.
        credential_id: Its WebAuthn credential id.
        user_handle: The WebAuthn ``user.id`` it was registered with.
    """

    id: int
    account_id: int | None
    owner_name: str | None
    name: str
    rp_id: str
    alg: int
    sign_count: int
    backup_eligible: bool
    backup_state: bool
    transports: list[str]
    aaguid: str | None
    created_at: float
    created_by: str | None
    last_used_at: float | None
    last_used_ip: str | None
    clone_warning_at: float | None
    credential_id: bytes = field(repr=False)
    user_handle: bytes = field(repr=False)

    @property
    def synced(self) -> bool:
        """Whether it may live in a password manager's cloud, per the authenticator."""
        return self.backup_eligible

    @property
    def algorithm(self) -> str:
        """The algorithm's name, such as ``ES256``."""
        return _ALGORITHM_NAMES.get(self.alg, str(self.alg))

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the passkey for the API and ``--json``, with no key material.

        Returns:
            Every public field.
        """
        return {
            "id": self.id,
            "account_id": self.account_id,
            "owner": self.owner_name if self.account_id is not None else "master",
            "name": self.name,
            "rp_id": self.rp_id,
            "algorithm": self.algorithm,
            "synced": self.synced,
            "backup_state": self.backup_state,
            "transports": list(self.transports),
            "created_at": self.created_at,
            "created_by": self.created_by,
            "last_used_at": self.last_used_at,
            "last_used_ip": self.last_used_ip,
            "clone_warning_at": self.clone_warning_at,
        }


@dataclass(frozen=True)
class Registration:
    """
    A passkey just registered.

    Attributes:
        passkey: The passkey.
        backup_codes: The account's backup codes, in clear, when this was its
            first second factor and it had none; shown exactly once.
    """

    passkey: Passkey
    backup_codes: list[str] | None = None


def _passkey(row: sqlite3.Row) -> Passkey:
    """
    Args:
        row: A row selected with :data:`_COLUMNS`.

    Returns:
        The passkey.
    """
    try:
        transports = json.loads(row["transports"] or "[]")
    except ValueError:
        transports = []
    return Passkey(
        id=int(row["id"]),
        account_id=row["account_id"],
        owner_name=row["owner_name"],
        name=str(row["name"]),
        rp_id=str(row["rp_id"]),
        alg=int(row["alg"]),
        sign_count=int(row["sign_count"] or 0),
        backup_eligible=bool(row["backup_eligible"]),
        backup_state=bool(row["backup_state"]),
        transports=[str(item) for item in transports] if isinstance(transports, list) else [],
        aaguid=row["aaguid"],
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
        last_used_at=row["last_used_at"],
        last_used_ip=row["last_used_ip"],
        clone_warning_at=row["clone_warning_at"],
        credential_id=bytes(row["credential_id"]),
        user_handle=bytes(row["user_handle"]),
    )


def clean_name(value: str | None) -> str:
    """
    Check the name an owner gives a passkey.

    Args:
        value: The name as typed.

    Returns:
        The trimmed name.

    Raises:
        ValidationError: When it is empty, too long or carries control characters.
    """
    name = (value or "").strip()
    if (
        not name
        or len(name) > MAX_NAME_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise ValidationError(
            "Invalid passkey name",
            details=f"Use 1 to {MAX_NAME_LENGTH} printable characters, such as 'Work laptop'.",
            field="name",
        )
    return name


def _owner_clause(account_id: int | None) -> tuple[str, tuple[Any, ...]]:
    """
    Args:
        account_id: An account, or None for the master token.

    Returns:
        The WHERE clause selecting that owner's passkeys, and its parameters.
    """
    if account_id is None:
        return "p.account_id IS NULL", ()
    return "p.account_id = ?", (int(account_id),)


class PasskeyManager:
    """
    Register, use and govern passkeys.

    Args:
        store: The store; the process-wide one by default.
        clock: The time source, replaceable in tests.
        challenge_key: The key challenges are signed with; this process's own
            by default.
        allow_synced: Accept passkeys that may be synced; read from
            ``web.passkeys.allow_synced`` (default yes) when None.
        hostname: This machine's name, for the name an authenticator shows;
            the real one by default.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        *,
        clock: Callable[[], float] | None = None,
        challenge_key: bytes | None = None,
        allow_synced: bool | None = None,
        hostname: str | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or time.time
        self._key = challenge_key or _PROCESS_KEY
        self._allow_synced = allow_synced
        self._hostname = hostname

    # ------------------------------------------------------------ plumbing

    @property
    def store(self) -> NoustStore:
        """The store the passkeys live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """
        Args:
            sql: A SELECT.
            params: Its parameters.

        Returns:
            The rows.
        """
        # Through the store's own connection, as AccountManager does: its WAL
        # and busy settings, and the rehearsal semantics of --dry-run.
        return list(self.store._get_connection().execute(sql, params).fetchall())

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Cursor]:
        """
        Yields:
            A cursor inside one store transaction.
        """
        with self.store._transaction() as cursor:
            yield cursor

    def _allows_synced(self) -> bool:
        """
        Returns:
            Whether a passkey that may be synced is accepted.
        """
        if self._allow_synced is not None:
            return self._allow_synced
        from noust.core.config import Config

        return bool(Config().get("web.passkeys.allow_synced", True))

    def _host(self) -> str:
        """
        Returns:
            This machine's name, for what an authenticator shows.
        """
        return self._hostname or socket.gethostname()

    # ------------------------------------------------------------- queries

    def get(self, passkey_id: int) -> Passkey | None:
        """
        Args:
            passkey_id: Its id.

        Returns:
            The passkey, or None.
        """
        rows = self._rows(f"SELECT {_COLUMNS} FROM {_FROM} WHERE p.id = ?", (int(passkey_id),))  # noqa: S608 - fixed clauses
        return _passkey(rows[0]) if rows else None

    def by_credential_id(self, credential_id: bytes) -> Passkey | None:
        """
        Args:
            credential_id: A WebAuthn credential id.

        Returns:
            The passkey registered with it, or None.
        """
        sql = f"SELECT {_COLUMNS} FROM {_FROM} WHERE p.credential_id = ?"  # noqa: S608 - fixed clauses
        rows = self._rows(sql, (bytes(credential_id),))
        return _passkey(rows[0]) if rows else None

    # builtins.list in the annotations below: inside this class, 'list' is this method.
    def list(self, account_id: int | None) -> builtins.list[Passkey]:
        """
        Args:
            account_id: An account, or None for the master token.

        Returns:
            That owner's passkeys, oldest first.
        """
        where, params = _owner_clause(account_id)
        rows = self._rows(f"SELECT {_COLUMNS} FROM {_FROM} WHERE {where} ORDER BY p.id", params)  # noqa: S608 - fixed clauses
        return [_passkey(row) for row in rows]

    def list_all(self) -> builtins.list[Passkey]:
        """
        Returns:
            Every passkey on this server, by owner.
        """
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM {_FROM} ORDER BY a.username IS NULL, a.username, p.id"  # noqa: S608 - fixed clauses
        )
        return [_passkey(row) for row in rows]

    def count(self, account_id: int | None) -> int:
        """
        Args:
            account_id: An account, or None for the master token.

        Returns:
            How many passkeys that owner has.
        """
        where, params = _owner_clause(account_id)
        sql = f"SELECT COUNT(*) AS n FROM account_passkeys p WHERE {where}"  # noqa: S608 - fixed clauses
        return int(self._rows(sql, params)[0]["n"])

    @staticmethod
    def credential_id_b64(passkey: Passkey) -> str:
        """
        Args:
            passkey: A passkey.

        Returns:
            Its credential id, as the browser's JSON writes it.
        """
        return b64url_encode(passkey.credential_id)

    @staticmethod
    def user_handle_b64(passkey: Passkey) -> str:
        """
        Args:
            passkey: A passkey.

        Returns:
            Its user handle, as the browser's JSON writes it.
        """
        return b64url_encode(passkey.user_handle)

    # ---------------------------------------------------------- challenges

    def _mac(self, purpose: str, binding: str, rp_id: str, body: bytes) -> bytes:
        """
        Args:
            purpose: What the challenge may be spent on.
            binding: The session it belongs to, or empty for a sign-in.
            rp_id: The RP ID it was issued for.
            body: The challenge's own bytes before the MAC.

        Returns:
            The MAC that makes the challenge this server's.
        """
        message = b"\x00".join(
            (_CHALLENGE_DOMAIN, purpose.encode(), binding.encode(), rp_id.encode(), body)
        )
        return hmac.new(self._key, message, hashlib.sha256).digest()

    def _challenge(self, purpose: str, binding: str, rp_id: str, handle: bytes = b"") -> bytes:
        """
        Sign a new challenge.

        Args:
            purpose: One of :data:`PURPOSES`.
            binding: The session it belongs to, or empty for a sign-in.
            rp_id: The RP ID it is for.
            handle: For a registration, the user handle the options carry, so
                the answer can be stored with it without keeping any state.

        Returns:
            ``version | nonce | expiry | handle length | handle | MAC``.
        """
        expires = int(self.now()) + CHALLENGE_SECONDS
        body = (
            bytes([_CHALLENGE_VERSION])
            + secrets.token_bytes(_NONCE_BYTES)
            + struct.pack(">Q", expires)
            + bytes([len(handle)])
            + handle
        )
        return body + self._mac(purpose, binding, rp_id, body)

    def _spend(
        self, purpose: str, binding: str, rp_id: str, captured: dict[str, bytes]
    ) -> Callable[[bytes], bool]:
        """
        Build the check a ceremony's challenge goes through.

        Args:
            purpose: What it must have been issued for.
            binding: The session it must belong to.
            rp_id: The RP ID it must have been issued for.
            captured: Receives the user handle a registration challenge carries.

        Returns:
            A callable that answers whether a challenge is valid, recording its
            nonce as spent when it is.
        """

        def check(challenge: bytes) -> bool:
            head = 1 + _NONCE_BYTES + 8 + 1
            if len(challenge) < head + _MAC_BYTES or challenge[0] != _CHALLENGE_VERSION:
                return False
            handle_length = challenge[head - 1]
            body_length = head + handle_length
            if len(challenge) != body_length + _MAC_BYTES:
                return False
            body, mac = challenge[:body_length], challenge[body_length:]
            if not hmac.compare_digest(mac, self._mac(purpose, binding, rp_id, body)):
                return False
            nonce = body[1 : 1 + _NONCE_BYTES]
            (expires,) = struct.unpack(">Q", body[1 + _NONCE_BYTES : head - 1])
            now = self.now()
            if expires < now:
                return False
            try:
                with self._write() as cursor:
                    cursor.execute("DELETE FROM webauthn_spent WHERE expires_at < ?", (now,))
                    cursor.execute(
                        "INSERT INTO webauthn_spent (nonce, expires_at) VALUES (?, ?)",
                        (nonce, float(expires)),
                    )
            except sqlite3.IntegrityError:
                return False
            captured["handle"] = body[head:]
            return True

        return check

    # --------------------------------------------------------- registration

    def _handle_for(self, account_id: int | None) -> bytes:
        """
        Args:
            account_id: An account, or None for the master token.

        Returns:
            The owner's user handle: the one its passkeys carry, or a new
            random one for its first.
        """
        where, params = _owner_clause(account_id)
        rows = self._rows(
            f"SELECT p.user_handle FROM account_passkeys p WHERE {where} ORDER BY p.id LIMIT 1",  # noqa: S608 - fixed clauses
            params,
        )
        if rows:
            return bytes(rows[0]["user_handle"])
        return secrets.token_bytes(USER_HANDLE_BYTES)

    def _descriptors(self, account_id: int | None, rp_id: str) -> builtins.list[dict[str, Any]]:
        """
        Args:
            account_id: An account, or None for the master token.
            rp_id: The RP ID.

        Returns:
            That owner's credentials under that RP ID, as descriptors for
            ``excludeCredentials`` or ``allowCredentials``.
        """
        return [
            {
                "type": "public-key",
                "id": b64url_encode(passkey.credential_id),
                **({"transports": passkey.transports} if passkey.transports else {}),
            }
            for passkey in self.list(account_id)
            if passkey.rp_id == rp_id
        ]

    def registration_options(
        self, owner: PasskeyOwner, rp: RelyingParty, *, binding: str
    ) -> dict[str, Any]:
        """
        Build the options ``navigator.credentials.create()`` takes.

        Args:
            owner: Whose passkey it will be.
            rp: This console as a relying party.
            binding: The session the ceremony belongs to.

        Returns:
            A PublicKeyCredentialCreationOptionsJSON.

        Raises:
            PasskeysUnavailable: ``library_missing``.
        """
        require_library()
        handle = self._handle_for(owner.account_id)
        host = self._host()
        greeting = owner.display_name or owner.username
        return {
            "rp": {"id": rp.id, "name": rp.name},
            "user": {
                "id": b64url_encode(handle),
                "name": f"{owner.username}@{host}",
                "displayName": f"{greeting} (Noust on {host})",
            },
            "challenge": b64url_encode(self._challenge("register", binding, rp.id, handle)),
            "pubKeyCredParams": [
                {"type": "public-key", "alg": alg} for alg in supported_algorithms()
            ],
            "timeout": CEREMONY_TIMEOUT_MS,
            "excludeCredentials": self._descriptors(owner.account_id, rp.id),
            "authenticatorSelection": {
                "residentKey": "required",
                "requireResidentKey": True,
                "userVerification": "required",
            },
            "attestation": "none",
            "extensions": {"credProps": True},
        }

    def register(
        self,
        owner: PasskeyOwner,
        rp: RelyingParty,
        credential: Mapping[str, Any],
        *,
        name: str,
        binding: str,
        created_by: str | None,
    ) -> Registration:
        """
        Verify a new passkey and keep it.

        Args:
            owner: Whose passkey it is.
            rp: This console as a relying party.
            credential: The RegistrationResponseJSON.
            name: What the owner calls it.
            binding: The session the ceremony belongs to.
            created_by: Who registered it, for the record.

        Returns:
            The passkey, and the account's backup codes when this was its first
            second factor.

        Raises:
            ValidationError: When the name is refused.
            AccountError: When the owner has too many passkeys, the passkey is
                already registered, or it may be synced and this server only
                accepts device-bound ones.
            WebAuthnError: When the ceremony does not verify.
            PasskeysUnavailable: ``library_missing``.
        """
        label = clean_name(name)
        require_library()
        if self.count(owner.account_id) >= MAX_PASSKEYS_PER_OWNER:
            raise AccountError(
                f"There are already {MAX_PASSKEYS_PER_OWNER} passkeys for this account",
                details="Remove one you no longer use before adding another.",
            )
        captured: dict[str, bytes] = {}
        verified = verify_registration(
            credential,
            rp_id=rp.id,
            origins=rp.origins,
            challenge_ok=self._spend("register", binding, rp.id, captured),
        )
        if verified.backup_eligible and not self._allows_synced():
            raise AccountError(
                "This server only accepts passkeys bound to one device, and this one may be synced",
                details=(
                    "Use a security key or a device-bound authenticator, or allow synced "
                    "passkeys with web.passkeys.allow_synced."
                ),
            )
        handle = captured.get("handle") or self._handle_for(owner.account_id)
        now = self.now()
        try:
            with self._write() as cursor:
                cursor.execute(
                    "INSERT INTO account_passkeys (account_id, credential_id, user_handle, rp_id, "
                    "public_key, alg, sign_count, backup_eligible, backup_state, transports, "
                    "aaguid, name, created_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        owner.account_id,
                        verified.credential_id,
                        handle,
                        rp.id,
                        verified.public_key,
                        verified.alg,
                        verified.sign_count,
                        int(verified.backup_eligible),
                        int(verified.backup_state),
                        json.dumps(verified.transports),
                        verified.aaguid,
                        label,
                        now,
                        created_by,
                    ),
                )
                passkey_id = int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            raise AccountError(
                "This passkey is already registered here",
                details="Each passkey is added once; give another authenticator a try.",
            ) from exc
        passkey = self.get(passkey_id)
        if passkey is None:
            raise AccountError("The passkey was not saved", details="Try again.")
        return Registration(passkey=passkey, backup_codes=self._first_codes(owner.account_id))

    def _first_codes(self, account_id: int | None) -> builtins.list[str] | None:
        """
        Issue backup codes to an account whose first second factor this is.

        Args:
            account_id: The account, or None for the master token (whose
                recovery is root's ``noust passkey reset --master``).

        Returns:
            The codes in clear, or None when the owner already had some.
        """
        if account_id is None:
            return None
        from noust.core.accounts.manager import AccountManager

        accounts = AccountManager(self.store)
        account = accounts.get(int(account_id))
        if account is None or account.backup_codes_remaining > 0:
            return None
        return accounts.regenerate_backup_codes(account.id)

    # --------------------------------------------------------------- use

    def authentication_options(
        self,
        rp: RelyingParty,
        *,
        purpose: str,
        binding: str,
        account_id: int | None = None,
        any_owner: bool = False,
    ) -> dict[str, Any]:
        """
        Build the options ``navigator.credentials.get()`` takes.

        A sign-in lists no credential: the passkey is discoverable, and a list
        would tell an anonymous caller who has one. A confirmation lists the
        owner's, so another server's passkey reached as ``localhost`` is not
        offered.

        Args:
            rp: This console as a relying party.
            purpose: ``login`` or ``elevate``.
            binding: The session a confirmation belongs to; empty for a sign-in.
            account_id: The owner a confirmation is for; None for the master token.
            any_owner: List nothing (a sign-in).

        Returns:
            A PublicKeyCredentialRequestOptionsJSON.

        Raises:
            PasskeysUnavailable: ``library_missing``.
        """
        require_library()
        if purpose not in PURPOSES or purpose == "register":
            raise ValueError(f"Not an assertion purpose: {purpose!r}")
        allowed = [] if any_owner or purpose == "login" else self._descriptors(account_id, rp.id)
        return {
            "challenge": b64url_encode(self._challenge(purpose, binding, rp.id)),
            "rpId": rp.id,
            "timeout": CEREMONY_TIMEOUT_MS,
            "userVerification": "required",
            "allowCredentials": allowed,
        }

    def authenticate(
        self,
        rp: RelyingParty,
        credential: Mapping[str, Any],
        *,
        purpose: str,
        binding: str,
        client_ip: str | None,
        account_id: int | None = None,
        any_owner: bool = False,
    ) -> Passkey:
        """
        Verify an assertion and record the use.

        Args:
            rp: This console as a relying party.
            credential: The AuthenticationResponseJSON.
            purpose: ``login`` or ``elevate``.
            binding: The session the ceremony belongs to; empty for a sign-in.
            client_ip: Where it came from.
            account_id: The owner it must belong to, unless ``any_owner``.
            any_owner: Accept any owner's passkey (a sign-in says who by it).

        Returns:
            The passkey, as it is after the use.

        Raises:
            WebAuthnError: When it does not verify; ``wrong_owner`` for another
                owner's passkey, ``counter`` (recorded on the passkey) for a
                counter that went back.
            PasskeysUnavailable: ``library_missing``.
        """
        require_library()
        found: dict[str, Passkey] = {}

        def lookup(credential_id: bytes) -> StoredCredential | None:
            sql = f"SELECT {_COLUMNS} FROM {_FROM} WHERE p.credential_id = ?"  # noqa: S608 - fixed clauses
            rows = self._rows(sql, (credential_id,))
            if not rows:
                return None
            passkey = _passkey(rows[0])
            if not any_owner and passkey.account_id != account_id:
                raise WebAuthnError(
                    "wrong_owner",
                    "This passkey belongs to someone else",
                    credential_id=credential_id,
                )
            found["passkey"] = passkey
            public_key = rows[0]["public_key"]
            return StoredCredential(
                credential_id=passkey.credential_id,
                public_key=bytes(public_key),
                sign_count=passkey.sign_count,
                backup_eligible=passkey.backup_eligible,
                user_handle=passkey.user_handle,
                rp_id=passkey.rp_id,
            )

        try:
            verified = verify_assertion(
                credential,
                rp_id=rp.id,
                origins=rp.origins,
                challenge_ok=self._spend(purpose, binding, rp.id, {}),
                lookup=lookup,
                require_user_handle=purpose == "login",
            )
        except WebAuthnError as exc:
            if exc.reason == "counter" and "passkey" in found:
                with self._write() as cursor:
                    cursor.execute(
                        "UPDATE account_passkeys SET clone_warning_at = ? WHERE id = ?",
                        (self.now(), found["passkey"].id),
                    )
            raise
        passkey = found["passkey"]
        with self._write() as cursor:
            cursor.execute(
                "UPDATE account_passkeys SET sign_count = ?, backup_state = ?, last_used_at = ?, "
                "last_used_ip = ? WHERE id = ?",
                (
                    verified.sign_count,
                    int(verified.backup_state),
                    self.now(),
                    client_ip,
                    passkey.id,
                ),
            )
        return self.get(passkey.id) or passkey

    # --------------------------------------------------------- governance

    def _owned(self, passkey_id: int, account_id: int | None) -> Passkey:
        """
        Args:
            passkey_id: A passkey's id.
            account_id: The owner acting; None for the master token.

        Returns:
            The passkey, when it is that owner's.

        Raises:
            AccountError: When there is no such passkey of that owner - one
                answer for both, so an owner cannot probe for others' ids.
        """
        passkey = self.get(passkey_id)
        if passkey is None or passkey.account_id != account_id:
            raise AccountError(
                f"You have no passkey with id {passkey_id}",
                details="List yours with GET /api/auth/passkeys.",
            )
        return passkey

    def rename(self, passkey_id: int, name: str, *, account_id: int | None) -> Passkey:
        """
        Rename one of an owner's passkeys.

        Args:
            passkey_id: Its id.
            name: The new name.
            account_id: The owner acting; None for the master token.

        Returns:
            The passkey after the change.

        Raises:
            ValidationError: When the name is refused.
            AccountError: When it is not that owner's.
        """
        label = clean_name(name)
        passkey = self._owned(passkey_id, account_id)
        with self._write() as cursor:
            cursor.execute("UPDATE account_passkeys SET name = ? WHERE id = ?", (label, passkey.id))
        return self.get(passkey.id) or passkey

    def remove(self, passkey_id: int, *, account_id: int | None, force: bool = False) -> Passkey:
        """
        Remove a passkey.

        Args:
            passkey_id: Its id.
            account_id: The owner acting; None for the master token. Ignored
                with ``force``.
            force: Remove it whoever owns it and whatever it leaves: root's
                ``noust passkey remove``.

        Returns:
            The passkey as it was.

        Raises:
            AccountError: When it is not that owner's, or it is an account's
                only second factor (every account keeps one; a security officer
                replaces it with ``reset-mfa``).
        """
        if force:
            passkey = self.get(passkey_id)
            if passkey is None:
                raise AccountError(
                    f"No passkey with id {passkey_id}",
                    details="List them with 'noust passkey list'.",
                )
        else:
            passkey = self._owned(passkey_id, account_id)
            if passkey.account_id is not None and self._only_factor(passkey.account_id):
                raise AccountError(
                    "This passkey is your only second factor",
                    details=(
                        "Add another passkey or an authenticator first. If you lost the others, "
                        "ask a security officer to reset your second factor."
                    ),
                )
        with self._write() as cursor:
            cursor.execute("DELETE FROM account_passkeys WHERE id = ?", (passkey.id,))
        return passkey

    def _only_factor(self, account_id: int) -> bool:
        """
        Args:
            account_id: An account.

        Returns:
            True when one passkey is all the second factor it has.
        """
        rows = self._rows("SELECT totp_secret FROM accounts WHERE id = ?", (int(account_id),))
        has_totp = bool(rows and rows[0]["totp_secret"])
        return not has_totp and self.count(account_id) <= 1

    def reset(self, account_id: int | None) -> int:
        """
        Remove every passkey of one owner: root's recovery lever.

        Args:
            account_id: An account, or None for the master token.

        Returns:
            How many were removed.
        """
        where, params = _owner_clause(account_id)
        with self._write() as cursor:
            cursor.execute(
                f"DELETE FROM account_passkeys WHERE id IN (SELECT p.id FROM account_passkeys p WHERE {where})",  # noqa: S608 - fixed clauses
                params,
            )
            return int(cursor.rowcount or 0)
