# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
AWS Signature Version 4, for the one request Noust sends to an AWS API.

Amazon SES takes a message only from a request signed with an access key. The
signing is a fixed recipe of HMAC-SHA256 over a canonical form of the request
(``AWS4-HMAC-SHA256``), small enough to write with the standard library and
not worth a dependency of its own: a package such as ``boto3`` is hundreds of
modules for one POST.

Pure: it reads no clock and no network. The caller passes the moment, so the
signature of a request is reproducible and the module is tested against the
worked example Amazon publishes for the algorithm.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, unquote, urlsplit

ALGORITHM = "AWS4-HMAC-SHA256"

#: The characters a URI component keeps as they are (RFC 3986 unreserved).
_UNRESERVED = "-_.~"


def _hmac(key: bytes, text: str) -> bytes:
    """
    Args:
        key: The HMAC key.
        text: The message.

    Returns:
        HMAC-SHA256 of the message.
    """
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key: str, date: str, region: str, service: str) -> bytes:
    """
    Derive the key a request of one day, region and service is signed with.

    Args:
        secret_key: The secret access key.
        date: The day, ``YYYYMMDD``.
        region: The AWS region.
        service: The service's signing name (``ses``).

    Returns:
        The derived key.
    """
    key = _hmac(("AWS4" + secret_key).encode("utf-8"), date)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    return key


def _canonical_path(path: str) -> str:
    """
    Args:
        path: The URL's path, as sent.

    Returns:
        It as the canonical request has it: every segment encoded twice, the
        rule for every service but S3.
    """
    if not path:
        return "/"
    segments = [
        quote(quote(unquote(part), safe=_UNRESERVED), safe=_UNRESERVED) for part in path.split("/")
    ]
    return "/".join(segments) or "/"


def _canonical_query(query: str) -> str:
    """
    Args:
        query: The URL's query string.

    Returns:
        The pairs, each side encoded, sorted by name and then value.
    """
    pairs = sorted(
        (quote(name, safe=_UNRESERVED), quote(value, safe=_UNRESERVED))
        for name, value in parse_qsl(query, keep_blank_values=True)
    )
    return "&".join(f"{name}={value}" for name, value in pairs)


def sign(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    moment: datetime,
    session_token: str = "",
) -> dict[str, str]:
    """
    Sign a request.

    Args:
        method: The HTTP method.
        url: The full URL; its host is signed.
        headers: The headers the request carries and that must be signed
            (``Content-Type`` at least); ``Host`` and ``X-Amz-Date`` are added
            here and need not be given.
        body: The exact bytes sent.
        access_key: The access key id.
        secret_key: The secret access key.
        region: The AWS region the endpoint is in.
        service: The service's signing name.
        moment: The time of the request (timezone-aware).
        session_token: A temporary credential's session token, when there is
            one; it is signed as ``X-Amz-Security-Token``.

    Returns:
        The headers to add to the request: ``X-Amz-Date``, ``Authorization``
        and, with a session token, ``X-Amz-Security-Token``.
    """
    moment = moment.astimezone(timezone.utc)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    parts = urlsplit(url)
    signed: dict[str, str] = {
        name.lower(): " ".join(str(value).split()) for name, value in headers.items()
    }
    signed["host"] = parts.netloc
    signed["x-amz-date"] = amz_date
    if session_token:
        signed["x-amz-security-token"] = session_token
    names = sorted(signed)
    canonical_headers = "".join(f"{name}:{signed[name]}\n" for name in names)
    signed_headers = ";".join(names)
    canonical = "\n".join(
        (
            method.upper(),
            _canonical_path(parts.path),
            _canonical_query(parts.query),
            canonical_headers,
            signed_headers,
            hashlib.sha256(body).hexdigest(),
        )
    )
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        (ALGORITHM, amz_date, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest())
    )
    signature = hmac.new(
        signing_key(secret_key, date, region, service), to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    added = {
        "X-Amz-Date": amz_date,
        "Authorization": (
            f"{ALGORITHM} Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }
    if session_token:
        added["X-Amz-Security-Token"] = session_token
    return added
