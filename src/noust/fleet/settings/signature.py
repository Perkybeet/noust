# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a push of the fleet's settings says who approved it, and how a node reads that.

A person saves a setting of the fleet on the central, in sudo mode, and that is recorded as a
change with an id. The pushes that apply it (also the ones made later, when a server comes back,
with nobody in front of the central) carry two headers:

- ``X-Noust-Actor: fleet-sync on behalf of <person> (change <id>)``, which is what the node's
  audit trail names;
- ``X-Noust-Elevated: change:<id>``, the reference of the approved change, which stands where the
  ``1`` of a person's sudo mode stands.

The node takes the confirmation as given only when the two name the same change
(:meth:`SyncSignature.parse`), only for the settings of the fleet and only when its ceiling is
``admin`` (:mod:`noust.web.auth.fleet_sync`): a central cannot confirm for itself anything else.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from noust.core.exceptions import ValidationError

__all__ = [
    "ACTOR_PREFIX",
    "ELEVATION_PREFIX",
    "ParsedSignature",
    "SyncSignature",
    "SyncSignatureError",
    "new_change_id",
]

#: What an actor header starts with when the push is the reconciler's.
ACTOR_PREFIX = "fleet-sync on behalf of "

#: What the elevation header starts with when it carries the reference of a change.
ELEVATION_PREFIX = "change:"

#: A person as the central labels one; the same alphabet the actor header has always had.
_PERSON = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,63}")

#: A change's id: a letter and twelve hex digits.
_CHANGE = re.compile(r"c[0-9a-f]{12}")

#: The whole actor header.
_ACTOR = re.compile(
    re.escape(ACTOR_PREFIX) + r"(?P<person>[A-Za-z0-9][A-Za-z0-9._:@+-]{0,63})"
    r" \(change (?P<change>c[0-9a-f]{12})\)"
)


class SyncSignatureError(ValidationError):
    """A signature that is not exactly the format, or names a person or a change that cannot be."""


def new_change_id() -> str:
    """
    Returns:
        A new change id: ``c`` and twelve hex digits.
    """
    return "c" + secrets.token_hex(6)


@dataclass(frozen=True)
class SyncSignature:
    """
    The person who approved a change and the change a push carries.

    Attributes:
        person: The central's label of the person (``ana``, ``token:ci``).
        change: The change's id.

    Raises:
        SyncSignatureError: When either could forge a second field of the header.
    """

    person: str
    change: str

    def __post_init__(self) -> None:
        if not _PERSON.fullmatch(self.person):
            raise SyncSignatureError(
                f"Not a person a push can be signed for: {self.person!r}",
                details="A person is 1 to 64 letters, digits or ._:@+-, starting with a letter "
                "or a digit.",
            )
        if not _CHANGE.fullmatch(self.change):
            raise SyncSignatureError(
                f"Not a change id: {self.change!r}",
                details="A change id is a 'c' and twelve hex digits; new_change_id() makes one.",
            )

    @property
    def actor(self) -> str:
        """The ``X-Noust-Actor`` header of a push."""
        return f"{ACTOR_PREFIX}{self.person} (change {self.change})"

    @property
    def elevation(self) -> str:
        """The ``X-Noust-Elevated`` header of a push."""
        return f"{ELEVATION_PREFIX}{self.change}"

    @classmethod
    def parse(cls, actor: str | None, elevated: str | None) -> ParsedSignature | None:
        """
        Read the two headers of a request.

        Args:
            actor: ``X-Noust-Actor``, when sent.
            elevated: ``X-Noust-Elevated``, when sent.

        Returns:
            None when the actor is not a push of the reconciler; otherwise the signature, and
            whether the confirmation names the change it is signed with.

        Raises:
            SyncSignatureError: When the actor starts as a signature and is not exactly one.
        """
        if actor is None or not actor.startswith(ACTOR_PREFIX):
            return None
        match = _ACTOR.fullmatch(actor)
        if match is None:
            raise SyncSignatureError(
                "The actor header of a fleet push is not 'fleet-sync on behalf of <person> "
                "(change <id>)'",
                details="The central sends it; check the central's version.",
            )
        signature = cls(match.group("person"), match.group("change"))
        return ParsedSignature(signature, attested=elevated == signature.elevation)


@dataclass(frozen=True)
class ParsedSignature:
    """
    A signature as a node read it.

    Attributes:
        signature: The person and the change the actor header names.
        attested: Whether the elevation header named the same change.
    """

    signature: SyncSignature
    attested: bool
