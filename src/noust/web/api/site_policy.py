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
from noust.managers.site_forward_auth import PRESETS
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


class ForwardAuthFields(BaseModel):
    """
    The authentication service every request to the site is asked about.

    Attributes:
        url: Where each request is asked about, such as ``http://127.0.0.1:4180/oauth2/auth``.
            An empty address turns it off.
        sign_in: Where a visitor who is not signed in is sent; the page they asked for is
            appended. An address, or a path on this site such as ``/oauth2/start?rd=``. Empty
            answers 401.
        headers: Response headers of the service handed on to the application, by name.
    """

    url: str = ""
    sign_in: str = ""
    headers: list[str] = Field(default_factory=list)


class ForwardAuthPreset(BaseModel):
    """
    What a common authentication service answers with, to fill a form from.

    Attributes:
        name: The preset's name.
        url: The service's verify address on this machine.
        sign_in: The sign-in path.
        headers: The response headers it sets for the application.
        note: What to do on the service's side.
    """

    name: str
    url: str
    sign_in: str
    headers: list[str]
    note: str


class ForwardAuthPresets(BaseModel):
    """
    The presets.

    Attributes:
        presets: One per common service.
    """

    presets: list[ForwardAuthPreset]


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
        forward_auth: The authentication service requests are asked about; None for none.
        http3: Serve the site over HTTP/3 (QUIC) as well.
        client_certs: Answer only a visitor with a certificate the site's own authority signed.
        waf: The web application firewall: ``detect``, ``block`` or empty for none.
        waf_exclusions: Numbers of the rules the firewall leaves out for this site.
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
    forward_auth: ForwardAuthFields | None = None
    http3: bool = False
    client_certs: bool = False
    waf: str = ""
    waf_exclusions: list[int] = Field(default_factory=list)
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
        forward_auth: Ask this authentication service about every request; an empty ``url``
            turns it off. The server's to name (it is an address nginx will call), and refused
            while the service does not answer unless ``accept_unreachable_auth`` is set.
        http3: Serve the site over HTTP/3 as well; refused where the server cannot.
        client_certs: Answer only a visitor with a client certificate (``/client-certs`` makes
            them); refused while none is valid unless ``accept_lockout`` is set, on Apache and
            on a site with no HTTPS.
        waf: ``detect`` (write down what the rules see), ``block`` (refuse what they score too
            high), or ``off``; refused where the server has no ModSecurity.
        waf_exclusions: Replaces the numbers of the rules left out for this site whole.
        routes: Replaces the routes whole; an empty list clears them. A route that hands a
            prefix to a port no visible application listens on is the server's to ask for.
        accept_lockout: Go ahead although the lists refuse the caller's own address.
        accept_unreachable_auth: Go ahead although the authentication service does not answer.
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
    forward_auth: ForwardAuthFields | None = None
    http3: bool | None = None
    client_certs: bool | None = None
    waf: str | None = None
    waf_exclusions: list[int] | None = None
    routes: list[RouteFields] | None = None
    accept_lockout: bool = False
    accept_unreachable_auth: bool = False


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
        "http3",
        "client_certs",
        "waf",
        "waf_exclusions",
    ):
        value = getattr(request, name)
        if value is not None:
            changes[name] = value
    if request.headers is not None or request.hidden_headers is not None:
        merged: dict[str, str | None] = dict(request.headers or {})
        for name in request.hidden_headers or []:
            merged[name] = None
        changes["headers"] = merged
    if request.forward_auth is not None:
        changes["forward_auth"] = dump_model(request.forward_auth)
    if request.routes is not None:
        changes["routes"] = [dump_model(route) for route in request.routes]
    if request.hsts is not None:
        if request.hsts not in _HSTS_TO_POLICY:
            raise ValidationError("hsts is default, on or off", field="hsts")
        changes["hsts"] = _HSTS_TO_POLICY[request.hsts]
    return changes


class Http3Model(BaseModel):
    """
    Whether this server can serve HTTP/3, and what stands in the way.

    Attributes:
        available: nginx is 1.25.0 or later and was built with the HTTP/3 module.
        webserver: ``nginx`` or ``apache``.
        version: nginx's version.
        problem: What is missing, when something is.
        hint: How to fix it.
        anchor: Whether the site that holds the QUIC socket is in place.
        udp_allowed: Whether the firewall lets UDP 443 in; None when it filters nothing or
            cannot be read.
        firewall_hint: The command that opens UDP 443, when it is closed.
    """

    available: bool
    webserver: str
    version: str = ""
    problem: str = ""
    hint: str = ""
    anchor: bool = False
    udp_allowed: bool | None = None
    firewall_hint: str = ""


@router.get("/{domain}/http3", response_model=Http3Model)
def get_http3(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> Http3Model:
    """
    Say whether this application's server can serve it over HTTP/3.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        What is available, what is missing and the firewall's answer for UDP 443.
    """
    return Http3Model(**access.http3_support(_require_app(domain)).to_dict())


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
    if data.forward_auth is not None:
        # nginx will call whatever address is named, from this machine: the server's to choose.
        require_instance_scope(Permission.APPS_MANAGE)
    view = access.set_policy(
        validated,
        changes_from(data),
        operator_addresses=[get_client_ip(request)],
        accept_lockout=data.accept_lockout,
        accept_unreachable_auth=data.accept_unreachable_auth,
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


@ranges_router.get("/forward-auth-presets", response_model=ForwardAuthPresets)
def get_forward_auth_presets(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> ForwardAuthPresets:
    """
    List what the common authentication services answer with, to fill a form from.

    Args:
        session: Authenticated session, injected.

    Returns:
        One preset per service.
    """
    return ForwardAuthPresets(
        presets=[ForwardAuthPreset(name=name, **values) for name, values in PRESETS.items()]
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
