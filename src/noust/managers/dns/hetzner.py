# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Hetzner DNS, through the Hetzner Cloud API.

Hetzner moved its DNS into the Cloud API (``api.hetzner.cloud``): the old DNS API at
``dns.hetzner.com`` and its separate tokens are gone, and a zone has to be migrated to the Cloud
Console before it can be managed here. A token of a Cloud project with read and write rights
manages every zone of that project. Zones are named, records live in sets (RRSets) of one name
and type, and each value of a set can carry a comment, which is where the ownership marker of
:mod:`noust.managers.dns.records` is kept like at Cloudflare.

Changes are actions that finish a moment after the request is accepted; a change is waited for
before it is reported, so what Noust says is there is there.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from noust.managers.dns.provider import DnsProviderError, Zone, unquote
from noust.managers.dns.rest import Opener, RestClient
from noust.managers.dns.rrset import Rrset, RrsetProvider

#: Hetzner's Cloud API.
API_URL = "https://api.hetzner.cloud/v1"

#: Zones per page, and pages read at most.
PAGE_SIZE = 50
MAX_PAGES = 100

#: How many times an action is looked at, and the pause between looks, in seconds.
ACTION_POLLS = 40
ACTION_PAUSE = 0.5

_APEX = "@"


def client(token: str, opener: Opener | None = None) -> RestClient:
    """
    Args:
        token: A Hetzner Cloud project's API token.
        opener: Replacement for :func:`urllib.request.urlopen` (tests).

    Returns:
        A client holding it.
    """
    return RestClient(
        "Hetzner", API_URL, {"Authorization": f"Bearer {token}"}, opener=opener, hint=_hint
    )


def _hint(status: int) -> str:
    if status in (401, 403):
        return (
            "Hetzner did not accept this token for that. It must be an API token of the Cloud "
            "project that holds the zones, with read and write rights (the old DNS Console "
            "tokens no longer work: the zone has to be migrated to the Cloud Console first)."
        )
    if status == 404:
        return "Hetzner does not show this to the token: the zone may not be migrated to the Cloud Console."
    if status == 429:
        return "Hetzner is rate limiting this token; try again in a minute."
    return "Retry in a moment; if it persists, check https://status.hetzner.com."


def relative(name: str, zone: Zone) -> str:
    """
    Args:
        name: A full name inside the zone.
        zone: The zone.

    Returns:
        The name as Hetzner writes it: ``@`` for the apex, else without the zone.
    """
    full = name.lower().rstrip(".")
    if full == zone.name:
        return _APEX
    return full[: -len(zone.name) - 1] if full.endswith(f".{zone.name}") else full


def full(name: str, zone: Zone) -> str:
    """
    Args:
        name: A name as Hetzner writes it.
        zone: The zone.

    Returns:
        The full name.
    """
    return zone.name if name == _APEX else f"{name}.{zone.name}"


class HetznerProvider(RrsetProvider):
    """
    A Hetzner Cloud project's zones and their records.

    Args:
        api: The API client, holding the token.
        sleep: Pauses between looks at an action (tests replace it).
    """

    kind = "hetzner"
    comments = True

    def __init__(self, api: RestClient, sleep: Callable[[float], None] = time.sleep) -> None:
        self.api = api
        self._sleep = sleep

    def zones(self) -> list[Zone]:
        """
        Returns:
            The zones this token can edit.

        Raises:
            DnsProviderError: The token was refused or Hetzner failed.
        """
        found: list[Zone] = []
        for page in range(1, MAX_PAGES + 1):
            answer = self.api.json("GET", "/zones", query={"page": page, "per_page": PAGE_SIZE})
            items = answer.get("zones") if isinstance(answer, dict) else None
            if not isinstance(items, list):
                raise DnsProviderError("Hetzner's answer to GET /zones is not a listing")
            for item in items:
                if (
                    isinstance(item, dict)
                    and item.get("id") is not None
                    and item.get("name")
                    and item.get("mode", "primary") == "primary"
                ):
                    found.append(Zone(id=str(item["id"]), name=str(item["name"]).lower()))
            last = ((answer.get("meta") or {}).get("pagination") or {}).get("last_page") or 1
            if page >= int(last) or not items:
                break
        return found

    def read_rrset(self, zone: Zone, name: str, record_type: str) -> Rrset | None:
        """
        Args:
            zone: The zone.
            name: The set's full name.
            record_type: Its type.

        Returns:
            The set, or None.

        Raises:
            DnsProviderError: Hetzner failed.
        """
        answer = self.api.json(
            "GET",
            f"/zones/{zone.id}/rrsets",
            query={"name": relative(name, zone), "type": record_type},
        )
        for item in (answer or {}).get("rrsets") or []:
            if not isinstance(item, dict):
                continue
            if item.get("name") == relative(name, zone) and item.get("type") == record_type:
                return self._rrset(item, zone)
        return None

    def write_rrset(self, zone: Zone, rrset: Rrset, *, existed: bool) -> None:
        """
        Args:
            zone: The zone.
            rrset: What the set must hold.
            existed: Whether it is already at Hetzner.

        Raises:
            DnsProviderError: Hetzner refused it.
        """
        records = [
            {"value": self._wire(rrset.type, value), **({"comment": note} if note else {})}
            for value, note in rrset.values
        ]
        short = relative(rrset.name, zone)
        if existed:
            answer = self.api.json(
                "POST",
                f"/zones/{zone.id}/rrsets/{short}/{rrset.type}/actions/set_records",
                body={"records": records},
            )
        else:
            body: dict[str, Any] = {"name": short, "type": rrset.type, "records": records}
            if rrset.ttl is not None:
                body["ttl"] = max(60, rrset.ttl)
            answer = self.api.json("POST", f"/zones/{zone.id}/rrsets", body=body)
        self._wait(zone, answer)

    def remove_rrset(self, zone: Zone, name: str, record_type: str) -> bool:
        """
        Args:
            zone: The zone.
            name: The set's full name.
            record_type: Its type.

        Returns:
            True when it was removed, False when it was not there.

        Raises:
            DnsProviderError: Hetzner refused it.
        """
        try:
            answer = self.api.json(
                "DELETE", f"/zones/{zone.id}/rrsets/{relative(name, zone)}/{record_type}"
            )
        except DnsProviderError as exc:
            if exc.status == 404:
                return False
            raise
        self._wait(zone, answer)
        return True

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _wire(record_type: str, value: str) -> str:
        """
        Args:
            record_type: The type.
            value: A value as Noust holds it.

        Returns:
            As Hetzner takes it: a TXT value in quotes.
        """
        return f'"{value}"' if record_type == "TXT" and not value.startswith('"') else value

    @staticmethod
    def _rrset(item: dict[str, Any], zone: Zone) -> Rrset:
        """
        Args:
            item: A set of Hetzner's answer.
            zone: Its zone.

        Returns:
            The set.
        """
        record_type = str(item["type"])
        values = tuple(
            (
                unquote(str(entry["value"])) if record_type == "TXT" else str(entry["value"]),
                entry.get("comment") or None,
            )
            for entry in item.get("records") or []
            if isinstance(entry, dict) and "value" in entry
        )
        ttl = item.get("ttl")
        return Rrset(full(str(item["name"]), zone), record_type, int(ttl) if ttl else None, values)

    def _wait(self, zone: Zone, answer: Any) -> None:
        """
        Wait for the action a change started to finish.

        Args:
            zone: The zone.
            answer: What the change answered.

        Raises:
            DnsProviderError: The action failed or did not finish.
        """
        action = (answer or {}).get("action") if isinstance(answer, dict) else None
        if not isinstance(action, dict) or action.get("id") is None:
            return
        for attempt in range(ACTION_POLLS):
            status = action.get("status")
            if status == "success":
                return
            if status == "error":
                error = action.get("error") or {}
                raise DnsProviderError(
                    "Hetzner could not apply the change",
                    output=str(error.get("message") or error.get("code") or "unknown error"),
                )
            if attempt:
                self._sleep(ACTION_PAUSE)
            polled = self.api.json("GET", f"/zones/{zone.id}/actions/{action['id']}")
            action = (polled or {}).get("action") or action
        raise DnsProviderError(
            "Hetzner did not finish the change in time",
            details="Look at it in the Cloud Console; the change may still complete.",
        )
