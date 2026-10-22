# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Amazon Route 53.

Route 53 is signed, not bearer-authenticated: every request carries a SigV4 signature made from
the access key's secret (:func:`sign`), and speaks XML. Its zones are hosted zones with ids of
their own, and its records live in sets of one name and type read and written whole. Route 53
keeps no note with a record, so the ownership marker rests on what Noust remembers it created
(see :mod:`noust.managers.dns.rrset`).

The signing is the published algorithm and nothing else, in the standard library; a test pins it
against the signature AWS publishes for its own test suite. The credential is an access key of an
IAM user allowed ``route53:ListHostedZones``, ``route53:ListResourceRecordSets`` and
``route53:ChangeResourceRecordSets`` on the zones. A change is not waited for with ``GetChange``:
the challenge waits for propagation anyway (60 seconds by default here), and a record that is
visible to Noust's own reads is not a promise about the authority's resolvers.
Route 53 is global; its requests are signed for ``us-east-1``.
"""

from __future__ import annotations

import hashlib
import hmac
import re

# The only XML read is Route 53's own answer, over https from a fixed host and bounded to
# MAX_RESPONSE_BYTES; the standard parser does not fetch external entities, and no document of
# anyone else's goes through it.
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from urllib.parse import quote
from xml.sax.saxutils import escape

from noust.managers.dns.provider import DnsProviderError, Zone, unquote
from noust.managers.dns.rest import Opener, RestClient
from noust.managers.dns.rrset import Rrset, RrsetProvider

#: Route 53's global endpoint, and the region requests to it are signed for.
HOST = "route53.amazonaws.com"
API_URL = f"https://{HOST}/2013-04-01"
REGION = "us-east-1"
SERVICE = "route53"

#: Seconds to live of a set made without one.
DEFAULT_TTL = 300

_NS = "{https://route53.amazonaws.com/doc/2013-04-01/}"
_XMLNS = "https://route53.amazonaws.com/doc/2013-04-01/"
_ACCESS_KEY = re.compile(r"[A-Z0-9]{16,128}")
_ESCAPED = re.compile(r"\\(\d{3})")


def sign(
    method: str,
    host: str,
    path: str,
    query: Mapping[str, str],
    payload: bytes,
    *,
    access_key: str,
    secret_key: str,
    region: str = REGION,
    service: str = SERVICE,
    now: datetime | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """
    Sign a request with AWS Signature Version 4.

    Args:
        method: The HTTP method.
        host: The host the request goes to.
        path: The path, already percent-encoded.
        query: The query parameters, not encoded.
        payload: The request's body.
        access_key: The access key's id.
        secret_key: Its secret. Never leaves this function but as a signature.
        region: The region the request is signed for.
        service: The service's signing name.
        now: The moment to sign for; the present by default.
        extra_headers: Headers to sign besides ``host`` and ``x-amz-date``.

    Returns:
        The headers to send: ``x-amz-date``, ``Authorization`` and the extra ones.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    day = moment.strftime("%Y%m%d")
    headers = {
        "host": host,
        "x-amz-date": stamp,
        **{k.lower(): v for k, v in (extra_headers or {}).items()},
    }
    names = sorted(headers)
    canonical_query = "&".join(
        f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}"
        for key, value in sorted(query.items())
    )
    canonical = "\n".join(
        [
            method.upper(),
            path or "/",
            canonical_query,
            "".join(f"{name}:{headers[name].strip()}\n" for name in names),
            ";".join(names),
            hashlib.sha256(payload).hexdigest(),
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    key = f"AWS4{secret_key}".encode()
    for part in (day, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    sent = {k: v for k, v in headers.items() if k != "host"}
    sent["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return sent


def _hint(status: int) -> str:
    if status in (401, 403):
        return (
            "Amazon did not accept this access key for that. The IAM user needs "
            "route53:ListHostedZones, route53:ListResourceRecordSets and "
            "route53:ChangeResourceRecordSets on the zones."
        )
    if status == 400:
        return "Route 53 refused the request as invalid."
    if status == 404:
        return "Route 53 does not show this to the key: the hosted zone may not exist."
    if status in (429, 503):
        return "Route 53 is throttling this key; try again in a minute."
    return "Retry in a moment; if it persists, check https://health.aws.amazon.com."


def _words(raw: bytes) -> str:
    """
    Args:
        raw: The body of an error answer.

    Returns:
        Route 53's own ``Code: Message``, verbatim.
    """
    try:
        root = ET.fromstring(raw)  # noqa: S314 - see the comment at the import
    except ET.ParseError:
        return raw.decode("utf-8", "replace").strip()[:300]
    code = root.findtext(".//Code") or root.findtext(f".//{_NS}Code") or ""
    message = root.findtext(".//Message") or root.findtext(f".//{_NS}Message") or ""
    return f"{code}: {message}".strip(": ")


class Route53Client:
    """
    Signed XML over HTTPS to Route 53, with one access key.

    Args:
        access_key: The access key's id.
        secret_key: Its secret.
        opener: Replacement for :func:`urllib.request.urlopen` (tests).
        clock: Gives the moment to sign for (tests); the present by default.
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        *,
        opener: Opener | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._clock = clock
        self._rest = RestClient("Route 53", API_URL, {}, opener=opener, explain=_words, hint=_hint)

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: str | None = None,
    ) -> ET.Element:
        """
        Send one signed request and parse its XML answer.

        Args:
            method: The HTTP method.
            path: A path under the API root, starting with ``/``.
            query: Query parameters.
            body: An XML document, or None.

        Returns:
            The root of the answer.

        Raises:
            DnsProviderError: Route 53 could not be reached, refused it or answered nothing
                that is XML.
        """
        payload = body.encode() if body is not None else b""
        full_path = f"/2013-04-01{path}"
        headers = sign(
            method,
            HOST,
            quote(full_path, safe="/-_.~"),
            query or {},
            payload,
            access_key=self._access_key,
            secret_key=self._secret_key,
            now=self._clock() if self._clock is not None else None,
        )
        headers["Accept"] = "application/xml"
        if body is not None:
            headers["Content-Type"] = "application/xml"
        raw = self._rest.request(
            method,
            path,
            query=query,
            headers=headers,
            raw_body=payload if body is not None else None,
        )
        try:
            return ET.fromstring(raw)  # noqa: S314 - see the comment at the import
        except ET.ParseError as exc:
            raise DnsProviderError(
                f"Route 53's answer to {method} {path} is not XML", details=str(exc)
            ) from None


def client(access_key: str, secret_key: str, opener: Opener | None = None) -> Route53Client:
    """
    Args:
        access_key: The access key's id.
        secret_key: Its secret.
        opener: Replacement for :func:`urllib.request.urlopen` (tests).

    Returns:
        A client holding them.

    Raises:
        DnsProviderError: The access key's id is not the shape of one.
    """
    if not _ACCESS_KEY.fullmatch(access_key):
        raise DnsProviderError(
            "That is not an AWS access key id",
            details="It is 16 to 128 capital letters and digits, such as AKIAIOSFODNN7EXAMPLE.",
        )
    return Route53Client(access_key, secret_key, opener=opener)


def _unescape(name: str) -> str:
    """
    Args:
        name: A name as Route 53 writes it (``\\052`` for a ``*``, a final dot).

    Returns:
        The name in plain form, lower case, without the final dot.
    """
    plain = _ESCAPED.sub(lambda match: chr(int(match.group(1), 8)), name)
    return plain.lower().rstrip(".")


#: The elements that make a set something other than a plain list of values.
_ROUTING = ("SetIdentifier", "Weight", "Region", "Failover", "GeoLocation", "MultiValueAnswer")


def _foreign(item: ET.Element) -> str:
    """
    Args:
        item: One ``ResourceRecordSet`` of an answer.

    Returns:
        Why Noust must not touch it, or an empty string for a plain set. An alias has no values
        of its own and a routing policy needs its identifier in every change, so writing either
        back as a plain set would replace what the operator made.
    """
    if item.find(f"{_NS}AliasTarget") is not None:
        return "an alias"
    if any(item.find(f"{_NS}{tag}") is not None for tag in _ROUTING):
        return "part of a routing policy"
    return ""


def _quote_txt(value: str) -> str:
    return (
        value
        if value.startswith('"')
        else '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    )


class Route53Provider(RrsetProvider):
    """
    The hosted zones of an AWS account and their records.

    Args:
        api: The signed client.
    """

    kind = "route53"
    comments = False

    def __init__(self, api: Route53Client) -> None:
        self.api = api

    def zones(self) -> list[Zone]:
        """
        Returns:
            The public hosted zones the key can read.

        Raises:
            DnsProviderError: The key was refused or Route 53 failed.
        """
        found: list[Zone] = []
        marker = ""
        for _ in range(100):
            query = {"maxitems": "100", **({"marker": marker} if marker else {})}
            root = self.api.request("GET", "/hostedzone", query=query)
            for zone in root.iter(f"{_NS}HostedZone"):
                if (zone.findtext(f"{_NS}Config/{_NS}PrivateZone") or "false").lower() == "true":
                    continue
                identifier = (zone.findtext(f"{_NS}Id") or "").rsplit("/", 1)[-1]
                name = _unescape(zone.findtext(f"{_NS}Name") or "")
                if identifier and name:
                    found.append(Zone(id=identifier, name=name))
            marker = root.findtext(f"{_NS}NextMarker") or ""
            if (root.findtext(f"{_NS}IsTruncated") or "false").lower() != "true" or not marker:
                break
        return found

    def read_rrset(self, zone: Zone, name: str, record_type: str) -> Rrset | None:
        """
        Args:
            zone: The hosted zone.
            name: The set's full name.
            record_type: Its type.

        Returns:
            The set, or None.

        Raises:
            DnsProviderError: Route 53 failed.
        """
        root = self.api.request(
            "GET",
            f"/hostedzone/{zone.id}/rrset",
            query={"name": f"{name.lower()}.", "type": record_type, "maxitems": "5"},
        )
        for item in root.iter(f"{_NS}ResourceRecordSet"):
            if (
                _unescape(item.findtext(f"{_NS}Name") or "") != name.lower()
                or item.findtext(f"{_NS}Type") != record_type
            ):
                continue
            values = tuple(
                (
                    unquote(value) if record_type == "TXT" else value,
                    None,
                )
                for value in (
                    record.findtext(f"{_NS}Value") or ""
                    for record in item.iter(f"{_NS}ResourceRecord")
                )
                if value
            )
            ttl = item.findtext(f"{_NS}TTL")
            return Rrset(
                name.lower(),
                record_type,
                int(ttl) if ttl else None,
                values,
                foreign=_foreign(item),
            )
        return None

    def write_rrset(self, zone: Zone, rrset: Rrset, *, existed: bool) -> None:
        """
        Args:
            zone: The hosted zone.
            rrset: What the set must hold.
            existed: Whether it is already at Route 53 (an UPSERT does both).

        Raises:
            DnsProviderError: Route 53 refused it.
        """
        self._change(zone, "UPSERT", rrset)

    def remove_rrset(self, zone: Zone, name: str, record_type: str) -> bool:
        """
        Args:
            zone: The hosted zone.
            name: The set's full name.
            record_type: Its type.

        Returns:
            True when it was removed, False when it was not there.

        Raises:
            DnsProviderError: Route 53 refused it.
        """
        current = self.read_rrset(zone, name, record_type)
        if current is None or current.foreign:
            return False
        self._change(zone, "DELETE", current)
        return True

    # -- internals ---------------------------------------------------------

    def _change(self, zone: Zone, action: str, rrset: Rrset) -> None:
        """
        Send one change.

        Args:
            zone: The hosted zone.
            action: ``UPSERT`` or ``DELETE``.
            rrset: The set the change is about; for a delete, exactly what is there.

        Raises:
            DnsProviderError: Route 53 refused it.
        """
        records = "".join(
            f"<ResourceRecord><Value>{escape(_quote_txt(value) if rrset.type == 'TXT' else value)}</Value></ResourceRecord>"
            for value, _note in rrset.values
        )
        body = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<ChangeResourceRecordSetsRequest xmlns="{_XMLNS}"><ChangeBatch>'
            f"<Comment>managed by Noust</Comment><Changes><Change><Action>{action}</Action>"
            f"<ResourceRecordSet><Name>{escape(rrset.name)}.</Name><Type>{rrset.type}</Type>"
            f"<TTL>{rrset.ttl if rrset.ttl and rrset.ttl > 1 else DEFAULT_TTL}</TTL>"
            f"<ResourceRecords>{records}</ResourceRecords></ResourceRecordSet></Change></Changes>"
            f"</ChangeBatch></ChangeResourceRecordSetsRequest>"
        )
        self.api.request("POST", f"/hostedzone/{zone.id}/rrset", body=body)
