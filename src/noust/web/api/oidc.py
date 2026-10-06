# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Single sign-in over HTTP: ``/api/auth/oidc``.

A thin client of :class:`noust.core.accounts.oidc.OidcManager`, the manager
``noust auth oidc`` drives: every rule about who may come in is there. What this
module adds is what only the console knows: the browser's redirects and its
cookie, the address the provider is to call back, the lockout and the audit
trail, and the one ending an account's sign-in has however it began
(:func:`noust.web.api.auth.account_signed_in`).

The sign-in is three requests, and the browser makes two of them as plain
navigations:

1. ``GET /start`` remembers the sign-in and redirects to the provider, with a
   cookie that ties the answer to this browser (``SameSite=Lax``: the answer
   arrives from another site, and a ``Strict`` cookie would not travel with it).
2. ``GET /callback`` is where the provider sends the browser back. It verifies
   everything and redirects to the sign-in page with a single-use ticket, or
   with the reason in one word. It never answers JSON to a browser and never
   sets the session.
3. ``POST /complete`` is the console collecting the ticket: the account then
   signs in as any account does, with its second factor, or enrolling one. The
   provider is its first factor and nothing else.

Mounted under ``/api/auth``, so a central's fleet token never reaches it.
Configuring a provider and linking people to accounts is ``accounts.manage``,
in sudo mode; reading it ``accounts.read``; the client secret is never in an
answer.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from noust.core.accounts.oidc import OidcManager
from noust.core.accounts.oidc_model import OidcError, OidcProvider
from noust.core.accounts.oidc_store import FLOW_SECONDS, digest
from noust.core.config import Config
from noust.core.exceptions import ConfigError, DependencyError
from noust.web.api.auth import (
    LoginResponse,
    SecondFactorStep,
    account_signed_in,
    login_failure,
    second_factor_methods,
)
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import (
    actor_label,
    audit_event,
    get_client_ip,
    is_secure_request,
    record_auth_failure,
    require_auth,
)
from noust.web.pydantic_compat import dump_model
from noust.web.server import get_token_manager

router = APIRouter(route_class=NoustErrorRoute)

#: The browser's tie to a sign-in: where it is sent, and what it is called.
BINDING_COOKIE = "noust_oidc"
START_PATH = "/api/auth/oidc/start"
CALLBACK_PATH = "/api/auth/oidc/callback"
COMPLETE_PATH = "/api/auth/oidc/complete"

#: What the sign-in page is told when a sign-in does not finish: one word each,
#: never the reason, which is for the audit log.
SSO_ERRORS = ("unavailable", "expired", "denied", "failed", "refused", "locked")

#: A provider's own failures are not guesses at a credential and are not counted
#: against the address that happened to be signing in.
_NOT_COUNTED = ("unavailable", "refused", "denied")


def manager() -> OidcManager:
    """
    Returns:
        The single sign-in manager, over the console's own account manager.
    """
    accounts = get_token_manager().accounts
    return OidcManager(accounts.store, accounts=accounts)


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


def callback_url(request: Request, provider: OidcProvider) -> str:
    """
    Args:
        request: The request.
        provider: The provider; its own redirect URI, when it has one, wins.

    Returns:
        The address the provider calls back, to register with it.
    """
    return provider.redirect_uri or origin_of(request) + CALLBACK_PATH


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


class ProviderChoice(BaseModel):
    """
    One way to sign in, as the sign-in page offers it.

    Attributes:
        name: The provider's short name, for ``/start?provider=``.
        label: What the button says after "Sign in with".
    """

    name: str
    label: str


class ProviderChoices(BaseModel):
    """
    The ways to sign in besides an account's password.

    Attributes:
        providers: Every enabled provider.
    """

    providers: list[ProviderChoice]


@router.get("/providers", response_model=ProviderChoices)
def list_choices(response: Response) -> ProviderChoices:
    """
    Name the providers the sign-in page offers. Anonymous: it shows buttons.

    Args:
        response: Used to keep the answer out of caches.

    Returns:
        The name and label of each enabled provider, nothing else of them.
    """
    response.headers["Cache-Control"] = "no-store"
    return ProviderChoices(
        providers=[ProviderChoice(**item) for item in manager().public_providers()]
    )


@router.get("/start", status_code=302, response_class=RedirectResponse)
def start(
    request: Request,
    provider: str = Query(max_length=32),
    next: str | None = Query(default=None, max_length=512),
) -> RedirectResponse:
    """
    Begin a sign-in: remember it and send the browser to the provider.

    Anonymous, and it writes only a short-lived record of the sign-in; the
    provider is chosen from those configured, never from the request. Whatever
    goes wrong, the browser lands on the sign-in page with a one-word reason.

    Args:
        request: The request.
        provider: The provider's name.
        next: The console page to go to afterwards; reduced to a path of this console.

    Returns:
        A redirect to the provider, setting the cookie that ties the answer to
        this browser, or to the sign-in page.
    """
    client_ip = get_client_ip(request)
    oidc = manager()
    found = oidc.provider(provider)
    try:
        if found is None:
            raise OidcError(
                "unknown_provider", "That sign-in is not available", public="unavailable"
            )
        begun = oidc.begin(
            found.name,
            redirect_uri=callback_url(request, found),
            client_ip=client_ip,
            next_path=next,
        )
    except OidcError as exc:
        audit_event(
            "auth.oidc.login",
            "failure",
            client_ip=client_ip,
            target=f"provider:{provider[:32]}",
            detail=f"sign-in could not start: {exc.reason}",
        )
        return to_login(exc.public)
    response = RedirectResponse(begun.url, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    # Lax, not Strict: the answer arrives as a navigation from another site,
    # and a Strict cookie is not sent with one. Lax still withholds it from a
    # cross-site POST or subresource, and the path keeps it off everything
    # but the API.
    response.set_cookie(
        BINDING_COOKIE,
        begun.binding,
        max_age=FLOW_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_secure_request(request),
        path="/api",
    )
    return response


@router.get("/callback", status_code=303, response_class=RedirectResponse)
def callback(
    request: Request,
    code: str | None = Query(default=None, max_length=8192),
    state: str | None = Query(default=None, max_length=256),
    error: str | None = Query(default=None, max_length=64),
) -> RedirectResponse:
    """
    Take the provider's answer: verify it, admit the person, hand over a ticket.

    A guess at the answer (an unknown or replayed state, another browser's
    cookie, a token that does not verify) is counted against the address like a
    wrong password and audited; a provider that is down, or a person the policy
    does not admit, is audited but not counted.

    Args:
        request: The request, from the provider's redirect.
        code: The authorization code.
        state: The state the sign-in began with.
        error: The provider's error code, when it refused.

    Returns:
        A redirect to the sign-in page carrying the ticket, or the reason.
    """
    client_ip = get_client_ip(request)
    oidc = manager()
    binding = request.cookies.get(BINDING_COOKIE)
    response = to_login()
    try:
        if not state or (not code and not error):
            raise OidcError("malformed", "The answer has no state or code", public="failed")
        if error:
            # The provider said no (the person declined, or it refused): spend
            # the sign-in so its state cannot be answered again, and say so.
            oidc.data.spend_flow(digest(state))
            audit_event(
                "auth.oidc.login",
                "failure",
                client_ip=client_ip,
                target="provider",
                detail=f"refused at the provider: {_printable(error)}",
            )
            response = to_login("denied")
        else:
            answer = oidc.finish(state=state, code=code or "", binding=binding, client_ip=client_ip)
            response = to_login(ticket=answer.ticket)
    except OidcError as exc:
        audit_event(
            "auth.oidc.login",
            "failure",
            client_ip=client_ip,
            target=f"identity:{exc.who}" if exc.who else "provider",
            detail=f"sign-in refused: {exc.reason}",
        )
        if exc.public not in _NOT_COUNTED and not exc.reason.startswith("idp_"):
            record_auth_failure(client_ip, "/api/auth/oidc/callback", "oidc")
        response = to_login(exc.public)
    except DependencyError as exc:
        audit_event(
            "auth.oidc.login",
            "failure",
            client_ip=client_ip,
            target="provider",
            detail=f"sign-in unavailable: {exc.message}",
        )
        response = to_login("unavailable")
    return response


class CompleteRequest(BaseModel):
    """
    The console collecting the ticket of a sign-in the provider vouched for.

    Attributes:
        ticket: The single-use ticket the sign-in page was sent to.
        bearer: Also return the session token in the body, for clients
            without a cookie jar.
    """

    ticket: str = Field(max_length=128)
    bearer: bool = False


class CompleteResponse(BaseModel):
    """
    What collecting a ticket gives: where the sign-in stands, and where to go.

    Attributes:
        login: Exactly what ``POST /api/auth/login`` answers: a session, with
            ``mfa_required`` when the account still has to enrol a second
            factor, or ``second_factor`` when it has one to present first.
        next: The console page the person was going to.
        provider: The provider's name.
        label: Its button label, for "signed in with ...".
    """

    login: LoginResponse
    next: str
    provider: str
    label: str


@router.post("/complete", response_model=CompleteResponse)
def complete(request: Request, response: Response, body: CompleteRequest) -> CompleteResponse:
    """
    Finish a sign-in a provider vouched for, as an account's sign-in finishes.

    An account with a second factor is answered the step it owes
    (``login.second_factor``), to be finished at
    ``POST /api/auth/login/second-factor`` or with a passkey; one without is
    signed in, its session fit only to enrol one. The ticket is single use, and
    only the browser that began the sign-in, from the address that received the
    answer, can collect it.

    Args:
        request: The request.
        response: Used to set the session cookies and clear the sign-in's.
        body: The ticket.

    Returns:
        The login result and where to go.

    Raises:
        HTTPException: 401 ``sign_in_expired`` when there is nothing to
            collect (unknown, spent, expired, another browser or address, or
            the account can no longer sign in). 429 ``locked_out``.
    """
    client_ip = get_client_ip(request)
    token_manager = get_token_manager()
    oidc = manager()
    try:
        ticket, account = oidc.collect(body.ticket, request.cookies.get(BINDING_COOKIE), client_ip)
    except OidcError as exc:
        audit_event(
            "auth.oidc.login",
            "failure",
            client_ip=client_ip,
            target="ticket",
            detail=f"the ticket was not collected: {exc.reason}",
        )
        if exc.reason in ("binding", "address"):
            record_auth_failure(client_ip, COMPLETE_PATH, "oidc")
        raise login_failure(
            "sign_in_expired", "This sign-in has expired. Start again with your provider."
        ) from exc
    response.delete_cookie(BINDING_COOKIE, path="/api")
    provider = oidc.provider(ticket.provider)
    label = provider.label if provider is not None else ticket.provider

    if account.has_mfa:
        challenge, expires_in = token_manager.issue_login_challenge(
            account.id, client_ip, via="oidc"
        )
        login = LoginResponse(
            success=False,
            expires_in=0,
            csrf_token="",
            second_factor=SecondFactorStep(
                challenge=challenge,
                expires_in=expires_in,
                methods=second_factor_methods(account),
            ),
        )
    else:
        login = account_signed_in(
            request,
            response,
            account,
            bearer=body.bearer,
            auth_method="oidc",
            event="auth.oidc.login",
            detail=ticket.detail,
            lockout_key=client_ip,
        )
    return CompleteResponse(
        login=login, next=ticket.next_path, provider=ticket.provider, label=label
    )


# ---------------------------------------------------------------------- admin


class Mapping(BaseModel):
    """
    One line of a provider's group mapping.

    Attributes:
        group: The group, as the provider's claim spells it.
        role: The instance role it gives (``viewer``, ``operator`` or ``admin``).
        team: The team it puts the person in.
        team_role: Their role there.
    """

    group: str = Field(max_length=256)
    role: str | None = None
    team: str | None = None
    team_role: str | None = None


class ProviderInfo(BaseModel):
    """
    A configured provider, with no secret in it.

    Attributes:
        name: Its short name.
        label: What the button says after "Sign in with".
        issuer: Its issuer, as its tokens write it.
        client_id: The client Noust is registered as.
        client_secret_set: Whether a client secret is stored (never its value).
        scopes: Space separated.
        enabled: Whether the sign-in page offers it.
        require_email_verified: Whether the e-mail must be verified.
        assume_email_verified: Whether an e-mail the provider says nothing
            about counts as verified.
        clock_skew_seconds: Tolerated clock difference.
        provisioning: ``off``, ``invite`` or ``auto``.
        default_role: The role of an account a first sign-in creates.
        default_team: The team it joins instead of the default one.
        default_team_role: Its role there.
        link_by_email: Whether a first sign-in links by verified e-mail.
        allowed_domains: When not empty, the e-mail domains allowed.
        require_amr: When not empty, the methods one of which the provider must have used.
        groups_claim: Where the token lists groups.
        group_sync: Whether groups decide roles and teams.
        group_mappings: What each group means.
        redirect_uri: The callback address to register with the provider.
        created_at: When it was configured, UNIX seconds.
        created_by: Who configured it.
        updated_at: Last change, UNIX seconds.
        updated_by: Who made it.
    """

    name: str
    label: str
    issuer: str
    client_id: str
    client_secret_set: bool
    scopes: str
    enabled: bool
    require_email_verified: bool
    assume_email_verified: bool
    clock_skew_seconds: int
    provisioning: str
    default_role: str
    default_team: str | None = None
    default_team_role: str | None = None
    link_by_email: bool
    allowed_domains: list[str]
    require_amr: list[str]
    groups_claim: str
    group_sync: bool
    group_mappings: list[Mapping]
    redirect_uri: str
    created_at: float
    created_by: str | None = None
    updated_at: float
    updated_by: str | None = None

    @classmethod
    def of(cls, provider: OidcProvider, request: Request, has_secret: bool) -> ProviderInfo:
        """
        Args:
            provider: The provider.
            request: The request, for the callback address it implies.
            has_secret: Whether a client secret is stored.

        Returns:
            Its public description.
        """
        body = provider.to_dict()
        body["redirect_uri"] = callback_url(request, provider)
        return cls(client_secret_set=has_secret, **body)


class ProvidersResponse(BaseModel):
    """
    Every configured provider.

    Attributes:
        providers: The providers, by name.
        redirect_uri: The callback address to register with each provider
            that has no address of its own.
    """

    providers: list[ProviderInfo]
    redirect_uri: str


class ProviderSettings(BaseModel):
    """
    What to set on a provider; whatever is left out keeps its value.

    An empty string clears a setting that can be empty (``default_team``,
    ``default_team_role``, ``redirect_uri``).

    Attributes:
        label: What the button says after "Sign in with".
        issuer: The issuer URL, exactly as the provider's tokens write it.
        client_id: The client id.
        client_secret: The client secret to store; never returned.
        clear_secret: Forget the stored secret (a public client).
        scopes: Space separated; ``openid`` is always asked.
        enabled: Whether the sign-in page offers it.
        require_email_verified: Refuse a sign-in whose e-mail is not verified.
        assume_email_verified: Count an e-mail the provider says nothing about
            as verified (a directory the organisation controls).
        clock_skew_seconds: Tolerated clock difference, up to 300.
        provisioning: ``off``, ``invite`` or ``auto``.
        default_role: ``viewer``, ``operator`` or ``admin``.
        default_team: The team a created account joins.
        default_team_role: Its role there.
        link_by_email: Link a first sign-in to the account of its verified e-mail.
        allowed_domains: Only these e-mail domains.
        require_amr: The provider must have used one of these methods.
        groups_claim: Where the token lists groups.
        group_sync: Let groups decide roles and teams.
        group_mappings: What each group means.
        redirect_uri: The callback address, when it cannot be worked out.
    """

    label: str | None = Field(default=None, max_length=64)
    issuer: str | None = Field(default=None, max_length=512)
    client_id: str | None = Field(default=None, max_length=256)
    client_secret: str | None = Field(default=None, max_length=4096)
    clear_secret: bool = False
    scopes: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    require_email_verified: bool | None = None
    assume_email_verified: bool | None = None
    clock_skew_seconds: int | None = None
    provisioning: str | None = Field(default=None, max_length=16)
    default_role: str | None = Field(default=None, max_length=16)
    default_team: str | None = Field(default=None, max_length=64)
    default_team_role: str | None = Field(default=None, max_length=16)
    link_by_email: bool | None = None
    allowed_domains: list[str] | None = None
    require_amr: list[str] | None = None
    groups_claim: str | None = Field(default=None, max_length=128)
    group_sync: bool | None = None
    group_mappings: list[Mapping] | None = None
    redirect_uri: str | None = Field(default=None, max_length=512)


class CheckInfo(BaseModel):
    """
    One thing looked at when a provider is tested.

    Attributes:
        name: What.
        ok: Whether it is right.
        detail: What was found, or what is wrong.
    """

    name: str
    ok: bool
    detail: str


class TestResponse(BaseModel):
    """
    A provider looked at the way a sign-in would, without signing anybody in.

    Attributes:
        ok: Whether every check passed.
        checks: Each thing looked at.
    """

    ok: bool
    checks: list[CheckInfo]


class IdentityInfo(BaseModel):
    """
    A person at a provider, linked to an account.

    Attributes:
        id: Store id, to unlink it.
        account: The account's name.
        provider: The provider it was linked through.
        issuer: The provider's issuer.
        subject: The provider's id for the person.
        email: The address the provider last gave; a label, never a key.
        mapped_role: The role a group mapping last gave.
        created_at: When it was linked, UNIX seconds.
        created_by: Who or what linked it.
        last_login_at: Its last sign-in.
        last_login_ip: From where.
    """

    id: int
    account: str
    provider: str
    issuer: str
    subject: str
    email: str | None = None
    mapped_role: str | None = None
    created_at: float
    created_by: str | None = None
    last_login_at: float | None = None
    last_login_ip: str | None = None


class IdentitiesResponse(BaseModel):
    """
    The links between accounts and people at providers.

    Attributes:
        identities: Each link, oldest first.
    """

    identities: list[IdentityInfo]


class LinkRequest(BaseModel):
    """
    A link to make by hand.

    Attributes:
        provider: The provider.
        username: The account.
        subject: The provider's id (``sub``) for the person.
    """

    provider: str = Field(max_length=32)
    username: str = Field(max_length=64)
    subject: str = Field(max_length=255)


class RemovedResponse(BaseModel):
    """
    A confirmation that something was removed.

    Attributes:
        success: Always true.
        removed: What it was.
    """

    success: bool
    removed: str


def _identity_info(oidc: OidcManager, identity: Any) -> IdentityInfo:
    """
    Args:
        oidc: The manager, to name the account.
        identity: A stored identity.

    Returns:
        Its public description.
    """
    account = oidc.accounts.get(identity.account_id)
    return IdentityInfo(account=account.username if account else "", **identity.to_dict())


def _not_found(exc: ConfigError) -> HTTPException:
    """
    Args:
        exc: The manager's "no such provider".

    Returns:
        The 404 that says so.
    """
    return HTTPException(status_code=404, detail=exc.message)


@router.get("/config", response_model=ProvidersResponse)
def list_providers(
    request: Request, _session: dict[str, Any] = Depends(require_auth)
) -> ProvidersResponse:
    """
    List the configured providers, secrets left out.

    Args:
        request: The request, for the callback address it implies.
        _session: The authenticated payload.

    Returns:
        The providers.
    """
    oidc = manager()
    return ProvidersResponse(
        providers=[
            ProviderInfo.of(found, request, oidc.has_secret(found.name))
            for found in oidc.providers()
        ],
        redirect_uri=origin_of(request) + CALLBACK_PATH,
    )


@router.put("/config/{provider}", response_model=ProviderInfo)
def put_provider(
    provider: str,
    request: Request,
    body: ProviderSettings,
    session: dict[str, Any] = Depends(require_elevated),
) -> ProviderInfo:
    """
    Add a provider or change one, in sudo mode: it decides who may sign in.

    Args:
        provider: The provider's name.
        request: The request.
        body: The settings to set.
        session: The authenticated payload, in sudo mode.

    Returns:
        The provider as it is now.

    Raises:
        ValidationError: When a setting is refused; nothing is written.
    """
    given = dump_model(body)
    secret = given.pop("client_secret")
    clear = bool(given.pop("clear_secret"))
    changes = {key: value for key, value in given.items() if value is not None}
    if body.group_mappings is not None:
        changes["group_mappings"] = [
            {key: value for key, value in item.items() if value} for item in given["group_mappings"]
        ]
    oidc = manager()
    existed = oidc.provider(provider) is not None
    saved = oidc.configure(
        provider, changes, client_secret=secret, clear_secret=clear, actor=actor_label(session)
    )
    audit_event(
        "auth.oidc.config",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"provider:{saved.name}",
        detail=(
            ("changed " if existed else "added ")
            + ", ".join(sorted([*changes, *(["client_secret"] if secret else [])]))
        ),
    )
    return ProviderInfo.of(saved, request, oidc.has_secret(saved.name))


@router.delete("/config/{provider}", response_model=RemovedResponse)
def delete_provider(
    provider: str, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> RemovedResponse:
    """
    Remove a provider and its client secret, in sudo mode.

    Accounts keep their links and their other ways in; one that had no password
    can no longer sign in until it is given one (``noust user invite``).

    Args:
        provider: The provider's name.
        request: The request.
        session: The authenticated payload, in sudo mode.

    Returns:
        A confirmation.

    Raises:
        HTTPException: 404 when there is no such provider.
    """
    try:
        removed = manager().remove(provider)
    except ConfigError as exc:
        raise _not_found(exc) from exc
    audit_event(
        "auth.oidc.config",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"provider:{removed.name}",
        detail="removed",
    )
    return RemovedResponse(success=True, removed=removed.name)


@router.post("/config/{provider}/test", response_model=TestResponse)
def try_provider(
    provider: str, request: Request, _session: dict[str, Any] = Depends(require_auth)
) -> TestResponse:
    """
    Look at a provider the way a sign-in would: discovery, keys, secret, callback.

    Signs nobody in and writes nothing. A provider that cannot be reached is a
    failed check, not an error.

    Args:
        provider: The provider's name.
        request: The request, for the callback address.
        _session: The authenticated payload.

    Returns:
        Each check and whether all passed.

    Raises:
        HTTPException: 404 when there is no such provider.
    """
    oidc = manager()
    try:
        found = oidc.require_provider(provider)
    except ConfigError as exc:
        raise _not_found(exc) from exc
    checks = oidc.test(found.name, redirect_uri=callback_url(request, found))
    return TestResponse(
        ok=all(check.ok for check in checks), checks=[CheckInfo(**c.to_dict()) for c in checks]
    )


@router.get("/identities", response_model=IdentitiesResponse)
def list_identities(
    username: str | None = Query(default=None, max_length=64),
    _session: dict[str, Any] = Depends(require_auth),
) -> IdentitiesResponse:
    """
    List the people at providers that are linked to accounts.

    Args:
        username: Only this account's.
        _session: The authenticated payload.

    Returns:
        The links.
    """
    oidc = manager()
    return IdentitiesResponse(
        identities=[_identity_info(oidc, item) for item in oidc.identities(username)]
    )


@router.post("/identities", response_model=IdentityInfo, status_code=201)
def link_identity(
    request: Request, body: LinkRequest, session: dict[str, Any] = Depends(require_elevated)
) -> IdentityInfo:
    """
    Link an account to a person at a provider by hand, in sudo mode.

    Args:
        request: The request.
        body: The provider, the account and the provider's id for the person.
        session: The authenticated payload, in sudo mode.

    Returns:
        The link.

    Raises:
        HTTPException: 404 when the provider does not exist.
        OidcError: ``already_linked`` when that person is linked already.
        AccountNotFoundError: When there is no such account.
    """
    oidc = manager()
    try:
        identity = oidc.link(body.provider, body.username, body.subject, actor=actor_label(session))
    except ConfigError as exc:
        raise _not_found(exc) from exc
    audit_event(
        "auth.oidc.link",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{body.username.strip().lower()}",
        detail=f"linked to {identity.provider}, subject {identity.subject[:64]}",
    )
    return _identity_info(oidc, identity)


@router.delete("/identities/{identity_id}", response_model=RemovedResponse)
def unlink_identity(
    identity_id: int, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> RemovedResponse:
    """
    Cut a link between an account and a person at a provider, in sudo mode.

    Args:
        identity_id: The link's id.
        request: The request.
        session: The authenticated payload, in sudo mode.

    Returns:
        A confirmation.

    Raises:
        HTTPException: 404 when there is no such link.
    """
    oidc = manager()
    found = oidc.data.get_identity(identity_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No identity with id {identity_id}")
    account = oidc.accounts.get(found.account_id)
    account_name = account.username if account else ""
    removed = oidc.unlink_identity(identity_id)
    audit_event(
        "auth.oidc.unlink",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"account:{account_name}",
        detail=f"unlinked from {removed.provider}, subject {removed.subject[:64]}",
    )
    return RemovedResponse(success=True, removed=str(removed.id))


def _printable(value: str, limit: int = 48) -> str:
    """
    Args:
        value: Text a stranger sent.
        limit: Longest result.

    Returns:
        The text without control characters, cut at ``limit``, for the audit log.
    """
    return "".join(char for char in value if char.isprintable())[:limit]


__all__ = ["BINDING_COOKIE", "router"]
