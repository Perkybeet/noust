# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the fleet is made of, validated: node records, names, SSH targets, keys.

Every value here ends up somewhere that interprets it: a node name in a
secret's path and in ``HostKeyAlias``, a central name in a token name and an
``authorized_keys`` comment, a public key in ``authorized_keys`` itself, a
host in ssh's argv. So each is checked against the narrowest shape that works,
here, once; a newline in a key line would otherwise be a second, unrestricted
``authorized_keys`` entry.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import re
import socket
import struct
from dataclasses import dataclass

from noust.core.exceptions import NodeError
from noust.core.store import NODE_STATUSES, NodeRecord

__all__ = [
    "NODE_STATUSES",
    "NodeRecord",
    "PublicKey",
    "SSHTarget",
    "central_name",
    "parse_public_key",
    "parse_ssh_target",
    "validate_central_name",
    "validate_node_name",
    "validate_port",
    "validate_ssh_user",
]

#: A node or central name: what a DNS label allows, lower case, at most 32.
#: It becomes a directory under the secrets, an ssh ``HostKeyAlias``, a token
#: name and an ``authorized_keys`` comment, so dots, slashes and spaces are out.
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")

#: A Unix account name, as useradd accepts it by default.
_SSH_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")

#: One DNS label.
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")

#: Key types a central or a node key may be. ed25519 only: every OpenSSH
#: that ships Noust has it, and one type is one parser to get right.
KEY_TYPES = ("ssh-ed25519",)

#: Longest public key line accepted. An ed25519 line is about 100 characters.
MAX_KEY_LINE = 1024

#: Characters an ssh key comment may keep. Anything else is dropped rather
#: than carried into a file sshd parses.
_COMMENT = re.compile(r"[A-Za-z0-9@._:+-]{1,128}")

#: Length of an ed25519 public key, in bytes.
_ED25519_KEY_BYTES = 32


def validate_node_name(name: str) -> str:
    """
    Check a node's name.

    Args:
        name: The name the operator chose.

    Returns:
        The name.

    Raises:
        NodeError: When it is not 1 to 32 lower-case letters, digits or dashes.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise NodeError(
            f"Invalid node name: {name!r}",
            details="Use 1 to 32 lower-case letters, digits and dashes, such as 'web-2'.",
            field="name",
        )
    return name


def validate_central_name(name: str) -> str:
    """
    Check a central's name, as given to ``noust fleet authorize --name``.

    Args:
        name: The name.

    Returns:
        The name.

    Raises:
        NodeError: When it is not 1 to 32 lower-case letters, digits or dashes.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise NodeError(
            f"Invalid central name: {name!r}",
            details="Use 1 to 32 lower-case letters, digits and dashes, such as 'nas'.",
            field="name",
        )
    return name


def validate_ssh_user(user: str) -> str:
    """
    Check the account the central's key is installed for.

    Args:
        user: The account name.

    Returns:
        The name.

    Raises:
        NodeError: When it is not a plain Unix account name.
    """
    if not isinstance(user, str) or not _SSH_USER.fullmatch(user):
        raise NodeError(
            f"Invalid SSH user: {user!r}",
            details="Use a plain account name, such as 'root'.",
            field="ssh_user",
        )
    return user


def validate_port(port: object, what: str = "port") -> int:
    """
    Check a TCP port.

    Args:
        port: The candidate.
        what: What the port is, for the message.

    Returns:
        The port.

    Raises:
        NodeError: When it is not an integer between 1 and 65535.
    """
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise NodeError(f"Invalid {what}: {port!r}", details="Use a number from 1 to 65535.")
    return port


def _validate_host(host: str) -> str:
    """
    Check a host name or address ssh will be pointed at.

    Args:
        host: A DNS name, an IPv4 address or an IPv6 address without brackets.

    Returns:
        The host.

    Raises:
        NodeError: When it is neither.
    """
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    name = host[:-1] if host.endswith(".") else host
    if name and len(name) <= 253 and all(_LABEL.fullmatch(label) for label in name.split(".")):
        return host
    raise NodeError(
        f"Invalid host: {host!r}",
        details="Use a host name such as web2.example.com, or an IP address.",
        field="ssh",
    )


@dataclass(frozen=True)
class SSHTarget:
    """
    Where a central connects to reach a node.

    Attributes:
        host: Host name or address (IPv6 without brackets).
        user: Account to log in as, when the operator named one.
        port: SSH port, when the operator named one.
    """

    host: str
    user: str | None = None
    port: int | None = None


def parse_ssh_target(target: str) -> SSHTarget:
    """
    Read ``[USER@]HOST[:PORT]``, with IPv6 hosts in brackets when a port follows.

    Args:
        target: What the operator typed after ``--ssh``.

    Returns:
        The target.

    Raises:
        NodeError: When any part is malformed.
    """
    text = target.strip() if isinstance(target, str) else ""
    if not text:
        raise NodeError(
            "The SSH address is empty",
            details="Give it as USER@HOST[:PORT], such as root@web2.example.com.",
            field="ssh",
        )
    user: str | None = None
    if "@" in text:
        user, _, text = text.partition("@")
        validate_ssh_user(user)
    port: int | None = None
    if text.startswith("["):
        host, sep, rest = text[1:].partition("]")
        if not sep or (rest and not rest.startswith(":")):
            raise NodeError(f"Invalid SSH address: {target!r}", field="ssh")
        if rest:
            port = _port_text(rest[1:], target)
    elif text.count(":") == 1:
        host, _, port_text = text.partition(":")
        port = _port_text(port_text, target)
    else:
        host = text
    return SSHTarget(host=_validate_host(host), user=user, port=port)


def _port_text(text: str, target: str) -> int:
    """
    Read the port of an SSH address.

    Args:
        text: The digits after the colon.
        target: The whole address, for the message.

    Returns:
        The port.

    Raises:
        NodeError: When it is not a valid port.
    """
    if not text.isdigit():
        raise NodeError(f"Invalid port in SSH address: {target!r}", field="ssh")
    return validate_port(int(text), "SSH port")


@dataclass(frozen=True)
class PublicKey:
    """
    An OpenSSH public key, parsed and checked.

    Attributes:
        key_type: ``ssh-ed25519``.
        blob: The base64 key blob, exactly as it was given.
        comment: The comment, reduced to safe characters; empty when none.
    """

    key_type: str
    blob: str
    comment: str = ""

    @property
    def fingerprint(self) -> str:
        """``SHA256:...``, as ``ssh-keygen -l`` prints it."""
        digest = hashlib.sha256(base64.b64decode(self.blob)).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")

    def line(self, comment: str | None = None) -> str:
        """
        Spell the key as one ``authorized_keys``/``.pub`` line.

        Args:
            comment: A comment to use instead of the key's own; ``""`` for none.

        Returns:
            ``type blob [comment]``.
        """
        text = self.comment if comment is None else comment
        return f"{self.key_type} {self.blob} {text}".rstrip()

    @property
    def bare(self) -> str:
        """``type blob``, without a comment: what is pinned and compared."""
        return f"{self.key_type} {self.blob}"


def parse_public_key(line: str, *, what: str = "public key") -> PublicKey:
    """
    Parse one OpenSSH public key line, refusing anything but a clean ed25519 key.

    The blob is decoded and its structure checked - the type it declares
    inside must be the type the line declares, followed by exactly one
    32-byte key - so a line that merely looks like a key is refused.

    Args:
        line: ``ssh-ed25519 AAAA... [comment]``.
        what: What the key is, for the messages.

    Returns:
        The key.

    Raises:
        NodeError: When the line is not exactly one valid ed25519 public key.
    """
    if not isinstance(line, str):
        raise NodeError(f"The {what} is missing")
    text = line.strip()
    if not text or len(text) > MAX_KEY_LINE or any(ord(char) < 32 for char in text):
        raise NodeError(
            f"The {what} is not one line of an OpenSSH public key",
            details="Paste the whole line, starting with ssh-ed25519, on one line.",
        )
    fields = text.split(None, 2)
    if len(fields) < 2 or fields[0] not in KEY_TYPES:
        raise NodeError(
            f"The {what} is not an ed25519 key",
            details="Expected a line starting with 'ssh-ed25519 AAAA'.",
        )
    key_type, blob = fields[0], fields[1]
    try:
        raw = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise NodeError(f"The {what} is damaged", details=f"Its key is not base64: {exc}") from exc
    if _blob_type(raw) != (key_type.encode(), _ED25519_KEY_BYTES):
        raise NodeError(
            f"The {what} is damaged",
            details="Its key does not hold one ed25519 public key; copy the line again.",
        )
    comment = fields[2].strip() if len(fields) == 3 else ""
    if comment and not _COMMENT.fullmatch(comment):
        comment = ""
    return PublicKey(key_type=key_type, blob=blob, comment=comment)


def _blob_type(raw: bytes) -> tuple[bytes, int] | None:
    """
    Read the declared type and key length of an SSH public key blob.

    Args:
        raw: The decoded blob.

    Returns:
        The type and the length of the one field after it, or None when the
        blob is not exactly two length-prefixed fields.
    """
    fields: list[bytes] = []
    offset = 0
    while offset < len(raw):
        if offset + 4 > len(raw):
            return None
        (length,) = struct.unpack(">I", raw[offset : offset + 4])
        offset += 4
        if offset + length > len(raw):
            return None
        fields.append(raw[offset : offset + length])
        offset += length
    if len(fields) != 2:
        return None
    return fields[0], len(fields[1])


def _slug(text: str) -> str:
    """
    Reduce a host name to a valid fleet name.

    Args:
        text: A host name.

    Returns:
        Lower case, anything but letters and digits as dashes, at most 32.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32].strip("-")
    return slug if _NAME.fullmatch(slug) else ""


def central_name() -> str:
    """
    Name this central, as nodes know it: ``central.name`` or the host name.

    The name ends up in the ``fleet-<central>`` token on every node and in the
    comment of its ``authorized_keys`` line, so a node's operator can tell
    which central a credential belongs to.

    Returns:
        A valid central name.

    Raises:
        NodeError: When ``central.name`` is set to something invalid.
    """
    from noust.core.config import Config

    configured = Config().get("central.name")
    if configured:
        return validate_central_name(str(configured))
    return _slug(socket.gethostname()) or "central"


def local_node_name() -> str:
    """
    Suggest a name for this server as a node: its host name, reduced.

    Returns:
        A valid node name.
    """
    return _slug(socket.gethostname()) or "node"
