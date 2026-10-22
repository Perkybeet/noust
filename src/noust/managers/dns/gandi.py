# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Gandi LiveDNS.

A domain is its own zone, named by its fully qualified name, and its records live in sets of one
name and type read and written whole (``rrset_values``). The credential is a personal access
token with LiveDNS rights. Gandi keeps no note with a record, so the ownership marker rests on
what Noust remembers it created (see :mod:`noust.managers.dns.rrset`), and its smallest TTL is
300 seconds.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from noust.managers.dns.provider import DnsProviderError, Zone, unquote
from noust.managers.dns.rest import Opener, RestClient
from noust.managers.dns.rrset import Rrset, RrsetProvider

#: Gandi's LiveDNS API.
API_URL = "https://api.gandi.net/v5/livedns"

#: Gandi's smallest TTL, which is also what a record gets when none is asked for.
MINIMUM_TTL = 300

_APEX = "@"


def client(token: str, opener: Opener | None = None) -> RestClient:
    """
    Args:
        token: A Gandi personal access token.
        opener: Replacement for :func:`urllib.request.urlopen` (tests).

    Returns:
        A client holding it.
    """
    return RestClient(
        "Gandi", API_URL, {"Authorization": f"Bearer {token}"}, opener=opener, hint=_hint
    )


def _hint(status: int) -> str:
    if status in (401, 403):
        return (
            "Gandi did not accept this token for that. It must be a personal access token with "
            "the 'Manage domain name technical configurations' right (LiveDNS) on the domains "
            "(account.gandi.net, Organizations, Personal Access Token)."
        )
    if status == 404:
        return "Gandi does not show this to the token: the domain may not use LiveDNS or be in another organization."
    if status == 429:
        return "Gandi is rate limiting this token; try again in a minute."
    return "Retry in a moment; if it persists, check https://status.gandi.net."


def _short(name: str, zone: Zone) -> str:
    full = name.lower().rstrip(".")
    if full == zone.name:
        return _APEX
    return full[: -len(zone.name) - 1] if full.endswith(f".{zone.name}") else full


class GandiProvider(RrsetProvider):
    """
    The domains of a Gandi account and their records.

    Args:
        api: The API client, holding the token.
    """

    kind = "gandi"
    comments = False

    def __init__(self, api: RestClient) -> None:
        self.api = api

    def zones(self) -> list[Zone]:
        """
        Returns:
            The domains this token can edit.

        Raises:
            DnsProviderError: The token was refused or Gandi failed.
        """
        answer = self.api.json("GET", "/domains")
        if not isinstance(answer, list):
            raise DnsProviderError("Gandi's answer to GET /domains is not a listing")
        return [
            Zone(id=str(item["fqdn"]).lower(), name=str(item["fqdn"]).lower())
            for item in answer
            if isinstance(item, dict) and item.get("fqdn")
        ]

    def read_rrset(self, zone: Zone, name: str, record_type: str) -> Rrset | None:
        """
        Args:
            zone: The domain.
            name: The set's full name.
            record_type: Its type.

        Returns:
            The set, or None.

        Raises:
            DnsProviderError: Gandi failed.
        """
        try:
            item = self.api.json("GET", self._path(zone, name, record_type))
        except DnsProviderError as exc:
            if exc.status == 404:
                return None
            raise
        if not isinstance(item, dict):
            return None
        values = tuple(
            (unquote(str(value)) if record_type == "TXT" else str(value), None)
            for value in item.get("rrset_values") or []
        )
        if not values:
            return None
        ttl = item.get("rrset_ttl")
        return Rrset(name.lower(), record_type, int(ttl) if ttl else None, values)

    def write_rrset(self, zone: Zone, rrset: Rrset, *, existed: bool) -> None:
        """
        Args:
            zone: The domain.
            rrset: What the set must hold.
            existed: Whether it is already at Gandi (a PUT creates or replaces).

        Raises:
            DnsProviderError: Gandi refused it.
        """
        values = [
            f'"{value}"' if rrset.type == "TXT" and not value.startswith('"') else value
            for value, _note in rrset.values
        ]
        body: dict[str, Any] = {
            "rrset_values": values,
            "rrset_ttl": max(MINIMUM_TTL, rrset.ttl or MINIMUM_TTL),
        }
        self.api.json("PUT", self._path(zone, rrset.name, rrset.type), body=body)

    def remove_rrset(self, zone: Zone, name: str, record_type: str) -> bool:
        """
        Args:
            zone: The domain.
            name: The set's full name.
            record_type: Its type.

        Returns:
            True when it was removed, False when it was not there.

        Raises:
            DnsProviderError: Gandi refused it.
        """
        try:
            self.api.json("DELETE", self._path(zone, name, record_type))
        except DnsProviderError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    @staticmethod
    def _path(zone: Zone, name: str, record_type: str) -> str:
        """
        Args:
            zone: The domain.
            name: A full name in it.
            record_type: The type.

        Returns:
            The path of that set.
        """
        short = quote(_short(name, zone), safe="@*")
        return f"/domains/{quote(zone.name, safe='')}/records/{short}/{quote(record_type, safe='')}"
