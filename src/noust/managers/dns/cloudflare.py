# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one way Noust talks to Cloudflare's API.

A small JSON client over :mod:`urllib`, on purpose: no new dependency, one fixed
host, a timeout on every request, a bound on what is read, and one error for every
way a request can fail. Cloudflare's own words are kept verbatim in the error's
``output``: "Authentication error" and "Invalid zone" tell the operator which
permission the token lacks, and a paraphrase would hide exactly that.

What it serves, all through the same token:

- **zones and records** (:class:`CloudflareProvider`), for the managed records of
  :mod:`noust.managers.dns.records` and the DNS-01 challenge of
  :mod:`noust.managers.dns.challenge`;
- **tunnels** (:meth:`CloudflareProvider.create_tunnel` and its siblings), for
  :mod:`noust.managers.tunnels`: an HTTP ingress with no public address and no open
  port.

The token needs Zone:Read and Zone > DNS:Edit for the first, and Account >
Cloudflare Tunnel:Edit for the second. It is read from the secret store by the
caller and held here only for the life of the client.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from noust import __version__
from noust.managers.dns.provider import (
    AUTOMATIC_TTL,
    DnsProvider,
    DnsProviderError,
    RemoteRecord,
    Zone,
    unquote,
)

#: Cloudflare's API.
API_URL = "https://api.cloudflare.com/client/v4"

#: Seconds before a request is abandoned.
REQUEST_TIMEOUT = 15

#: Bytes of a response read at most.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

#: Zones per page, and pages read at most (Cloudflare's own limit is 50 per page).
PAGE_SIZE = 50
MAX_PAGES = 100

#: Anything with urlopen's calling convention.
Opener = Callable[..., IO[bytes]]


def _hint(status: int) -> str:
    """
    Say what an error status from Cloudflare usually means here.

    Args:
        status: The HTTP status.

    Returns:
        The hint shown above Cloudflare's own message.
    """
    if status == 400:
        return "Cloudflare refused the request as invalid."
    if status in (401, 403):
        return (
            "Cloudflare did not accept this token for that. It needs Zone:Read and DNS:Edit on "
            "the zones (and Account > Cloudflare Tunnel:Edit for tunnels). Create it at "
            "dash.cloudflare.com/profile/api-tokens and add the account again."
        )
    if status == 404:
        return "Cloudflare does not show this to the token: the zone or record may not exist."
    if status == 429:
        return "Cloudflare is rate limiting this token; try again in a minute."
    return "Retry in a moment; if it persists, check https://www.cloudflarestatus.com."


class CloudflareClient:
    """
    JSON over HTTPS to Cloudflare, with one token.

    Args:
        token: The API token. Sent as a bearer credential, never logged.
        base_url: The API root. Always :data:`API_URL` in Noust; tests pass a fake.
        timeout: Seconds per request.
        opener: Replacement for :func:`urllib.request.urlopen`.
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = API_URL,
        timeout: float = REQUEST_TIMEOUT,
        opener: Opener | None = None,
    ) -> None:
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener: Opener = opener or urllib.request.urlopen

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send one request and decode its answer.

        Args:
            method: HTTP method.
            path: Path on the API, starting with ``/``.
            body: JSON body, or None.
            query: Query string parameters.

        Returns:
            Cloudflare's decoded answer (``success``, ``result``, ``result_info``).

        Raises:
            DnsProviderError: The path is not a path, Cloudflare could not be
                reached, answered something that is not JSON, or said it failed.
        """
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise DnsProviderError(f"Refusing to request {path!r} from Cloudflare")
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": f"noust/{__version__}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise self._error(method, path, exc) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise DnsProviderError(
                "Cloudflare could not be reached",
                details="Check this server's outbound HTTPS access to api.cloudflare.com.",
                output=str(reason),
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DnsProviderError(f"Cloudflare's answer to {method.upper()} {path} is too large")
        try:
            answer = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DnsProviderError(
                f"Cloudflare's answer to {method.upper()} {path} is not JSON", details=str(exc)
            ) from None
        if not isinstance(answer, dict):
            raise DnsProviderError(f"Cloudflare's answer to {method.upper()} {path} is unexpected")
        if answer.get("success") is False:
            raise DnsProviderError(
                f"Cloudflare refused {method.upper()} {path}",
                details=_hint(400),
                output=_messages(answer),
            )
        return answer

    def _error(self, method: str, path: str, exc: HTTPError) -> DnsProviderError:
        """
        Turn an error status into the error Noust raises, Cloudflare's words kept.

        Args:
            method: The request's method.
            path: The request's path.
            exc: urllib's error, carrying the response.

        Returns:
            The error to raise.
        """
        message = ""
        try:
            payload = json.loads(exc.read(64 * 1024) or b"{}")
            if isinstance(payload, dict):
                message = _messages(payload)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            message = ""
        return DnsProviderError(
            f"Cloudflare refused {method.upper()} {path} ({exc.code})",
            status=exc.code,
            details=_hint(exc.code),
            output=message or str(exc.reason),
        )

    def paginate(self, path: str, *, query: Mapping[str, Any] | None = None) -> list[Any]:
        """
        Read every page of a listing.

        Args:
            path: The listing's path.
            query: Extra query parameters.

        Returns:
            Every item of ``result``, in Cloudflare's order.
        """
        items: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            answer = self.request(
                "GET", path, query={**(query or {}), "page": page, "per_page": PAGE_SIZE}
            )
            batch = answer.get("result")
            if not isinstance(batch, list):
                raise DnsProviderError(f"Cloudflare's answer to GET {path} is not a listing")
            items.extend(batch)
            info = answer.get("result_info") or {}
            if page >= int(info.get("total_pages") or 1) or not batch:
                break
        return items


def _messages(payload: Mapping[str, Any]) -> str:
    """
    Join the errors of a Cloudflare answer into one verbatim line.

    Args:
        payload: The decoded answer.

    Returns:
        ``code: message`` for each error, separated by semicolons.
    """
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return ""
    parts = []
    for error in errors:
        if isinstance(error, dict):
            parts.append(f"{error.get('code', '')}: {error.get('message', '')}".strip(": "))
        else:
            parts.append(str(error))
    return "; ".join(part for part in parts if part)


def _remote(item: Mapping[str, Any]) -> RemoteRecord:
    """
    Read a record out of Cloudflare's JSON.

    Args:
        item: One DNS record of an answer.

    Returns:
        The record.
    """
    comment = item.get("comment")
    return RemoteRecord(
        id=str(item.get("id") or ""),
        type=str(item.get("type") or ""),
        name=str(item.get("name") or "").lower(),
        content=unquote(str(item.get("content") or "")),
        ttl=int(item["ttl"]) if isinstance(item.get("ttl"), int) else None,
        proxied=bool(item["proxied"]) if "proxied" in item else None,
        comment=comment if isinstance(comment, str) else None,
    )


class CloudflareProvider(DnsProvider):
    """
    A Cloudflare account: its zones, their records, and its tunnels.

    Args:
        client: The API client, holding the token.
    """

    kind = "cloudflare"

    def __init__(self, client: CloudflareClient) -> None:
        self.client = client

    # -- zones and records ---------------------------------------------------

    def zones(self) -> list[Zone]:
        """
        Returns:
            Every zone the token can manage.

        Raises:
            DnsProviderError: The token was refused or Cloudflare failed.
        """
        found = []
        for item in self.client.paginate("/zones"):
            if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                continue
            account = item.get("account")
            found.append(
                Zone(
                    id=str(item["id"]),
                    name=str(item["name"]).lower(),
                    account_id=str(account["id"])
                    if isinstance(account, dict) and account.get("id")
                    else None,
                )
            )
        return found

    def find_records(self, zone: Zone, name: str, record_type: str) -> list[RemoteRecord]:
        """
        Args:
            zone: The zone.
            name: The record's full name.
            record_type: Its type.

        Returns:
            Every record of that name and type.

        Raises:
            DnsProviderError: Cloudflare failed.
        """
        answer = self.client.request(
            "GET",
            f"/zones/{zone.id}/dns_records",
            query={"type": record_type, "name": name.lower(), "per_page": 100},
        )
        return [_remote(item) for item in answer.get("result") or [] if isinstance(item, dict)]

    def get_record(self, zone: Zone, record_id: str) -> RemoteRecord | None:
        """
        Args:
            zone: The zone.
            record_id: Cloudflare's id.

        Returns:
            The record, or None when it no longer exists.

        Raises:
            DnsProviderError: Cloudflare failed.
        """
        try:
            answer = self.client.request("GET", f"/zones/{zone.id}/dns_records/{record_id}")
        except DnsProviderError as exc:
            if exc.status == 404:
                return None
            raise
        result = answer.get("result")
        return _remote(result) if isinstance(result, dict) else None

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
            ttl: Seconds to live; 1 is Cloudflare's "automatic".
            proxied: Let Cloudflare proxy it (A, AAAA and CNAME only).
            comment: A note to keep with it.

        Returns:
            The record as created.

        Raises:
            DnsProviderError: Cloudflare refused it.
        """
        body: dict[str, Any] = {
            "type": record_type,
            "name": name.lower(),
            "content": content,
            "ttl": ttl,
        }
        if record_type in ("A", "AAAA", "CNAME"):
            body["proxied"] = proxied
        if comment:
            body["comment"] = comment
        answer = self.client.request("POST", f"/zones/{zone.id}/dns_records", body=body)
        result = answer.get("result")
        if not isinstance(result, dict) or not result.get("id"):
            raise DnsProviderError("Cloudflare did not say which record it created")
        return _remote(result)

    def update_record(self, zone: Zone, record_id: str, content: str) -> RemoteRecord:
        """
        Change only what a record holds; its proxying, TTL and comment stay.

        Args:
            zone: The zone.
            record_id: Cloudflare's id.
            content: The new content.

        Returns:
            The record as it is now.

        Raises:
            DnsProviderError: Cloudflare refused it.
        """
        answer = self.client.request(
            "PATCH", f"/zones/{zone.id}/dns_records/{record_id}", body={"content": content}
        )
        result = answer.get("result")
        if not isinstance(result, dict):
            raise DnsProviderError("Cloudflare did not return the record it changed")
        return _remote(result)

    def delete_record(self, zone: Zone, record_id: str) -> bool:
        """
        Args:
            zone: The zone.
            record_id: Cloudflare's id.

        Returns:
            True when it was deleted, False when it was already gone.

        Raises:
            DnsProviderError: Cloudflare refused it.
        """
        try:
            self.client.request("DELETE", f"/zones/{zone.id}/dns_records/{record_id}")
        except DnsProviderError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    # -- tunnels -------------------------------------------------------------

    def accounts(self) -> list[tuple[str, str]]:
        """
        Returns:
            ``(id, name)`` of each account the token reaches.

        Raises:
            DnsProviderError: Cloudflare refused the token.
        """
        return [
            (str(item["id"]), str(item.get("name") or ""))
            for item in self.client.paginate("/accounts")
            if isinstance(item, dict) and item.get("id")
        ]

    def create_tunnel(self, account_id: str, name: str) -> str:
        """
        Create a tunnel whose routes are kept at Cloudflare.

        Args:
            account_id: The Cloudflare account.
            name: What the tunnel is called there.

        Returns:
            The tunnel's id.

        Raises:
            DnsProviderError: Cloudflare refused it.
        """
        answer = self.client.request(
            "POST",
            f"/accounts/{account_id}/cfd_tunnel",
            body={"name": name, "config_src": "cloudflare"},
        )
        result = answer.get("result")
        if not isinstance(result, dict) or not result.get("id"):
            raise DnsProviderError("Cloudflare did not say which tunnel it created")
        return str(result["id"])

    def tunnel_token(self, account_id: str, tunnel_id: str) -> str:
        """
        Fetch the token the tunnel's connector runs with.

        Args:
            account_id: The Cloudflare account.
            tunnel_id: The tunnel.

        Returns:
            The token. A secret: the caller keeps it in a 0600 file, never in an argument.

        Raises:
            DnsProviderError: Cloudflare refused it.
        """
        answer = self.client.request("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token")
        result = answer.get("result")
        if not isinstance(result, str) or not result:
            raise DnsProviderError("Cloudflare did not return the tunnel's token")
        return result

    def configure_tunnel(
        self, account_id: str, tunnel_id: str, ingress: list[dict[str, Any]]
    ) -> None:
        """
        Set the routes of a tunnel.

        Args:
            account_id: The Cloudflare account.
            tunnel_id: The tunnel.
            ingress: One rule per hostname, then the catch-all (``http_status:404``).

        Raises:
            DnsProviderError: Cloudflare refused it.
        """
        self.client.request(
            "PUT",
            f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
            body={"config": {"ingress": ingress}},
        )

    def delete_tunnel(self, account_id: str, tunnel_id: str) -> bool:
        """
        Delete a tunnel.

        Args:
            account_id: The Cloudflare account.
            tunnel_id: The tunnel.

        Returns:
            True when it was deleted, False when it was already gone.

        Raises:
            DnsProviderError: Cloudflare refused it (a tunnel with a connected
                connector cannot be deleted).
        """
        try:
            self.client.request("DELETE", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}")
        except DnsProviderError as exc:
            if exc.status == 404:
                return False
            raise
        return True
