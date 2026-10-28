# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The HTTPS side of four DNS providers, as callables with ``urlopen``'s convention.

Each wire is a small in-memory copy of one provider's API as far as Noust uses it: it keeps the
zones and the records, answers the requests the client makes in the shapes the provider documents,
refuses what the real one refuses (a delete that does not match, a missing zone), and remembers
every request so a test can say what went out. They are the opener a provider client is built over
(``digitalocean.client(token, wire)``), so the whole stack from the ownership rules down to the
request body is exercised without a network.
"""

from __future__ import annotations

import io
import itertools
import json
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlsplit
from xml.sax.saxutils import escape

TOKEN = "tok-" + "t" * 36
ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


@dataclass
class Seen:
    """
    One request a wire received.

    Attributes:
        method: The HTTP method.
        path: The path under the API root, percent-decoded.
        query: The query string as a mapping.
        body: The request body.
        headers: The request headers, names lower case.
        url: The full address, as sent.
    """

    method: str
    path: str
    query: dict[str, str]
    body: bytes
    headers: dict[str, str]
    url: str

    def json(self) -> Any:
        """
        Returns:
            The body decoded as JSON.
        """
        return json.loads(self.body)


@dataclass
class Wire:
    """
    A provider's API in memory.

    Attributes:
        root: The API root the client is given.
        requests: Every request, in order.
        refuse_next: ``(status, body)`` to answer the next request with instead.
        down: Raise a connection failure for every request.
    """

    root: str
    requests: list[Seen] = field(default_factory=list)
    refuse_next: tuple[int, bytes] | None = None
    down: bool = False

    def __call__(self, request: urllib.request.Request, timeout: float = 0) -> io.BytesIO:
        if self.down:
            raise URLError("connection refused")
        parts = urlsplit(request.full_url)
        assert parts.scheme == "https", "a provider is reached over https only"
        base = urlsplit(self.root)
        assert parts.netloc == base.netloc, f"unexpected host {parts.netloc}"
        seen = Seen(
            method=request.get_method(),
            path=unquote(parts.path.removeprefix(base.path)),
            query=dict(parse_qsl(parts.query)),
            body=request.data if isinstance(request.data, bytes) else b"",
            headers={k.lower(): v for k, v in request.header_items()},
            url=request.full_url,
        )
        self.requests.append(seen)
        if self.refuse_next is not None:
            status, payload = self.refuse_next
            self.refuse_next = None
        else:
            status, payload = self.handle(seen)
        if status >= 400:
            raise HTTPError(request.full_url, status, "error", {}, io.BytesIO(payload))  # type: ignore[arg-type]
        return io.BytesIO(payload)

    def handle(self, seen: Seen) -> tuple[int, bytes]:
        """
        Args:
            seen: The request.

        Returns:
            The status and body to answer with.
        """
        raise NotImplementedError

    def writes(self) -> list[Seen]:
        """
        Returns:
            The requests that changed something.
        """
        return [r for r in self.requests if r.method != "GET"]

    # What a test does to the zone behind the API, as an operator would.

    def seed(
        self, name: str, record_type: str, value: str, ttl: int = 300, comment: str | None = None
    ) -> None:
        """
        Put a record in the zone, as an operator would with the provider's own console.

        Args:
            name: The full name.
            record_type: The type.
            value: What it holds, unquoted.
            ttl: Seconds to live.
            comment: A note, where the provider keeps them.
        """
        raise NotImplementedError

    def values(self, name: str, record_type: str) -> list[str]:
        """
        Args:
            name: A full name.
            record_type: A type.

        Returns:
            What the zone holds at that name and type, unquoted.
        """
        raise NotImplementedError


def _json(payload: Any) -> bytes:
    return json.dumps(payload).encode()


def _plain(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


# -- DigitalOcean ------------------------------------------------------------------


class DigitalOceanWire(Wire):
    """DigitalOcean: domains, and records that each have a number."""

    def __init__(self, *domains: str, page_size: int = 200) -> None:
        super().__init__("https://api.digitalocean.com/v2")
        self.domains = list(domains)
        self.page_size = page_size
        self.records: dict[str, list[dict[str, Any]]] = {d: [] for d in domains}
        self._ids = itertools.count(1000)

    def _short(self, domain: str, name: str) -> str:
        return "@" if name == domain else name[: -len(domain) - 1]

    def seed(
        self, name: str, record_type: str, value: str, ttl: int = 300, comment: str | None = None
    ) -> None:
        domain = next(d for d in self.domains if name == d or name.endswith(f".{d}"))
        self.records[domain].append(
            {
                "id": next(self._ids),
                "type": record_type,
                "name": self._short(domain, name),
                "data": value,
                "ttl": ttl,
            }
        )

    def values(self, name: str, record_type: str) -> list[str]:
        domain = next(d for d in self.domains if name == d or name.endswith(f".{d}"))
        short = self._short(domain, name)
        return [
            r["data"]
            for r in self.records[domain]
            if r["name"] == short and r["type"] == record_type
        ]

    def edit(self, record_id: int, **changes: Any) -> None:
        """
        Change a record as the operator would.

        Args:
            record_id: Its number.
            **changes: The fields to set.
        """
        for records in self.records.values():
            for record in records:
                if record["id"] == record_id:
                    record.update(changes)

    def handle(self, seen: Seen) -> tuple[int, bytes]:
        assert seen.headers["authorization"] == f"Bearer {TOKEN}"
        parts = seen.path.strip("/").split("/")
        if parts == ["domains"]:
            page = int(seen.query.get("page", "1"))
            start = (page - 1) * self.page_size
            chunk = self.domains[start : start + self.page_size]
            more = start + self.page_size < len(self.domains)
            links = {"pages": {"next": f"{self.root}/domains?page={page + 1}"}} if more else {}
            return 200, _json({"domains": [{"name": d} for d in chunk], "links": links})
        if len(parts) < 3 or parts[0] != "domains" or parts[2] != "records":
            return 404, _json(
                {"id": "not_found", "message": "The resource you requested could not be found."}
            )
        domain = parts[1]
        if domain not in self.records:
            return 404, _json(
                {"id": "not_found", "message": "The resource you requested could not be found."}
            )
        records = self.records[domain]
        if len(parts) == 3 and seen.method == "GET":
            wanted = seen.query.get("name")
            kind = seen.query.get("type")
            found = [
                r
                for r in records
                if (wanted is None or self._full(domain, r["name"]) == wanted)
                and (kind is None or r["type"] == kind)
            ]
            return 200, _json({"domain_records": found})
        if len(parts) == 3 and seen.method == "POST":
            body = seen.json()
            record = {
                "id": next(self._ids),
                "type": body["type"],
                "name": body["name"],
                "data": body["data"],
                "ttl": body["ttl"],
            }
            records.append(record)
            return 201, _json({"domain_record": record})
        found_one = next((r for r in records if str(r["id"]) == parts[3]), None)
        if found_one is None:
            return 404, _json(
                {"id": "not_found", "message": "The resource you requested could not be found."}
            )
        if seen.method == "GET":
            return 200, _json({"domain_record": found_one})
        if seen.method == "PATCH":
            found_one["data"] = seen.json()["data"]
            return 200, _json({"domain_record": found_one})
        if seen.method == "DELETE":
            records.remove(found_one)
            return 204, b""
        return 405, b""

    def _full(self, domain: str, short: str) -> str:
        return domain if short == "@" else f"{short}.{domain}"


# -- Hetzner -----------------------------------------------------------------------


class HetznerWire(Wire):
    """Hetzner Cloud DNS: zones, record sets with a comment per value, and actions."""

    def __init__(self, *zones: str, slow: int = 0, action_error: str | None = None) -> None:
        super().__init__("https://api.hetzner.cloud/v1")
        self.zones = {str(4200 + i): name for i, name in enumerate(zones)}
        self.sets: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.slow = slow
        self.action_error = action_error
        self.polls = 0
        self._remaining: dict[int, int] = {}
        self._actions = itertools.count(1)
        self.secondary: list[str] = []

    def _zone_of(self, name: str) -> str:
        return next(i for i, z in self.zones.items() if name == z or name.endswith(f".{z}"))

    def _short(self, zone_id: str, name: str) -> str:
        zone = self.zones[zone_id]
        return "@" if name == zone else name[: -len(zone) - 1]

    def seed(
        self, name: str, record_type: str, value: str, ttl: int = 300, comment: str | None = None
    ) -> None:
        zone_id = self._zone_of(name)
        key = (zone_id, self._short(zone_id, name), record_type)
        wire_value = f'"{value}"' if record_type == "TXT" else value
        entry = {"value": wire_value, **({"comment": comment} if comment else {})}
        self.sets.setdefault(key, {"ttl": ttl, "records": []})["records"].append(entry)

    def values(self, name: str, record_type: str) -> list[str]:
        zone_id = self._zone_of(name)
        found = self.sets.get((zone_id, self._short(zone_id, name), record_type))
        return [_plain(r["value"]) for r in found["records"]] if found else []

    def comments(self, name: str, record_type: str) -> list[str | None]:
        """
        Args:
            name: A full name.
            record_type: A type.

        Returns:
            The comment of each value at that name and type.
        """
        zone_id = self._zone_of(name)
        found = self.sets.get((zone_id, self._short(zone_id, name), record_type))
        return [r.get("comment") for r in found["records"]] if found else []

    def _action(self) -> dict[str, Any]:
        number = next(self._actions)
        self._remaining[number] = self.slow
        # A failing action is found out on the first look, as Hetzner's are: it never answers with
        # the failure in the same breath as the request.
        return {"id": number, "status": "running" if self.slow or self.action_error else "success"}

    def handle(self, seen: Seen) -> tuple[int, bytes]:
        assert seen.headers["authorization"] == f"Bearer {TOKEN}"
        parts = seen.path.strip("/").split("/")
        missing = (404, _json({"error": {"code": "not_found", "message": "zone not found"}}))
        if parts == ["zones"]:
            zones = [
                {"id": int(i), "name": n, "mode": "primary"} for i, n in self.zones.items()
            ] + [{"id": 9, "name": n, "mode": "secondary"} for n in self.secondary]
            return 200, _json({"zones": zones, "meta": {"pagination": {"page": 1, "last_page": 1}}})
        if len(parts) < 3 or parts[0] != "zones":
            return missing
        zone_id = parts[1]
        if zone_id not in self.zones:
            return missing
        rest = parts[2:]
        if rest[0] == "actions" and seen.method == "GET":
            self.polls += 1
            number = int(rest[1])
            if self._remaining[number] > 0:
                self._remaining[number] -= 1
                return 200, _json({"action": {"id": number, "status": "running"}})
            return 200, _json({"action": self._finished(number)})
        if rest == ["rrsets"] and seen.method == "GET":
            found = [
                self._item(key, value)
                for key, value in self.sets.items()
                if key[0] == zone_id
                and key[1] == seen.query.get("name", key[1])
                and key[2] == seen.query.get("type", key[2])
            ]
            return 200, _json({"rrsets": found})
        if rest == ["rrsets"] and seen.method == "POST":
            body = seen.json()
            key = (zone_id, body["name"], body["type"])
            if key in self.sets:
                return 409, _json(
                    {"error": {"code": "conflict", "message": "rrset already exists"}}
                )
            self.sets[key] = {"ttl": body.get("ttl", 3600), "records": body["records"]}
            return 201, _json({"rrset": {}, "action": self._action()})
        name, record_type = rest[1], rest[2]
        key = (zone_id, name, record_type)
        if key not in self.sets:
            return 404, _json({"error": {"code": "not_found", "message": "rrset not found"}})
        if len(rest) == 5 and rest[3:] == ["actions", "set_records"]:
            self.sets[key]["records"] = seen.json()["records"]
            return 201, _json({"action": self._action()})
        if seen.method == "DELETE" and len(rest) == 3:
            del self.sets[key]
            return 201, _json({"action": self._action()})
        return 404, b""

    def _finished(self, number: int) -> dict[str, Any]:
        if self.action_error:
            return {
                "id": number,
                "status": "error",
                "error": {"code": "action_failed", "message": self.action_error},
            }
        return {"id": number, "status": "success"}

    def _item(self, key: tuple[str, str, str], value: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"{key[1]}/{key[2]}",
            "name": key[1],
            "type": key[2],
            "ttl": value["ttl"],
            "records": value["records"],
        }


# -- Gandi -------------------------------------------------------------------------


class GandiWire(Wire):
    """Gandi LiveDNS: domains, and record sets read and written whole."""

    def __init__(self, *domains: str) -> None:
        super().__init__("https://api.gandi.net/v5/livedns")
        self.domains = list(domains)
        self.sets: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _split(self, name: str) -> tuple[str, str]:
        domain = next(d for d in self.domains if name == d or name.endswith(f".{d}"))
        return domain, "@" if name == domain else name[: -len(domain) - 1]

    def seed(
        self, name: str, record_type: str, value: str, ttl: int = 300, comment: str | None = None
    ) -> None:
        domain, short = self._split(name)
        wire_value = f'"{value}"' if record_type == "TXT" else value
        found = self.sets.setdefault(
            (domain, short, record_type), {"rrset_ttl": ttl, "rrset_values": []}
        )
        found["rrset_values"].append(wire_value)

    def values(self, name: str, record_type: str) -> list[str]:
        domain, short = self._split(name)
        found = self.sets.get((domain, short, record_type))
        return [_plain(v) for v in found["rrset_values"]] if found else []

    def ttl(self, name: str, record_type: str) -> int:
        """
        Args:
            name: A full name.
            record_type: A type.

        Returns:
            The TTL of that set.
        """
        domain, short = self._split(name)
        return int(self.sets[(domain, short, record_type)]["rrset_ttl"])

    def handle(self, seen: Seen) -> tuple[int, bytes]:
        assert seen.headers["authorization"] == f"Bearer {TOKEN}"
        parts = seen.path.strip("/").split("/")
        if parts == ["domains"]:
            return 200, _json([{"fqdn": d, "href": "x"} for d in self.domains])
        missing = (404, _json({"status": "error", "message": "Cannot find record"}))
        if len(parts) != 5 or parts[0] != "domains" or parts[2] != "records":
            return missing
        key = (parts[1], parts[3], parts[4])
        if parts[1] not in self.domains:
            return missing
        if seen.method == "GET":
            found = self.sets.get(key)
            if found is None:
                return missing
            return 200, _json({"rrset_name": key[1], "rrset_type": key[2], **found})
        if seen.method == "PUT":
            body = seen.json()
            self.sets[key] = {"rrset_ttl": body["rrset_ttl"], "rrset_values": body["rrset_values"]}
            return 201, _json({"message": "DNS Record Created"})
        if seen.method == "DELETE":
            if key not in self.sets:
                return missing
            del self.sets[key]
            return 204, b""
        return 405, b""


# -- Amazon Route 53 ---------------------------------------------------------------

NS = "https://route53.amazonaws.com/doc/2013-04-01/"


def _escaped(name: str) -> str:
    return name.replace("*", "\\052")


class Route53Wire(Wire):
    """Route 53: hosted zones, record sets, signed requests and XML."""

    def __init__(self, *zones: str, page_size: int = 100) -> None:
        super().__init__("https://route53.amazonaws.com/2013-04-01")
        self.zones = {f"Z{1000 + i}": name for i, name in enumerate(zones)}
        self.private = {"ZPRIVATE": "corp.internal"}
        self.sets: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.page_size = page_size
        self.changes: list[tuple[str, str, str, int, list[str]]] = []

    def _zone_of(self, name: str) -> str:
        return next(i for i, z in self.zones.items() if name == z or name.endswith(f".{z}"))

    def seed(
        self,
        name: str,
        record_type: str,
        value: str,
        ttl: int = 300,
        comment: str | None = None,
        alias: bool = False,
        set_id: str | None = None,
    ) -> None:
        wire_value = f'"{value}"' if record_type == "TXT" else value
        found = self.sets.setdefault(
            (self._zone_of(name), name, record_type),
            {"ttl": ttl, "values": [], "alias": alias, "set_id": set_id},
        )
        if not alias:
            found["values"].append(wire_value)

    def values(self, name: str, record_type: str) -> list[str]:
        found = self.sets.get((self._zone_of(name), name, record_type))
        return [_plain(v) for v in found["values"]] if found else []

    def handle(self, seen: Seen) -> tuple[int, bytes]:
        authorization = seen.headers["authorization"]
        assert authorization.startswith(f"AWS4-HMAC-SHA256 Credential={ACCESS_KEY}/"), (
            "the request is signed with SigV4 for the access key"
        )
        assert "/us-east-1/route53/aws4_request, SignedHeaders=host;x-amz-date, Signature=" in (
            authorization
        )
        assert SECRET_KEY not in authorization and SECRET_KEY not in seen.url
        parts = seen.path.strip("/").split("/")
        if parts == ["hostedzone"] and seen.method == "GET":
            return self._list_zones(seen)
        if len(parts) == 3 and parts[0] == "hostedzone" and parts[2] == "rrset":
            if parts[1] not in self.zones:
                return self._error(
                    404, "NoSuchHostedZone", f"No hosted zone found with ID: {parts[1]}"
                )
            if seen.method == "GET":
                return self._list_sets(parts[1], seen)
            if seen.method == "POST":
                return self._change(parts[1], seen)
        return self._error(404, "NoSuchHostedZone", "no such resource")

    def _error(self, status: int, code: str, message: str) -> tuple[int, bytes]:
        return status, (
            f'<?xml version="1.0"?><ErrorResponse xmlns="{NS}"><Error><Type>Sender</Type>'
            f"<Code>{code}</Code><Message>{escape(message)}</Message></Error>"
            f"<RequestId>req-1</RequestId></ErrorResponse>"
        ).encode()

    def _list_zones(self, seen: Seen) -> tuple[int, bytes]:
        every = [(i, n, False) for i, n in self.zones.items()] + [
            (i, n, True) for i, n in self.private.items()
        ]
        start = int(seen.query.get("marker", "0"))
        chunk = every[start : start + self.page_size]
        more = start + self.page_size < len(every)
        zones = "".join(
            f"<HostedZone><Id>/hostedzone/{i}</Id><Name>{n}.</Name>"
            f"<Config><PrivateZone>{str(private).lower()}</PrivateZone></Config></HostedZone>"
            for i, n, private in chunk
        )
        marker = f"<NextMarker>{start + self.page_size}</NextMarker>" if more else ""
        return 200, (
            f'<?xml version="1.0"?><ListHostedZonesResponse xmlns="{NS}"><HostedZones>{zones}'
            f"</HostedZones><IsTruncated>{str(more).lower()}</IsTruncated>{marker}"
            f"</ListHostedZonesResponse>"
        ).encode()

    def _list_sets(self, zone_id: str, seen: Seen) -> tuple[int, bytes]:
        start = (seen.query["name"].rstrip(".").lower(), seen.query["type"])
        ordered = sorted(
            ((key[1], key[2]), value) for key, value in self.sets.items() if key[0] == zone_id
        )
        # Route 53 lists from the name asked for onwards, so what follows is not the name asked for.
        chosen = [(k, v) for k, v in ordered if k >= start][
            : int(seen.query.get("maxitems", "100"))
        ]
        items = ""
        for (name, record_type), value in chosen:
            if value["alias"]:
                items += (
                    f"<ResourceRecordSet><Name>{_escaped(name)}.</Name><Type>{record_type}</Type>"
                    f"<AliasTarget><HostedZoneId>Z</HostedZoneId><DNSName>lb.example.net.</DNSName>"
                    f"</AliasTarget></ResourceRecordSet>"
                )
                continue
            records = "".join(
                f"<ResourceRecord><Value>{escape(v)}</Value></ResourceRecord>"
                for v in value["values"]
            )
            identifier = (
                f"<SetIdentifier>{value['set_id']}</SetIdentifier><Weight>10</Weight>"
                if value.get("set_id")
                else ""
            )
            items += (
                f"<ResourceRecordSet><Name>{_escaped(name)}.</Name><Type>{record_type}</Type>"
                f"{identifier}<TTL>{value['ttl']}</TTL><ResourceRecords>{records}</ResourceRecords>"
                f"</ResourceRecordSet>"
            )
        return 200, (
            f'<?xml version="1.0"?><ListResourceRecordSetsResponse xmlns="{NS}">'
            f"<ResourceRecordSets>{items}</ResourceRecordSets><IsTruncated>false</IsTruncated>"
            f"<MaxItems>100</MaxItems></ListResourceRecordSetsResponse>"
        ).encode()

    def _change(self, zone_id: str, seen: Seen) -> tuple[int, bytes]:
        root = ET.fromstring(seen.body)  # noqa: S314 - the test's own wire
        for change in root.iter(f"{{{NS}}}Change"):
            action = change.findtext(f"{{{NS}}}Action") or ""
            record_set = change.find(f"{{{NS}}}ResourceRecordSet")
            assert record_set is not None
            name = (record_set.findtext(f"{{{NS}}}Name") or "").rstrip(".").replace("\\052", "*")
            record_type = record_set.findtext(f"{{{NS}}}Type") or ""
            ttl = int(record_set.findtext(f"{{{NS}}}TTL") or "0")
            values = [
                v.findtext(f"{{{NS}}}Value") or ""
                for v in record_set.iter(f"{{{NS}}}ResourceRecord")
            ]
            key = (zone_id, name, record_type)
            self.changes.append((action, name, record_type, ttl, values))
            if action == "UPSERT":
                self.sets[key] = {"ttl": ttl, "values": values, "alias": False}
            elif action == "DELETE":
                current = self.sets.get(key)
                if current is None or current["values"] != values or current["ttl"] != ttl:
                    return self._error(
                        400,
                        "InvalidChangeBatch",
                        f"Tried to delete resource record set [name='{name}.', type='{record_type}'] "
                        f"but the values provided do not match the current values",
                    )
                del self.sets[key]
            else:
                return self._error(400, "InvalidInput", f"unknown action {action}")
        return 200, (
            f'<?xml version="1.0"?><ChangeResourceRecordSetsResponse xmlns="{NS}"><ChangeInfo>'
            f"<Id>/change/C1</Id><Status>PENDING</Status></ChangeInfo>"
            f"</ChangeResourceRecordSetsResponse>"
        ).encode()
