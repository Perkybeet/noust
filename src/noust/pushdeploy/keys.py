# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The keys that may push to an application's repository.

sshd reads one file for the push account, root's and outside its reach
(``/etc/ssh/noust/noust-git.keys``), and a line of it is a key together with what that key may
do: ``restrict`` (no terminal, no forwarding, no agent) and a forced command that runs
``git-receive-pack`` on one repository. A key of one application therefore cannot push to
another's, and cannot do anything but push. The line's comment (``noust-push:<domain>:<label>``)
is how Noust finds the lines it owns; nothing else is kept about a key.

The text an operator pastes is only ever a key: its type, its blob and a comment are read, the
blob is decoded and its structure checked, and everything else on the line (options, a second
key) is refused, so a pasted line cannot carry options of its own.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import ValidationError
from noust.fleet.authorize import AuthorizedKeys, tunnel_keys_file
from noust.validators.domain import validate_domain

#: Prefix of the comment of a line this module owns.
COMMENT_PREFIX = "noust-push:"

#: The key types a developer pushes with. ed25519 and ecdsa, their security-key forms, and RSA of
#: at least :data:`MIN_RSA_BITS`; DSA is gone from OpenSSH.
KEY_TYPES = frozenset(
    {
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    }
)

#: The shortest RSA modulus accepted.
MIN_RSA_BITS = 2048

#: Longest key line read.
MAX_KEY_LINE = 8192

#: A label names a key among an application's: a person or a machine.
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,39}")

#: The fingerprint ``ssh-keygen -l`` prints.
_FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")


@dataclass(frozen=True)
class PushKey:
    """
    A public key, parsed and checked.

    Attributes:
        key_type: ``ssh-ed25519``, ``ssh-rsa``...
        blob: The base64 key.
    """

    key_type: str
    blob: str

    @property
    def fingerprint(self) -> str:
        """``SHA256:...``, as ``ssh-keygen -l`` prints it."""
        digest = hashlib.sha256(base64.b64decode(self.blob, validate=True)).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")

    @property
    def bare(self) -> str:
        """``type blob``."""
        return f"{self.key_type} {self.blob}"


@dataclass(frozen=True)
class KeyEntry:
    """
    A key authorized for an application's repository.

    Attributes:
        domain: The application.
        label: The name it was added under.
        key_type: Its type.
        fingerprint: ``SHA256:...``.
    """

    domain: str
    label: str
    key_type: str
    fingerprint: str

    def to_dict(self) -> dict[str, str]:
        """
        Returns:
            The entry as plain data.
        """
        return {
            "label": self.label,
            "key_type": self.key_type,
            "fingerprint": self.fingerprint,
        }


def validate_label(label: str) -> str:
    """
    Check the name a key is added under.

    Args:
        label: What the operator typed.

    Returns:
        The label.

    Raises:
        ValidationError: Empty, too long, or with characters that could end the comment of a
            key line or be mistaken for the domain before it.
    """
    text = (label or "").strip()
    if not _LABEL.fullmatch(text):
        raise ValidationError(
            f"Not a name for a key: {text!r}",
            details="Use up to 40 letters, digits, dots, dashes, @ or underscores, starting with "
            "a letter or a digit: laptop, ci, ana@work.",
            field="label",
        )
    return text


def _structure(raw: bytes) -> tuple[bytes, list[bytes]]:
    """
    Split an SSH public key blob into its length-prefixed strings.

    Args:
        raw: The decoded blob.

    Returns:
        The first string (the key type) and the rest.

    Raises:
        ValueError: The blob does not hold well-formed strings to its end.
    """
    parts: list[bytes] = []
    offset = 0
    while offset < len(raw):
        if offset + 4 > len(raw):
            raise ValueError("truncated")
        (size,) = struct.unpack(">I", raw[offset : offset + 4])
        offset += 4
        if size > len(raw) - offset:
            raise ValueError("truncated")
        parts.append(raw[offset : offset + size])
        offset += size
    if not parts:
        raise ValueError("empty")
    return parts[0], parts[1:]


def parse_key(text: str) -> PushKey:
    """
    Read a public key a developer pastes.

    Args:
        text: One line, ``ssh-ed25519 AAAA... comment``.

    Returns:
        The key. Its comment is not kept: the line Noust writes carries its own.

    Raises:
        ValidationError: Not one line holding one key of an accepted type, with a blob that is
            what its type says it is (and an RSA modulus of at least 2048 bits).
    """
    line = (text or "").strip()
    if not line or len(line) > MAX_KEY_LINE or any(ord(char) < 32 for char in line):
        raise ValidationError(
            "The key is not one line of an OpenSSH public key",
            details="Paste the whole line of the .pub file (cat ~/.ssh/id_ed25519.pub), on "
            "one line.",
            field="key",
        )
    fields = line.split(None, 2)
    if len(fields) < 2 or fields[0] not in KEY_TYPES:
        raise ValidationError(
            "The key is not of a type that can push",
            details="Expected a line starting with ssh-ed25519, ssh-rsa, ecdsa-sha2-nistp256 "
            "(384, 521) or an sk- security key; options before the key are not accepted.",
            field="key",
        )
    key_type, blob = fields[0], fields[1]
    try:
        raw = base64.b64decode(blob, validate=True)
        declared, rest = _structure(raw)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(
            "The key is damaged",
            details=f"Its key is not a well-formed OpenSSH public key ({exc}); copy the line again.",
            field="key",
        ) from exc
    if declared.decode("ascii", errors="replace") != key_type:
        raise ValidationError(
            "The key is damaged",
            details="The type inside the key is not the one the line starts with; copy the line "
            "again.",
            field="key",
        )
    if key_type == "ssh-rsa":
        modulus = rest[1] if len(rest) > 1 else b""
        bits = len(modulus.lstrip(b"\x00")) * 8
        if bits < MIN_RSA_BITS:
            raise ValidationError(
                f"The RSA key has {bits} bits",
                details=f"Keys of fewer than {MIN_RSA_BITS} bits are refused. Make an ed25519 "
                "one: ssh-keygen -t ed25519.",
                field="key",
            )
    return PushKey(key_type=key_type, blob=blob)


def key_line(domain: str, label: str, key: PushKey, repository: Path) -> str:
    """
    Spell the line sshd reads for a key allowed to push to one repository.

    Args:
        domain: The application.
        label: The key's name.
        key: The key.
        repository: The application's bare repository.

    Returns:
        ``restrict,command="git-receive-pack '<repository>'" <key> noust-push:<domain>:<label>``.
        ``restrict`` takes away the terminal, forwarding, the agent and X11; the forced command
        is all the key can run, whatever the client asks for.
    """
    path = str(repository)
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", path):
        raise ValidationError(
            f"The repository path {path!r} cannot be written into a key line",
            details="The path to the push repositories has to be made of letters, digits, dots, "
            "dashes, underscores and slashes.",
        )
    options = f"restrict,command=\"git-receive-pack '{path}'\""
    return f"{options} {key.bare} {_comment(domain, label)}"


def _comment(domain: str, label: str) -> str:
    """
    Args:
        domain: The application.
        label: The key's name.

    Returns:
        The comment that marks the line as this key's.
    """
    return f"{COMMENT_PREFIX}{validate_domain(domain)}:{validate_label(label)}"


def _owner(line: str) -> tuple[str, str] | None:
    """
    Read whose a line of the key file is.

    Args:
        line: A line.

    Returns:
        ``(domain, label)`` when its last field is a comment of this module; None for a
        line that is not one of ours.
    """
    fields = line.split()
    if not fields or not fields[-1].startswith(COMMENT_PREFIX):
        return None
    domain, _, label = fields[-1][len(COMMENT_PREFIX) :].partition(":")
    if not domain or not label:
        return None
    return domain, label


def _entry(line: str) -> KeyEntry | None:
    """
    Args:
        line: A line of the key file.

    Returns:
        The entry it is, None for a line that is not one of ours or is damaged.
    """
    owner = _owner(line)
    if owner is None:
        return None
    fields = line.split()
    for index, field in enumerate(fields[:-1]):
        if field in KEY_TYPES:
            blob = fields[index + 1]
            try:
                fingerprint = PushKey(field, blob).fingerprint
            except (binascii.Error, ValueError):
                return None
            return KeyEntry(owner[0], owner[1], field, fingerprint)
    return None


class PushKeys:
    """
    The key file of the push account, as the keys of applications.

    Args:
        account: The push account, whose ``<name>.keys`` file sshd reads.
        keys_dir: Where the key files are.
    """

    def __init__(self, account: str, keys_dir: Path, *, editor: AuthorizedKeys | None = None):
        self.account = account
        self._editor = editor or tunnel_keys_file(
            account, keys_dir=keys_dir, comment_prefix=COMMENT_PREFIX
        )

    @property
    def path(self) -> Path:
        """The file sshd reads."""
        return self._editor.path

    def list(self, domain: str | None = None) -> list[KeyEntry]:
        """
        List the keys that may push.

        Args:
            domain: Only this application's; every application's when None.

        Returns:
            The entries, by application and then label.
        """
        wanted = validate_domain(domain) if domain else None
        entries = [
            entry
            for line in (self._editor.read() or "").splitlines()
            if (entry := _entry(line)) is not None and (wanted is None or entry.domain == wanted)
        ]
        return sorted(entries, key=lambda entry: (entry.domain, entry.label))

    def add(self, domain: str, label: str, key: PushKey, repository: Path) -> bool:
        """
        Allow a key to push to an application's repository.

        Args:
            domain: The application.
            label: The key's name.
            key: The key.
            repository: The application's bare repository.

        Returns:
            True when the file changed; False when it already held exactly this.

        Raises:
            ValidationError: The label is used by another key of the application, or the key
                is already one of its keys under another label.
        """
        domain = validate_domain(domain)
        label = validate_label(label)
        line = key_line(domain, label, key, repository)
        changed = False

        def change(lines: list[str]) -> list[str]:
            nonlocal changed
            for existing in lines:
                entry = _entry(existing)
                if entry is None:
                    continue
                if entry.domain != domain:
                    if entry.fingerprint == key.fingerprint:
                        raise ValidationError(
                            f"That key already pushes to {entry.domain}",
                            details="sshd uses the first line a key has, so a key serves one "
                            "application. Make one for this application (ssh-keygen -t ed25519 "
                            f"-f ~/.ssh/noust-{domain}) and add that.",
                            field="key",
                        )
                    continue
                if entry.label == label and existing == line:
                    return lines
                if entry.label == label:
                    raise ValidationError(
                        f"{domain} already has a key called {label}",
                        details="Remove it first (noust push key remove) or pick another name.",
                        field="label",
                    )
                if entry.fingerprint == key.fingerprint:
                    raise ValidationError(
                        f"That key is already one of {domain}'s, as {entry.label}",
                        details="A key is added once per application.",
                        field="key",
                    )
            changed = True
            return [*lines, line]

        self._editor.rewrite(change)
        return changed

    def remove(self, domain: str, which: str) -> int:
        """
        Take a key's permission to push away.

        Args:
            domain: The application.
            which: The key's label or its fingerprint (``SHA256:...``).

        Returns:
            How many lines were removed.

        Raises:
            ValidationError: Nothing is named.
        """
        domain = validate_domain(domain)
        text = (which or "").strip()
        if not text:
            raise ValidationError(
                "Name the key to remove", details="Its label or its fingerprint.", field="label"
            )
        by_fingerprint = bool(_FINGERPRINT.fullmatch(text))
        removed = 0

        def change(lines: list[str]) -> list[str]:
            nonlocal removed
            kept: list[str] = []
            for line in lines:
                entry = _entry(line)
                mine = entry is not None and entry.domain == domain
                if (
                    mine
                    and entry is not None
                    and (entry.fingerprint == text if by_fingerprint else entry.label == text)
                ):
                    removed += 1
                    continue
                kept.append(line)
            return kept

        self._editor.rewrite(change)
        return removed

    def remove_all(self, domain: str) -> int:
        """
        Take every key of an application away.

        Args:
            domain: The application.

        Returns:
            How many lines were removed.
        """
        domain = validate_domain(domain)
        removed = 0

        def change(lines: list[str]) -> list[str]:
            nonlocal removed
            kept = [line for line in lines if (_owner(line) or ("", ""))[0] != domain]
            removed = len(lines) - len(kept)
            return kept

        self._editor.rewrite(change)
        return removed
