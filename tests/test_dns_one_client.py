# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
There is one HTTP client for the DNS providers (rule 3, checklist E-39).

``dns/cloudflare.py`` and ``dns/rest.py`` were two of them, each with its own timeout, its own bound
on what is read and its own way of turning a refusal into an error. The transport is
:class:`~noust.managers.dns.rest.RestClient`'s, and no provider opens a connection of its own.
"""

from __future__ import annotations

import ast
import io
import urllib.error
from pathlib import Path

import pytest

from noust.managers import dns
from noust.managers.dns.cloudflare import CloudflareClient
from noust.managers.dns.provider import DnsProviderError
from noust.managers.dns.rest import MAX_RESPONSE_BYTES, RestClient

DNS_DIR = Path(dns.__file__).parent


def modules_that_open_connections() -> list[str]:
    found = []
    for path in sorted(DNS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(
                name in ("urllib.request", "http.client", "requests", "httpx") for name in names
            ):
                found.append(path.name)
    return found


def test_only_the_rest_client_opens_connections() -> None:
    assert modules_that_open_connections() == ["rest.py"]


def test_cloudflare_is_a_user_of_it() -> None:
    client = CloudflareClient("token", opener=lambda *a, **k: None)  # type: ignore[arg-type,return-value]

    assert isinstance(client._rest, RestClient)
    assert client.base_url == "https://api.cloudflare.com/client/v4"


class Answer(io.BytesIO):
    def __enter__(self) -> Answer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_the_bound_on_what_is_read_is_the_shared_one() -> None:
    def huge(request: object, timeout: float = 0) -> Answer:
        return Answer(b" " * (MAX_RESPONSE_BYTES + 10))

    with pytest.raises(DnsProviderError, match="too large"):
        CloudflareClient("token", opener=huge).request("GET", "/zones")


def test_a_refusal_keeps_cloudflares_own_words_under_the_shared_error() -> None:
    def refuse(request: object, timeout: float = 0) -> Answer:
        raise urllib.error.HTTPError(
            "https://api.cloudflare.com/client/v4/zones",
            403,
            "Forbidden",
            {},  # type: ignore[arg-type]
            io.BytesIO(b'{"errors":[{"code":9109,"message":"Invalid access token"}]}'),
        )

    with pytest.raises(DnsProviderError) as caught:
        CloudflareClient("token", opener=refuse).request("GET", "/zones")

    assert caught.value.status == 403
    assert "9109: Invalid access token" in str(caught.value.output)
    assert "Zone:Read" in caught.value.details


def test_a_path_that_is_not_a_path_is_refused_by_the_shared_check() -> None:
    with pytest.raises(DnsProviderError, match="Refusing to request"):
        CloudflareClient("token", opener=lambda *a, **k: None).request(  # type: ignore[arg-type,return-value]
            "GET", "https://evil.example/x"
        )
