# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/ens``: the compliance check, its evidence report, the profile and the inventory.

A thin translation of :mod:`noust.core.ens` for the console's Settings >
Security > Compliance view, like ``noust ens`` at the terminal: the same
checks, the same report, the same inventory. Reading the check or the report
is a sensitive read (it lists accounts, tokens and exposure), recorded as
``compliance.read``; permissions are in
:mod:`noust.web.permissions.routes_ens` (``compliance.read``: ``admin``,
``security`` and ``auditor``).

The check runs in a worker thread (FastAPI runs a plain ``def`` endpoint
there). By default it reuses the last hardening report rather than probing
the server again (``refresh=true`` probes); the console's exposure is read
from the running console itself, which knows it exactly.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from noust.core.audit import record
from noust.core.ens import checks, inventory, profile, report
from noust.core.ens.incident import lockdown_state
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict, Depends(get_current_session)]


class EnsFinding(BaseModel):
    """
    One check's result.

    Attributes:
        id: Stable id, such as ``ENS-ACC-02``.
        title: What is checked.
        status: ``ok``, ``warning``, ``fail`` or ``n/a``.
        measures: RD 311/2022 measures it answers, such as ``op.acc.6.r2``.
        summary: What was found, in a sentence.
        evidence: Values and paths, verbatim; never a secret.
        remediation: What to do; empty when nothing is.
    """

    id: str
    title: str
    status: str
    measures: list[str]
    summary: str
    evidence: list[str] = Field(default_factory=list)
    remediation: str = ""


class EnsCheckResponse(BaseModel):
    """
    The result of the compliance check.

    Attributes:
        profile: ``standard`` or ``ens-medium``.
        checked_at: When, ISO 8601 UTC.
        host: The server's name.
        version: The Noust it runs.
        verdict: ``ok``, ``warning`` or ``fail``: the worst finding.
        counts: Findings per status.
        findings: One per check, in catalog order.
        indicators: The op.mon.2 figures.
        errors: Areas that could not be read, verbatim.
    """

    profile: str
    checked_at: str
    host: str
    version: str
    verdict: str
    counts: dict[str, int]
    findings: list[EnsFinding]
    indicators: dict[str, Any] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)


class EnsBaselineItem(BaseModel):
    """
    One value the profile fixes.

    Attributes:
        key: The setting or behaviour.
        description: What it controls.
        standard_value: Outside the profile.
        ens_value: Under ``ens-medium``.
        measures: The measures it answers.
    """

    key: str
    description: str
    standard_value: str
    ens_value: str
    measures: list[str]


class EnsProfileResponse(BaseModel):
    """
    The profile in force and what ``ens-medium`` fixes.

    Attributes:
        profile: ``standard`` or ``ens-medium``.
        baseline: Every value the profile fixes.
    """

    profile: str
    baseline: list[EnsBaselineItem]


class EnsReportResponse(BaseModel):
    """
    The evidence report.

    Attributes:
        sha256: SHA-256 of the report (canonical JSON without this field),
            also recorded in the audit log.
        report: The report: findings grouped by measure, indicators,
            baseline, accounts, tokens, audit, backups, hardening, console,
            inventory, fleet.
    """

    sha256: str
    report: dict[str, Any]


class InventoryEntryModel(BaseModel):
    """
    One application in the inventory.

    Attributes:
        domain: The application.
        app_type: Its type.
        status: Its last known status.
        source: Where its code comes from, without credentials.
        owner: Who answers for it.
        criticality: ``low``, ``medium`` or ``high``.
        classification: ``public``, ``internal``, ``restricted`` or ``confidential``.
        notes: Anything else.
        updated_at: When the fields last changed.
        updated_by: Who changed them.
        complete: Whether it has an owner and a criticality.
    """

    domain: str
    app_type: str = ""
    status: str = ""
    source: str = ""
    owner: str | None = None
    criticality: str | None = None
    classification: str | None = None
    notes: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None
    complete: bool = False


class InventoryResponse(BaseModel):
    """
    The inventory.

    Attributes:
        applications: Every application, by domain.
        criticalities: The criticality values accepted.
        classifications: The classification values accepted.
    """

    applications: list[InventoryEntryModel]
    criticalities: list[str]
    classifications: list[str]


class InventoryUpdate(BaseModel):
    """
    A change to an application's inventory fields. An absent field is kept; an
    empty string clears it.

    Attributes:
        owner: Who answers for it.
        criticality: ``low``, ``medium`` or ``high``.
        classification: ``public``, ``internal``, ``restricted`` or ``confidential``.
        notes: Anything else.
    """

    owner: str | None = None
    criticality: str | None = None
    classification: str | None = None
    notes: str | None = None


class LockdownResponse(BaseModel):
    """
    Whether the console is locked down for an incident.

    Attributes:
        locked: True while only the master token signs in.
        since: Since when.
        by: Who locked it.
        reason: The incident reference.
        package: The evidence package taken with it.
    """

    locked: bool
    since: str | None = None
    by: str | None = None
    reason: str | None = None
    package: str | None = None


def _actor(request: Request, session: dict[str, Any]) -> Any:
    from noust.web.permissions.principal import actor_of

    return actor_of(session)


def _exposure() -> checks.ConsoleExposure:
    """
    Read the running console's exposure from its own configuration.

    Returns:
        How this very console is reached.
    """
    from noust.cli.commands.web import PANEL_TLS_CERT
    from noust.web.auth import get_security_config

    config = get_security_config()
    certificate = config.ssl_certfile or None
    loopback = config.host in ("127.0.0.1", "::1", "localhost")
    return checks.ConsoleExposure(
        source="console",
        host=config.host,
        port=config.port,
        certificate=certificate,
        self_signed=bool(certificate) and str(certificate) == str(PANEL_TLS_CERT),
        insecure_http=not loopback and not certificate,
        allow_ip=tuple(config.ip_whitelist),
    )


def _run(refresh: bool) -> tuple[checks.ComplianceCheck, checks.Facts]:
    from noust.web.server import get_token_manager

    return checks.run_check(
        checks.Sources(
            token_manager=get_token_manager(),
            exposure=_exposure,
            refresh_hardening=refresh,
        )
    )


RefreshQuery = Annotated[
    bool,
    Query(
        description="Probe the server again for the hardening checks instead of reusing the last run."
    ),
]


@router.get("/check", response_model=EnsCheckResponse)
def ens_check(request: Request, session: Session, refresh: RefreshQuery = False) -> Any:
    """
    Compare this server with the ens-medium profile, check by check.

    Args:
        request: The request.
        session: Authenticated session, injected.
        refresh: Probe the hardening checks again.

    Returns:
        The findings, their counts and verdict, and the op.mon.2 indicators.
    """
    result, _facts = _run(refresh)
    record("compliance.read", actor=_actor(request, session), target="ens:check")
    return result.to_dict()


@router.get(
    "/report",
    response_model=EnsReportResponse,
    responses={200: {"content": {"text/markdown": {}}}},
)
def ens_report(
    request: Request,
    session: Session,
    refresh: RefreshQuery = False,
    format: Annotated[Literal["json", "markdown"], Query()] = "json",
) -> Any:
    """
    Build the evidence report for an ENS auditor.

    Args:
        request: The request.
        session: Authenticated session, injected.
        refresh: Probe the hardening checks again.
        format: ``json`` (the report and its SHA-256) or ``markdown`` (a
            download).

    Returns:
        The report, or its Markdown rendering as an attachment.
    """
    result, facts = _run(refresh)
    built = report.build_report(result, facts)
    record(
        "compliance.read",
        actor=_actor(request, session),
        target="ens:report",
        details={"sha256": built["sha256"], "verdict": built["verdict"], "format": format},
    )
    if format == "markdown":
        name = f"ens-report-{built['host']}.md"
        return PlainTextResponse(
            report.render_markdown(built),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "X-Noust-Report-SHA256": built["sha256"],
            },
        )
    return {"sha256": built["sha256"], "report": built}


@router.get("/profile", response_model=EnsProfileResponse)
def ens_profile(session: Session) -> Any:
    """
    Say which profile is on, and every value ens-medium fixes.

    Args:
        session: Authenticated session, injected.

    Returns:
        The profile and the baseline.
    """
    return {
        "profile": profile.current_profile(),
        "baseline": [item.to_dict() for item in profile.baseline()],
    }


@router.get(
    "/inventory", response_model=InventoryResponse, responses={200: {"content": {"text/csv": {}}}}
)
def ens_inventory(
    session: Session,
    format: Annotated[Literal["json", "csv"], Query()] = "json",
) -> Any:
    """
    List the inventory, or export it as CSV.

    Args:
        session: Authenticated session, injected.
        format: ``json`` or ``csv`` (a download).

    Returns:
        Every application with its owner, criticality and classification.
    """
    entries = inventory.Inventory().list()
    if format == "csv":
        return PlainTextResponse(
            inventory.export_csv(entries),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="inventory.csv"'},
        )
    return {
        "applications": [entry.to_dict() for entry in entries],
        "criticalities": list(inventory.CRITICALITIES),
        "classifications": list(inventory.CLASSIFICATIONS),
    }


@router.put("/inventory/{domain}", response_model=InventoryEntryModel)
def ens_inventory_update(
    domain: str, body: InventoryUpdate, request: Request, session: Session
) -> Any:
    """
    Change an application's owner, criticality, classification or notes.

    Args:
        domain: The application.
        body: The fields to change; absent ones are kept, empty ones cleared.
        request: The request.
        session: Authenticated session, injected.

    Returns:
        The entry as it is now.
    """
    from noust.web.auth import actor_label

    fields = {
        name: value
        for name, value in (
            ("owner", body.owner),
            ("criticality", body.criticality),
            ("classification", body.classification),
            ("notes", body.notes),
        )
        if value is not None
    }
    entry = inventory.Inventory().set(domain, actor=actor_label(session), **fields)
    return entry.to_dict()


@router.get("/incident", response_model=LockdownResponse)
def ens_incident(session: Session) -> Any:
    """
    Say whether the console is locked down for an incident.

    Args:
        session: Authenticated session, injected.

    Returns:
        The lockdown, if any.
    """
    state = lockdown_state()
    if state is None:
        return {"locked": False}
    return {
        "locked": True,
        **{key: state.get(key) for key in ("since", "by", "reason", "package")},
    }


class AccessReviewResponse(BaseModel):
    """
    The list a reviewer attests (op.acc.4.4), and the past attestations.

    Attributes:
        accounts: Every account: username, role, status, person, second
            factor, last sign-in, tokens it owns.
        conflicts: People holding incompatible roles.
        exceptions: Separation-of-duties exceptions in force.
        tokens_without_owner: Live API tokens no account owns.
        generated_at: When the list was built.
        digest: SHA-256 of the list: what an attestation names.
        reviews: Past attestations, newest first (audit events).
    """

    accounts: list[dict[str, Any]]
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    exceptions: list[dict[str, Any]] = Field(default_factory=list)
    tokens_without_owner: int | None = None
    generated_at: str
    digest: str
    reviews: list[dict[str, Any]] = Field(default_factory=list)


class AccessReviewAttestation(BaseModel):
    """
    An attestation of the list with a given digest.

    Attributes:
        digest: The digest of the list that was reviewed; refused when the
            list changed since it was shown.
        notes: What was looked at and what was changed.
    """

    digest: str
    notes: str = ""


class AccessReviewRecorded(BaseModel):
    """
    What was recorded.

    Attributes:
        digest: The list's digest.
        accounts: Accounts in it.
        conflicts: Incompatible-role conflicts in it.
        exceptions: Exceptions in it.
        notes: The reviewer's notes.
        recorded_at: When.
    """

    digest: str
    accounts: int
    conflicts: int
    exceptions: int
    notes: str
    recorded_at: str


def _tokens() -> list[dict[str, Any]]:
    from noust.web.server import get_token_manager

    return list(get_token_manager().list_api_tokens())


@router.get("/access-review", response_model=AccessReviewResponse)
def ens_access_review(session: Session) -> Any:
    """
    Build the list of who may do what, for the periodic review.

    Args:
        session: Authenticated session, injected.

    Returns:
        The list, its digest and the past attestations.
    """
    from noust.core.ens import access_review

    review = access_review.build_review(tokens=_tokens())
    return {**review, "reviews": access_review.last_reviews()}


@router.post("/access-review", response_model=AccessReviewRecorded)
def ens_access_review_attest(
    body: AccessReviewAttestation, request: Request, session: Session
) -> Any:
    """
    Attest the list, as it was shown (the security officer's).

    Args:
        body: The digest of the list reviewed, and notes.
        request: The request.
        session: Authenticated session, injected.

    Returns:
        What was recorded.

    Raises:
        ValidationError: 400 when the list changed since it was shown.
    """
    from noust.core.ens import access_review
    from noust.core.exceptions import ValidationError

    review = access_review.build_review(tokens=_tokens())
    if body.digest != review["digest"]:
        raise ValidationError(
            "The list of accounts changed since it was shown",
            details="Reload it, review it again and attest the current one.",
            field="digest",
        )
    return access_review.attest(review, actor=_actor(request, session), notes=body.notes)
