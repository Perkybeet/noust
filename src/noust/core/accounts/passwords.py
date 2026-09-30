# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Password hashing and policy, with nothing but the standard library.

``hashlib.scrypt`` is memory-hard and has been in the standard library since
Python 3.6; the sealing of a central's secrets (:mod:`noust.core.sealing`)
already uses it with the same cost, so this adds no dependency. A hash is
stored as ``scrypt$v1$<n>$<r>$<p>$<salt>$<hash>`` (salt and hash in unpadded
URL-safe base64): the parameters travel with the hash, so raising the cost
later leaves every existing password verifiable, and :func:`needs_rehash`
says which to upgrade at their next sign-in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import socket
import threading

from noust.core.exceptions import ValidationError

SCHEME = "scrypt"
VERSION = "v1"

#: The cost every new hash is made with: 2^15 iterations of an 8-block
#: mixing function, one lane - 32 MiB and about 50 ms on a server core, the
#: parameters RFC 7914 and OWASP give for interactive logins. Tests lower
#: ``COST`` to keep the suite fast; production never changes it at runtime.
COST: tuple[int, int, int] = (2**15, 8, 1)
SALT_BYTES = 16
KEY_BYTES = 32

#: Longest password accepted. A bound keeps a megabyte "password" from being a
#: way to make the server hash a megabyte; nobody types more than this.
MAX_PASSWORD_LENGTH = 1024

_dummy_lock = threading.Lock()
_dummy_hash: str | None = None


def _b64(data: bytes) -> str:
    """
    Args:
        data: Raw bytes.

    Returns:
        Unpadded URL-safe base64.
    """
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    """
    Args:
        text: Unpadded URL-safe base64.

    Returns:
        The bytes it encodes.
    """
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """
    Run scrypt with enough memory allowed for its cost.

    Args:
        password: The password.
        salt: Random salt.
        n: CPU/memory cost.
        r: Block size.
        p: Parallelism.

    Returns:
        The derived key.
    """
    # OpenSSL's default ceiling is 32 MiB, exactly what n=2^15, r=8 needs, and
    # it refuses at the boundary; twice the requirement is always enough.
    maxmem = 2 * 128 * n * r * p + 1024 * 1024
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=maxmem
    )


def hash_password(password: str) -> str:
    """
    Hash a password for storage.

    Args:
        password: The password in clear.

    Returns:
        The encoded hash, parameters and salt included.
    """
    n, r, p = COST
    salt = secrets.token_bytes(SALT_BYTES)
    key = _derive(password, salt, n, r, p)
    return f"{SCHEME}${VERSION}${n}${r}${p}${_b64(salt)}${_b64(key)}"


def _parse(encoded: str) -> tuple[int, int, int, bytes, bytes] | None:
    """
    Split an encoded hash into its parameters.

    Args:
        encoded: A value :func:`hash_password` produced.

    Returns:
        ``(n, r, p, salt, key)``, or None when it is not one of ours.
    """
    parts = encoded.split("$")
    if len(parts) != 7 or parts[0] != SCHEME or parts[1] != VERSION:
        return None
    try:
        n, r, p = int(parts[2]), int(parts[3]), int(parts[4])
        salt, key = _unb64(parts[5]), _unb64(parts[6])
    except ValueError:
        return None
    # A stored hash is ours, but a store is a file: parameters that would make
    # a verification take minutes or gigabytes are refused, not honoured.
    if not (2 <= n <= 2**20 and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16):
        return None
    return n, r, p, salt, key


def verify_password(password: str, encoded: str | None) -> bool:
    """
    Check a password against a stored hash, in constant time.

    When there is no hash (an unknown account, one that has none yet) a
    dummy hash is verified instead, so that the answer takes as long as a
    real one and timing does not tell which accounts exist.

    Args:
        password: What was typed.
        encoded: The stored hash, or None.

    Returns:
        True only when a real stored hash matches.
    """
    parsed = _parse(encoded) if encoded else None
    if parsed is None or len(password) > MAX_PASSWORD_LENGTH:
        _burn(password[:MAX_PASSWORD_LENGTH])
        return False
    n, r, p, salt, key = parsed
    return hmac.compare_digest(_derive(password, salt, n, r, p), key)


def _burn(password: str) -> None:
    """
    Spend the time a real verification takes, for a verification that cannot succeed.

    Args:
        password: What was typed, hashed against a throwaway salt.
    """
    global _dummy_hash
    with _dummy_lock:
        if _dummy_hash is None:
            _dummy_hash = hash_password(secrets.token_urlsafe(16))
        dummy = _dummy_hash
    parsed = _parse(dummy)
    if parsed is not None:
        n, r, p, salt, _key = parsed
        _derive(password, salt, n, r, p)


def needs_rehash(encoded: str | None) -> bool:
    """
    Report whether a stored hash was made with other parameters than today's.

    Args:
        encoded: The stored hash.

    Returns:
        True when it should be replaced at the next successful sign-in.
    """
    parsed = _parse(encoded) if encoded else None
    return parsed is None or parsed[:3] != COST


def check_password_policy(password: str, *, username: str, min_length: int) -> None:
    """
    Refuse a password that is too easy to guess.

    Length, and nothing about composition: NIST SP 800-63B and the ENS guide
    both favour long passphrases over character-class rules, which only make
    people write passwords down.

    Args:
        password: The candidate password.
        username: The account it is for; the password may not contain it.
        min_length: The shortest length allowed (12, or 14 under the ENS
            profile).

    Raises:
        ValidationError: With what to change, when the password is refused.
    """
    if len(password) < min_length:
        raise ValidationError(
            f"The password is shorter than {min_length} characters",
            details="Use a longer passphrase: several unrelated words are easy to remember.",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(
            f"The password is longer than {MAX_PASSWORD_LENGTH} characters",
            details="Use a shorter passphrase.",
        )
    lowered = password.lower()
    if len(set(password)) < 4:
        raise ValidationError(
            "The password repeats too few different characters",
            details="Use a passphrase with more variety.",
        )
    if len(username) >= 3 and username.lower() in lowered:
        raise ValidationError(
            "The password contains the username",
            details="Use a passphrase that does not include the account's name.",
        )
    hostname = socket.gethostname().split(".", 1)[0].lower()
    if len(hostname) >= 4 and hostname in lowered:
        raise ValidationError(
            "The password contains this server's name",
            details="Use a passphrase that does not include the machine's name.",
        )
