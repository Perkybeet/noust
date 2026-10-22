# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
DigitalOcean DNS.

A domain is its own zone and each record has a number of its own, which makes this the closest
to Cloudflare's shape. DigitalOcean keeps no note with a record, so the ownership marker rests on
what Noust remembers it created (see :mod:`noust.managers.dns.rrset`). The credential is a
personal access token with write scope.

Record names are relative to the domain (``www``, ``@`` for the apex) and a CNAME's target is
relative too unless it ends with a dot, so a full target is sent with its final dot.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from noust.managers.dns.provider import (
    AUTOMATIC_TTL,
    DnsProvider,
    DnsProviderError,
    RemoteRecord,
    Zone,
)
from noust.managers.dns.rest import Opener, RestClient

#: DigitalOcean's API.
API_URL = "https://api.digitalocean.com/v2"

#: Items per page, and pages read at most.
PAGE_SIZE = 200
MAX_PAGES = 100

#: The TTL of a record made without one.
DEFAULT_TTL = 300

_APEX = "@"


def client(token: str, opener: Opener | None = None) -> RestClient:
    """
    Args:
        token: A DigitalOcean personal access token.
        opener: Replacement for :func:`urllib.request.urlopen` (tests).

    Returns:
        A client holding it.
    """
    return RestClient(
        "DigitalOcean", API_URL, {"Authorization": f"Bearer {token}"}, opener=opener, hint=_hint
    )


def _hint(status: int) -> str:
    if status in (401, 403):
        return (
            "DigitalOcean did not accept this token for that. It must be a personal access "
            "token with write scope (cloud.digitalocean.com/account/api/tokens)."
        )
    if status == 404:
        return "DigitalOcean does not show this to the token: the domain or record may not exist."
    if status == 429:
        return "DigitalOcean is rate limiting this token; try again in a minute."
    return "Retry in a moment; if it persists, check https://status.digitalocean.com."


def _remote(item: dict[str, Any], zone: Zone) -> RemoteRecord:
    """
    Args:
        item: One record of an answer.
        zone: Its zone.

    Returns:
        The record, named in full.
    """
    short = str(item.get("name") or _APEX)
    return RemoteRecord(
        id=str(item.get("id") or ""),
        type=str(item.get("type") or ""),
        name=zone.name if short == _APEX else f"{short}.{zone.name}".lower(),
        content=str(item.get("data") or ""),
        ttl=int(item["ttl"]) if isinstance(item.get("ttl"), int) else None,
    )


class DigitalOceanProvider(DnsProvider):
    """
    The domains of a DigitalOcean account and their records.

    Args:
        api: The API client, holding the token.
    """

    kind = "digitalocean"
    comments = False

    def __init__(self, api: RestClient) -> None:
        self.api = api

    def zones(self) -> list[Zone]:
        """
        Returns:
            The domains of the account.

        Raises:
            DnsProviderError: The token was refused or DigitalOcean failed.
        """
        found: list[Zone] = []
        for page in range(1, MAX_PAGES + 1):
            answer = self.api.json("GET", "/domains", query={"page": page, "per_page": PAGE_SIZE})
            items = answer.get("domains") if isinstance(answer, dict) else None
            if not isinstance(items, list):
                raise DnsProviderError("DigitalOcean's answer to GET /domains is not a listing")
            found.extend(
                Zone(id=str(item["name"]).lower(), name=str(item["name"]).lower())
                for item in items
                if isinstance(item, dict) and item.get("name")
            )
            if not items or not ((answer.get("links") or {}).get("pages") or {}).get("next"):
                break
        return found

    def find_records(self, zone: Zone, name: str, record_type: str) -> list[RemoteRecord]:
        """
        Args:
            zone: The domain.
            name: The record's full name.
            record_type: Its type.

        Returns:
            Every record of that name and type.

        Raises:
            DnsProviderError: DigitalOcean failed.
        """
        answer = self.api.json(
            "GET",
            f"/domains/{quote(zone.name, safe='')}/records",
            query={"name": name.lower(), "type": record_type, "per_page": PAGE_SIZE},
        )
        records = [
            _remote(item, zone)
            for item in (answer or {}).get("domain_records") or []
            if isinstance(item, dict)
        ]
        return [r for r in records if r.name == name.lower() and r.type == record_type]

    def get_record(self, zone: Zone, record_id: str) -> RemoteRecord | None:
        """
        Args:
            zone: The domain.
            record_id: DigitalOcean's number.

        Returns:
            The record, or None when it no longer exists.

        Raises:
            DnsProviderError: DigitalOcean failed.
        """
        try:
            answer = self.api.json(
                "GET", f"/domains/{quote(zone.name, safe='')}/records/{quote(record_id, safe='')}"
            )
        except DnsProviderError as exc:
            if exc.status == 404:
                return None
            raise
        item = (answer or {}).get("domain_record")
        return _remote(item, zone) if isinstance(item, dict) else None

    def create_record(
        self,
        zone: Zone,
        name: str,
        record_type: str,
        content: str,
        *,
        ttl: int = AUTOMATIC_TTL,
        proxied: bool = False,
        comment: str | None = None,
    ) -> RemoteRecord:
        """
        Args:
            zone: The domain.
            name: The record's full name.
            record_type: Its type.
            content: What it holds.
            ttl: Seconds to live; the default when "automatic" is asked.
            proxied: Ignored: DigitalOcean does not proxy.
            comment: Ignored: DigitalOcean keeps no note.

        Returns:
            The record as created.

        Raises:
            DnsProviderError: DigitalOcean refused it.
        """
        full = name.lower().rstrip(".")
        short = _APEX if full == zone.name else full[: -len(zone.name) - 1]
        data = content
        if record_type == "CNAME" and not data.endswith("."):
            data = f"{data}."
        answer = self.api.json(
            "POST",
            f"/domains/{quote(zone.name, safe='')}/records",
            body={
                "type": record_type,
                "name": short,
                "data": data,
                "ttl": DEFAULT_TTL if ttl <= AUTOMATIC_TTL else max(30, ttl),
            },
        )
        item = (answer or {}).get("domain_record")
        if not isinstance(item, dict) or not item.get("id"):
            raise DnsProviderError("DigitalOcean did not say which record it created")
        return _remote(item, zone)

    def update_record(self, zone: Zone, record_id: str, content: str) -> RemoteRecord:
        """
        Args:
            zone: The domain.
            record_id: DigitalOcean's number.
            content: The new content.

        Returns:
            The record as it is now.

        Raises:
            DnsProviderError: DigitalOcean refused it.
        """
        answer = self.api.json(
            "PATCH",
            f"/domains/{quote(zone.name, safe='')}/records/{quote(record_id, safe='')}",
            body={"data": content},
        )
        item = (answer or {}).get("domain_record")
        if not isinstance(item, dict):
            raise DnsProviderError("DigitalOcean did not return the record it changed")
        return _remote(item, zone)

    def delete_record(self, zone: Zone, record_id: str) -> bool:
        """
        Args:
            zone: The domain.
            record_id: DigitalOcean's number.

        Returns:
            True when it was deleted, False when it was already gone.

        Raises:
            DnsProviderError: DigitalOcean refused it.
        """
        try:
            self.api.json(
                "DELETE",
                f"/domains/{quote(zone.name, safe='')}/records/{quote(record_id, safe='')}",
            )
        except DnsProviderError as exc:
            if exc.status == 404:
                return False
            raise
        return True
