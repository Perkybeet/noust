# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The domains an application answers on, over the API.

A thin translation of :mod:`noust.deployers.domains` to HTTP - the rules, the
re-rendering and the certificate all live there, where ``noust domain`` reads
them too. Two things are decided here because they are about HTTP:

- **Certbot never runs on the request path.** Adding a domain records it and
  re-renders the site at once; when the site serves TLS, extending the
  certificate is queued as a job and its id returned, the same way
  ``POST /api/certs/{domain}`` queues issuance.
- **Removing a domain needs sudo mode**, like every other destructive action.

Mounted under ``/apps`` beside :mod:`noust.web.api.apps`, which owns no path
under ``/{domain}/domains``. :data:`dns_router` is the exception: a DNS check
against a domain that is not (yet) any application's, for the new-app wizard
to run before it creates one, so it is mounted separately under ``/domains``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.domain_certificates import check_mode
from noust.core.store import DomainKind, DomainRecord
from noust.core.teams import scoped_store
from noust.deployers.certificates import check_choice
from noust.deployers.domains import (
    DomainChange,
    add_domain,
    check_dns,
    disable_domain,
    enable_domain,
    issue_certificate,
    list_domains,
    reduce_certificate,
    release_certificate,
    remove_domain,
    set_canonical,
    set_domain_port,
)
from noust.deployers.name_certificates import NameBinding, bindings, set_certificate
from noust.deployers.temporary_domain import application_name, is_temporary, suggest
from noust.managers.site_policy import SitePolicyManager
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain, strict_server_name
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.pydantic_compat import iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)

#: DNS checks that do not need an application: mounted at ``/domains`` rather
#: than ``/apps/{domain}/domains``, so a bare domain can be checked before it
#: belongs to anything. Kept in this module, not a new one, because it shares
#: :class:`DnsCheckResponse` and :func:`noust.deployers.domains.check_dns` with
#: the app-scoped check below - one implementation of "resolve a domain and
#: compare it with this server", asked two ways.
dns_router = APIRouter(route_class=NoustErrorRoute)


class AppDomain(BaseModel):
    """
    One domain an application answers on.

    Attributes:
        domain: The domain.
        kind: ``primary`` (the domain the application was deployed as),
            ``alias`` (served the same) or ``redirect`` (sent permanently to
            the primary).
        created_at: When it was added, ISO 8601 with an explicit UTC offset.
        port: The port of the application this name is proxied to, when it is not
            the application's own.
        temporary: Whether it is a name made of this server's address (sslip.io), good
            for trying an application out and not for keeping.
        enabled: Whether it is served. A name that is switched off keeps its row and its
            port; the site and the certificate leave it out.
        certificate: ``shared`` when the application's certificate covers the name, ``own`` when
            it holds one of its own (a lineage named after it and a server block for it).
        served_by: The certificate that presents the name: a lineage certbot keeps (the
            application's, or the name's own) or an uploaded certificate. For a name whose own
            certificate is not issued yet, the shared one, which serves it meanwhile.
        certificate_pending: Whether the name asked for a certificate of its own that is not
            issued yet.
    """

    domain: str
    kind: str
    created_at: str | None = None
    port: int | None = None
    temporary: bool = False
    enabled: bool = True
    certificate: str = "shared"
    served_by: str | None = None
    certificate_pending: bool = False

    _iso_timestamps = iso_offset_validator("created_at")


class AppDomainList(BaseModel):
    """
    Every domain an application answers on.

    Attributes:
        app: The application's primary domain.
        domains: Its domains, primary first, then aliases, then redirects.
        canonical: ``www`` or ``apex`` when one spelling is served and the other
            redirects to it; empty when both are served as recorded.
    """

    app: str
    domains: list[AppDomain]
    canonical: str = ""


class AddAppDomainRequest(BaseModel):
    """
    A domain to add to an application.

    Attributes:
        domain: The bare domain name; ``*.example.com`` for a wildcard alias.
        kind: ``alias`` or ``redirect``.
        force: Add the name although another site of the web server already answers on it.
        port: Proxy this name to another port of the application than its own.
        dns: Point the name at this server in the DNS account that holds its zone.
        certificate: ``own`` to give the name a certificate of its own instead of covering it
            with the application's.
        challenge: How that certificate is proven, ``http-01`` or ``dns-01``; the application's
            own way by default.
        dns_provider: The DNS account that answers ``dns-01``.
    """

    domain: str
    kind: str = Field(default=DomainKind.ALIAS.value, description="alias or redirect")
    force: bool = False
    port: int | None = Field(default=None, ge=1, le=65535)
    dns: bool = True
    certificate: str | None = Field(default=None, description="shared (the default) or own")
    challenge: str | None = Field(default=None, description="http-01 or dns-01")
    dns_provider: str | None = None


class AppDomainChange(BaseModel):
    """
    An application's domains after a change.

    Attributes:
        app: The application's primary domain.
        domains: Its domains afterwards, primary first.
        tls: Whether its site serves TLS.
        adopted: Names the live site already answered on without a record,
            kept as aliases so the change did not drop them.
        certificate_job_id: The job extending the certificate to every
            domain, when the site serves TLS and a domain was added, or
            ordering it again without a domain that was removed.
        dns: What happened to the DNS records of the name: one entry per record
            (``hostname``, ``type``, ``outcome``, ``account``, and the words when it
            did not work). ``pending`` means an account could create it and is
            waiting to be told to.
        canonical: ``www`` or ``apex`` when one spelling is served.
    """

    app: str
    domains: list[AppDomain]
    tls: bool
    adopted: list[str] = Field(default_factory=list)
    certificate_job_id: str | None = None
    dns: list[dict[str, Any]] = Field(default_factory=list)
    canonical: str = ""


class DnsCheckResponse(BaseModel):
    """
    Whether a domain resolves to this server.

    Attributes:
        domain: The domain that was resolved.
        expected_addresses: This server's addresses.
        resolved_addresses: What the domain resolves to, A and AAAA.
        points_here: Whether every address it resolves to is this server's, or a CDN's
            that reaches it.
        verdict: ``here``, ``cdn``, ``elsewhere``, ``none`` or ``unknown`` (this server
            only sees private addresses, so it cannot say what its public one is).
        cdn: The CDN every address belongs to, when ``verdict`` is ``cdn``.
        resolver: The public resolver that answered; empty for the machine's own.
    """

    domain: str
    expected_addresses: list[str]
    resolved_addresses: list[str]
    points_here: bool
    verdict: str = ""
    cdn: str | None = None
    resolver: str = ""


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


def _entry(record: DomainRecord, binding: NameBinding | None = None) -> AppDomain:
    """
    Translate a domain record to its response model.

    Args:
        record: The store's record.
        binding: Which certificate serves the name, when it is known.

    Returns:
        The response model.
    """
    return AppDomain(
        domain=record.domain,
        kind=record.kind,
        created_at=record.created_at,
        port=record.port,
        temporary=is_temporary(record.domain),
        enabled=record.enabled,
        certificate=record.certificate,
        served_by=binding.served_by if binding is not None else None,
        certificate_pending=binding.pending if binding is not None else False,
    )


def _entries(app_domain: str, records: Sequence[DomainRecord]) -> list[AppDomain]:
    """
    Translate an application's domain records, saying which certificate serves each.

    Args:
        app_domain: The application's primary domain.
        records: Its rows.

    Returns:
        The response models, in the rows' order.
    """
    served = {binding.name: binding for binding in bindings(app_domain)}
    return [_entry(record, served.get(record.domain)) for record in records]


def _changed(change: DomainChange, job_id: str | None = None) -> AppDomainChange:
    """
    Translate the result of a change to its response model.

    Args:
        change: What the operation did.
        job_id: The certificate job queued after it, if any.

    Returns:
        The response model.
    """
    return AppDomainChange(
        app=change.app,
        domains=_entries(change.app, change.domains),
        tls=change.tls,
        adopted=list(change.adopted),
        certificate_job_id=job_id,
        dns=[dict(entry) for entry in change.dns],
        canonical=change.canonical,
    )


def certificate_job(app_domain: str, job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Extend an application's certificate to every one of its domains, as a job.

    Args:
        app_domain: The application's primary domain.
        job_context: Injected by the job manager.

    Returns:
        The application and the domains its certificate now covers.

    Raises:
        CertificateError: When certbot fails; the job fails with its output.
    """
    if job_context is not None:
        job_context.set_metadata("domain", app_domain)
        job_context.update("Extending the certificate to every domain", 20)
    change = issue_certificate(app_domain)
    if job_context is not None:
        job_context.update("Certificate covers every domain", 100)
    return {"domain": change.app, "domains": [record.domain for record in change.domains]}


@router.get("/{domain}/domains", response_model=AppDomainList)
def get_domains(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> AppDomainList:
    """
    List the domains an application answers on.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        Its domains, primary first.
    """
    validated = _require_app(domain)
    return AppDomainList(
        app=validated,
        domains=_entries(validated, list_domains(validated)),
        canonical=SitePolicyManager().get(validated).canonical,
    )


def reduce_certificate_job(
    app_domain: str, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Order an application's certificate again with only the names it answers on, as a job.

    Args:
        app_domain: The application's primary domain.
        job_context: Injected by the job manager.

    Returns:
        The application and whether a certificate was ordered.

    Raises:
        CertificateError: When certbot fails; the job fails with its output and the
            certificate that served keeps serving.
    """
    if job_context is not None:
        job_context.set_metadata("domain", app_domain)
        job_context.update("Removing the domain from the certificate", 20)
    change = reduce_certificate(app_domain)
    if job_context is not None:
        job_context.update("Certificate covers only the remaining domains", 100)
    return {"domain": change.app, "reduced": change.certificate_reduced}


@router.post("/{domain}/domains", response_model=AppDomainChange, status_code=201)
def post_domain(
    domain: str,
    data: AddAppDomainRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> AppDomainChange:
    """
    Make an application answer on another domain.

    The domain is recorded and the site re-rendered and reloaded before this
    returns. When the site serves TLS, extending the certificate to the new
    domain is queued as a job; follow ``certificate_job_id``.

    Args:
        domain: The application's primary domain.
        data: The domain to add and its kind.
        session: Authenticated session, injected.

    Returns:
        The application's domains afterwards.
    """
    validated = _require_app(domain)
    name = strict_server_name(data.domain)
    change = add_domain(
        validated,
        name,
        data.kind,
        issue_cert=False,
        force=data.force,
        dns=data.dns,
        port=data.port,
        certificate=data.certificate or "shared",
        challenge=data.challenge,
        dns_provider=data.dns_provider,
    )

    job_id: str | None = None
    if change.tls:
        job = get_job_manager().create_job(
            job_type=JobType.CERT_CREATE,
            name=f"SSL for {validated}",
            description=f"Extending the certificate of {validated} to {name}",
            func=certificate_job,
            kwargs={"app_domain": validated},
            metadata={"domain": validated},
            actor=actor_label(session),
        )
        job_id = job.id
    return _changed(change, job_id)


@router.delete("/{domain}/domains/{name}", response_model=AppDomainChange)
def delete_domain(
    domain: str,
    name: str,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> AppDomainChange:
    """
    Stop an application answering on a domain.

    The site is re-rendered and reloaded before this returns. The certificate
    is left alone and nothing is revoked.

    Args:
        domain: The application's primary domain.
        name: The alias or redirect to remove.
        session: Authenticated and elevated session, injected.

    Returns:
        The application's domains afterwards.
    """
    validated = _require_app(domain)
    change = remove_domain(validated, strict_server_name(name), reduce_cert=False)
    job_id: str | None = None
    if change.lineage_pending:
        job = get_job_manager().create_job(
            job_type=JobType.CERT_CREATE,
            name=f"SSL for {validated}",
            description=f"Deleting the certificate {name} held on its own",
            func=release_certificate_job,
            kwargs={"app_domain": validated, "name": change.lineage_pending},
            metadata={"domain": validated},
            actor=actor_label(session),
        )
        job_id = job.id
    elif change.tls:
        job = get_job_manager().create_job(
            job_type=JobType.CERT_CREATE,
            name=f"SSL for {validated}",
            description=f"Ordering the certificate of {validated} again without {name}",
            func=reduce_certificate_job,
            kwargs={"app_domain": validated},
            metadata={"domain": validated},
            actor=actor_label(session),
        )
        job_id = job.id
    return _changed(change, job_id)


class CanonicalRequest(BaseModel):
    """
    Which spelling of the application's name is served.

    Attributes:
        canonical: ``www`` to serve ``www.<name>`` and redirect the bare name,
            ``apex`` for the other way, ``none`` to serve both as recorded.
    """

    canonical: str = Field(description="www, apex or none")


class DomainEnabledRequest(BaseModel):
    """
    Whether a name is served.

    Attributes:
        enabled: False lets the name rest (the site and the certificate leave it out, the
            row and its DNS record stay); True serves it again.
    """

    enabled: bool


class DomainPortRequest(BaseModel):
    """
    The port of the application a name is proxied to.

    Attributes:
        port: The port, 1 to 65535; null for the application's own.
    """

    port: int | None = Field(default=None, ge=1, le=65535)


@router.put("/{domain}/domains/canonical", response_model=AppDomainChange)
def put_canonical(
    domain: str,
    data: CanonicalRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> AppDomainChange:
    """
    Choose whether ``www`` or the bare name is the one served; the other redirects to it.

    Both names are recorded as domains of the application so the certificate covers
    them; when the site serves TLS, extending it is queued as a job.

    Args:
        domain: The application's primary domain.
        data: The choice.
        session: Authenticated session, injected.

    Returns:
        The application's domains afterwards.

    Raises:
        ValidationError: The name has no www spelling, or the web server refuses the site.
    """
    validated = _require_app(domain)
    change = set_canonical(validated, data.canonical, issue_cert=False)
    job_id: str | None = None
    if change.tls and data.canonical not in ("", "none"):
        job = get_job_manager().create_job(
            job_type=JobType.CERT_CREATE,
            name=f"SSL for {validated}",
            description=f"Extending the certificate of {validated} to its www pair",
            func=certificate_job,
            kwargs={"app_domain": validated},
            metadata={"domain": validated},
            actor=actor_label(session),
        )
        job_id = job.id
    return _changed(change, job_id)


@router.put("/{domain}/domains/{name}/port", response_model=AppDomainChange)
def put_domain_port(
    domain: str,
    name: str,
    data: DomainPortRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> AppDomainChange:
    """
    Choose which port of the application an alias is proxied to.

    Args:
        domain: The application's primary domain.
        name: The alias.
        data: The port, or null for the application's own.
        session: Authenticated session, injected.

    Returns:
        The application's domains afterwards.
    """
    validated = _require_app(domain)
    return _changed(set_domain_port(validated, strict_server_name(name), data.port))


@router.put("/{domain}/domains/{name}/enabled", response_model=AppDomainChange)
def put_domain_enabled(
    domain: str,
    name: str,
    data: DomainEnabledRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> AppDomainChange:
    """
    Switch a name off without deleting it, or serve it again.

    The site is re-rendered and reloaded before this returns. When the site serves TLS,
    ordering the certificate again (without the name, or with it) is queued as a job.

    Args:
        domain: The application's primary domain.
        name: The alias or redirect.
        data: Whether it is served.
        session: Authenticated session, injected.

    Returns:
        The application's domains afterwards.

    Raises:
        DomainError: The name is the primary or the canonical pair, or not the application's.
    """
    validated = _require_app(domain)
    bare = strict_server_name(name)
    if data.enabled:
        change = enable_domain(validated, bare, issue_cert=False)
        job_func, wording = certificate_job, f"Extending the certificate of {validated} to {bare}"
    else:
        change = disable_domain(validated, bare, reduce_cert=False)
        job_func = reduce_certificate_job
        wording = f"Ordering the certificate of {validated} again without {bare}"
    job_id: str | None = None
    if change.tls:
        job = get_job_manager().create_job(
            job_type=JobType.CERT_CREATE,
            name=f"SSL for {validated}",
            description=wording,
            func=job_func,
            kwargs={"app_domain": validated},
            metadata={"domain": validated},
            actor=actor_label(session),
        )
        job_id = job.id
    return _changed(change, job_id)


def release_certificate_job(
    app_domain: str, name: str, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Delete the certificate a removed name held on its own, as a job.

    Args:
        app_domain: The application's primary domain.
        name: The name that was removed.
        job_context: Injected by the job manager.

    Returns:
        The application, the name and whether a lineage was deleted.

    Raises:
        CertificateError: When certbot refuses; the job fails with its output.
    """
    if job_context is not None:
        job_context.set_metadata("domain", app_domain)
        job_context.update(f"Deleting the certificate of {name}", 20)
    removed = release_certificate(app_domain, name)
    if job_context is not None:
        job_context.update("The certificate is deleted", 100)
    return {"domain": app_domain, "name": name, "removed": removed}


def name_certificate_job(
    app_domain: str,
    name: str,
    certificate: str,
    challenge: str | None,
    dns_provider: str | None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Give a name a certificate of its own, or back to the shared one, as a job.

    Args:
        app_domain: The application's primary domain.
        name: The alias or redirect.
        certificate: ``own`` or ``shared``.
        challenge: How the name's own certificate is proven.
        dns_provider: The DNS account that answers ``dns-01``.
        job_context: Injected by the job manager.

    Returns:
        What changed: the name, its certificate afterwards, whether one was ordered and
        what did not work after the site was already right.

    Raises:
        CertificateError: certbot refused; the job fails with its output and the certificate
            that served keeps serving.
    """
    if job_context is not None:
        job_context.set_metadata("domain", app_domain)
        job_context.update(
            "Ordering the certificate of its own" if certificate == "own" else "Giving it back", 20
        )
    change = set_certificate(
        app_domain, name, certificate, challenge=challenge, dns_provider=dns_provider
    )
    if job_context is not None:
        job_context.update("The certificate is in place", 100)
    return {
        "domain": app_domain,
        "name": change.name,
        "certificate": change.certificate,
        "changed": change.changed,
        "ordered": change.ordered,
        "warnings": list(change.warnings),
    }


class DomainCertificateRequest(BaseModel):
    """
    Whether a name holds a certificate of its own.

    Attributes:
        certificate: ``own`` for a certificate kept under the name, ordered for it alone;
            ``shared`` to cover it with the application's again.
        challenge: With ``own``, how it is proven: ``http-01`` or ``dns-01``. What it already had,
            else the application's own way.
        dns_provider: The DNS account that answers ``dns-01``.
    """

    certificate: str = Field(description="own or shared")
    challenge: str | None = Field(default=None, description="http-01 or dns-01")
    dns_provider: str | None = None


@router.put("/{domain}/domains/{name}/certificate", response_model=AppDomainChange)
def put_domain_certificate(
    domain: str,
    name: str,
    data: DomainCertificateRequest,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> AppDomainChange:
    """
    Give a name a certificate of its own, or back to the application's.

    Certbot never runs on the request path: the change is queued as a job and its id returned
    as ``certificate_job_id``. The certificate is ordered while the site goes on serving the
    name as it did, and only then does the site get a server block for it; if the order fails
    the job fails with certbot's own words and nothing has changed.

    Args:
        domain: The application's primary domain.
        name: The alias or redirect.
        data: Whether the name holds a certificate of its own, and how it is proven.
        session: Authenticated session, injected.

    Returns:
        The application's domains as they are now, and the job.

    Raises:
        ValidationError: The mode or the way of proving it is not acceptable (400).
        DomainError: The name is not the application's, or is its primary (400).
    """
    validated = _require_app(domain)
    bare = strict_server_name(name)
    check_mode(data.certificate)
    if data.challenge is not None:
        check_choice(data.challenge, data.dns_provider)
    job = get_job_manager().create_job(
        job_type=JobType.CERT_CREATE,
        name=f"SSL for {validated}",
        description=(
            f"Ordering a certificate of its own for {bare}"
            if data.certificate == "own"
            else f"Covering {bare} with the certificate of {validated} again"
        ),
        func=name_certificate_job,
        kwargs={
            "app_domain": validated,
            "name": bare,
            "certificate": data.certificate,
            "challenge": data.challenge,
            "dns_provider": data.dns_provider,
        },
        metadata={"domain": validated},
        actor=actor_label(session),
    )
    app = scoped_store().get_app(validated)
    return AppDomainChange(
        app=validated,
        domains=_entries(validated, list_domains(validated)),
        tls=bool(app is not None and app.ssl_enabled),
        certificate_job_id=job.id,
        canonical=SitePolicyManager().get(validated).canonical,
    )


@router.get("/{domain}/domains/{name}/dns", response_model=DnsCheckResponse)
def get_domain_dns(
    domain: str,
    name: str,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> DnsCheckResponse:
    """
    Check whether a domain resolves to this server.

    Meant for before a domain is added, so the name does not have to be one
    of the application's yet.

    Args:
        domain: The application's primary domain.
        name: The domain to resolve.
        session: Authenticated session, injected.

    Returns:
        What it resolves to, compared with this server's addresses.
    """
    _require_app(domain)
    check = check_dns(strict_domain(name))
    return DnsCheckResponse(
        domain=check.domain,
        expected_addresses=list(check.expected_addresses),
        resolved_addresses=list(check.resolved_addresses),
        points_here=check.points_here,
        verdict=check.verdict,
        cdn=check.cdn,
        resolver=check.resolver,
    )


class TemporaryNameResponse(BaseModel):
    """
    A name for a server that has none, made of its public address.

    Attributes:
        address: The public address.
        domain: The name that resolves to it.
        service: The service that answers it.
        declared: Whether the address was declared (``dns.public_addresses``) rather than
            read from a network interface.
    """

    address: str
    domain: str
    service: str
    declared: bool = False


@dns_router.get("/temporary", response_model=TemporaryNameResponse)
def get_temporary_name(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    prefix: Annotated[
        str | None,
        Query(
            max_length=120,
            description="What the application is called, for a name of its own "
            "(shop.203-0-113-10.sslip.io). Numbered when another application has it.",
        ),
    ] = None,
    source: Annotated[
        str | None,
        Query(
            max_length=2048,
            description="What it is deployed from, when it has no name yet: the name is read "
            "out of the repository, archive or directory. A prefix wins over it.",
        ),
    ] = None,
    image: Annotated[
        str | None,
        Query(
            max_length=512,
            description="The registry image it is deployed from; read like a source.",
        ),
    ] = None,
) -> TemporaryNameResponse:
    """
    Work out a temporary name for this server, to add to an application like any other.

    It is made of the server's public address (``203-0-113-10.sslip.io``), which sslip.io
    answers with that address, so the name resolves here and a certificate can be ordered.
    With a prefix it is the name of one application (``shop.203-0-113-10.sslip.io``), free
    among the names and the application names of the server. Nothing is added: the caller
    adds it with ``POST /api/apps/{domain}/domains``, or deploys an application under it.

    Args:
        session: Authenticated session, injected.
        prefix: What the application is called.
        source: What it is deployed from, read for its name when no prefix is given.
        image: The registry image it is deployed from, read the same way.

    Returns:
        The name and the address it is made of.

    Raises:
        DomainError: This server has no public address (400, saying how to declare one).
    """
    found = suggest(application_name(prefix=prefix, source=source, image=image))
    return TemporaryNameResponse(
        address=found.address, domain=found.domain, service=found.service, declared=found.declared
    )


@dns_router.get("/dns", response_model=DnsCheckResponse)
def get_bare_dns(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    name: Annotated[str, Query(description="The domain to resolve")],
) -> DnsCheckResponse:
    """
    Check whether a domain resolves to this server, before it is any application's.

    The same check :func:`get_domain_dns` runs for a domain already added to
    an application, through the same :func:`noust.deployers.domains.check_dns`
    - the new-app wizard needs an answer before anything is created, and there
    is no application yet to hang the path off.

    Args:
        session: Authenticated session, injected.
        name: The domain to resolve.

    Returns:
        What it resolves to, compared with this server's addresses.
    """
    check = check_dns(strict_domain(name))
    return DnsCheckResponse(
        domain=check.domain,
        expected_addresses=list(check.expected_addresses),
        resolved_addresses=list(check.resolved_addresses),
        points_here=check.points_here,
        verdict=check.verdict,
        cdn=check.cdn,
        resolver=check.resolver,
    )
