# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
IONOS Hosting DNS (``api.hosting.ionos.com/dns/v1``).

A domain is a zone with an id of its own, and each record has an id of its own too, which makes
this the shape of DigitalOcean's. The credential is the API key IONOS issues in its developer
portal: a public prefix and a secret joined by a dot, sent whole in ``X-API-Key``. IONOS keeps no
note with a record, so the ownership marker rests on what Noust remembers it created (see
:mod:`noust.managers.dns.rrset`).

What this module relies on, from IONOS's public API documentation (developer.hosting.ionos.com):
records are named in full (``www.example.com``), created in an array, changed with a body of
``content``, ``ttl``, ``prio`` and ``disabled`` and removed by id; a TXT value travels in double
quotes; a zone of type ``SLAVE`` is a copy of somebody else's and cannot be written; an error is a
list of ``{"code", "message"}``. A record the operator switched off (``disabled``) answers
nothing, so it is not one Noust finds.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from noust.managers.dns.provider import (
    AUTOMATIC_TTL,
    DnsProvider,
    DnsProviderError,
    RemoteRecord,
    Zone,
    unquote,
)
from noust.managers.dns.rest import Opener, RestClient, json_words

#: IONOS's Hosting DNS API.
API_URL = "https://api.hosting.ionos.com/dns/v1"

#: The TTL of a record made without one, and the smallest one asked for (a minute, the smallest
#: the control panel offers).
DEFAULT_TTL = 3600
MINIMUM_TTL = 60

#: What a key is: the public prefix, a dot, the secret.
_KEY = re.compile(r"[^\s.]+\.\S+")


def client(key: str, opener: Opener | None = None) -> RestClient:
    """
    Args:
        key: An IONOS API key, ``prefix.secret``.
        opener: Replacement for :func:`urllib.request.urlopen` (tests).

    Returns:
        A client holding it.

    Raises:
        DnsProviderError: The key is not a prefix and a secret joined by a dot.
    """
    if not _KEY.fullmatch(key):
        raise DnsProviderError(
            "That is not an IONOS API key: it is a public prefix and a secret joined by a dot",
            details="Create one at developer.hosting.ionos.com/keys and paste it as prefix.secret.",
        )
    return RestClient(
        "IONOS", API_URL, {"X-API-Key": key}, opener=opener, hint=_hint, explain=_words
    )


def _hint(status: int) -> str:
    if status in (401, 403):
        return (
            "IONOS did not accept this key for that. Create an API key at "
            "developer.hosting.ionos.com/keys, paste it whole as prefix.secret, and note that the "
            "domain's DNS must be hosted by IONOS."
        )
    if status == 404:
        return "IONOS does not show this to the key: the zone or the record may not exist."
    if status == 429:
        return "IONOS is rate limiting this key; try again in a minute."
    if status in (400, 422):
        return "IONOS refused the request as invalid."
    return "Retry in a moment; if it persists, check IONOS's status page."


def _words(raw: bytes) -> str:
    """
    Read IONOS's error body: a list of ``{"code", "message"}``, or one such object.

    Args:
        raw: The body.

    Returns:
        ``code: message`` for each, joined; the generic reading when it is neither.
    """
    try:
        payload = json.loads(raw or b"[]")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return json_words(raw)
    items = payload if isinstance(payload, list) else [payload]
    parts = [
        f"{item['code']}: {item['message']}" if item.get("code") else str(item["message"])
        for item in items
        if isinstance(item, dict) and item.get("message")
    ]
    return "; ".join(parts) or json_words(raw)


def _remote(item: dict[str, Any], zone: Zone) -> RemoteRecord:
    """
    Args:
        item: One record of an answer.
        zone: Its zone.

    Returns:
        The record.
    """
    record_type = str(item.get("type") or "")
    content = str(item.get("content") or "")
    return RemoteRecord(
        id=str(item.get("id") or ""),
        type=record_type,
        name=str(item.get("name") or zone.name).lower().rstrip("."),
        content=unquote(content) if record_type == "TXT" else content,
        ttl=int(item["ttl"]) if isinstance(item.get("ttl"), int) else None,
    )


class IonosProvider(DnsProvider):
    """
    The zones of an IONOS account and their records.

    Args:
        api: The API client, holding the key.
    """

    kind = "ionos"
    comments = False

    def __init__(self, api: RestClient) -> None:
        self.api = api

    def zones(self) -> list[Zone]:
        """
        Returns:
            The zones this key can edit: not the ones IONOS only copies from elsewhere.

        Raises:
            DnsProviderError: The key was refused or IONOS failed.
        """
        answer = self.api.json("GET", "/zones")
        if not isinstance(answer, list):
            raise DnsProviderError("IONOS's answer to GET /zones is not a listing")
        return [
            Zone(id=str(item["id"]), name=str(item["name"]).lower().rstrip("."))
            for item in answer
            if isinstance(item, dict)
            and item.get("id")
            and item.get("name")
            and item.get("type") != "SLAVE"
        ]

    def find_records(self, zone: Zone, name: str, record_type: str) -> list[RemoteRecord]:
        """
        Args:
            zone: The zone.
            name: The record's full name.
            record_type: Its type.

        Returns:
            Every enabled record of that name and type.

        Raises:
            DnsProviderError: IONOS failed.
        """
        wanted = name.lower().rstrip(".")
        answer = self.api.json(
            "GET",
            f"/zones/{quote(zone.id, safe='')}",
            query={"recordName": wanted, "recordType": record_type},
        )
        items = answer.get("records") if isinstance(answer, dict) else None
        records = [
            _remote(item, zone)
            for item in items or []
            if isinstance(item, dict) and not item.get("disabled")
        ]
        return [r for r in records if r.name == wanted and r.type == record_type]

    def get_record(self, zone: Zone, record_id: str) -> RemoteRecord | None:
        """
        Args:
            zone: The zone.
            record_id: IONOS's id.

        Returns:
            The record, or None when it no longer exists or was switched off.

        Raises:
            DnsProviderError: IONOS failed.
        """
        item = self._read(zone, record_id)
        if item is None or item.get("disabled"):
            return None
        return _remote(item, zone)

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
            zone: The zone.
            name: The record's full name.
            record_type: Its type.
            content: What it holds.
            ttl: Seconds to live; an hour when "automatic" is asked, a minute at least.
            proxied: Ignored: IONOS does not proxy.
            comment: Ignored: IONOS keeps no note.

        Returns:
            The record as created.

        Raises:
            DnsProviderError: IONOS refused it.
        """
        answer = self.api.json(
            "POST",
            f"/zones/{quote(zone.id, safe='')}/records",
            body=[
                {
                    "name": name.lower().rstrip("."),
                    "type": record_type,
                    "content": f'"{content}"' if record_type == "TXT" else content,
                    "ttl": DEFAULT_TTL if ttl <= AUTOMATIC_TTL else max(MINIMUM_TTL, ttl),
                    "prio": 0,
                    "disabled": False,
                }
            ],
        )
        item = answer[0] if isinstance(answer, list) and answer else None
        if not isinstance(item, dict) or not item.get("id"):
            raise DnsProviderError("IONOS did not say which record it created")
        return _remote(item, zone)

    def update_record(self, zone: Zone, record_id: str, content: str) -> RemoteRecord:
        """
        Change only what a record holds: its TTL, priority and switch stay.

        Args:
            zone: The zone.
            record_id: IONOS's id.
            content: The new content.

        Returns:
            The record as it is now.

        Raises:
            DnsProviderError: The record is gone, or IONOS refused it.
        """
        current = self._read(zone, record_id)
        if current is None:
            raise DnsProviderError("The record to change is not there any more", status=404)
        record_type = str(current.get("type") or "")
        answer = self.api.json(
            "PUT",
            f"/zones/{quote(zone.id, safe='')}/records/{quote(record_id, safe='')}",
            body={
                "content": f'"{content}"' if record_type == "TXT" else content,
                "ttl": current.get("ttl") or DEFAULT_TTL,
                "prio": current.get("prio") or 0,
                "disabled": bool(current.get("disabled")),
            },
        )
        if not isinstance(answer, dict):
            raise DnsProviderError("IONOS did not return the record it changed")
        return _remote(answer, zone)

    def delete_record(self, zone: Zone, record_id: str) -> bool:
        """
        Args:
            zone: The zone.
            record_id: IONOS's id.

        Returns:
            True when it was deleted, False when it was already gone.

        Raises:
            DnsProviderError: IONOS refused it.
        """
        try:
            self.api.json(
                "DELETE",
                f"/zones/{quote(zone.id, safe='')}/records/{quote(record_id, safe='')}",
            )
        except DnsProviderError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    def _read(self, zone: Zone, record_id: str) -> dict[str, Any] | None:
        """
        Args:
            zone: The zone.
            record_id: IONOS's id.

        Returns:
            The record as IONOS has it, or None when there is none.

        Raises:
            DnsProviderError: IONOS failed.
        """
        try:
            answer = self.api.json(
                "GET", f"/zones/{quote(zone.id, safe='')}/records/{quote(record_id, safe='')}"
            )
        except DnsProviderError as exc:
            if exc.status == 404:
                return None
            raise
        return answer if isinstance(answer, dict) else None
