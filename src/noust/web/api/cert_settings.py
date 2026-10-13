# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where certificates come from, over the API: the authority, uploads, and each application's choice.

A thin translation of :mod:`noust.managers.acme`, :mod:`noust.managers.custom_certificates`
and :mod:`noust.deployers.certificates`, which ``noust cert`` reads too. Two routers:

- :data:`router` is mounted at ``/certs`` *before* the certificates router, because its
  paths (``/acme``, ``/uploaded``) would otherwise be read as a domain named ``acme``.
- :data:`apps_router` is mounted at ``/apps`` and owns ``/{domain}/certificate``.

Certbot never runs on the request path: registering an account and ordering a certificate
are queued as jobs, as ``POST /api/certs/{domain}`` does. Uploading a key, and removing an
upload, ask for sudo mode; the key is never in a response.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.deployers import certificates
from noust.managers.acme import AUTHORITIES, AcmeSettings
from noust.managers.cert_manager import CertManager
from noust.managers.custom_certificates import CustomCertificates
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    ensure_elevated,
    require_elevated,
    strict_domain,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

router = APIRouter(route_class=NoustErrorRoute)
apps_router = APIRouter(route_class=NoustErrorRoute)


class AuthorityModel(BaseModel):
    """
    A certificate authority Noust knows by name.

    Attributes:
        name: The value of ``ssl.ca``.
        label: How to call it.
        needs_eab: It asks for an external account first.
        trusted: Browsers trust what it issues.
    """

    name: str
    label: str
    needs_eab: bool
    trusted: bool


class AcmeModel(BaseModel):
    """
    Which authority orders, and as whom.

    Attributes:
        email: The address registered with the authority.
        ca: The authority.
        label: How it is called.
        server: The directory URL orders go to; empty for Let's Encrypt's own.
        needs_eab: It asks for an external account.
        eab_kid: The external account's key id.
        eab_configured: Its HMAC key is stored (it is never shown).
        trusted: Browsers trust what it issues.
        authorities: The ones that can be chosen.
    """

    email: str
    ca: str
    label: str
    server: str
    needs_eab: bool
    eab_kid: str
    eab_configured: bool
    trusted: bool
    authorities: list[AuthorityModel]


class AcmeRequest(BaseModel):
    """
    Changes to the ACME settings. A field left out is not changed.

    Attributes:
        email: The address to register with.
        ca: The authority.
        acme_server: The directory URL, for ``custom``.
        eab_kid: The external account's key id.
        eab_hmac_key: Its HMAC key; an empty string removes it. Needs sudo mode.
    """

    email: str | None = Field(default=None, max_length=254)
    ca: str | None = None
    acme_server: str | None = Field(default=None, max_length=2048)
    eab_kid: str | None = Field(default=None, max_length=256)
    eab_hmac_key: str | None = Field(default=None, max_length=1024)


class UploadedCertificate(BaseModel):
    """
    A certificate that was uploaded. Never its key.

    Attributes:
        name: What it is called.
        names: The names it covers.
        subject: Its subject.
        issuer: Its issuer.
        key_type: ``RSA 2048``, ``EC prime256v1``...
        fingerprint: SHA-256, colon separated.
        not_before: Start of validity, ISO 8601 UTC.
        not_after: End of validity, ISO 8601 UTC.
        days_remaining: Whole days until it expires, negative once it has.
        uploaded_by: Who uploaded it.
        uploaded_at: When.
        used_by: The applications that serve it.
        chain_warning: What is wrong with its chain, if anything.
    """

    name: str
    names: list[str]
    subject: str | None = None
    issuer: str | None = None
    key_type: str | None = None
    fingerprint: str
    not_before: str | None = None
    not_after: str | None = None
    days_remaining: int | None = None
    uploaded_by: str | None = None
    uploaded_at: str
    used_by: list[str] = Field(default_factory=list)
    chain_warning: str | None = None


class UploadedList(BaseModel):
    """
    Every uploaded certificate.

    Attributes:
        certificates: The uploads, by name.
    """

    certificates: list[UploadedCertificate]


class UploadRequest(BaseModel):
    """
    A certificate and its key to keep.

    Attributes:
        name: What to call it: lower case letters, digits, dots, dashes, underscores.
        certificate: PEM: the certificate, and its chain if it comes in the same text.
        private_key: PEM: the key for it, not protected by a passphrase.
        chain: PEM: the intermediates, when they come apart.
    """

    name: str
    certificate: str = Field(max_length=65536)
    private_key: str = Field(max_length=65536)
    chain: str = Field(default="", max_length=65536)


class CertificateModel(BaseModel):
    """
    What an application serves.

    Attributes:
        name: The lineage or upload.
        names: The names on it.
        issuer: Its issuer.
        expires_on: ``YYYY-MM-DD``.
        days_remaining: Whole days until it expires.
        source: ``letsencrypt`` or ``custom``.
    """

    name: str
    names: list[str]
    issuer: str | None = None
    expires_on: str | None = None
    days_remaining: int | None = None
    source: str = "letsencrypt"


class AppCertificate(BaseModel):
    """
    How an application's certificate comes to be, and what it is.

    Attributes:
        app: The application's primary domain.
        source: ``acme`` (ordered), ``custom`` (uploaded) or ``lineage`` (another's).
        name: The upload or lineage, for those sources.
        challenge: ``http-01`` or ``dns-01``.
        dns_provider: The DNS account that answers DNS-01.
        authority: The authority that orders.
        tls: The site serves TLS now.
        names: The names the application answers on.
        certificate: What is served, if a certificate is on disk.
        job_id: The job ordering the certificate, when one was queued.
    """

    app: str
    source: str
    name: str | None = None
    challenge: str
    dns_provider: str | None = None
    authority: str
    tls: bool
    names: list[str]
    certificate: CertificateModel | None = None
    job_id: str | None = None


class ChooseCertificateRequest(BaseModel):
    """
    Where the application's certificate comes from.

    Attributes:
        source: ``acme`` to have it ordered, ``custom`` to serve an upload.
        name: The upload, for ``custom``.
        challenge: ``http-01`` or ``dns-01``, for ``acme``.
        dns_provider: The DNS account, for ``dns-01``.
        order: Order the certificate now when the choice changes it.
    """

    source: str = "acme"
    name: str | None = None
    challenge: str = "http-01"
    dns_provider: str | None = None
    order: bool = True


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


def _acme(settings: AcmeSettings) -> AcmeModel:
    """
    Describe the ACME settings.

    Args:
        settings: The settings.

    Returns:
        The response model; the HMAC key is not in it.
    """
    view = settings.current()
    return AcmeModel(
        **view.to_dict(),
        authorities=[
            AuthorityModel(name=a.name, label=a.label, needs_eab=a.needs_eab, trusted=a.trusted)
            for a in AUTHORITIES.values()
        ],
    )


def _view(view: certificates.CertificateView, job_id: str | None = None) -> AppCertificate:
    """
    Translate a certificate view to its response model.

    Args:
        view: What the orchestration returned.
        job_id: The job queued after it, if any.

    Returns:
        The response model.
    """
    data = view.to_dict()
    certificate = data.pop("certificate")
    return AppCertificate(
        **data,
        certificate=CertificateModel(**certificate) if certificate else None,
        job_id=job_id,
    )


@router.get("/acme", response_model=AcmeModel)
def get_acme(session: Annotated[dict[str, Any], Depends(get_current_session)]) -> AcmeModel:
    """
    Show which certificate authority orders and as whom.

    Args:
        session: Authenticated session, injected.

    Returns:
        The settings. The external account's HMAC key is never in it.
    """
    return _acme(AcmeSettings())


@router.put("/acme", response_model=AcmeModel)
def put_acme(
    request: Request,
    data: AcmeRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> AcmeModel:
    """
    Change the authority, the account address or the external account.

    A new HMAC key needs sudo mode: a credential goes in.

    Args:
        request: The request, for sudo mode.
        data: The changes.
        session: Authenticated session, injected.

    Returns:
        The settings now in force.

    Raises:
        ValidationError: A value is not acceptable (400, naming the field).
    """
    if data.eab_hmac_key is not None:
        ensure_elevated(request, session)
    settings = AcmeSettings()
    settings.update(
        email=data.email,
        ca=data.ca,
        acme_server=data.acme_server,
        eab_kid=data.eab_kid,
        eab_hmac_key=data.eab_hmac_key,
    )
    return _acme(settings)


def register_account_job(job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Register the ACME account with the chosen authority, as a job.

    Args:
        job_context: Injected by the job manager.

    Returns:
        The authority the account is with.

    Raises:
        CertificateError: The authority refused it; the job fails with its words.
    """
    label = AcmeSettings().authority().label
    if job_context is not None:
        job_context.update(f"Registering the account with {label}", 30)
    CertManager().register_account()
    if job_context is not None:
        job_context.update("Account registered", 100)
    return {"authority": label}


@router.post("/acme/register", response_model=JobAcceptedResponse, status_code=202)
def register_acme_account(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Register the account with the chosen authority, proving the external account.

    Queued as a job: it is a round trip to the authority.

    Args:
        session: Authenticated session, injected.

    Returns:
        The queued job.
    """
    job = get_job_manager().create_job(
        job_type=JobType.CERT_CREATE,
        name="Register the ACME account",
        description="Registering the ACME account with the certificate authority",
        func=register_account_job,
        kwargs={},
        metadata={"domain": "acme"},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Registration queued",
        job=job.to_dict(),
    )


@router.get("/uploaded", response_model=UploadedList)
def list_uploaded(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> UploadedList:
    """
    List the certificates that were uploaded, and which applications serve them.

    Args:
        session: Authenticated session, injected.

    Returns:
        The uploads. Their keys are never read back.
    """
    from datetime import datetime, timezone

    uploads = CustomCertificates()
    out: list[UploadedCertificate] = []
    for item in uploads.uploaded():
        days = None
        if item.not_after:
            days = (datetime.fromisoformat(item.not_after) - datetime.now(timezone.utc)).days
        out.append(
            UploadedCertificate(
                name=item.name,
                names=list(item.names),
                subject=item.subject,
                issuer=item.issuer,
                key_type=item.key_type,
                fingerprint=item.fingerprint,
                not_before=item.not_before,
                not_after=item.not_after,
                days_remaining=days,
                uploaded_by=item.uploaded_by,
                uploaded_at=item.uploaded_at,
                used_by=uploads.records.domains_using("custom", item.name),
                chain_warning=uploads.chain_warning(item.name),
            )
        )
    return UploadedList(certificates=out)


@router.post("/uploaded", response_model=UploadedCertificate, status_code=201)
def upload_certificate(
    data: UploadRequest, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> UploadedCertificate:
    """
    Check a certificate and its key and keep them.

    Needs sudo mode: a private key travels in. The pair is checked before anything is
    written: the key is the certificate's, it has no passphrase, the certificate has
    not expired and names something.

    Args:
        data: The certificate, its key and optionally its chain.
        session: Authenticated and elevated session, injected.

    Returns:
        The record (never the key).

    Raises:
        ValidationError: The PEM, the key or the name is not acceptable (400, naming the field).
    """
    uploads = CustomCertificates()
    kept = uploads.upload(
        data.name,
        data.certificate,
        data.private_key,
        data.chain,
        uploaded_by=actor_label(session),
    )
    return UploadedCertificate(
        name=kept.name,
        names=list(kept.names),
        subject=kept.subject,
        issuer=kept.issuer,
        key_type=kept.key_type,
        fingerprint=kept.fingerprint,
        not_before=kept.not_before,
        not_after=kept.not_after,
        uploaded_by=kept.uploaded_by,
        uploaded_at=kept.uploaded_at,
        chain_warning=uploads.chain_warning(kept.name),
    )


@router.delete("/uploaded/{name}", status_code=204)
def remove_uploaded(
    name: str, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> None:
    """
    Delete an uploaded certificate and its key.

    Args:
        name: The certificate.
        session: Authenticated and elevated session, injected.

    Raises:
        ValidationError: An application still serves it (400).
    """
    CustomCertificates().remove(name)


def choose_certificate_job(
    app_domain: str,
    challenge: str,
    dns_provider: str | None,
    order: bool,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Order an application's certificate the way it was asked to be, as a job.

    Args:
        app_domain: The application's primary domain.
        challenge: ``http-01`` or ``dns-01``.
        dns_provider: The DNS account that answers DNS-01.
        order: Order now when the choice changes the certificate.
        job_context: Injected by the job manager.

    Returns:
        The application and whether a certificate was ordered.

    Raises:
        CertificateError: The order failed; the job fails with certbot's words and the
            certificate that served keeps serving.
    """
    if job_context is not None:
        job_context.set_metadata("domain", app_domain)
        job_context.update("Ordering the certificate", 20)
    change = certificates.use_acme(
        app_domain, challenge=challenge, dns_provider=dns_provider, order=order
    )
    if job_context is not None:
        job_context.update("The certificate is in place", 100)
    return {"domain": app_domain, "ordered": change.ordered}


@apps_router.get("/{domain}/certificate", response_model=AppCertificate)
def get_app_certificate(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> AppCertificate:
    """
    Show how an application's certificate comes to be, and what it is.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        The description.
    """
    return _view(certificates.describe(_require_app(domain)))


@apps_router.put("/{domain}/certificate", response_model=AppCertificate)
def put_app_certificate(
    domain: str,
    data: ChooseCertificateRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> AppCertificate:
    """
    Choose where the application's certificate comes from.

    ``custom`` serves an upload at once (nothing is ordered). ``acme`` is queued as a job
    when the choice changes how a certificate is proven: certbot never runs on the request
    path. ``job_id`` is that job; the certificate that served keeps serving until it is done.

    Args:
        domain: The application's primary domain.
        data: The choice.
        session: Authenticated session, injected.

    Returns:
        The description, with the job when one was queued.

    Raises:
        ValidationError: The choice is not acceptable (400, naming the field).
    """
    validated = _require_app(domain)
    if data.source == "custom":
        if not data.name:
            raise ValidationError("Name the uploaded certificate", field="name")
        return _view(certificates.use_uploaded(validated, data.name))
    if data.source != "acme":
        raise ValidationError("The source is acme or custom", field="source")
    certificates.check_choice(data.challenge, data.dns_provider)
    job = get_job_manager().create_job(
        job_type=JobType.CERT_CREATE,
        name=f"Certificate for {validated}",
        description=f"Ordering the certificate of {validated} ({data.challenge})",
        func=choose_certificate_job,
        kwargs={
            "app_domain": validated,
            "challenge": data.challenge,
            "dns_provider": data.dns_provider,
            "order": data.order,
        },
        metadata={"domain": validated},
        actor=actor_label(session),
    )
    return _view(certificates.describe(validated), job.id)
