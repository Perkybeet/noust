# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The public keys that open this server: parsed, fingerprinted and classified.

Read in Python, never through ``ssh-keygen``: a line is options, a type, a
base64 blob and a comment, the fingerprint is ``SHA256:`` and the base64 of
the blob's SHA-256 without padding (what ``ssh-keygen -l`` prints), and an RSA
key's size is the bit length of its modulus inside the blob.

Each key is classified, because "a key" is not one thing here:

- ``operator``: no options; whoever holds it gets a shell.
- ``central``: a Noust central's tunnel key (the ``noust-central:`` comment or
  the ``permitlisten`` marker :mod:`noust.fleet.authorize` writes). It
  forwards one port and runs nothing; it never counts as a way in for a person.
- ``cloud_disabled``: the ``command="echo 'Please login as the user ...'"``
  line cloud images put in root's file; it logs nobody in.
- ``restricted``: any other options (``command=``, ``from=``, ``restrict``).

Where the files are comes from sshd itself (``AuthorizedKeysFile`` in
``sshd -T -C user=<account>``, which a ``Match User`` block can change), and
each file is checked the way ``StrictModes`` will check it, because a key sshd
silently ignores is the most common "I added my key and it does not work".
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import SecurityError
from noust.fleet.authorize import KEY_COMMENT_PREFIX, NO_LISTEN
from noust.managers.server.host import HostPaths
from noust.managers.server.security_accounts import Account

#: Key types an operator may add. DSA is gone from OpenSSH and never added.
ADDABLE_TYPES = frozenset(
    {
        "ssh-ed25519",
        "sk-ssh-ed25519@openssh.com",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ecdsa-sha2-nistp256@openssh.com",
        "ssh-rsa",
    }
)

#: Every type recognised when reading: the addable ones, DSA, and certificates.
KNOWN_TYPES = ADDABLE_TYPES | {"ssh-dss"}

#: Smallest RSA modulus that is not weak.
MIN_RSA_BITS = 2048

#: Longest key line accepted from an operator. A 16384-bit RSA line is ~2.8 KB.
MAX_KEY_LINE = 8192

#: The marker of a cloud image's root line that only prints "log in as ...".
CLOUD_REFUSAL_MARKER = "Please login as"


@dataclass(frozen=True)
class AuthorizedKey:
    """
    One key line of an ``authorized_keys`` file.

    Attributes:
        line_number: Where it is in its file, from 1.
        options: Its options, verbatim and in order.
        key_type: ``ssh-ed25519``, ``ssh-rsa``...
        blob: The base64 key.
        comment: The comment, verbatim; empty when none.
        fingerprint: ``SHA256:...``.
        bits: The key's size.
        kind: ``operator``, ``central``, ``cloud_disabled`` or ``restricted``.
        weak: Why the key is weak (DSA, RSA under 2048 bits), or empty.
    """

    line_number: int
    options: tuple[str, ...]
    key_type: str
    blob: str
    comment: str
    fingerprint: str
    bits: int | None
    kind: str
    weak: str = ""

    @property
    def line(self) -> str:
        """The key line, as it would be written back."""
        head = ",".join(self.options)
        body = f"{self.key_type} {self.blob} {self.comment}".rstrip()
        return f"{head} {body}" if head else body


def fingerprint_of(blob: str) -> str:
    """
    Fingerprint a key the way ``ssh-keygen -l`` does.

    Args:
        blob: The base64 key.

    Returns:
        ``SHA256:`` and the unpadded base64 of the blob's digest.

    Raises:
        SecurityError: The blob is not base64.
    """
    raw = _decode(blob)
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")


def _decode(blob: str) -> bytes:
    """
    Decode a key blob.

    Args:
        blob: The base64 text.

    Returns:
        The bytes.

    Raises:
        SecurityError: It is not base64.
    """
    try:
        return base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SecurityError("The key is damaged: its key is not base64") from exc


def _fields(raw: bytes) -> list[bytes]:
    """
    Split a key blob into its length-prefixed fields.

    Args:
        raw: The decoded blob.

    Returns:
        The fields, or an empty list when the blob is not well formed.
    """
    fields: list[bytes] = []
    offset = 0
    while offset < len(raw):
        if offset + 4 > len(raw):
            return []
        (length,) = struct.unpack(">I", raw[offset : offset + 4])
        offset += 4
        if offset + length > len(raw):
            return []
        fields.append(raw[offset : offset + length])
        offset += length
    return fields


def key_bits(key_type: str, blob: str) -> int | None:
    """
    Read a key's size from its blob.

    Args:
        key_type: The declared type.
        blob: The base64 key.

    Returns:
        Bits; None when the blob does not hold the declared type.
    """
    fields = _fields(_decode(blob))
    if not fields or fields[0].decode("ascii", "replace") != key_type:
        return None
    if key_type == "ssh-rsa" and len(fields) == 3:
        return int.from_bytes(fields[2], "big").bit_length()
    if key_type == "ssh-dss":
        return int.from_bytes(fields[1], "big").bit_length() if len(fields) >= 2 else None
    if "ed25519" in key_type:
        return 256
    for curve, bits in (("nistp256", 256), ("nistp384", 384), ("nistp521", 521)):
        if curve in key_type:
            return bits
    return None


def _split_options(line: str) -> tuple[str, str]:
    """
    Separate the options at the start of a key line from the rest.

    Args:
        line: The line.

    Returns:
        The options text (empty when the line starts with a key type) and the rest.
    """
    first = line.split(None, 1)[0]
    if first in KNOWN_TYPES or first.endswith("-cert-v01@openssh.com"):
        return "", line
    quoted = False
    for index, char in enumerate(line):
        if char == '"' and (index == 0 or line[index - 1] != "\\"):
            quoted = not quoted
        elif char in " \t" and not quoted:
            return line[:index], line[index:].strip()
    return line, ""


def _option_list(text: str) -> tuple[str, ...]:
    """
    Split an options text on the commas outside quotes.

    Args:
        text: ``restrict,command="a,b",from="10.0.0.0/8"``.

    Returns:
        Each option, verbatim.
    """
    options: list[str] = []
    current = ""
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
        if char == "," and not quoted:
            options.append(current)
            current = ""
            continue
        current += char
    if current:
        options.append(current)
    return tuple(options)


def classify(options: tuple[str, ...], comment: str) -> str:
    """
    Say what a key is for.

    Args:
        options: Its options.
        comment: Its comment.

    Returns:
        ``central``, ``cloud_disabled``, ``restricted`` or ``operator``.
    """
    if comment.startswith(KEY_COMMENT_PREFIX) or f'permitlisten="{NO_LISTEN}"' in options:
        return "central"
    if any(option.startswith("command=") and CLOUD_REFUSAL_MARKER in option for option in options):
        return "cloud_disabled"
    if options:
        return "restricted"
    return "operator"


def parse_line(line: str, line_number: int = 0) -> AuthorizedKey | None:
    """
    Parse one ``authorized_keys`` line.

    Args:
        line: The line.
        line_number: Where it is in its file.

    Returns:
        The key, or None for a blank line, a comment or a line that holds no
        key this understands.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    options_text, rest = _split_options(text)
    fields = rest.split(None, 2)
    if len(fields) < 2:
        return None
    key_type, blob = fields[0], fields[1]
    comment = fields[2].strip() if len(fields) == 3 else ""
    try:
        fingerprint = fingerprint_of(blob)
        base_type = key_type.replace("-cert-v01@openssh.com", "")
        bits = key_bits(key_type, blob) if base_type == key_type else None
    except SecurityError:
        return None
    options = _option_list(options_text)
    weak = ""
    if base_type == "ssh-dss":
        weak = "DSA keys are refused by OpenSSH 9.8 and later"
    elif base_type == "ssh-rsa" and bits is not None and bits < MIN_RSA_BITS:
        weak = f"RSA key of {bits} bits; use at least {MIN_RSA_BITS}, or ed25519"
    return AuthorizedKey(
        line_number=line_number,
        options=options,
        key_type=key_type,
        blob=blob,
        comment=comment,
        fingerprint=fingerprint,
        bits=bits,
        kind=classify(options, comment),
        weak=weak,
    )


def parse_file(text: str) -> list[AuthorizedKey]:
    """
    Parse an ``authorized_keys`` file.

    Args:
        text: Its content.

    Returns:
        Every key line in it, in order.
    """
    return [
        key
        for number, line in enumerate(text.splitlines(), start=1)
        if (key := parse_line(line, number)) is not None
    ]


def parse_new_key(line: str) -> AuthorizedKey:
    """
    Check a public key an operator wants to add.

    The line is exactly ``type base64 [comment]``: options are refused (an
    operator key is an operator key; restricting one is done by hand), the
    blob must hold the type it declares, and weak keys are refused.

    Args:
        line: The public key, as in a ``.pub`` file.

    Returns:
        The key, its comment reduced to safe characters.

    Raises:
        SecurityError: Anything else.
    """
    text = (line or "").strip()
    if not text or len(text) > MAX_KEY_LINE or "\n" in text or any(ord(c) < 32 for c in text):
        raise SecurityError(
            "The public key is not one line of an OpenSSH public key",
            details="Paste the whole content of your .pub file, on one line.",
        )
    fields = text.split(None, 2)
    if len(fields) < 2 or fields[0] not in ADDABLE_TYPES:
        raise SecurityError(
            "The public key's type is not one Noust adds",
            details="Use an ed25519 key (ssh-keygen -t ed25519), or ECDSA or RSA of at least "
            f"{MIN_RSA_BITS} bits. Options such as from= or command= are not accepted here.",
        )
    key_type, blob = fields[0], fields[1]
    bits = key_bits(key_type, blob)
    if bits is None:
        raise SecurityError(
            "The public key is damaged",
            details="Its key does not hold the type the line declares; copy the line again.",
        )
    if key_type == "ssh-rsa" and bits < MIN_RSA_BITS:
        raise SecurityError(
            f"The RSA key has {bits} bits, which is too weak",
            details=f"Use at least {MIN_RSA_BITS} bits, or an ed25519 key.",
        )
    comment = "".join(
        char
        for char in (fields[2] if len(fields) == 3 else "")
        if char.isalnum() or char in "@._:+- "
    ).strip()[:128]
    return AuthorizedKey(
        line_number=0,
        options=(),
        key_type=key_type,
        blob=blob,
        comment=comment,
        fingerprint=fingerprint_of(blob),
        bits=bits,
        kind="operator",
    )


def expand_key_files(entries: tuple[str, ...], account: Account) -> list[str]:
    """
    Expand ``AuthorizedKeysFile`` for one account.

    Args:
        entries: The values ``sshd -T`` printed, unexpanded.
        account: The account.

    Returns:
        Absolute paths, in the order sshd tries them; ``none`` dropped.
    """
    files: list[str] = []
    for entry in entries:
        for token in entry.split():
            if token.lower() == "none":
                continue
            expanded = (
                token.replace("%%", "\x00")
                .replace("%h", account.home)
                .replace("%u", account.name)
                .replace("%U", str(account.uid))
                .replace("\x00", "%")
            )
            path = (
                expanded if expanded.startswith("/") else f"{account.home.rstrip('/')}/{expanded}"
            )
            if path not in files:
                files.append(path)
    return files


def strict_mode_problems(host: HostPaths, account: Account, path: str) -> list[str]:
    """
    List what would make ``StrictModes`` ignore a key file.

    sshd refuses the file when it, or any directory from it up to the
    account's home, is writable by group or others or owned by anyone but the
    account or root.

    Args:
        host: Where the files are.
        account: The account the file belongs to.
        path: The file, absolute.

    Returns:
        One sentence per problem, with the command that fixes it.
    """
    problems: list[str] = []
    home = account.home.rstrip("/") or "/"
    current = path
    while True:
        local = host.at(current)
        try:
            info = os.lstat(local)
        except FileNotFoundError:
            break
        except OSError as exc:
            problems.append(f"{current} cannot be checked: {exc.strerror}")
            break
        if stat.S_ISLNK(info.st_mode) and current == path:
            problems.append(f"{current} is a symbolic link; replace it with a regular file")
        if info.st_uid not in (0, account.uid):
            problems.append(
                f"{current} belongs to uid {info.st_uid}, not to {account.name}: "
                f"chown {account.name} {current}"
            )
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            problems.append(
                f"{current} is writable by others ({stat.filemode(info.st_mode)}): "
                f"chmod go-w {current}"
            )
        if current in (home, "/") or not current.startswith(home):
            break
        current = str(Path(current).parent)
    return problems
