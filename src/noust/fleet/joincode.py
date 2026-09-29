# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The join code: everything a central needs to reach a node, in one line.

``noust fleet authorize`` prints it on the node; the operator pastes it into
the central. It carries the node's own SSH host key, so the central pins the
key it was told rather than trusting whatever answers the first connection
(no trust on first use), and the fingerprint of the central key the node
authorized, so a code pasted into the wrong central, or for the wrong node, is
refused before anything is stored.

Format: ``noust-join:v1:<base64url(JSON)>``. The JSON is a fixed set of
fields; parsing is strict about every one (types, lengths, shapes) and
refuses fields it does not know, because a code is input from a clipboard.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import NodeError
from noust.fleet.models import (
    parse_public_key,
    validate_central_name,
    validate_node_name,
    validate_port,
    validate_ssh_user,
)

#: What every join code starts with; the version names the field set.
JOIN_PREFIX = "noust-join:v1:"

#: Longest code accepted. A real one is about 500 characters.
MAX_JOIN_CODE = 4096

#: A fleet token: the API token prefix and ``secrets.token_urlsafe(32)``.
_TOKEN = re.compile(r"noust_tok_[A-Za-z0-9_-]{20,128}")

#: A Noust version string.
_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,32}")

#: A key fingerprint, as :attr:`~noust.fleet.models.PublicKey.fingerprint`.
_FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")

#: A token name: ``fleet-<central>`` or ``fleet-<central>.<n>``.
_TOKEN_NAME = re.compile(r"fleet-[a-z0-9-]{1,32}(?:\.[0-9]{1,4})?")

_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")

_REQUIRED = (
    "ssh_host_key_line",
    "ssh_user",
    "ssh_port",
    "console_port",
    "token",
    "noust_version",
    "central_key_fp",
)
_OPTIONAL = ("node_name", "token_name", "central")


@dataclass(frozen=True)
class JoinCode:
    """
    What a node hands a central.

    Attributes:
        ssh_host_key: The node's ed25519 host key, ``ssh-ed25519 AAAA...``.
        ssh_user: The account whose ``authorized_keys`` holds the central key.
        ssh_port: The port the node's sshd listens on.
        console_port: The loopback port the node's console listens on.
        token: The fleet token. Secret: never logged, never in argv.
        noust_version: The node's Noust version.
        central_key_fp: Fingerprint of the central key the node authorized.
        node_name: The name the node suggests for itself.
        token_name: The token's name on the node, ``fleet-<central>``.
        central: The central name the node authorized.
    """

    ssh_host_key: str
    ssh_user: str
    ssh_port: int
    console_port: int
    token: str
    noust_version: str
    central_key_fp: str
    node_name: str | None = None
    token_name: str | None = None
    central: str | None = None

    def __repr__(self) -> str:
        """Describe the code without its token."""
        return (
            f"JoinCode(node_name={self.node_name!r}, ssh_user={self.ssh_user!r}, "
            f"ssh_port={self.ssh_port}, console_port={self.console_port}, "
            f"central={self.central!r}, token=***)"
        )

    __str__ = __repr__

    def encode(self) -> str:
        """
        Spell the code as the one line the node prints.

        Returns:
            ``noust-join:v1:...``.
        """
        document: dict[str, Any] = {
            "ssh_host_key_line": self.ssh_host_key,
            "ssh_user": self.ssh_user,
            "ssh_port": self.ssh_port,
            "console_port": self.console_port,
            "token": self.token,
            "noust_version": self.noust_version,
            "central_key_fp": self.central_key_fp,
        }
        for name in _OPTIONAL:
            value = getattr(self, name)
            if value is not None:
                document[name] = value
        raw = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
        return JOIN_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, code: str) -> JoinCode:
        """
        Read a join code, refusing anything that is not exactly one.

        Args:
            code: The line the operator pasted. Surrounding whitespace is ignored.

        Returns:
            The code.

        Raises:
            NodeError: When it is not a valid v1 join code. The message never
                repeats the code, which holds a token.
        """
        text = code.strip() if isinstance(code, str) else ""
        if len(text) > MAX_JOIN_CODE:
            raise _invalid(f"it is longer than {MAX_JOIN_CODE} characters")
        if not text.startswith(JOIN_PREFIX):
            if text.startswith("noust-join:"):
                raise NodeError(
                    "This join code is from a newer Noust than this central",
                    details="Upgrade Noust on the central, then paste the code again.",
                    field="join_code",
                )
            raise _invalid("it does not start with 'noust-join:v1:'")
        payload = text[len(JOIN_PREFIX) :]
        if not _BASE64URL.fullmatch(payload):
            raise _invalid("it holds characters a join code never has")
        try:
            raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            document = json.loads(raw.decode("utf-8"))
        except (binascii.Error, ValueError) as exc:
            raise _invalid("it does not decode") from exc
        if not isinstance(document, dict):
            raise _invalid("it does not hold an object")
        unknown = set(document) - set(_REQUIRED) - set(_OPTIONAL)
        if unknown:
            raise _invalid(f"it holds unknown fields: {', '.join(sorted(unknown))}")
        missing = [name for name in _REQUIRED if name not in document]
        if missing:
            raise _invalid(f"it lacks {', '.join(missing)}")

        host_key = parse_public_key(_text(document, "ssh_host_key_line"), what="node's host key")
        token = _text(document, "token")
        if not _TOKEN.fullmatch(token):
            raise _invalid("its token is not a fleet token")
        version = _text(document, "noust_version")
        if not _VERSION.fullmatch(version):
            raise _invalid("its version is malformed")
        fingerprint = _text(document, "central_key_fp")
        if not _FINGERPRINT.fullmatch(fingerprint):
            raise _invalid("its central key fingerprint is malformed")
        node_name = _optional_text(document, "node_name")
        if node_name is not None:
            validate_node_name(node_name)
        token_name = _optional_text(document, "token_name")
        if token_name is not None and not _TOKEN_NAME.fullmatch(token_name):
            raise _invalid("its token name is malformed")
        central = _optional_text(document, "central")
        if central is not None:
            validate_central_name(central)

        return cls(
            ssh_host_key=host_key.bare,
            ssh_user=validate_ssh_user(_text(document, "ssh_user")),
            ssh_port=validate_port(document["ssh_port"], "SSH port"),
            console_port=validate_port(document["console_port"], "console port"),
            token=token,
            noust_version=version,
            central_key_fp=fingerprint,
            node_name=node_name,
            token_name=token_name,
            central=central,
        )


def _invalid(why: str) -> NodeError:
    """
    Build the refusal of a malformed join code.

    Args:
        why: What is wrong with it.

    Returns:
        The error, to raise.
    """
    return NodeError(
        f"Invalid join code: {why}",
        details=(
            "Copy the whole line 'noust fleet authorize' printed on the node, "
            "starting with noust-join:v1:, and paste it again."
        ),
        field="join_code",
    )


def _text(document: dict[str, Any], name: str) -> str:
    """
    Read a required string field.

    Args:
        document: The decoded code.
        name: The field.

    Returns:
        Its value.

    Raises:
        NodeError: When it is not a string.
    """
    value = document.get(name)
    if not isinstance(value, str):
        raise _invalid(f"its {name} is not text")
    return value


def _optional_text(document: dict[str, Any], name: str) -> str | None:
    """
    Read an optional string field.

    Args:
        document: The decoded code.
        name: The field.

    Returns:
        Its value, or None when absent or null.

    Raises:
        NodeError: When present and not a string.
    """
    if document.get(name) is None:
        return None
    return _text(document, name)
