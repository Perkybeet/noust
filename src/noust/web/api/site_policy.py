# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's site does besides serving it, over the API.

A thin translation of :mod:`noust.deployers.access`, which ``noust site policy``
reads too: a password, address lists, the real client address behind a CDN, rate
limits, response headers and which of www or the bare name is canonical. The
rules, the lockout check and the rewrite of the site (tested by the web server
before it is reloaded, put back if refused) live there.

The address the request comes from is handed to the operation as the
operator's own, so a list that would refuse them is caught. Cloudflare's
ranges are the server's, not an application's, so their routes are mounted
apart (:data:`ranges_router`).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.deployers import access
from noust.managers import trusted_proxies
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain
from noust.web.api.scope import require_instance_scope
from noust.web.auth import get_client_ip
from noust.web.permissions import Permission
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)

#: The server's own list of Cloudflare ranges: mounted at ``/policy``.
ranges_router = APIRouter(route_class=NoustErrorRoute)


class HeaderLine(BaseModel):
    """One response header the site sends."""

    name: str
    value: str


class RouteFields(BaseModel):
    """
    What happens at one prefix of the site.

    Attributes:
        path: The prefix, such as ``/admin``; it matches the prefix and everything under it.
        kind: ``site`` (served as the rest of the site, with its own password rule and
            headers), ``proxy`` (handed to another service on this machine) or ``redirect``.
        auth: ``inherit``, ``password`` (ask for it here) or ``open`` (lift the site's here).
        port: The local port a ``proxy`` hands the prefix to.
        strip: Whether a ``proxy`` takes the prefix off the path the service sees.
        to: Where a ``redirect`` goes: an address or an absolute path.
        code: A ``redirect``'s status: 301, 302, 307 or 308.
        keep_path: Whether a ``redirect`` carries what follows the prefix to the target.
        headers: Response headers sent at this prefix, over the site's.
    """

    path: str
    kind: str = "site"
    auth: str = "inherit"
    port: int = 0
    strip: bool = False
    to: str = ""
    code: int = 301
    keep_path: bool = True
    headers: dict[str, str] = Field(default_factory=dict)


class PolicyFields(BaseModel):
    """
    A site's policy, field by field.

    Attributes:
        basic_auth: Ask visitors for a name and password.
        realm: The sign-in prompt's title.
        access: ``all`` (the password and the address list must both pass) or ``any``.
        allow_ips: Addresses and networks allowed; everyone else is refused when there are any.
        deny_ips: Addresses and networks refused first.
        real_ip: ``off``, ``cloudflare`` or ``custom``.
        real_ip_header: The header carrying the client address (``custom``).
        trusted_proxies: The networks the header is believed from (``custom``).
        rate_limit: Requests per client address, ``10r/s`` or ``300r/m``; empty for none.
        rate_burst: Requests above the rate let through before refusing.
        connection_limit: Concurrent connections per client address; 0 for none.
        headers: Response headers set by the operator, over the defaults.
        hidden_headers: Default headers the site does not send.
        hsts: ``default`` (the server's setting), ``on`` or ``off``.
        noindex: Tell search engines not to index the site.
        canonical: ``www``, ``apex`` or empty.
        routes: What happens at prefixes of the site, longest path first.
    """

    basic_auth: bool = False
    realm: str = "Restricted"
    access: str = "all"
    allow_ips: list[str] = Field(default_factory=list)
    deny_ips: list[str] = Field(default_factory=list)
    real_ip: str = "off"
    real_ip_header: str = ""
    trusted_proxies: list[str] = Field(default_factory=list)
    rate_limit: str = ""
    rate_burst: int = 20
    connection_limit: int = 0
    headers: dict[str, str] = Field(default_factory=dict)
    hidden_headers: list[str] = Field(default_factory=list)
    hsts: str = "default"
    noindex: bool = False
    canonical: str = ""
    routes: list[RouteFields] = Field(default_factory=list)


class PolicyResponse(BaseModel):
    """
    An application's policy and what it comes to.

    Attributes:
        app: The application's primary domain.
        policy: The policy in force.
        users: The names of the basic-authentication accounts; never their hashes.
        webserver: ``nginx`` or ``apache``.
        applicable: Whether the policy reaches the site.
        reason: Why it does not, when it does not.
        headers: The response headers the site sends.
        real_ip_networks: The networks the client address is believed from.
        real_ip_source: ``fetched`` or ``built-in`` for Cloudflare's ranges.
    """

    app: str
    policy: PolicyFields
    users: list[str]
    webserver: str
    applicable: bool
    reason: str = ""
    headers: list[HeaderLine] = Field(default_factory=list)
    real_ip_networks: list[str] = Field(default_factory=list)
    real_ip_source: str = ""


class PolicyRequest(BaseModel):
    """
    Changes to a policy. A field left out is not changed.

    Attributes:
        basic_auth: See :class:`PolicyFields`.
        realm: See :class:`PolicyFields`.
        access: See :class:`PolicyFields`.
        allow_ips: See :class:`PolicyFields`.
        deny_ips: See :class:`PolicyFields`.
        real_ip: See :class:`PolicyFields`.
        real_ip_header: See :class:`PolicyFields`.
        trusted_proxies: See :class:`PolicyFields`.
        rate_limit: See :class:`PolicyFields`; an empty string clears it.
        rate_burst: See :class:`PolicyFields`.
        connection_limit: See :class:`PolicyFields`.
        headers: Replaces the operator's headers whole.
        hidden_headers: Replaces the list of default headers left out.
        hsts: ``default``, ``on`` or ``off``.
        noindex: See :class:`PolicyFields`.
        canonical: See :class:`PolicyFields`; an empty string clears it.
        routes: Replaces the routes whole; an empty list clears them. A route that hands a
            prefix to a port no visible application listens on is the server's to ask for.
        accept_lockout: Go ahead although the lists refuse the caller's own address.
    """

    basic_auth: bool | None = None
    realm: str | None = None
    access: str | None = None
    allow_ips: list[str] | None = None
    deny_ips: list[str] | None = None
    real_ip: str | None = None
    real_ip_header: str | None = None
    trusted_proxies: list[str] | None = None
    rate_limit: str | None = None
    rate_burst: int | None = None
    connection_limit: int | None = None
    headers: dict[str, str] | None = None
    hidden_headers: list[str] | None = None
    hsts: str | None = None
    noindex: bool | None = None
    canonical: str | None = None
    routes: list[RouteFields] | None = None
    accept_lockout: bool = False


class UserRequest(BaseModel):
    """
    An account of an application's basic authentication.

    Attributes:
        password: The password: 8 to 256 characters on one line. Hashed at once.
    """

    password: str = Field(min_length=1, max_length=1024)


class RangesResponse(BaseModel):
    """
    The networks a site trusts to name the client behind Cloudflare.

    Attributes:
        networks: The ranges in force.
        source: ``fetched`` or ``built-in``.
        fetched_at: When the fetched list was read, ISO 8601 UTC.
        stale: Whether a refresh is due.
    """

    networks: list[str]
    source: str
    fetched_at: str | None = None
    stale: bool


class RangesRefresh(RangesResponse):
    """
    What fetching the ranges again did.

    Attributes:
        changed: Whether the list differs from the one before.
        applied: The applications whose site was rendered again.
        failed: Those that could not be, with the reason.
    """

    changed: bool
    applied: list[str] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict)


#: How ``hsts`` is said in the API and in the policy.
_HSTS_TO_POLICY: dict[str, bool | None] = {"default": None, "on": True, "off": False}


def _require_app(domain: str) -> str:
    """
    Validate an application's domain and require that it is deployed.

    Args:
        domain: The domain from the path.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


def _response(view: access.AccessView) -> PolicyResponse:
    """
    Translate a view to its response model.

    Args:
        view: What the operation returned.

    Returns:
        The response model.
    """
    data = view.to_dict()
    policy = data["policy"]
    overrides = {name: value for name, value in view.policy.headers.items() if value is not None}
    hidden = [name for name, value in view.policy.headers.items() if value is None]
    hsts = {True: "on", False: "off", None: "default"}[view.policy.hsts]
    return PolicyResponse(
        app=data["app"],
        policy=PolicyFields(
            **{
                **{key: value for key, value in policy.items() if key not in ("headers", "hsts")},
                "headers": overrides,
                "hidden_headers": hidden,
                "hsts": hsts,
            }
        ),
        users=data["users"],
        webserver=data["webserver"],
        applicable=data["applicable"],
        reason=data["reason"],
        headers=[HeaderLine(**line) for line in data["headers"]],
        real_ip_networks=data["real_ip_networks"],
        real_ip_source=data["real_ip_source"],
    )


def changes_from(request: PolicyRequest) -> dict[str, Any]:
    """
    Turn a request into the changes the operation takes.

    Args:
        request: The body.

    Returns:
        Field to new value, for the fields that were sent.
    """
    changes: dict[str, Any] = {}
    for name in (
        "basic_auth",
        "realm",
        "access",
        "allow_ips",
        "deny_ips",
        "real_ip",
        "real_ip_header",
        "trusted_proxies",
        "rate_limit",
        "rate_burst",
        "connection_limit",
        "noindex",
        "canonical",
    ):
        value = getattr(request, name)
        if value is not None:
            changes[name] = value
    if request.headers is not None or request.hidden_headers is not None:
        merged: dict[str, str | None] = dict(request.headers or {})
        for name in request.hidden_headers or []:
            merged[name] = None
        changes["headers"] = merged
    if request.routes is not None:
        changes["routes"] = [dump_model(route) for route in request.routes]
    if request.hsts is not None:
        if request.hsts not in _HSTS_TO_POLICY:
            raise ValidationError("hsts is default, on or off", field="hsts")
        changes["hsts"] = _HSTS_TO_POLICY[request.hsts]
    return changes


def _guard_proxy_ports(routes: list[RouteFields] | None) -> None:
    """
    Keep a prefix from being handed to a service that is not a visible application's.

    A route that proxies to a local port can reach anything listening on this
    machine, so a caller that sees only some teams may name only the ports of the
    applications it sees; any other port is the server's to ask for.

    Args:
        routes: The routes of the request, if it sent any.

    Raises:
        PermissionDenied: A restricted caller names a port no visible application has.
    """
    ports = {route.port for route in routes or [] if route.kind == "proxy"}
    if ports and ports - {app.port for app in scoped_store().list_apps() if app.port}:
        require_instance_scope(Permission.APPS_MANAGE)


@router.get("/{domain}/policy", response_model=PolicyResponse)
def get_policy(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> PolicyResponse:
    """
    Show an application's policy, its accounts and what the site sends.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        The policy in force. No password hash is ever returned.
    """
    return _response(access.describe(_require_app(domain)))


@router.put("/{domain}/policy", response_model=PolicyResponse)
def put_policy(
    domain: str,
    request: Request,
    data: PolicyRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> PolicyResponse:
    """
    Change fields of an application's policy and render its site again.

    The site is tested by the web server before it is reloaded, and a change it
    refuses is put back. The caller's own address is checked against the lists:
    one that would refuse it is rejected unless ``accept_lockout`` is set.

    Args:
        domain: The application's primary domain.
        request: The request, for the caller's address.
        data: The fields to change.
        session: Authenticated session, injected.

    Returns:
        The policy now in force.

    Raises:
        ValidationError: A value is not acceptable (400, naming the field).
    """
    validated = _require_app(domain)
    _guard_proxy_ports(data.routes)
    view = access.set_policy(
        validated,
        changes_from(data),
        operator_addresses=[get_client_ip(request)],
        accept_lockout=data.accept_lockout,
    )
    return _response(view)


@router.delete("/{domain}/policy", response_model=PolicyResponse)
def delete_policy(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> PolicyResponse:
    """
    Take every field of the policy back to its default. The accounts are kept.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        The policy now in force.
    """
    return _response(access.reset_policy(_require_app(domain)))


@router.put("/{domain}/policy/users/{username}", response_model=PolicyResponse)
def put_policy_user(
    domain: str,
    username: str,
    data: UserRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> PolicyResponse:
    """
    Add an account to the application's basic authentication, or change its password.

    Args:
        domain: The application's primary domain.
        username: The login name.
        data: The password; hashed at once and never stored or returned.
        session: Authenticated session, injected.

    Returns:
        The policy and its accounts.
    """
    return _response(access.set_user(_require_app(domain), username, data.password))


@router.delete("/{domain}/policy/users/{username}", response_model=PolicyResponse)
def delete_policy_user(
    domain: str,
    username: str,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> PolicyResponse:
    """
    Remove an account of the application's basic authentication.

    Args:
        domain: The application's primary domain.
        username: The login name.
        session: Authenticated session, injected.

    Returns:
        The policy and its accounts.
    """
    return _response(access.remove_user(_require_app(domain), username))


def _ranges() -> RangesResponse:
    """
    Returns:
        The ranges in force.
    """
    current = trusted_proxies.current_ranges()
    return RangesResponse(
        networks=list(current.networks),
        source=current.source,
        fetched_at=current.fetched_at,
        stale=trusted_proxies.is_stale(),
    )


@ranges_router.get("/cloudflare-ranges", response_model=RangesResponse)
def get_cloudflare_ranges(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> RangesResponse:
    """
    Show the networks a site trusts to name the client behind Cloudflare.

    Args:
        session: Authenticated session, injected.

    Returns:
        The ranges in force and where they came from.
    """
    return _ranges()


@ranges_router.post("/cloudflare-ranges/refresh", response_model=RangesRefresh)
def refresh_cloudflare_ranges(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> RangesRefresh:
    """
    Fetch Cloudflare's published ranges again and render the sites that trust them.

    Args:
        session: Authenticated session, injected.

    Returns:
        What changed and which sites were rendered again.
    """
    outcome = access.refresh_trusted_ranges()
    base = _ranges()
    return RangesRefresh(
        networks=base.networks,
        source=base.source,
        fetched_at=base.fetched_at,
        stale=base.stale,
        changed=bool(outcome["changed"]),
        applied=list(outcome["applied"]),
        failed=dict(outcome["failed"]),
    )
