# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The client a credential says it is: a name and a version, kept as the client said them (items 184, 185).

When a person decides whether a call from a token goes ahead, and when they read what a token did, it
helps to know which program was on the other end: a CI runner, a script, a client of MCP. The server cannot
know that. What it has is what the client says of itself, twice:

- ``client_info``: the ``clientInfo`` of the ``initialize`` of MCP, a name and a version the client chose.
- ``user_agent``: the first product of the ``User-Agent`` of an API request.

Both are text from outside, so they are never a fact. They are cut to a size, made of characters a person
can read, kept a few to each credential (the oldest forgotten), written at most once in a while for the
same client, and shown labelled as what the client said (``declared``). They decide nothing: not which
class of caller a request is (:mod:`noust.core.callers`), not what it may do.

The credential is a token's name, which is on every audit line and never changes (names are unique across
revocations). Nothing here reads a token or stores one.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.timeutil import utc_now_iso

if TYPE_CHECKING:
    from noust.core.store import NoustStore

SOURCE_CLIENT_INFO = "client_info"
SOURCE_USER_AGENT = "user_agent"
SOURCES: tuple[str, ...] = (SOURCE_CLIENT_INFO, SOURCE_USER_AGENT)

#: Longest name and version kept; a client that sends more is cut.
MAX_NAME = 64
MAX_VERSION = 32
#: Clients kept for one credential.
MAX_PER_CREDENTIAL = 20
#: The same client of the same credential is written again after this long, not before.
REWRITE_SECONDS = 600.0
#: How many (credential, client) pairs the process remembers having written.
_REMEMBERED = 2048

#: What a name may be made of: a word of letters, digits, dots, dashes, plus signs, at signs and spaces.
_NAME = re.compile(r"[\w][\w .+@-]*")
_VERSION = re.compile(r"[\w][\w.+-]*")
_PRODUCT = re.compile(r"\s*([^\s/()]+)(?:/([^\s/()]*))?")

#: The gateway of the MCP server calls the API as its caller with its own user agent: it is Noust, not a client.
_OWN = frozenset({"noust-mcp"})

_written: dict[tuple[str, str, str, str, str], float] = {}


def _clean(text: Any, limit: int, pattern: re.Pattern[str]) -> str:
    """
    Args:
        text: What a client said.
        limit: The most it keeps.
        pattern: What the text must be made of.

    Returns:
        The text cut to ``limit``, or an empty string when it is not text a person can read.
    """
    if not isinstance(text, str):
        return ""
    cut = text.strip()[:limit]
    return cut if pattern.fullmatch(cut) else ""


def parse_user_agent(header: str | None) -> tuple[str, str] | None:
    """
    Read the product a ``User-Agent`` starts with.

    Args:
        header: The header as received.

    Returns:
        ``(name, version)`` of its first product, with an empty version when it has none; None when it
        says nothing a person can read, or is Noust's own gateway of MCP.
    """
    if not header:
        return None
    first = header.replace("\r", "\n").split("\n", 1)[0]
    found = _PRODUCT.match(first)
    if found is None:
        return None
    name = _clean(found.group(1), MAX_NAME, _NAME)
    if not name or name.lower() in _OWN:
        return None
    return name, _clean(found.group(2) or "", MAX_VERSION, _VERSION)


def client_label(name: str, version: str) -> str:
    """
    Args:
        name: A client's name.
        version: Its version, or empty.

    Returns:
        ``name version``, as one line to show.
    """
    return f"{name} {version}".strip()


@dataclass(frozen=True)
class ClientSeen:
    """
    A client a credential was seen with.

    Attributes:
        name: What the client called itself.
        version: Its version, or empty.
        source: ``client_info`` or ``user_agent``.
        first_seen: When it was first seen, ISO 8601.
        last_seen: When it was last seen.
    """

    name: str
    version: str
    source: str
    first_seen: str
    last_seen: str

    @property
    def label(self) -> str:
        """The name and version, as one line."""
        return client_label(self.name, self.version)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The client as the API shows it: ``declared`` is always true, so that a screen cannot show it
            as anything but what the client said.
        """
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "label": self.label,
            "declared": True,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


class CredentialClients:
    """
    Note and read the clients a credential was seen with.

    Args:
        store: The store; the process-wide one by default.
        clock: Seconds on a clock that only goes forward, replaceable in tests.
    """

    def __init__(
        self, store: NoustStore | None = None, *, clock: Callable[[], float] | None = None
    ) -> None:
        self._store = store
        self._clock = clock or time.monotonic

    @property
    def store(self) -> NoustStore:
        """The store the clients live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    def note(self, credential: str, source: str, name: str, version: str = "") -> bool:
        """
        Remember that a credential was seen with a client.

        Args:
            credential: The token's name.
            source: ``client_info`` or ``user_agent``.
            name: What the client called itself.
            version: Its version.

        Returns:
            Whether something was written: False for a client that is not text a person can read, an
            unknown source, or one written a moment ago.
        """
        clean_name = _clean(name, MAX_NAME, _NAME)
        clean_version = _clean(version, MAX_VERSION, _VERSION)
        if not credential or source not in SOURCES or not clean_name:
            return False
        key = (str(self.store.db_path), credential, source, clean_name, clean_version)
        now = self._clock()
        last = _written.get(key)
        if last is not None and now - last < REWRITE_SECONDS:
            return False
        if len(_written) >= _REMEMBERED:
            _written.clear()
        _written[key] = now
        moment = utc_now_iso()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO credential_clients (credential, source, name, version, first_seen, "
                "last_seen) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (credential, source, name, "
                "version) DO UPDATE SET last_seen = excluded.last_seen",
                (credential, source, clean_name, clean_version, moment, moment),
            )
            cursor.execute(
                "DELETE FROM credential_clients WHERE credential = ? AND rowid NOT IN ("
                "SELECT rowid FROM credential_clients WHERE credential = ? "
                "ORDER BY last_seen DESC, rowid DESC LIMIT ?)",
                (credential, credential, MAX_PER_CREDENTIAL),
            )
        return True

    def note_user_agent(self, credential: str, header: str | None) -> bool:
        """
        Remember the client a request's ``User-Agent`` names.

        Args:
            credential: The token's name.
            header: The header as received.

        Returns:
            Whether something was written.
        """
        parsed = parse_user_agent(header)
        return parsed is not None and self.note(credential, SOURCE_USER_AGENT, *parsed)

    def note_client_info(self, credential: str, info: object) -> bool:
        """
        Remember the client MCP's ``initialize`` says it is.

        Args:
            credential: The token's name.
            info: The ``clientInfo`` of the handshake: an object with ``name`` and ``version``.

        Returns:
            Whether something was written.
        """
        if not isinstance(info, Mapping):
            return False
        name = info.get("name")
        version = info.get("version")
        return self.note(
            credential,
            SOURCE_CLIENT_INFO,
            name if isinstance(name, str) else "",
            version if isinstance(version, str) else "",
        )

    def seen(self, credential: str, *, limit: int = 5) -> list[ClientSeen]:
        """
        Args:
            credential: The token's name.
            limit: At most this many.

        Returns:
            The clients it was seen with, the one seen last first.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT name, version, source, first_seen, last_seen FROM credential_clients "
                "WHERE credential = ? ORDER BY last_seen DESC, rowid DESC LIMIT ?",
                (credential, max(1, min(int(limit), MAX_PER_CREDENTIAL))),
            )
            return [ClientSeen(*(str(value) for value in row)) for row in cursor.fetchall()]

    def current(self, credential: str) -> ClientSeen | None:
        """
        The client to show beside a call of this credential.

        Args:
            credential: The token's name.

        Returns:
            What the MCP handshake said when it ever did (a name a client chose), else the newest
            ``User-Agent`` product; None when nobody was seen.
        """
        found = self.seen(credential, limit=MAX_PER_CREDENTIAL)
        for item in found:
            if item.source == SOURCE_CLIENT_INFO:
                return item
        return found[0] if found else None


__all__ = [
    "MAX_NAME",
    "MAX_PER_CREDENTIAL",
    "MAX_VERSION",
    "SOURCES",
    "SOURCE_CLIENT_INFO",
    "SOURCE_USER_AGENT",
    "ClientSeen",
    "CredentialClients",
    "client_label",
    "parse_user_agent",
]
