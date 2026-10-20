# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Exporting an application's definition, and importing one (2.3).

A client of :mod:`noust.deployers.app_export`, like ``noust app export`` and
``noust app import``. ``GET /api/apps/{domain}/export`` answers the document;
its secret values need an admin credential in sudo mode and are audited.
``POST /api/apps/import`` checks the document and the domain in the request,
so a bad document or a missing secret is a 400 the form can show, and queues
the import as a deploy job: the application is created by the same deploy
job ``POST /api/apps`` queues, then the rest is applied through the managers.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from noust.core import legacy_names
from noust.core.store import get_store
from noust.core.teams import scoped_store
from noust.deployers.app_clone import deploy_through_job
from noust.deployers.app_export import (
    ImportPlan,
    apply_import,
    export_app,
    plan_import,
    plan_summary,
    report_summary,
)
from noust.deployers.recorder import CapturingLogger
from noust.validators.port import find_available_port
from noust.web.api.apps import DEFAULT_PORT, _require_local_source_privilege
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    ensure_elevated,
    require_elevated,
    strict_domain,
)
from noust.web.auth import actor_label, ensure_scope, get_audit_logger, get_client_ip
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)


class ExportLimits(BaseModel):
    """The unit's limits; null is no limit."""

    memory_max_mb: int | None = None
    cpu_quota_percent: int | None = None
    tasks_max: int | None = None


class ExportHealth(BaseModel):
    """What the health gate asks; null is the default."""

    path: str | None = None
    expect: str | None = None
    timeout: int | None = None


class ExportZeroDowntime(BaseModel):
    """Blue/green activation."""

    enabled: bool = False
    drain_seconds: int | None = None


class ExportApp(BaseModel):
    """The application itself."""

    domain: str
    app_type: str
    source: str | None = Field(
        default=None, description="Credentials inside a URL are replaced by ***"
    )
    branch: str | None = None
    layout: str | None = Field(default=None, description="inplace, releases, or null")
    port: int | None = None
    webserver: str | None = "nginx"
    ssl: bool = True
    include_www: bool = False
    persistent_paths: list[str] = Field(default_factory=list)
    keep_releases: int | None = None
    build: str | None = Field(
        default=None, description="The operator's build block, as YAML; null for the repository's"
    )
    limits: ExportLimits = Field(default_factory=ExportLimits)
    health: ExportHealth = Field(default_factory=ExportHealth)
    zero_downtime: ExportZeroDowntime = Field(default_factory=ExportZeroDowntime)


class ExportDomains(BaseModel):
    """The names the application answers on besides its domain (www is include_www)."""

    aliases: list[str] = Field(default_factory=list)
    redirects: list[str] = Field(default_factory=list)


class ExportEnvEntry(BaseModel):
    """One environment variable."""

    secret: bool = False
    value: str | None = Field(
        default=None, description="Null for a secret when secrets were not included"
    )


class ExportCronJob(BaseModel):
    """One cron job of the application."""

    name: str
    schedule: str
    command: str
    enabled: bool = True
    user: str | None = None
    working_directory: str | None = None


class ExportBackupDestination(BaseModel):
    """A remote copy, by name; its credentials never leave the server."""

    name: str
    retention_count: int | None = None
    retention_days: int | None = None


class ExportBackup(BaseModel):
    """The backup schedule."""

    schedule: str
    include_databases: bool = True
    retention_count: int | None = None
    retention_days: int | None = None
    destinations: list[ExportBackupDestination] = Field(default_factory=list)


class ExportPreviews(BaseModel):
    """Pull request preview settings."""

    base_domain: str
    max_previews: int | None = None
    ttl_hours: int | None = None
    allow_bots: bool = False
    exclude_env: list[str] = Field(default_factory=list)


class ExportGitHub(BaseModel):
    """Whether it cloned through a GitHub App installation."""

    installation_linked: bool = False


class ExportDatabase(BaseModel):
    """A database linked to the application, named only."""

    engine: str
    name: str | None = None


class ExportMount(BaseModel):
    """A file Noust keeps for the application, or a path of the server bound into a container."""

    kind: str = Field(description="file or path")
    target: str = Field(description="Where the application sees it")
    mode: str | None = Field(default=None, description="A file's permissions, in octal")
    secret: bool = Field(default=False, description="A file's text may hold a secret")
    content: str | None = Field(
        default=None,
        description="A file's text; null unless secrets were included (or it could not travel)",
    )
    note: str | None = Field(default=None, description="Why a file's text is not here")
    source: str | None = Field(default=None, description="A path's location on the server")
    read_only: bool = True


class ExportContainer(BaseModel):
    """How a container application is built and run; its registry password never is."""

    method: str = "dockerfile"
    image: str | None = None
    dockerfile: str | None = None
    context: str = "."
    target: str | None = None
    build_args: dict[str, str] = Field(default_factory=dict)
    build_secrets: list[str] = Field(
        default_factory=list, description="Ids only: their values are not exported"
    )
    container_port: int | None = None
    volumes: list[str] = Field(default_factory=list)
    restart_policy: str = "unless-stopped"
    registry_server: str | None = None
    registry_username: str | None = None
    entrypoint: list[str] | None = None
    command: list[str] | None = None


class AppExportDocument(BaseModel):
    """An application's definition, as ``noust app export`` writes it."""

    format: str = Field(description='Always "noust-app"; the name before 4.0 is still read')
    version: int = Field(description="Shape of the document; this release writes 1")
    exported_at: str | None = None
    noust_version: str | None = None
    legacy_version: str | None = Field(  # type: ignore[literal-required]  # the name is spelled once
        default=None,
        alias=legacy_names.EXPORT_VERSION_KEY,
        description="Which release made an export of before 4.0 (this release writes "
        "noust_version); read, never written",
    )
    secrets_included: bool = False
    app: ExportApp
    domains: ExportDomains = Field(default_factory=ExportDomains)
    source_options: dict[str, Any] = Field(
        default_factory=dict, description="How the repository is fetched (submodules, lfs, ...)"
    )
    container: ExportContainer | None = None
    env: dict[str, ExportEnvEntry] = Field(default_factory=dict)
    env_secret_marks: dict[str, bool] = Field(default_factory=dict)
    mounts: list[ExportMount] = Field(default_factory=list)
    delivery: dict[str, Any] = Field(
        default_factory=dict,
        description="History, stop policy, rollback pause and update schedule",
    )
    cron: list[ExportCronJob] = Field(default_factory=list)
    backup: ExportBackup | None = None
    previews: ExportPreviews | None = None
    github: ExportGitHub = Field(default_factory=ExportGitHub)
    databases: list[ExportDatabase] = Field(default_factory=list)


class ImportAppRequest(BaseModel):
    """An export document to create an application from."""

    document: AppExportDocument
    domain: str | None = Field(
        default=None, description="Create it on this domain instead of the exported one"
    )
    source: str | None = Field(
        default=None, description="Deploy from this source instead of the exported one"
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Values for variables, over the document's: the secrets it left out",
    )


@router.get("/{domain}/export", response_model=AppExportDocument)
def get_app_export(
    domain: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    with_secrets: Annotated[bool, Query()] = False,
) -> AppExportDocument:
    """
    Export everything that defines an application.

    Admin scope: the document names the source, the variables and every
    schedule. Secret values are null unless ``with_secrets``, which needs
    sudo mode and is audited, naming the caller and never a value.

    Args:
        domain: Domain of the application.
        request: The incoming request, for the audit record.
        session: The authenticated session.
        with_secrets: Include secret values in clear.

    Returns:
        The document.

    Raises:
        HTTPException: 403 below admin scope or, with secrets, outside sudo
            mode; 404 when the application is unknown.
    """
    ensure_scope(request, session, "admin")
    if with_secrets:
        ensure_elevated(request, session)
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    document = export_app(validated, with_secrets=with_secrets, actor=actor_label(session))
    if with_secrets:
        audit = get_audit_logger()
        if audit:
            audit.record(
                action="apps.export.secrets",
                result="success",
                client_ip=get_client_ip(request),
                actor=actor_label(session),
                resource=f"/api/apps/{validated}/export",
                detail=f"exported {len(document['env'])} variable(s) with secret values",
            )
    return AppExportDocument(**document)


def import_app_job(
    plan: ImportPlan, actor: str = "api", job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Carry out an import as a background job.

    The job's log opens with the plan, line by line, so what ran is on
    record next to what it did.

    Args:
        plan: What :func:`plan_import` decided in the request.
        actor: Who asked, for the audit record of each cron job created.
        job_context: Injected by the job manager.

    Returns:
        What was applied and what was not.

    Raises:
        DeploymentError: The deploy failed; nothing else was applied.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", plan.domain)
        logger.attach_sink(job_context.log)
    logger.info(f"Importing {plan.exported_domain} as {plan.domain}")
    for line in plan.steps:
        logger.substep(line)
    for skipped in plan.skipped:
        logger.substep(f"will not apply {skipped.part}")

    deploy = deploy_through_job(job_context)

    report = apply_import(plan, deploy=deploy, logger=logger, actor=actor)
    if job_context is not None:
        job_context.update("Import complete", 100)
    return report_summary(report)


@router.post("/import", response_model=JobAcceptedResponse, status_code=202)
def import_app(
    body: ImportAppRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue the creation of an application from an export document.

    Checked before anything is queued: the document, the domain (409 when
    taken), the source (a local path is the operator's alone, as for ``POST
    /api/apps``) and the secret values the export left out (400 naming every
    one missing). Sudo mode: it deploys as root, and the document may create
    cron jobs and previews, which is what the console's confirmation is for.
    The job's ``metadata.plan`` carries the whole plan (every cron job with
    its user, directory and command; what previews copy; the reasons it
    needed confirming), and its log opens with it.

    Args:
        body: The document, and what to change about it.
        request: The incoming request, for the audit record of a refusal.
        session: The authenticated session.

    Returns:
        The queued job; its result lists what was applied and what was not.

    Raises:
        ValidationError: The document is not valid, or a value is missing.
        DomainConflictError: The domain is taken.
        HTTPException: 403 for a local source the credential may not deploy,
            503 when no port is free.
    """
    domain = strict_domain(body.domain) if body.domain else None
    plan = plan_import(dump_model(body.document), domain=domain, source=body.source, env=body.env)
    return queue_import(plan, request, session)


def queue_import(
    plan: ImportPlan, request: Request, session: Mapping[str, Any]
) -> JobAcceptedResponse:
    """
    Queue the creation of the application a plan describes, as a deploy job.

    The one place an import becomes a job: ``POST /api/apps/import`` and the creation of an
    application per workspace (``POST /api/workspaces/deploy``) both end here.

    Args:
        plan: What :func:`plan_import` decided.
        request: The incoming request, for the audit record of a refusal.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 403 for a local source the credential may not deploy, 503 when no
            port is free.
    """
    _require_local_source_privilege(request, session, plan.create.source)
    if plan.create.port is None:
        # As POST /api/apps does: the deploy job would otherwise fall back to
        # the type's default port, which another application may hold.
        port = find_available_port(
            preferred=DEFAULT_PORT, exclude=get_store().ports_owned_by_apps()
        )
        if port is None:
            raise HTTPException(status_code=503, detail="No available port found")
        plan.create = dataclasses.replace(plan.create, port=port)

    summary = plan_summary(plan)
    job = get_job_manager().create_job(
        job_type=JobType.DEPLOY,
        name=f"Import {plan.domain}",
        description=f"Importing {plan.exported_domain} as {plan.domain}",
        func=import_app_job,
        kwargs={"plan": plan, "actor": actor_label(session)},
        metadata={
            "domain": plan.domain,
            "app_type": plan.create.app_type,
            "import": True,
            "plan": summary,
        },
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Import queued for {plan.domain}: {'; '.join(summary['steps'])}"
        + (
            f". Will not apply: {', '.join(step['part'] for step in summary['skipped'])}"
            if summary["skipped"]
            else ""
        ),
        job=job.to_dict(),
    )
