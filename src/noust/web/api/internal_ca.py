# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The server's own certificate authority over the API: names that only resolve inside a private
network.

A thin translation of :mod:`noust.managers.internal_ca` (the authority and what it signs),
:mod:`noust.managers.ca_trust` (how to trust it on each device) and
:mod:`noust.deployers.internal_certificates`, which ``noust cert ca`` calls too. Mounted at
``/certs/ca`` *before* the certificates router, because ``/certs/{domain}`` would read ``ca`` as a
domain.

Making and deleting the authority, and trusting it on this server, ask for sudo mode: a root of
trust is a decision about every device that installs it. The root certificate is public and can
be downloaded by anyone who can read certificates; the key never leaves the server and is in no
response. An application chooses to be served by it through ``PUT /api/apps/{domain}/certificate``.
"""

from __future__ import annotations

import socket
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from noust.deployers import internal_certificates
from noust.managers import ca_trust
from noust.managers.internal_ca import (
    DEFAULT_DNS,
    DEFAULT_NETWORKS,
    LEAF_DAYS,
    MAX_LEAF_DAYS,
    CaStatus,
    InternalCA,
    IssuedName,
    NoAuthorityError,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated

router = APIRouter(route_class=NoustErrorRoute)


class AuthorityModel(BaseModel):
    """
    The authority. Never its key.

    Attributes:
        subject: What it is called on a device.
        fingerprint: The root's SHA-256, which a device compares before it trusts it.
        not_before: When it begins to be valid, ISO 8601 UTC.
        not_after: When it expires.
        days_left: Whole days until then.
        permitted_dns: The suffixes it can sign for.
        permitted_networks: The address ranges it can sign for.
        issued: How many certificates it signed that are kept.
        warnings: Sentences about its state (it expires within six months).
    """

    subject: str
    fingerprint: str
    not_before: str
    not_after: str
    days_left: int
    permitted_dns: list[str]
    permitted_networks: list[str]
    issued: int = 0
    warnings: list[str] = Field(default_factory=list)


class IssuedModel(BaseModel):
    """
    A certificate the authority signed. Never its key.

    Attributes:
        name: What it is kept under (``internal-wiki``), also its upload's name.
        names: The DNS names on it.
        not_after: When it stops being valid, ISO 8601 UTC.
        days_left: Whole days until then.
        fingerprint: Its SHA-256.
        apps: The applications that serve it.
        expiring: It is within its renewal window, and the network maintenance will sign it
            again.
    """

    name: str
    names: list[str]
    not_after: str
    days_left: int
    fingerprint: str
    apps: list[str] = Field(default_factory=list)
    expiring: bool = False


class OverviewModel(BaseModel):
    """
    The authority, what it signed, and whether this server trusts it.

    Attributes:
        authority: The authority, or None while this server has none.
        issued: The certificates it signed that are kept, soonest to expire first.
        root_pem: The root certificate (public: what a device installs), or None while there is
            no authority. The console offers it as a download in both forms.
        trusted_here: This server's own store holds the root.
        system_store: Which system's store that is (``Debian, Ubuntu and Alpine``), or None when
            Noust does not know how to update this system's.
        default_dns: The suffixes a new authority signs for unless told otherwise.
        default_networks: The address ranges, the same.
    """

    authority: AuthorityModel | None = None
    issued: list[IssuedModel] = Field(default_factory=list)
    root_pem: str | None = None
    trusted_here: bool = False
    system_store: str | None = None
    default_dns: list[str]
    default_networks: list[str]


class InitRequest(BaseModel):
    """
    The authority to make.

    Attributes:
        permit_dns: More suffixes to sign for, besides the private defaults.
        permit_networks: More private address ranges.
        only: Sign for exactly these and not for the defaults.
        days: How long the root is valid.
        name: What it is called on a device.
        trust_here: Also trust it on this server.
    """

    permit_dns: list[str] = Field(default_factory=list, max_length=50)
    permit_networks: list[str] = Field(default_factory=list, max_length=50)
    only: bool = False
    days: int = Field(default=3650, ge=30, le=7300)
    name: str | None = Field(default=None, max_length=64)
    trust_here: bool = False


class IssueRequest(BaseModel):
    """
    A certificate to sign.

    Attributes:
        name: What it is for (``wiki``); kept as ``internal-wiki``.
        names: DNS names (a wildcard is one label) and addresses, at least one DNS name.
        days: How long it is valid.
    """

    name: str = Field(max_length=64)
    names: list[str] = Field(min_length=1, max_length=20)
    days: int = Field(default=LEAF_DAYS, ge=1, le=MAX_LEAF_DAYS)


class GuideModel(BaseModel):
    """
    How to trust the authority on one kind of device.

    Attributes:
        id: ``debian``, ``macos``, ``windows``...
        title: What the person calls it.
        file: The form of the root it wants: ``pem`` (``ca.crt``) or ``der`` (``ca.cer``).
        steps: What to do, in order; a step that is a command starts with ``$ ``.
        note: What is worth knowing.
    """

    id: str
    title: str
    file: str
    steps: list[str]
    note: str = ""


class TrustModel(BaseModel):
    """
    The root's fingerprint and the guides.

    Attributes:
        fingerprint: What the device compares before it trusts the file.
        guides: One per kind of device.
    """

    fingerprint: str
    guides: list[GuideModel]


def _authority(status: CaStatus) -> AuthorityModel:
    """
    Args:
        status: The authority.

    Returns:
        It as the API says it.
    """
    return AuthorityModel(**status.to_dict())


def _issued(item: IssuedName) -> IssuedModel:
    """
    Args:
        item: A certificate it signed.

    Returns:
        It as the API says it.
    """
    return IssuedModel(**item.to_dict())


def _require(authority: InternalCA) -> CaStatus:
    """
    Args:
        authority: The authority.

    Returns:
        Its state.

    Raises:
        NoAuthorityError: This server has none (404).
    """
    status = authority.status()
    if status is None:
        raise NoAuthorityError(
            "This server has no internal certificate authority",
            details="Make it: POST /api/certs/ca, or noust cert ca init",
        )
    return status


@router.get("", response_model=OverviewModel)
def get_overview(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> OverviewModel:
    """
    Show the authority, the certificates it signed, and whether this server trusts it.

    Args:
        session: Authenticated session, injected.

    Returns:
        The overview; ``authority`` is null while there is none.
    """
    authority = InternalCA()
    status = authority.status()
    trust = ca_trust.ServerTrust()
    store = trust.store()
    root = authority.root_pem() if status else None
    return OverviewModel(
        authority=_authority(status) if status else None,
        issued=[_issued(item) for item in authority.issued()] if status else [],
        root_pem=root,
        trusted_here=root is not None and trust.installed(root),
        system_store=store.title if store else None,
        default_dns=list(DEFAULT_DNS),
        default_networks=list(DEFAULT_NETWORKS),
    )


@router.post("", response_model=AuthorityModel, status_code=201)
def make_authority(
    data: InitRequest, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> AuthorityModel:
    """
    Make this server's authority. Needs sudo mode.

    It can only ever sign for the suffixes and private ranges it is made with (a critical
    extension of its certificate that every device enforces). It is made once and never again by
    itself: a new root has to be trusted again on every device.

    Args:
        data: What it may sign for, how long it is valid, and its name.
        session: Authenticated and elevated session, injected.

    Returns:
        The authority.

    Raises:
        ValidationError: A suffix or range is not acceptable (400, naming the field).
        CertificateError: There is one already.
    """
    authority = InternalCA()
    status = authority.init(
        permit_dns=data.permit_dns,
        permit_networks=data.permit_networks,
        only=data.only,
        days=data.days,
        name=data.name,
    )
    if data.trust_here:
        ca_trust.ServerTrust().install(authority.root_pem())
    return _authority(status)


@router.delete("", status_code=204)
def remove_authority(
    session: Annotated[dict[str, Any], Depends(require_elevated)], force: bool = False
) -> None:
    """
    Delete the authority and its key. Needs sudo mode.

    Args:
        session: Authenticated and elevated session, injected.
        force: Go on although certificates it signed are still kept.

    Raises:
        ValidationError: Certificates it signed are kept and ``force`` is false (400).
    """
    InternalCA().remove(force=force)


class RootCertificate(Response):
    """The authority's public certificate as a file; the schema says which forms there are."""

    media_type = "application/x-pem-file"


@router.get(
    "/root",
    response_class=RootCertificate,
    responses={
        200: {
            "description": "The authority's certificate, public: PEM (ca.crt) or DER (ca.cer).",
            "content": {
                "application/x-pem-file": {"schema": {"type": "string"}},
                "application/x-x509-ca-cert": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
def get_root(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    format: Literal["pem", "der"] = "pem",
) -> Response:
    """
    Download the authority's certificate: public, what a device installs.

    Args:
        session: Authenticated session, injected.
        format: ``pem`` (``ca.crt``) or ``der`` (``ca.cer``: Windows, Android, Java).

    Returns:
        The file. The key is not one.

    Raises:
        NoAuthorityError: This server has no authority (404).
    """
    authority = InternalCA()
    _require(authority)
    if format == "der":
        return Response(
            authority.root_der(),
            media_type="application/x-x509-ca-cert",
            headers={"Content-Disposition": 'attachment; filename="ca.cer"'},
        )
    return Response(
        authority.root_pem(),
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="ca.crt"'},
    )


@router.get("/trust", response_model=TrustModel)
def get_trust(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> TrustModel:
    """
    Say how to trust the authority on each kind of device, with the fingerprint to compare.

    Nothing is changed on any device: the steps are text to follow on it.

    Args:
        session: Authenticated session, injected.

    Returns:
        The fingerprint and a guide per kind of device.

    Raises:
        NoAuthorityError: This server has no authority (404).
    """
    status = _require(InternalCA())
    guides = ca_trust.guides(status.fingerprint, socket.gethostname() or "server")
    return TrustModel(
        fingerprint=status.fingerprint, guides=[GuideModel(**item.to_dict()) for item in guides]
    )


@router.post("/trust-here", response_model=OverviewModel)
def trust_here(
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> OverviewModel:
    """
    Trust the authority on this server: the programs that run here reach each other by the names
    it signs. Needs sudo mode.

    Args:
        session: Authenticated and elevated session, injected.

    Returns:
        The overview afterwards.

    Raises:
        CertificateError: There is no authority, or this system has no store Noust can update.
    """
    authority = InternalCA()
    _require(authority)
    ca_trust.ServerTrust().install(authority.root_pem())
    return get_overview(session)


@router.delete("/trust-here", response_model=OverviewModel)
def untrust_here(
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> OverviewModel:
    """
    Stop trusting the authority on this server. Needs sudo mode.

    Args:
        session: Authenticated and elevated session, injected.

    Returns:
        The overview afterwards.
    """
    ca_trust.ServerTrust().remove()
    return get_overview(session)


@router.post("/issued", response_model=IssuedModel, status_code=201)
def issue_certificate(
    data: IssueRequest, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> IssuedModel:
    """
    Sign a certificate and keep it as an uploaded one named ``internal-<name>``.

    Args:
        data: What it is for, the names on it, and how long it is valid.
        session: Authenticated session, injected.

    Returns:
        The certificate. Never its key.

    Raises:
        ValidationError: A name is outside what the authority signs for (400).
        CertificateError: There is no authority, or it has expired.
    """
    authority = InternalCA()
    kept = authority.issue(data.name, data.names, days=data.days)
    return _issued(next(item for item in authority.issued() if item.name == kept.name))


@router.post("/issued/{name}/renew", response_model=IssuedModel)
def renew_certificate(
    name: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> IssuedModel:
    """
    Sign a certificate again, for the same names and as long, and reload the sites that serve it.

    Args:
        name: The certificate (``internal-wiki``).
        session: Authenticated session, injected.

    Returns:
        The certificate afterwards.

    Raises:
        CertificateError: There is no authority, or the certificate was not signed by it.
    """
    internal_certificates.renew(name)
    authority = InternalCA()
    return _issued(next(item for item in authority.issued() if item.name == name))
