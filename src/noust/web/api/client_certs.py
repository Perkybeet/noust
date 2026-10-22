# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Client certificates over the API: who may open an application's site by presenting one.

A thin translation of :mod:`noust.deployers.access`, which ``noust app policy clients`` calls too.
Turning the requirement on is the policy's own field (``client_certs`` in
``PUT /api/apps/{domain}/policy``); what is here is what the requirement needs around it: making a
certificate, listing them and revoking one.

Making one returns the private key, once: the bundle a browser imports, its password and the PEM
pair. It is sudo mode for that reason, and nothing returned is kept. Revoking one renders the site
again, so the web server refuses its serial from then on.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.client_cert_store import ClientCertificate
from noust.core.teams import scoped_store
from noust.deployers import access
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain

router = APIRouter(route_class=NoustErrorRoute)


class ClientCertificateOut(BaseModel):
    """
    A certificate signed for the clients of a site.

    Attributes:
        name: What it is for.
        serial: Its serial, which names it everywhere else.
        fingerprint: Its SHA-256 fingerprint.
        not_before: When it begins to be valid, ISO 8601 UTC.
        not_after: When it stops, ISO 8601 UTC.
        issued_by: Who asked for it.
        issued_at: When, ISO 8601 UTC.
        revoked: The site refuses it.
        revoked_at: When it was revoked.
        revoked_by: Who revoked it.
    """

    name: str
    serial: str
    fingerprint: str
    not_before: str
    not_after: str
    issued_by: str | None = None
    issued_at: str
    revoked: bool
    revoked_at: str | None = None
    revoked_by: str | None = None


class ClientCertificatesOut(BaseModel):
    """
    An application's client certificates.

    Attributes:
        enabled: The site answers only a visitor with one.
        authority: Its authority exists (it is made with the first certificate).
        certificates: The certificates, newest first, revoked ones included.
    """

    enabled: bool
    authority: bool
    certificates: list[ClientCertificateOut]


class IssueClientCertificateRequest(BaseModel):
    """
    A certificate to make.

    Attributes:
        name: What it is for (``ana-laptop``): letters, digits, ``.``, ``_``, ``@`` and ``-``.
        days: How long it is valid, 1 to 825.
    """

    name: str = Field(min_length=1, max_length=64)
    days: int = Field(default=365, ge=1, le=825)


class IssuedClientCertificateOut(BaseModel):
    """
    A certificate just made, with what its holder needs. Shown once; Noust keeps none of the
    private parts.

    Attributes:
        certificate: The record.
        bundle: The PKCS#12 file a browser or a keychain imports, base64.
        password: What opens the bundle.
        certificate_pem: The certificate, PEM.
        key_pem: The private key, PEM.
        ca_pem: The authority's certificate, PEM.
    """

    certificate: ClientCertificateOut
    bundle: str
    password: str
    certificate_pem: str
    key_pem: str
    ca_pem: str


class ClientAuthorityOut(BaseModel):
    """
    An application's client authority.

    Attributes:
        certificate: Its certificate, PEM; None before the first certificate is made.
    """

    certificate: str | None = None


def _out(certificate: ClientCertificate) -> ClientCertificateOut:
    """
    Args:
        certificate: A record.

    Returns:
        The record as the API shows it.
    """
    return ClientCertificateOut(
        name=certificate.name,
        serial=certificate.serial,
        fingerprint=certificate.fingerprint,
        not_before=certificate.not_before,
        not_after=certificate.not_after,
        issued_by=certificate.issued_by,
        issued_at=certificate.issued_at,
        revoked=certificate.revoked,
        revoked_at=certificate.revoked_at,
        revoked_by=certificate.revoked_by,
    )


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


@router.get("/{domain}/client-certs", response_model=ClientCertificatesOut)
def list_client_certificates(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> ClientCertificatesOut:
    """
    List an application's client certificates.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        Whether the site asks for one, and the certificates made for it.
    """
    validated = _require_app(domain)
    return ClientCertificatesOut(
        enabled=access.describe(validated).policy.client_certs,
        authority=access.client_authority(validated) is not None,
        certificates=[_out(certificate) for certificate in access.client_certificates(validated)],
    )


@router.post("/{domain}/client-certs", response_model=IssuedClientCertificateOut, status_code=201)
def issue_client_certificate(
    domain: str,
    body: IssueClientCertificateRequest,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> IssuedClientCertificateOut:
    """
    Make a certificate for a client of the site.

    Needs sudo mode: the private key is in the answer, once.

    Args:
        domain: The application's primary domain.
        body: What it is for, and for how long.
        session: Authenticated and elevated session, injected.

    Returns:
        The certificate, the bundle and its password, and the PEM pair.

    Raises:
        ValidationError: The name or the validity is not acceptable, or the name is taken (400).
    """
    issued = access.issue_client_certificate(_require_app(domain), body.name, days=body.days)
    return IssuedClientCertificateOut(
        certificate=_out(issued.record),
        bundle=issued.bundle_base64(),
        password=issued.password,
        certificate_pem=issued.certificate,
        key_pem=issued.key,
        ca_pem=issued.ca,
    )


@router.delete("/{domain}/client-certs/{serial}", status_code=204)
def revoke_client_certificate(
    domain: str,
    serial: str,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
    accept_lockout: bool = False,
) -> None:
    """
    Revoke a certificate: the site refuses it from now on.

    Args:
        domain: The application's primary domain.
        serial: The certificate's serial.
        session: Authenticated and elevated session, injected.
        accept_lockout: Go ahead although it is the last valid one of a site that asks for them.

    Raises:
        ValidationError: There is no such certificate, or it is the last valid one (400).
    """
    access.revoke_client_certificate(_require_app(domain), serial, accept_lockout=accept_lockout)


@router.get("/{domain}/client-certs/ca", response_model=ClientAuthorityOut)
def get_client_authority(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> ClientAuthorityOut:
    """
    Show the certificate of the application's client authority.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        Its certificate, which is public; None before the first certificate is made.
    """
    return ClientAuthorityOut(certificate=access.client_authority(_require_app(domain)))
