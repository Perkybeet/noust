# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A DNS provider that lives in memory, and the packets a resolver answers with.

:class:`FakeProvider` is a whole provider account: zones, records with comments and
ids, and a log of what was asked of it, so a test can say "the record was created with
the marker" and "nothing was deleted" without a network. :func:`dns_answer` builds the
datagram a resolver sends back, for the tests of the resolver client.
"""

from __future__ import annotations

import io
import ipaddress
import itertools
import json
import struct
import urllib.request
from dataclasses import replace
from typing import Any
from urllib.error import HTTPError

from noust.managers.dns.provider import (
    AUTOMATIC_TTL,
    DnsProvider,
    DnsProviderError,
    RemoteRecord,
    Zone,
    same_value,
)


class Cloudflare:
    """A Cloudflare that answers canned JSON and remembers what it was asked."""

    def __init__(self) -> None:
        self.requests: list[urllib.request.Request] = []
        self.answers: list[Any] = []

    def say(self, payload: Any, status: int = 200) -> None:
        self.answers.append((status, payload))

    def __call__(self, request: urllib.request.Request, timeout: float = 0) -> Any:
        self.requests.append(request)
        status, payload = self.answers.pop(0)
        body = json.dumps(payload).encode()
        if status >= 400:
            raise HTTPError(request.full_url, status, "error", {}, io.BytesIO(body))  # type: ignore[arg-type]
        return io.BytesIO(body)


def zones(*names: str) -> dict[str, Any]:
    return {
        "success": True,
        "result": [{"id": f"id-{n}", "name": n, "account": {"id": "acc"}} for n in names],
        "result_info": {"total_pages": 1},
    }


class FakeProvider(DnsProvider):
    """
    An in-memory DNS account.

    Attributes:
        calls: ``(operation, detail)`` for every change asked of it, in order.
    """

    kind = "cloudflare"

    def __init__(self, *zones: str) -> None:
        self._zones = [Zone(id=f"zone-{name}", name=name) for name in zones]
        self.records: dict[str, list[RemoteRecord]] = {zone.id: [] for zone in self._zones}
        self.calls: list[tuple[str, str]] = []
        self._ids = itertools.count(1)
        self.fail_on: str | None = None

    def zones(self) -> list[Zone]:
        return list(self._zones)

    def find_records(self, zone: Zone, name: str, record_type: str) -> list[RemoteRecord]:
        return [
            record
            for record in self.records[zone.id]
            if record.name == name.lower() and record.type == record_type
        ]

    def get_record(self, zone: Zone, record_id: str) -> RemoteRecord | None:
        return next((r for r in self.records[zone.id] if r.id == record_id), None)

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
        self._maybe_fail("create")
        record = RemoteRecord(
            id=f"rec-{next(self._ids)}",
            type=record_type,
            name=name.lower(),
            content=content,
            ttl=ttl,
            proxied=proxied,
            comment=comment,
        )
        self.records[zone.id].append(record)
        self.calls.append(("create", f"{record_type} {name} {content}"))
        return record

    def update_record(self, zone: Zone, record_id: str, content: str) -> RemoteRecord:
        self._maybe_fail("update")
        records = self.records[zone.id]
        for index, record in enumerate(records):
            if record.id == record_id:
                records[index] = replace(record, content=content)
                self.calls.append(("update", f"{record.name} {content}"))
                return records[index]
        raise DnsProviderError("no such record", status=404)

    def delete_record(self, zone: Zone, record_id: str) -> bool:
        self._maybe_fail("delete")
        records = self.records[zone.id]
        for record in records:
            if record.id == record_id:
                records.remove(record)
                self.calls.append(("delete", f"{record.type} {record.name}"))
                return True
        return False

    def seed(
        self,
        zone: str,
        name: str,
        record_type: str,
        content: str,
        comment: str | None = None,
    ) -> RemoteRecord:
        """
        Put a record there as if the operator had made it by hand.

        Args:
            zone: The zone's name.
            name: The record's name.
            record_type: Its type.
            content: What it holds.
            comment: A note on it.

        Returns:
            The record.
        """
        target = next(z for z in self._zones if z.name == zone)
        record = RemoteRecord(
            id=f"rec-{next(self._ids)}",
            type=record_type,
            name=name.lower(),
            content=content,
            ttl=1,
            proxied=False,
            comment=comment,
        )
        self.records[target.id].append(record)
        return record

    def holds(self, zone: str, name: str, record_type: str, content: str) -> bool:
        """
        Args:
            zone: The zone's name.
            name: A record's name.
            record_type: Its type.
            content: What it should hold.

        Returns:
            Whether the zone has such a record.
        """
        target = next(z for z in self._zones if z.name == zone)
        return any(
            r.name == name.lower() and r.type == record_type and same_value(r.content, content)
            for r in self.records[target.id]
        )

    def _maybe_fail(self, operation: str) -> None:
        if self.fail_on == operation:
            raise DnsProviderError(f"Cloudflare refused the {operation}", status=403)


class FakeTunnelProvider(FakeProvider):
    """
    An in-memory Cloudflare that also runs tunnels.

    Attributes:
        tunnels: Tunnel id to name, for every tunnel that exists.
        ingress: Tunnel id to the last ingress it was given.
        cloudflare_accounts: What the token reaches, ``(id, name)``.
        token: What the connector's token is.
        refuse_delete: Deleting a tunnel is refused, as when a connector still counts as connected.
    """

    def __init__(self, *zones: str) -> None:
        super().__init__(*zones)
        self.tunnels: dict[str, str] = {}
        self.ingress: dict[str, list[dict[str, Any]]] = {}
        self.cloudflare_accounts: list[tuple[str, str]] = [("acc-1", "Main")]
        self.token = "eyJhIjoi" + "Z" * 60
        self.refuse_delete = False
        self.refuse_configure = False
        self._tunnel_ids = itertools.count(1)

    def accounts(self) -> list[tuple[str, str]]:
        return list(self.cloudflare_accounts)

    def create_tunnel(self, account_id: str, name: str) -> str:
        self._maybe_fail("create_tunnel")
        tunnel_id = f"tun-{next(self._tunnel_ids)}"
        self.tunnels[tunnel_id] = name
        self.calls.append(("create_tunnel", name))
        return tunnel_id

    def tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        return self.token

    def configure_tunnel(
        self, account_id: str, tunnel_id: str, ingress: list[dict[str, Any]]
    ) -> None:
        if self.refuse_configure:
            raise DnsProviderError("Cloudflare refused the configuration", status=400)
        self.ingress[tunnel_id] = ingress
        self.calls.append(("configure", tunnel_id))

    def delete_tunnel(self, account_id: str, tunnel_id: str) -> bool:
        if self.refuse_delete:
            raise DnsProviderError("Cannot delete a tunnel with active connections", status=400)
        self.calls.append(("delete_tunnel", tunnel_id))
        return self.tunnels.pop(tunnel_id, None) is not None


def dns_answer(ident: int, qtype: int, addresses: list[str], rcode: int = 0) -> bytes:
    """
    Build the datagram a resolver sends back for one question.

    Args:
        ident: The id of the question.
        qtype: ``1`` for A, ``28`` for AAAA.
        addresses: What to answer with; those of the other family are left out.
        rcode: The response code (3 is "no such name").

    Returns:
        The datagram.
    """
    question = b"\x04test\x07example\x03com\x00" + struct.pack(">HH", qtype, 1)
    answers = b""
    count = 0
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if (qtype == 1) != (parsed.version == 4):
            continue
        answers += (
            b"\xc0\x0c" + struct.pack(">HHIH", qtype, 1, 60, len(parsed.packed)) + parsed.packed
        )
        count += 1
    header = struct.pack(">HHHHHH", ident, 0x8180 | rcode, 1, count, 0, 0)
    return header + question + answers
