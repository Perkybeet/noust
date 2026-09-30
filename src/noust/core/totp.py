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
import os
import secrets
import struct
import time
from pathlib import Path
from urllib.parse import quote

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
# the database file alone discloses nothing.
#
# The standard library has no block cipher, and spawning openssl on every
# sign-in would put a process in the login path, so the construction is
# built from HMAC-SHA256 alone: the keystream is HMAC-SHA256 of a random
# nonce and a counter (a PRF in counter mode, the construction of HKDF's
# expand step), XORed with the secret, and the result is authenticated with
# HMAC-SHA256 under a second key over a context that binds it to its
# account (encrypt-then-MAC). A secret moved to another account's row, or
# changed by one bit, is refused before anything reads it.

#: Prefix of a secret stored encrypted. A secret without it is from before
#: encryption at rest and is read as it is, then sealed.
SEALED_PREFIX = "noust-totp:v1:"

#: The key's file name, in the store's directory.
KEY_FILE_NAME = "totp.key"

#: Owner-only, from the moment the file exists.
KEY_MODE = 0o600

_NONCE_BYTES = 16
_TAG_BYTES = 32
_KEY_BYTES = 32


class TotpSealError(ValueError):
    """A stored TOTP secret cannot be opened: altered, moved, or the key is not its key."""


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

    Written with ``O_EXCL`` and :data:`KEY_MODE` so it is never readable by
    anyone else, not even for an instant, and two processes creating it at
    once agree on one key. Never follows a symlink.

    Args:
        path: The key file.
        create: Create a key when there is none.

    Returns:
        The 32-byte key, or None when there is none and ``create`` is false.

    Raises:
        OSError: The file exists and cannot be read, or holds no key.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except FileNotFoundError:
        if not create:
            return None
        key = secrets.token_bytes(_KEY_BYTES)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, KEY_MODE)
        except FileExistsError:
            return load_key(path, create=False)
        try:
            os.write(descriptor, key.hex().encode("ascii") + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return key
    with os.fdopen(descriptor, "rb") as handle:
        text = handle.read().decode("ascii", errors="replace").strip()
    try:
        key = bytes.fromhex(text)
    except ValueError as exc:
        raise OSError(f"{path} does not hold a TOTP key") from exc
    if len(key) != _KEY_BYTES:
        raise OSError(f"{path} holds a key that is not 256 bits")
    return key


def _subkeys(key: bytes) -> tuple[bytes, bytes]:
    """
    Derive the encryption and MAC keys, so neither is ever used for both.

    Args:
        key: The stored key.

    Returns:
        ``(encryption key, MAC key)``.
    """
    return (
        hmac.new(key, b"noust-totp-encrypt", hashlib.sha256).digest(),
        hmac.new(key, b"noust-totp-authenticate", hashlib.sha256).digest(),
    )


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """
    HMAC-SHA256 of the nonce and a block counter, as long as the plaintext.

    Args:
        key: The encryption key.
        nonce: This secret's random nonce.
        length: Bytes needed.

    Returns:
        The keystream.
    """
    blocks = bytearray()
    counter = 0
    while len(blocks) < length:
        blocks += hmac.new(key, nonce + struct.pack(">I", counter), hashlib.sha256).digest()
        counter += 1
    return bytes(blocks[:length])


def _tag(key: bytes, context: str, nonce: bytes, ciphertext: bytes) -> bytes:
    """
    Authenticate a sealed secret and what it belongs to.

    Args:
        key: The MAC key.
        context: What it is bound to, such as ``account:17``.
        nonce: Its nonce.
        ciphertext: Its ciphertext.

    Returns:
        The HMAC-SHA256.
    """
    message = b"noust-totp:v1\0" + context.encode("utf-8") + b"\0" + nonce + ciphertext
    return hmac.new(key, message, hashlib.sha256).digest()


def is_sealed(stored: str | None) -> bool:
    """
    Report whether a stored secret is encrypted.

    Args:
        stored: The column's value.

    Returns:
        True when it carries :data:`SEALED_PREFIX`.
    """
    return bool(stored) and str(stored).startswith(SEALED_PREFIX)


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
    encrypt, authenticate = _subkeys(key)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    plaintext = secret_b32.encode("ascii")
    ciphertext = bytes(
        a ^ b for a, b in zip(plaintext, _keystream(encrypt, nonce, len(plaintext)), strict=True)
    )
    tag = _tag(authenticate, context, nonce, ciphertext)
    return SEALED_PREFIX + base64.urlsafe_b64encode(nonce + ciphertext + tag).decode("ascii")


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
    if not is_sealed(stored):
        return stored
    if key is None:
        raise TotpSealError(
            "The TOTP secrets are encrypted and their key is missing: restore "
            f"{KEY_FILE_NAME} beside the store, or reset the account's second factor."
        )
    try:
        raw = base64.urlsafe_b64decode(stored[len(SEALED_PREFIX) :].encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise TotpSealError("A stored TOTP secret is damaged: it is not base64.") from exc
    if len(raw) <= _NONCE_BYTES + _TAG_BYTES:
        raise TotpSealError("A stored TOTP secret is damaged: it is too short.")
    nonce, ciphertext, tag = (
        raw[:_NONCE_BYTES],
        raw[_NONCE_BYTES:-_TAG_BYTES],
        raw[-_TAG_BYTES:],
    )
    encrypt, authenticate = _subkeys(key)
    if not hmac.compare_digest(_tag(authenticate, context, nonce, ciphertext), tag):
        raise TotpSealError(
            "A stored TOTP secret failed its integrity check: it was altered, moved from "
            "another account, or sealed under another key."
        )
    plaintext = bytes(
        a ^ b for a, b in zip(ciphertext, _keystream(encrypt, nonce, len(ciphertext)), strict=True)
    )
    return plaintext.decode("ascii")
