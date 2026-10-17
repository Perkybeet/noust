# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/auth/saml``: signing in through a SAML 2.0 identity provider, and administering the providers.

A thin client of :class:`~noust.core.accounts.saml.SamlManager`, the manager ``noust auth saml``
drives (rule 3): every rule about what a response must be, and who it lets in, is there. What this
module adds is what only the console knows: the browser's redirects and its cookie, the addresses
the provider is told, the lockout and the audit trail, and the one ending an account's sign-in
has however it began (:func:`noust.web.api.auth.external_sign_in`).

The sign-in is the same three requests as OpenID Connect's, the answer being a form the provider
makes the browser POST:

1. ``GET /start`` remembers the sign-in and redirects to the provider with the request, and a
   cookie that ties the answer to this browser.
2. ``POST /acs`` (the assertion consumer service) is where the provider's page posts the signed
   response. It verifies everything and redirects to the sign-in page with a single-use ticket, or
   with the reason in one word. It never sets the session.
3. The console collects the ticket at ``POST /api/auth/oidc/complete``, the one place every
   provider's ticket is collected: the account then signs in as any account does, with its second
   factor, or enrolling one.

``GET /metadata/{provider}`` is what the provider is registered from: the entity id, the address
the response is posted to, and that assertions must be signed. It holds nothing secret.

Mounted under ``/api/auth``, so a central's fleet token never reaches it. Configuring a provider is
``accounts.manage``, in sudo mode; reading it ``accounts.read``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.core.accounts.oidc_model import OidcError
from noust.core.accounts.saml import (
    ACS_PATH,
    METADATA_PATH,
    SamlManager,
    describe_certificates,
    entity_id_of,
    sp_metadata,
)
from noust.core.accounts.saml_store import SamlProvider
from noust.core.exceptions import DependencyError, ValidationError
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.api.federation_models import CheckInfo, Mapping, TestResponse
from noust.web.api.sso_flow import (
    BINDING_COOKIE,
    NOT_COUNTED,
    origin_of,
    set_binding_cookie,
    to_login,
)
from noust.web.auth import (
    actor_label,
    audit_event,
    get_client_ip,
    record_auth_failure,
    require_auth,
)
from noust.web.pydantic_compat import dump_model
from noust.web.server import get_token_manager

router = APIRouter(route_class=NoustErrorRoute)

FULL_ACS_PATH = "/api/auth/saml/acs"

#: The form a provider's page posts: two fields, a handful of bytes each side of a response.
MAX_FORM_FIELDS = 8

_ACS_FORM = {
    "requestBody": {
        "required": True,
        "content": {
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "SAMLResponse": {"type": "string", "description": "The base64 response."},
                        "RelayState": {
                            "type": "string",
                            "description": "What the sign-in sent with its request.",
                        },
                    },
                    "required": ["SAMLResponse", "RelayState"],
                }
            }
        },
    }
}


def saml_manager() -> SamlManager:
    """
    Returns:
        The SAML manager, over the console's own account manager.
    """
    accounts = get_token_manager().accounts
    return SamlManager(accounts.store, accounts=accounts)


@router.get("/start", status_code=302, response_class=RedirectResponse)
def start(
    request: Request,
    provider: str = Query(max_length=32),
    next: str | None = Query(default=None, max_length=512),
) -> RedirectResponse:
    """
    Begin a sign-in: remember it and send the browser to the identity provider.

    Anonymous, and it writes only a short-lived record of the sign-in; the provider is chosen from
    those configured, never from the request. Whatever goes wrong, the browser lands on the
    sign-in page with a one-word reason.

    Args:
        request: The request.
        provider: The provider's name.
        next: The console page to go to afterwards; reduced to a path of this console.

    Returns:
        A redirect to the provider, setting the cookie that ties the answer to this browser, or to
        the sign-in page.
    """
    client_ip = get_client_ip(request)
    try:
        begun = saml_manager().begin(
            provider, origin=origin_of(request), client_ip=client_ip, next_path=next
        )
    except OidcError as exc:
        audit_event(
            "auth.saml.login",
            "failure",
            client_ip=client_ip,
            target=f"provider:{provider[:32]}",
            detail=f"sign-in could not start: {exc.reason}",
        )
        return to_login(exc.public)
    response = RedirectResponse(begun.url, status_code=302)
    response.headers["Cache-Control"] = "no-store"
    set_binding_cookie(response, request, begun.binding, answered_by_post=True)
    return response


async def _form(request: Request) -> dict[str, str]:
    """
    Read the urlencoded form a provider's page posts, without a multipart parser.

    Args:
        request: The request.

    Returns:
        Each field's single value.

    Raises:
        OidcError: ``malformed`` when it is not that form, repeats a field or has too many.
    """
    kind = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if kind != "application/x-www-form-urlencoded":
        raise OidcError("malformed", "The answer is not a form", public="failed")
    raw = await request.body()
    try:
        parsed = parse_qs(
            raw.decode("ascii"),
            keep_blank_values=True,
            max_num_fields=MAX_FORM_FIELDS,
            strict_parsing=True,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise OidcError("malformed", "The answer is not a valid form", public="failed") from exc
    if any(len(values) != 1 for values in parsed.values()):
        raise OidcError("malformed", "The answer repeats a field", public="failed")
    return {name: values[0] for name, values in parsed.items()}


@router.post(
    "/acs",
    status_code=303,
    response_class=RedirectResponse,
    openapi_extra=_ACS_FORM,
)
async def assertion_consumer_service(request: Request) -> RedirectResponse:
    """
    Take the identity provider's answer: verify it, admit the person, hand over a ticket.

    A response that is not right (a signature that does not verify, another audience, an assertion
    seen before, another browser's cookie) is counted against the address like a wrong password
    and audited; a provider that says the person did not sign in, one that is off, or a person the
    policy does not admit, is audited but not counted. The reason is for the audit trail: the
    sign-in page is told one word.

    Args:
        request: The request, the browser posting the provider's form.

    Returns:
        A redirect to the sign-in page carrying the ticket, or the reason.
    """
    client_ip = get_client_ip(request)
    binding = request.cookies.get(BINDING_COOKIE)
    try:
        fields = await _form(request)
        if not fields.get("SAMLResponse") or not fields.get("RelayState"):
            raise OidcError(
                "malformed", "The answer has no response or relay state", public="failed"
            )
        answer = await run_in_threadpool(
            saml_manager().finish,
            response=fields["SAMLResponse"],
            relay_state=fields["RelayState"],
            binding=binding,
            client_ip=client_ip,
        )
        return to_login(ticket=answer.ticket)
    except OidcError as exc:
        audit_event(
            "auth.saml.login",
            "failure",
            client_ip=client_ip,
            target=f"identity:{exc.who}" if exc.who else "provider",
            detail=f"sign-in refused: {exc.reason}: {_printable(exc.message, 160)}",
        )
        if exc.public not in NOT_COUNTED and not exc.reason.startswith("idp_"):
            record_auth_failure(client_ip, FULL_ACS_PATH, "saml")
        return to_login(exc.public)
    except DependencyError as exc:
        audit_event(
            "auth.saml.login",
            "failure",
            client_ip=client_ip,
            target="provider",
            detail=f"sign-in unavailable: {exc.message}",
        )
        return to_login("unavailable")


@router.get("/metadata/{provider}")
def metadata(request: Request, provider: str = Path(max_length=32)) -> Response:
    """
    Describe this server to an identity provider: what to register it from.

    Anonymous, because the provider fetches it: the entity id, where responses are posted and that
    assertions must be signed are not secret.

    Args:
        request: The request, for the address people reach the console at.
        provider: The provider's name.

    Returns:
        The service provider's metadata document.

    Raises:
        HTTPException: 404 when there is no such provider.
    """
    found = saml_manager().provider(provider)
    if found is None:
        raise HTTPException(status_code=404, detail="No such SAML provider")
    return Response(
        content=sp_metadata(found, origin=origin_of(request)),
        media_type="application/samlmetadata+xml",
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------- admin


class CertificateInfo(BaseModel):
    """
    A signing certificate, described the way an operator compares it with the provider's.

    Attributes:
        subject: Its subject.
        fingerprint: Its SHA-256 fingerprint, hex.
        not_after: When it stops being valid, UNIX seconds.
    """

    subject: str
    fingerprint: str
    not_after: float


class SamlProviderInfo(BaseModel):
    """
    A configured SAML provider. Nothing in it is secret.

    Attributes:
        name: Its short name.
        label: What the button says after "Sign in with".
        enabled: Whether the sign-in page offers it.
        idp_entity_id: Its entity id.
        sso_url: Where the browser is sent to sign in.
        certificates: Its signing certificates, in PEM.
        certificate_details: Each of them, described.
        sp_entity_id: The entity id the operator set; empty derives it.
        entity_id: The entity id Noust presents to it.
        acs_url: The address its response is posted to, to register with it.
        metadata_url: Where this server's metadata for it is published.
        subject_attribute: The attribute that identifies a person; empty uses the NameID.
        email_attribute: Where their address is.
        name_attribute: Where their display name is.
        username_attribute: Where their login name is.
        groups_attribute: Where their groups are.
        clock_skew_seconds: Tolerated clock difference.
        provisioning: ``off``, ``invite`` or ``auto``.
        default_role: The role of an account a first sign-in creates.
        default_team: The team it joins.
        default_team_role: Its role there.
        link_by_email: Whether a first sign-in links by e-mail.
        allowed_domains: When not empty, the e-mail domains allowed.
        require_email_verified: Whether an address must be present.
        assume_email_verified: Whether the provider's address counts as verified.
        require_amr: When not empty, the authentication contexts one of which is required.
        group_sync: Whether groups decide roles and teams.
        group_mappings: What each group means.
        created_at: When it was configured, UNIX seconds.
        created_by: Who configured it.
        updated_at: Last change, UNIX seconds.
        updated_by: Who made it.
    """

    name: str
    label: str
    enabled: bool
    idp_entity_id: str
    sso_url: str
    certificates: list[str]
    certificate_details: list[CertificateInfo]
    sp_entity_id: str
    entity_id: str
    acs_url: str
    metadata_url: str
    subject_attribute: str
    email_attribute: str
    name_attribute: str
    username_attribute: str
    groups_attribute: str
    clock_skew_seconds: int
    provisioning: str
    default_role: str
    default_team: str | None = None
    default_team_role: str | None = None
    link_by_email: bool
    allowed_domains: list[str]
    require_email_verified: bool
    assume_email_verified: bool
    require_amr: list[str]
    group_sync: bool
    group_mappings: list[Mapping]
    created_at: float
    created_by: str | None = None
    updated_at: float
    updated_by: str | None = None

    @classmethod
    def of(cls, provider: SamlProvider, request: Request) -> SamlProviderInfo:
        """
        Args:
            provider: The provider.
            request: The request, for the addresses it implies.

        Returns:
            Its public description.
        """
        origin = origin_of(request)
        body = provider.to_dict()
        return cls(
            certificate_details=[
                CertificateInfo(subject=c.subject, fingerprint=c.fingerprint, not_after=c.not_after)
                for c in describe_certificates(provider)
            ],
            entity_id=entity_id_of(provider, origin),
            acs_url=origin + ACS_PATH,
            metadata_url=f"{origin}{METADATA_PATH}/{provider.name}",
            **body,
        )


class SamlProvidersResponse(BaseModel):
    """
    Every configured SAML provider.

    Attributes:
        providers: The providers, by name.
        acs_url: The address a response is posted to: the same for every provider.
        secure: Whether the console is reached over TLS, which a POST answer needs.
    """

    providers: list[SamlProviderInfo]
    acs_url: str
    secure: bool


class SamlSettings(BaseModel):
    """
    What to set on a SAML provider; whatever is left out keeps its value.

    Attributes:
        label: What the button says after "Sign in with".
        enabled: Whether the sign-in page offers it.
        idp_metadata: The identity provider's metadata XML: its entity id, sign-in address and
            signing certificates are read from it (the settings beside it win).
        idp_entity_id: Its entity id.
        sso_url: Its sign-in address (HTTP-Redirect binding).
        certificates: Its signing certificates, PEM or bare base64: replaces the list.
        sp_entity_id: The entity id Noust presents; empty derives it from the console's address.
        subject_attribute: The attribute that identifies a person; empty uses the NameID.
        email_attribute: Where their address is.
        name_attribute: Where their display name is.
        username_attribute: Where their login name is.
        groups_attribute: Where their groups are.
        clock_skew_seconds: Tolerated clock difference, up to 300.
        provisioning: ``off``, ``invite`` or ``auto``.
        default_role: ``viewer``, ``operator`` or ``admin``.
        default_team: The team a created account joins.
        default_team_role: Its role there.
        link_by_email: Link a first sign-in to the account of its address.
        allowed_domains: Only these e-mail domains.
        require_email_verified: Refuse a person without an address.
        assume_email_verified: Take the provider's address as verified.
        require_amr: One of these authentication context classes must be reported.
        group_sync: Let groups decide roles, memberships and scoped roles.
        group_mappings: What each group means.
    """

    label: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    idp_metadata: str | None = Field(default=None, max_length=1_048_576)
    idp_entity_id: str | None = Field(default=None, max_length=1024)
    sso_url: str | None = Field(default=None, max_length=1024)
    certificates: list[str] | None = Field(default=None, max_length=12)
    sp_entity_id: str | None = Field(default=None, max_length=1024)
    subject_attribute: str | None = Field(default=None, max_length=256)
    email_attribute: str | None = Field(default=None, max_length=256)
    name_attribute: str | None = Field(default=None, max_length=256)
    username_attribute: str | None = Field(default=None, max_length=256)
    groups_attribute: str | None = Field(default=None, max_length=256)
    clock_skew_seconds: int | None = None
    provisioning: str | None = Field(default=None, max_length=16)
    default_role: str | None = Field(default=None, max_length=16)
    default_team: str | None = Field(default=None, max_length=64)
    default_team_role: str | None = Field(default=None, max_length=16)
    link_by_email: bool | None = None
    allowed_domains: list[str] | None = None
    require_email_verified: bool | None = None
    assume_email_verified: bool | None = None
    require_amr: list[str] | None = None
    group_sync: bool | None = None
    group_mappings: list[Mapping] | None = None


class SamlRemoved(BaseModel):
    """
    A confirmation that a provider was removed.

    Attributes:
        success: Always true.
        removed: Its name.
    """

    success: bool
    removed: str


def _not_found(exc: ValidationError) -> HTTPException:
    """
    Args:
        exc: The manager's "no such provider".

    Returns:
        The 404 that says so.
    """
    return HTTPException(status_code=404, detail=exc.message)


def _printable(value: str, limit: int = 48) -> str:
    """
    Args:
        value: Text a stranger or a library produced.
        limit: Longest result.

    Returns:
        The text without control characters, cut at ``limit``, for the audit log.
    """
    return "".join(char for char in value if char.isprintable())[:limit]


@router.get("/config", response_model=SamlProvidersResponse)
def list_providers(
    request: Request, _session: dict[str, Any] = Depends(require_auth)
) -> SamlProvidersResponse:
    """
    List the configured SAML providers.

    Args:
        request: The request, for the addresses it implies.
        _session: The authenticated payload.

    Returns:
        The providers and the address a response is posted to.
    """
    origin = origin_of(request)
    return SamlProvidersResponse(
        providers=[SamlProviderInfo.of(found, request) for found in saml_manager().providers()],
        acs_url=origin + ACS_PATH,
        secure=origin.startswith("https://"),
    )


@router.put("/config/{provider}", response_model=SamlProviderInfo)
def put_provider(
    provider: str,
    request: Request,
    body: SamlSettings,
    session: dict[str, Any] = Depends(require_elevated),
) -> SamlProviderInfo:
    """
    Add a SAML provider or change one, in sudo mode: it decides who may sign in.

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
    changes = {key: value for key, value in given.items() if value is not None}
    if body.group_mappings is not None:
        changes["group_mappings"] = [
            {key: value for key, value in item.items() if value} for item in given["group_mappings"]
        ]
    saml = saml_manager()
    existed = saml.provider(provider) is not None
    saved = saml.configure(provider, changes, actor=actor_label(session))
    audit_event(
        "auth.saml.config",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"provider:{saved.name}",
        detail=("changed " if existed else "added ") + ", ".join(sorted(changes)),
    )
    return SamlProviderInfo.of(saved, request)


@router.delete("/config/{provider}", response_model=SamlRemoved)
def delete_provider(
    provider: str, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> SamlRemoved:
    """
    Remove a SAML provider, in sudo mode.

    Accounts keep their links and their other ways in; one that had no password can no longer sign
    in until it is given one (``noust user invite``).

    Args:
        provider: The provider's name.
        request: The request.
        session: The authenticated payload, in sudo mode.

    Returns:
        A confirmation.

    Raises:
        HTTPException: 404 when there is no such provider.
        LocalLoginRefused: 400 while local sign-in is off and this is the last way in.
    """
    try:
        removed = saml_manager().remove(provider)
    except ValidationError as exc:
        raise _not_found(exc) from exc
    audit_event(
        "auth.saml.config",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"provider:{removed.name}",
        detail="removed",
    )
    return SamlRemoved(success=True, removed=removed.name)


@router.post("/config/{provider}/test", response_model=TestResponse)
def try_provider(
    provider: str, request: Request, _session: dict[str, Any] = Depends(require_auth)
) -> TestResponse:
    """
    Look at a provider the way a sign-in would: its certificates, its address, what it is told.

    Signs nobody in and writes nothing.

    Args:
        provider: The provider's name.
        request: The request, for the addresses this server tells the provider.
        _session: The authenticated payload.

    Returns:
        Each check and whether all passed.

    Raises:
        HTTPException: 404 when there is no such provider.
    """
    try:
        checks = saml_manager().test(provider, origin=origin_of(request))
    except ValidationError as exc:
        raise _not_found(exc) from exc
    return TestResponse(
        ok=all(check.ok for check in checks),
        checks=[CheckInfo(name=c.name, ok=c.ok, detail=c.detail) for c in checks],
    )


__all__ = ["router", "saml_manager"]
