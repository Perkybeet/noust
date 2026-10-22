# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Small secrets encrypted under a key kept in a file of their own, with nothing but the standard library.

Two kinds of secret have to be readable by a process nobody is watching: the
second factor of an account (checked at every sign-in) and the credentials of a
backup destination (used by a timer at three in the morning). A passphrase
that someone types cannot protect those, so they are encrypted under a random
key kept apart from what it protects: a copy of the store, of the secrets
directory, or of an incident package then discloses nothing.

The standard library has no block cipher, and spawning openssl for a value that
is read in the login path or inside a loop of uploads is a poor trade, so the
construction is built from HMAC-SHA256 alone: the keystream is HMAC-SHA256 of a
random nonce and a counter (a PRF in counter mode, the construction of HKDF's
expand step), XORed with the secret, and the result is authenticated with
HMAC-SHA256 under a second key over a context that binds it to what it belongs
to (encrypt-then-MAC). A secret moved to another owner's row, or changed by one
bit, is refused before anything reads it. A passphrase-sealed store
(:mod:`noust.core.sealing`) is the other construction, for a central whose
operator is there to unlock it; this one is for a server that must work alone.

This module is the one implementation (rule 3): :mod:`noust.core.totp` seals an
account's second factor with it and :class:`~noust.core.secrets.SecretStore`
the secrets that say so. Each use is a :class:`Domain`, whose label keeps the
keys, the prefix and the MACs of one use from ever opening another's.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path

#: Owner-only, from the moment a key file exists.
KEY_MODE = 0o600

_NONCE_BYTES = 16
_TAG_BYTES = 32
_KEY_BYTES = 32


class AtRestError(ValueError):
    """A secret cannot be opened: altered, moved, or the key is not its key."""


def load_key(path: Path, *, create: bool) -> bytes | None:
    """
    Read a key, creating it when asked and missing.

    Written with ``O_EXCL`` and :data:`KEY_MODE` so it is never readable by
    anyone else, not even for an instant, and two processes creating it at once
    agree on one key. Never follows a symlink.

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
        raise OSError(f"{path} does not hold a key") from exc
    if len(key) != _KEY_BYTES:
        raise OSError(f"{path} holds a key that is not 256 bits")
    return key


@dataclass(frozen=True)
class Domain:
    """
    One use of the construction, with a name that keeps it apart from the others.

    Attributes:
        prefix: What a value stored encrypted starts with; one without it is
            from before encryption at rest and is read as it is.
        label: Mixed into the two derived keys and into every MAC, so a key,
            a value or a tag of one domain never verifies in another.
        noun: What the value is, for the error messages (``TOTP secret``).
        error: The exception raised when a value cannot be opened.
    """

    prefix: str
    label: str
    noun: str
    error: type[AtRestError] = AtRestError

    def _subkeys(self, key: bytes) -> tuple[bytes, bytes]:
        """
        Derive the encryption and MAC keys, so neither is ever used for both.

        Args:
            key: The stored key.

        Returns:
            ``(encryption key, MAC key)``.
        """
        return (
            hmac.new(key, f"{self.label}-encrypt".encode(), hashlib.sha256).digest(),
            hmac.new(key, f"{self.label}-authenticate".encode(), hashlib.sha256).digest(),
        )

    @staticmethod
    def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
        """
        HMAC-SHA256 of the nonce and a block counter, as long as the plaintext.

        Args:
            key: The encryption key.
            nonce: This value's random nonce.
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

    def _tag(self, key: bytes, context: str, nonce: bytes, ciphertext: bytes) -> bytes:
        """
        Authenticate a sealed value and what it belongs to.

        Args:
            key: The MAC key.
            context: What it is bound to, such as ``account:17``.
            nonce: Its nonce.
            ciphertext: Its ciphertext.

        Returns:
            The HMAC-SHA256.
        """
        message = f"{self.label}:v1\0".encode() + context.encode("utf-8") + b"\0"
        return hmac.new(key, message + nonce + ciphertext, hashlib.sha256).digest()

    def is_sealed(self, stored: str | None) -> bool:
        """
        Report whether a stored value is encrypted.

        Args:
            stored: What the column or the file holds.

        Returns:
            True when it carries this domain's prefix.
        """
        return bool(stored) and str(stored).startswith(self.prefix)

    def seal(self, plaintext: str, key: bytes, context: str) -> str:
        """
        Encrypt and authenticate a value for storage.

        Args:
            plaintext: The value (any text).
            key: The domain's key.
            context: What it belongs to; opening it under another context fails.

        Returns:
            The value to store.
        """
        encrypt, authenticate = self._subkeys(key)
        nonce = secrets.token_bytes(_NONCE_BYTES)
        data = plaintext.encode("utf-8")
        ciphertext = bytes(
            a ^ b for a, b in zip(data, self._keystream(encrypt, nonce, len(data)), strict=True)
        )
        tag = self._tag(authenticate, context, nonce, ciphertext)
        return self.prefix + base64.urlsafe_b64encode(nonce + ciphertext + tag).decode("ascii")

    def open(self, stored: str, key: bytes | None, context: str, *, missing_key: str = "") -> str:
        """
        Read a stored value.

        Args:
            stored: What is stored; one without the prefix is from before
                encryption at rest, returned as it is.
            key: The domain's key; None when there is none.
            context: What it belongs to, as it was sealed.
            missing_key: What to add to the message when the key is missing
                (where to restore it from).

        Returns:
            The value.

        Raises:
            AtRestError: (This domain's ``error``) It is sealed and the key is
                missing, is not its key, or the value was altered or moved.
        """
        if not self.is_sealed(stored):
            return stored
        if key is None:
            raise self.error(
                f"The {self.noun}s are encrypted and their key is missing. {missing_key}".strip()
            )
        try:
            raw = base64.urlsafe_b64decode(stored[len(self.prefix) :].encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise self.error(f"A stored {self.noun} is damaged: it is not base64.") from exc
        if len(raw) <= _NONCE_BYTES + _TAG_BYTES:
            raise self.error(f"A stored {self.noun} is damaged: it is too short.")
        nonce, ciphertext, tag = (
            raw[:_NONCE_BYTES],
            raw[_NONCE_BYTES:-_TAG_BYTES],
            raw[-_TAG_BYTES:],
        )
        encrypt, authenticate = self._subkeys(key)
        if not hmac.compare_digest(self._tag(authenticate, context, nonce, ciphertext), tag):
            raise self.error(
                f"A stored {self.noun} failed its integrity check: it was altered, moved from "
                "another owner, or sealed under another key."
            )
        plaintext = bytes(
            a ^ b
            for a, b in zip(
                ciphertext, self._keystream(encrypt, nonce, len(ciphertext)), strict=True
            )
        )
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise self.error(f"A stored {self.noun} is damaged: it is not text.") from exc
