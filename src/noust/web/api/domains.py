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

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.store import DomainKind, DomainRecord
from noust.core.teams import scoped_store
from noust.deployers.domains import (
    DomainChange,
    add_domain,
    check_dns,
    issue_certificate,
    list_domains,
    reduce_certificate,
    remove_domain,
    set_canonical,
    set_domain_port,
)
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
    """

    domain: str
    kind: str
    created_at: str | None = None
    port: int | None = None

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
    """

    domain: str
    kind: str = Field(default=DomainKind.ALIAS.value, description="alias or redirect")
    force: bool = False
    port: int | None = Field(default=None, ge=1, le=65535)
    dns: bool = True


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


def _entry(record: DomainRecord) -> AppDomain:
    """
    Translate a domain record to its response model.

    Args:
        record: The store's record.

    Returns:
        The response model.
    """
    return AppDomain(
        domain=record.domain, kind=record.kind, created_at=record.created_at, port=record.port
    )


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
        domains=[_entry(record) for record in change.domains],
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
        domains=[_entry(r) for r in list_domains(validated)],
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
    if change.tls:
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
