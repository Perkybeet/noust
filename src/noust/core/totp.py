# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Time-based one-time passwords, RFC 6238, with nothing but the standard library.

The panel's second factor is ~60 lines of ``hmac`` + ``struct`` + ``base64``,
which is the whole algorithm. A dependency here would have to be declared in
four packaging files and exist on every target distribution, and ``pyotp`` is
not packaged everywhere Noust ships; the RFC is shorter than that negotiation.

SHA-1 is what the RFC specifies and what every authenticator app implements.
Its collision weakness is irrelevant to HMAC truncated to six digits, so this
is not the place to be original.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

from noust.core import at_rest
from noust.core.at_rest import AtRestError, Domain

#: 160 bits of secret, the length RFC 4226 recommends for HMAC-SHA1. It also
#: encodes to exactly 32 base32 characters, so the secret never needs padding.
SECRET_BYTES = 20

#: The time step and code length every authenticator app defaults to.
PERIOD = 30
DIGITS = 6


def generate_secret() -> str:
    """
    Generate a shared secret for enrolment.

    Returns:
        The secret, base32-encoded without padding, ready to be typed into an
        authenticator app or embedded in a provisioning URI.
    """
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode_secret(secret_b32: str) -> bytes:
    """
    Decode a base32 secret, tolerating missing padding and stray spaces.

    Authenticator apps display secrets in spaced groups and without padding,
    and an operator typing one back should not be failed over either.

    Args:
        secret_b32: The base32-encoded secret.

    Returns:
        The raw key bytes.

    Raises:
        binascii.Error: When the value is not base32 at all. The secret is
            server-generated, so this is a corrupt state file, not user input.
    """
    compact = secret_b32.strip().replace(" ", "")
    padded = compact + "=" * (-len(compact) % 8)
    return base64.b32decode(padded, casefold=True)


def _hotp(secret_b32: str, counter: int, digits: int = DIGITS) -> str:
    """
    Compute one HOTP value, RFC 4226 section 5.

    Args:
        secret_b32: The base32-encoded shared secret.
        counter: The moving factor.
        digits: Length of the code.

    Returns:
        The code, zero-padded to ``digits``.
    """
    key = _decode_secret(secret_b32)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)


def totp_now(secret_b32: str, *, t: float | None = None, digits: int = DIGITS) -> str:
    """
    Compute the TOTP value for a moment in time.

    Args:
        secret_b32: The base32-encoded shared secret.
        t: UNIX timestamp to compute for; the current time when omitted.
        digits: Length of the code.

    Returns:
        The code an authenticator app would show at that moment.
    """
    moment = time.time() if t is None else t
    return _hotp(secret_b32, int(moment // PERIOD), digits)


def matched_step(
    secret_b32: str, code: str, *, window: int = 1, t: float | None = None
) -> int | None:
    """
    Find the time step a code belongs to, allowing for clock drift.

    Every candidate in the window is compared in constant time, and all of
    them are computed whether or not an earlier one already matched, so the
    comparison leaks nothing about which step a code belongs to. The step is
    what a verifier remembers to refuse the same code twice (RFC 6238, 5.2).

    Args:
        secret_b32: The base32-encoded shared secret.
        code: The code the client typed.
        window: Steps of drift tolerated on either side. The default accepts
            the previous and the next 30-second step, which is what a phone a
            few seconds off needs and no more.
        t: UNIX timestamp to verify against; the current time when omitted.

    Returns:
        The step number (UNIX time divided by :data:`PERIOD`) the code is
        valid for, or None when it is valid for no step inside the window.
    """
    candidate = code.strip()
    if not candidate.isdigit() or len(candidate) != DIGITS or not secret_b32:
        return None

    now = int((time.time() if t is None else t) // PERIOD)
    found: int | None = None
    for offset in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret_b32, now + offset), candidate) and found is None:
            found = now + offset
    return found


#: The purposes whose spent steps a code for a purpose has to be newer than, besides its own.
#: Sudo mode takes a code without the password, so a code someone typed at sign-in (and that
#: anyone looking over the shoulder read) would still be good for it inside its window; an
#: elevation code therefore has to come after the one that signed in.
STEP_FLOORS: dict[str, tuple[str, ...]] = {"elevate": ("login",)}


def newest_spent_step(spent: Mapping[str, int], purpose: str) -> int | None:
    """
    The step a code for a purpose has to be newer than.

    Args:
        spent: The last step accepted per purpose.
        purpose: What the code is being spent on.

    Returns:
        The highest step spent for the purpose or for any purpose it has to follow
        (:data:`STEP_FLOORS`); None when none has been spent.
    """
    steps = [spent[name] for name in (purpose, *STEP_FLOORS.get(purpose, ())) if name in spent]
    return max(steps) if steps else None


def verify(secret_b32: str, code: str, *, window: int = 1, t: float | None = None) -> bool:
    """
    Check a code against the secret, allowing for clock drift.

    Args:
        secret_b32: The base32-encoded shared secret.
        code: The code the client typed.
        window: Steps of drift tolerated on either side.
        t: UNIX timestamp to verify against; the current time when omitted.

    Returns:
        True when the code is valid for some step inside the window. Says
        nothing about whether it was used before; see :func:`matched_step`.
    """
    return matched_step(secret_b32, code, window=window, t=t) is not None


def provisioning_uri(secret_b32: str, *, issuer: str = "Noust", account: str = "admin") -> str:
    """
    Build the ``otpauth://`` URI an authenticator app enrols from.

    Args:
        secret_b32: The base32-encoded shared secret.
        issuer: Name the app files the account under.
        account: Name of the account itself; the hostname reads best for a
            panel, so an operator with several servers can tell them apart.

    Returns:
        The URI, with every component percent-encoded, suitable for a QR code
        or for pasting into an app by hand.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={quote(secret_b32, safe='')}"
        f"&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )


# -- Secrets at rest (ENS G22, op.exp.10) ------------------------------------------
#
# A copy of the store - in a backup, an incident package, a support bundle -
# must not carry every account's second factor. The secrets are encrypted
# under a key kept in a file of its own beside the store (never in it), so
# the database file alone discloses nothing. The construction is
# :mod:`noust.core.at_rest`'s, the one implementation of encrypting a small
# secret under a key file; this module only names its domain, which keeps the
# bytes it has always stored.


class TotpSealError(AtRestError):
    """A stored TOTP secret cannot be opened: altered, moved, or the key is not its key."""


_DOMAIN = Domain(
    prefix="noust-totp:v1:",
    label="noust-totp",
    noun="TOTP secret",
    error=TotpSealError,
)

#: Prefix of a secret stored encrypted. A secret without it is from before
#: encryption at rest and is read as it is, then sealed.
SEALED_PREFIX = _DOMAIN.prefix

#: The key's file name, in the store's directory.
KEY_FILE_NAME = "totp.key"

#: Owner-only, from the moment the file exists.
KEY_MODE = at_rest.KEY_MODE


def key_path(store_path: Path) -> Path:
    """
    Where the key of a store's TOTP secrets lives.

    Args:
        store_path: The store's database file.

    Returns:
        :data:`KEY_FILE_NAME` in the store's directory.
    """
    return store_path.parent / KEY_FILE_NAME


def load_key(path: Path, *, create: bool) -> bytes | None:
    """
    Read the key, creating it when asked and missing.

    Args:
        path: The key file.
        create: Create a key when there is none.

    Returns:
        The 32-byte key, or None when there is none and ``create`` is false.

    Raises:
        OSError: The file exists and cannot be read, or holds no key.
    """
    return at_rest.load_key(path, create=create)


def is_sealed(stored: str | None) -> bool:
    """
    Report whether a stored secret is encrypted.

    Args:
        stored: The column's value.

    Returns:
        True when it carries :data:`SEALED_PREFIX`.
    """
    return _DOMAIN.is_sealed(stored)


def seal_secret(secret_b32: str, key: bytes, context: str) -> str:
    """
    Encrypt and authenticate a TOTP secret for the store.

    Args:
        secret_b32: The secret, base32.
        key: The store's TOTP key.
        context: What it belongs to, such as ``account:17``; opening it under
            another context fails.

    Returns:
        The value to store.
    """
    return _DOMAIN.seal(secret_b32, key, context)


def open_secret(stored: str, key: bytes | None, context: str) -> str:
    """
    Read a stored TOTP secret.

    Args:
        stored: The column's value; one without :data:`SEALED_PREFIX` is a
            secret stored in clear before encryption at rest, returned as it is.
        key: The store's TOTP key; None when there is none.
        context: What it belongs to, as it was sealed.

    Returns:
        The secret, base32.

    Raises:
        TotpSealError: It is sealed and the key is missing, is not its key, or
            the value was altered or moved from another account.
    """
    return _DOMAIN.open(
        stored,
        key,
        context,
        missing_key=(
            f"Restore {KEY_FILE_NAME} beside the store, or reset the account's second factor."
        ),
    )
