# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What every sign-in that leaves for another site and comes back shares.

OpenID Connect and SAML are two protocols over one browser dance: the console remembers the
sign-in, sends the browser away with a cookie that ties the answer to it, and receives the answer
on a route that redirects to the sign-in page carrying a single-use ticket or one word saying why
not. This module is that dance's common part, so that a change to the cookie, the redirect or the
address a provider calls back reaches both (rule 3).
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

from noust.core.accounts.oidc_store import FLOW_SECONDS
from noust.core.config import Config
from noust.web.auth import is_secure_request

#: The browser's tie to a sign-in: where it is sent, and what it is called.
BINDING_COOKIE = "noust_oidc"

#: What the sign-in page is told when a sign-in does not finish: one word each,
#: never the reason, which is for the audit log.
SSO_ERRORS = ("unavailable", "expired", "denied", "failed", "refused", "locked")

#: A provider's own failures are not guesses at a credential and are not counted
#: against the address that happened to be signing in.
NOT_COUNTED = ("unavailable", "refused", "denied")


def origin_of(request: Request) -> str:
    """
    The address people reach this console at, as the provider is to call it back.

    ``web.public_url`` when the operator set it (behind a proxy whose name
    differs, or to be exact about it); else the host the request arrived on,
    which the console's allowed-hosts check already vetted, over the scheme the
    request really used (a trusted proxy's word counts).

    Args:
        request: The request.

    Returns:
        ``scheme://host[:port]``, without a trailing slash.
    """
    configured = str(Config().get("web.public_url") or "").strip().rstrip("/")
    if configured:
        return configured
    host = request.headers.get("host") or request.url.netloc
    return f"{'https' if is_secure_request(request) else 'http'}://{host}"


def to_login(error: str | None = None, ticket: str | None = None) -> RedirectResponse:
    """
    Send the browser to the sign-in page, with what to do next.

    Args:
        error: One of :data:`SSO_ERRORS`, when the sign-in did not finish.
        ticket: The single-use ticket of one that did.

    Returns:
        A ``303`` redirect, never cached.
    """
    query = f"sso={quote(ticket)}" if ticket else f"sso_error={quote(error or 'failed')}"
    response = RedirectResponse(f"/login?{query}", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def set_binding_cookie(
    response: RedirectResponse, request: Request, binding: str, *, answered_by_post: bool = False
) -> None:
    """
    Tie the answer of a sign-in to this browser.

    ``SameSite=Lax``, not ``Strict``: the answer arrives as a navigation from another site, and a
    ``Strict`` cookie is not sent with one. ``Lax`` still withholds it from a cross-site
    subresource, and the path keeps it off everything but the API. A provider that answers with a
    form POST (SAML's HTTP-POST binding) is a cross-site POST, which ``Lax`` also withholds: for
    that one the cookie is ``SameSite=None``, which a browser only keeps when it is ``Secure``,
    so a console reached over plain HTTP cannot take a POST answer.

    Args:
        response: The redirect to the provider.
        request: The request, to know whether it came over TLS.
        binding: The secret the answer must come back with.
        answered_by_post: Whether the provider's answer is a cross-site POST.
    """
    secure = is_secure_request(request)
    cross_site = answered_by_post and secure
    response.set_cookie(
        BINDING_COOKIE,
        binding,
        max_age=FLOW_SECONDS,
        httponly=True,
        samesite="none" if cross_site else "lax",
        secure=secure,
        path="/api",
    )


__all__ = [
    "BINDING_COOKIE",
    "NOT_COUNTED",
    "SSO_ERRORS",
    "origin_of",
    "set_binding_cookie",
    "to_login",
]
