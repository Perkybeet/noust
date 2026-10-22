# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The link code: everything a central needs to accept a server that dials in.

A server with no SSH port anyone can reach (behind NAT, behind a carrier's
CGNAT) cannot be reached by a central the way :mod:`noust.fleet.joincode`
describes. It reaches the central instead, and ``noust fleet link`` prints
this code on the server: the public half of the key it dials in with, the
fleet token for its console, the console port and the invitation it answers.
The operator pastes it into the central, which installs the key (restricted to
listening on one loopback port) and registers the server.

Format: ``noust-link:v1:<base64url(JSON)>``. A fixed set of fields, every one
checked, fields it does not know refused: a code is input from a clipboard.
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
)

#: What every link code starts with; the version names the field set.
LINK_PREFIX = "noust-link:v1:"

#: Longest code accepted. A real one is about 450 characters.
MAX_LINK_CODE = 4096

#: A fleet token: the API token prefix and ``secrets.token_urlsafe(32)``.
_TOKEN = re.compile(r"noust_tok_[A-Za-z0-9_-]{20,128}")

#: A Noust version string.
_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,32}")

#: An invitation's nonce: 128 random bits, in hex.
NONCE = re.compile(r"[0-9a-f]{32}")

#: A token name: ``fleet-<central>`` or ``fleet-<central>.<n>``.
_TOKEN_NAME = re.compile(r"fleet-[a-z0-9-]{1,32}(?:\.[0-9]{1,4})?")

_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")

_REQUIRED = ("node_name", "central", "link_key", "console_port", "token", "noust_version", "nonce")
_OPTIONAL = ("token_name",)


@dataclass(frozen=True)
class LinkCode:
    """
    What a server that dials in hands its central.

    Attributes:
        node_name: The name the server suggests for itself.
        central: The central the server linked to, by the name it was invited as.
        link_key: The public half of the key the server dials in with,
            ``ssh-ed25519 AAAA...``.
        console_port: The loopback port the server's console listens on.
        token: The fleet token. Secret: never logged, never in argv.
        noust_version: The server's Noust version.
        nonce: The invitation this code answers.
        token_name: The token's name on the server, ``fleet-<central>``.
    """

    node_name: str
    central: str
    link_key: str
    console_port: int
    token: str
    noust_version: str
    nonce: str
    token_name: str | None = None

    def __repr__(self) -> str:
        """Describe the code without its token."""
        return (
            f"LinkCode(node_name={self.node_name!r}, central={self.central!r}, "
            f"console_port={self.console_port}, token=***)"
        )

    __str__ = __repr__

    def encode(self) -> str:
        """
        Spell the code as the one line the server prints.

        Returns:
            ``noust-link:v1:...``.
        """
        document: dict[str, Any] = {
            "node_name": self.node_name,
            "central": self.central,
            "link_key": self.link_key,
            "console_port": self.console_port,
            "token": self.token,
            "noust_version": self.noust_version,
            "nonce": self.nonce,
        }
        if self.token_name is not None:
            document["token_name"] = self.token_name
        raw = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
        return LINK_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, code: str) -> LinkCode:
        """
        Read a link code, refusing anything that is not exactly one.

        Args:
            code: The line the operator pasted. Surrounding whitespace is ignored.

        Returns:
            The code.

        Raises:
            NodeError: When it is not a valid v1 link code. The message never
                repeats the code, which holds a token.
        """
        text = code.strip() if isinstance(code, str) else ""
        if len(text) > MAX_LINK_CODE:
            raise _invalid(f"it is longer than {MAX_LINK_CODE} characters")
        if not text.startswith(LINK_PREFIX):
            if text.startswith("noust-join:"):
                raise NodeError(
                    "This is a join code, not a link code",
                    details=(
                        "A join code is for a server the central can reach over SSH "
                        "('noust node add'). A server behind NAT prints a link code from "
                        "'noust fleet link'."
                    ),
                    field="link_code",
                )
            if text.startswith("noust-link:"):
                raise NodeError(
                    "This link code is from a newer Noust than this central",
                    details="Upgrade Noust on the central, then paste the code again.",
                    field="link_code",
                )
            raise _invalid("it does not start with 'noust-link:v1:'")
        payload = text[len(LINK_PREFIX) :]
        if not _BASE64URL.fullmatch(payload):
            raise _invalid("it holds characters a link code never has")
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

        key = parse_public_key(_text(document, "link_key"), what="the server's link key")
        token = _text(document, "token")
        if not _TOKEN.fullmatch(token):
            raise _invalid("its token is not a fleet token")
        version = _text(document, "noust_version")
        if not _VERSION.fullmatch(version):
            raise _invalid("its version is malformed")
        nonce = _text(document, "nonce")
        if not NONCE.fullmatch(nonce):
            raise _invalid("its invitation is malformed")
        token_name = document.get("token_name")
        if token_name is not None and (
            not isinstance(token_name, str) or not _TOKEN_NAME.fullmatch(token_name)
        ):
            raise _invalid("its token name is malformed")
        return cls(
            node_name=validate_node_name(_text(document, "node_name")),
            central=validate_central_name(_text(document, "central")),
            link_key=key.bare,
            console_port=validate_port(document["console_port"], "console port"),
            token=token,
            noust_version=version,
            nonce=nonce,
            token_name=token_name,
        )


def _invalid(why: str) -> NodeError:
    """
    Build the refusal of a malformed link code.

    Args:
        why: What is wrong with it.

    Returns:
        The error, to raise.
    """
    return NodeError(
        f"Invalid link code: {why}",
        details=(
            "Copy the whole line 'noust fleet link' printed on the server, starting with "
            "noust-link:v1:, and paste it again."
        ),
        field="link_code",
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
