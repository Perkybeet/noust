"""
Applications API endpoints.

The panel does not deploy applications itself: it hands the work to the job
manager, which drives the same deployers the CLI drives. Two things changed
here for that to be true.

- **A deploy no longer runs inside the request.** ``POST /api/apps`` used to
  call ``deployer.deploy()`` from an ``async def`` handler, which pinned the
  event loop for the whole build - minutes of ``npm install`` during which the
  panel served nothing, not even a heartbeat - and then timed out the client
  anyway. It now answers ``202 Accepted`` with a job id.
- **Logs come from the service manager.** The handler used to run
  ``journalctl`` through :mod:`subprocess` with the service name interpolated
  by hand, bypassing the shared command runner.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.core import app_state
from noust.core.app_state import AppState, resolve_state_with_status, resolve_states_with_status
from noust.core.config import REDACTED
from noust.core.exceptions import DeploymentError, NoustError, SourceError, ValidationError
from noust.core.runner import CommandCancelled
from noust.core.secret_detection import Secrecy, classify
from noust.core.store import (
    DEFAULT_KEEP_RELEASES,
    App,
    DeploymentRecord,
    DeploymentTrigger,
    Service,
    get_store,
)
from noust.core.utils import domain_to_app_name
from noust.deployers.base import BaseDeployer
from noust.deployers.helpers.app_env import read_app_env, write_app_env
from noust.deployers.helpers.env_manager import redact_url_credentials
from noust.deployers.helpers.health_gate import HealthCheck
from noust.deployers.helpers.layout import RELEASES, app_root
from noust.deployers.helpers.package_manager import SUPPORTED_PACKAGE_MANAGERS
from noust.deployers.helpers.php_fpm import is_php_fpm
from noust.deployers.inspect import SourceInspection, inspect_source
from noust.deployers.lifecycle import (
    activate_release,
    check_adopted_directory,
    list_releases,
    set_branch,
    set_follow_tags,
    set_health_check,
    set_release_retention,
    set_resource_limits,
)
from noust.deployers.migrate import MigrationPlan, plan_migration
from noust.deployers.php_fpm import control_pool
from noust.deployers.registry import DeployerRegistry, available_types
from noust.deployers.releases import is_release_id
from noust.managers.backup_manager import RollbackManager
from noust.managers.database.service import OWN_DATABASE_TYPES
from noust.managers.service_manager import ResourceLimits, ServiceManager
from noust.recipes import RecipeError, get_recipe
from noust.recipes.deploy import refuse_conflicts
from noust.validators.environment import EnvironmentValidationError
from noust.validators.port import find_available_port, validate_port
from noust.validators.source import validate_source
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    ensure_elevated,
    require_elevated,
    strict_domain,
)
from noust.web.api.platform_proposal import (
    PlatformProposalResponse,
    platform_proposal_response,
)
from noust.web.auth import (
    actor_label,
    ensure_scope,
    get_audit_logger,
    get_client_ip,
    scope_satisfies,
)
from noust.web.jobs import (
    JobType,
    delete_app_job,
    deploy_app_job,
    get_job_manager,
    migrate_app_job,
)
from noust.web.pydantic_compat import dump_model, iso_offset_validator

#: app_state's display labels, translated to the fixed API vocabulary.
#: Decoupled from AppState.label on purpose: that string is for a terminal
#: column and free to reword, this one is a public contract every client
#: parses.
_STATUS_LABELS: dict[str, str] = {
    app_state.RUNNING: "running",
    app_state.RESTARTING: "restarting",
    app_state.NOT_RESPONDING: "no_answer",
    app_state.STOPPED: "stopped",
    app_state.FAILED: "failed",
    app_state.STATIC: "static",
    app_state.UNKNOWN: "unknown",
    app_state.RUNNING_UNMANAGED: "running_unmanaged",
}

router = APIRouter(route_class=NoustErrorRoute)

_logger = logging.getLogger(__name__)

#: Port preferred when the client does not pick one.
DEFAULT_PORT = 3000


class LastDeploymentOut(BaseModel):
    """
    An application's most recent deployment attempt.

    Attributes:
        id: Deployment id, the store's own primary key.
        status: One of :class:`~noust.core.store.DeploymentStatus`: ``queued``,
            ``running``, ``success``, ``failed`` or ``rolled_back``.
        finished_at: When it finished, ISO 8601 with an explicit UTC offset;
            None while it is still running.
        git_commit: Short commit it deployed, when the source is git.
    """

    id: int
    status: str
    finished_at: str | None = None
    git_commit: str | None = None

    _iso_timestamps = iso_offset_validator("finished_at")


class AppInfo(BaseModel):
    """
    A deployed application and the live state of its service.

    Attributes:
        name: Application name, which is its domain.
        domain: Domain the application is served on.
        status: What is true about it right now, resolved by
            :func:`noust.core.app_state.resolve_state` - the one place the CLI
            and the panel agree on this: ``running``, ``restarting`` (systemd
            is crash-looping the unit), ``no_answer`` (the unit is up but
            nothing accepts connections on its port), ``stopped``, ``failed``
            (systemd gave up on it), ``static`` (served directly by the web
            server, there is no unit), ``running_unmanaged`` (a Compose stack
            whose containers run while its unit is stopped: hand it back with
            ``POST .../reclaim``) or ``unknown`` (systemd could not be asked).
        active: Whether the unit is active.
        enabled: Whether the unit starts on boot.
        pid: Main PID when running.
        uptime: How long the unit has been up.
        port: Upstream port.
        app_type: Deployer that owns it.
        path: Application directory.
        source: Git URL or local path it was deployed from.
        branch: Git branch it tracks, or None for a source that has none.
        follow_tags: The glob of git tags it deploys instead of a branch
            (``v*``), or None when it follows its branch.
        layout: ``inplace`` or ``releases``.
        keep_releases: Release directories kept before older ones are pruned.
            Meaningful only on ``releases``; the in-place default otherwise.
        build_command: Argv the deployer runs to build the project, read off
            the same ``get_build_command()`` the deploy used. Empty in a
            list response - computing it means instantiating the deployer
            once per application, which this endpoint does not do for a
            list - and filled in when this application is fetched on its own.
        start_command: What its unit runs, exactly as the deploy recorded it
            on the service row. None for a static site, which has no unit.
        memory_max_mb: Memory limit of its unit, in MB, or None.
        cpu_quota_percent: CPU quota of its unit, in percent of one CPU, or None.
        tasks_max: Task limit of its unit, or None.
        health_path: Path the health gate probes, or None for ``/``.
        health_expect: Statuses the health gate accepts, such as
            ``200-399``, or None for any status below 500.
        health_timeout: Seconds the health gate waits, or None for its
            default of 30.
        webhook_enabled: Whether a webhook secret is set for it. The secret
            itself is never part of this or any other response; it is set
            through ``POST /api/apps/{domain}/webhook-secret`` and cleared
            through the ``DELETE`` of the same path.
        unit: The systemd unit that runs it, or None for a static site.
        run_as: The account its unit runs as, or None for a static site.
        last_deployment: Its most recent deployment attempt, or None when
            nothing has ever been recorded for it.
        zero_downtime: Whether it runs as two instances behind an nginx
            upstream (blue/green); details at ``/zero-downtime``.
        preview_parent: The application it previews a pull request of, or
            None when it is not a preview.
        backup_before_update: Whether an update of a Docker Compose stack
            copies its databases first (on by default; meaningless for any
            other kind). Set through ``PATCH .../backup-before-update``.
    """

    name: str
    domain: str
    status: str
    active: bool
    enabled: bool
    pid: int | None = None
    uptime: str | None = None
    port: int | None = None
    app_type: str | None = None
    path: str | None = None
    source: str | None = None
    branch: str | None = None
    follow_tags: str | None = None
    layout: str = "inplace"
    keep_releases: int = DEFAULT_KEEP_RELEASES
    build_command: list[str] = Field(default_factory=list)
    start_command: str | None = None
    memory_max_mb: int | None = None
    cpu_quota_percent: int | None = None
    tasks_max: int | None = None
    health_path: str | None = None
    health_expect: str | None = None
    health_timeout: int | None = None
    webhook_enabled: bool = False
    unit: str | None = None
    run_as: str | None = None
    last_deployment: LastDeploymentOut | None = None
    zero_downtime: bool = False
    preview_parent: str | None = None
    backup_before_update: bool = True


class AppListResponse(BaseModel):
    """Response for listing applications."""

    apps: list[AppInfo]
    total: int


class NewAppDatabaseRequest(BaseModel):
    """
    A database to create with a new application, before its first build.

    Attributes:
        engine: The engine (``GET /api/databases/engines``), installed and
            running.
        name: The database; derived from the application when omitted
            (``<app>_db``), a slot number for Redis (0 by default).
        env_var: The variable; ``DATABASE_URL``, or ``REDIS_URL`` for Redis.
        extra_vars: Also write ``DB_HOST``, ``DB_PORT``, ``DB_NAME``,
            ``DB_USER`` and ``DB_PASSWORD``.
    """

    engine: str = Field(..., description="Database engine")
    name: str | None = Field(default=None, description="Database name")
    env_var: str | None = Field(default=None, description="Variable to write")
    extra_vars: bool = Field(default=False, description="Also write the DB_* variables")


class CreateAppRequest(BaseModel):
    """
    Request to deploy a new application.

    The deployer-specific fields carry exactly what the deployers'
    ``configure`` methods already accept, no more: ``subdomain_overrides``,
    ``workspace_filter`` and ``skip_database`` are read by the monorepo
    deployer, ``compose_file`` and ``compose_profiles`` by the docker-compose
    one, and every deployer ignores the options that do not concern it, which
    is the interface's own contract.
    """

    domain: str = Field(..., description="Target domain name")
    source: str | None = Field(
        default=None,
        description="Git URL, archive URL or local path. Required unless recipe is given, "
        "and refused with one",
    )
    app_type: str = Field(
        default="auto", description="Application type. Left on auto with a recipe"
    )
    recipe: str | None = Field(
        default=None,
        description="Deploy a known application from its recipe (GET /api/recipes): its "
        "source, type, database, variables and settings come from the recipe, and env_vars "
        "are applied over its variables. The job's result carries the recipe's notes",
    )
    port: int | None = Field(default=None, description="Application port")
    webserver: str = Field(default="nginx", description="Web server to use")
    branch: str | None = Field(default=None, description="Git branch to deploy")
    ssl: bool = Field(default=True, description="Obtain a certificate")
    env_vars: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    subdomain_overrides: dict[str, str] = Field(
        default_factory=dict,
        description="Monorepo: workspace name to subdomain overrides",
    )
    workspace_filter: list[str] | None = Field(
        default=None, description="Monorepo: deploy only these workspaces"
    )
    skip_database: bool = Field(default=False, description="Monorepo: skip database provisioning")
    compose_file: str | None = Field(
        default=None, description="Docker Compose: compose file, relative to the project"
    )
    compose_profiles: list[str] | None = Field(
        default=None, description="Docker Compose: profiles to activate"
    )
    layout: Literal["inplace", "releases"] | None = Field(
        default=None,
        description="Build every deploy as a release behind a health gate, or in place. "
        "Omitted: the server's deploy.layout",
    )
    include_www: bool = Field(
        default=False, description="Also answer on www.<domain>, as a redirect to it"
    )
    persistent_paths: list[str] | None = Field(
        default=None,
        description="Releases only: paths, relative to the application, linked into shared/ "
        "and kept across every release (uploads, storage)",
    )
    memory_max_mb: int | None = Field(
        default=None, description="MemoryMax for the unit, in MB; at least 64. Null: no limit"
    )
    cpu_quota_percent: int | None = Field(
        default=None,
        description="CPUQuota for the unit, in percent of one CPU (200 is two CPUs); "
        "1 to 100 per CPU. Null: no limit",
    )
    tasks_max: int | None = Field(
        default=None,
        description="TasksMax for the unit, processes and threads; at least 16. Null: no limit",
    )
    package_manager: str | None = Field(
        default=None,
        description="Node package manager to install and build with (npm, pnpm, yarn, bun); "
        "omitted or null detects it from the project's lock file. Ignored by app types that "
        "do not use one (monorepo, docker-compose).",
    )
    health_path: str | None = Field(
        default=None,
        description="Path the health gate probes from the first deployment, such as /healthz; "
        "omitted: the type's own (/)",
    )
    health_expect: str | None = Field(
        default=None, description="Statuses that mean up, such as 200-399; omitted: below 500"
    )
    health_timeout: int | None = Field(
        default=None, description="Seconds the health gate waits; omitted: its default"
    )
    github_installation_id: int | None = Field(
        default=None,
        description="GitHub App installation that clones this application's repository, "
        "as the repository list returned it; kept for every later update",
    )
    database: NewAppDatabaseRequest | None = Field(
        default=None,
        description="Create a database for the application before its first build, owned by "
        "an account of its own, and write its connection string into the application's "
        "environment, marked secret: the first build and the first start already have it. "
        "Not with a recipe, a monorepo or a Docker Compose project, which provision their "
        "own. A first deploy that fails keeps the database, and its error says how to drop it",
    )


class AppActionResponse(BaseModel):
    """Response for an application action that completed immediately."""

    success: bool
    message: str
    domain: str


class AppLogsResponse(BaseModel):
    """Response carrying journal output for an application."""

    domain: str
    logs: str
    lines: int


class EnvSecrecyOut(BaseModel):
    """
    Why one environment variable is, or is not, treated as a secret.

    Attributes:
        secret: Whether the value must not be shown or logged in clear.
        reason: One of ``"marked secret"``, ``"marked not secret"``,
            ``"name"``, ``"value: <kind>"``, ``"value"``, ``"url
            credentials"`` or ``"plain"`` - see
            :class:`~noust.core.secret_detection.Secrecy`. ``"value: <kind>"``
            names the vendor a value's shape matched (``"value: stripe"``),
            which is itself a fact about the value; a credential below admin
            scope gets the generic ``"value"`` instead (see
            :func:`~noust.web.api.apps._secrets_map`).
        marked: Whether this came from an operator's own mark rather than
            from the variable's name or value.
    """

    secret: bool
    reason: str
    marked: bool


class AppEnvResponse(BaseModel):
    """
    An application's environment, as recorded in its ``.env`` file.

    Attributes:
        domain: Domain of the application.
        variables: Name to value mapping. Unless ``unmasked`` is true, a
            secret-looking name and a URL credential embedded in a value are
            both replaced by the fixed :data:`~noust.core.config.REDACTED`
            placeholder, exactly as ``noust env show`` does on the terminal.
        unmasked: Whether this response carries values in clear.
        secrets: Every variable's classification, from
            :func:`~noust.core.secret_detection.classify` - present whether or
            not ``unmasked`` is true, so the console can label a variable
            (and let the operator override it) without asking to see its
            value. For a credential below admin scope, a value-based
            verdict's ``reason`` never names the vendor it matched (see
            :class:`EnvSecrecyOut`).
    """

    domain: str
    variables: dict[str, str]
    unmasked: bool
    secrets: dict[str, EnvSecrecyOut] = Field(default_factory=dict)


class UpdateAppEnvRequest(BaseModel):
    """Request to replace an application's ``.env`` file wholesale."""

    variables: dict[str, str] = Field(
        default_factory=dict,
        description="The complete name to value mapping the .env file should hold",
    )


class AppEnvUpdateResponse(BaseModel):
    """
    Confirmation that an application's environment was rewritten.

    Attributes:
        domain: Domain of the application.
        restart_required: Always true. The running process, if any, keeps the
            environment it started with; the caller must restart it to pick
            up the change.
    """

    domain: str
    restart_required: bool = True


class UpdateEnvMarksRequest(BaseModel):
    """
    Request to set or clear operator overrides on an application's environment.

    Attributes:
        marks: Variable name to ``true`` (always treat as a secret),
            ``false`` (never treat as a secret) or ``null`` (remove any
            existing mark and judge the variable by its name and value
            again).
    """

    marks: dict[str, bool | None] = Field(
        default_factory=dict,
        description="Variable name to true (secret), false (not secret), or null to clear",
    )


class AppEnvSecretsResponse(BaseModel):
    """The secrecy classification of every variable of an application's environment."""

    domain: str
    secrets: dict[str, EnvSecrecyOut]


def _last_deployment_out(record: DeploymentRecord | None) -> LastDeploymentOut | None:
    """
    Translate a store deployment row to its API model.

    Args:
        record: The application's newest deployment, when it has one.

    Returns:
        None when there is no history, or the row has no id (never
        persisted); otherwise the API model.
    """
    if record is None or record.id is None:
        return None
    return LastDeploymentOut(
        id=record.id,
        status=record.status,
        finished_at=record.finished_at,
        git_commit=record.git_commit,
    )


#: A user name alone in an http(s) URL. Forges accept an access token in that
#: position (``https://<token>@github.com/...``), so in a source it is as much
#: a credential as a password after a colon is. ssh's ``git@host:path`` names
#: the account ssh logs in as, has no scheme, and is left alone.
_HTTP_USERINFO = re.compile(r"(?P<prefix>https?://)[^:/?#@\s]+@", re.IGNORECASE)


def public_source(source: str | None) -> str | None:
    """
    Render a stored source the way it may leave the server.

    Releases before this one stored the URL they were given, and a clone URL
    is where an operator puts a forge token to reach a private repository.
    The stored value is left as it is, because the next update clones from
    it; only what every application read sends back is redacted.

    Args:
        source: The source as stored.

    Returns:
        The source with every credential inside a URL replaced by ``***``, or
        None when there is no source.
    """
    if not source:
        return None
    redacted = redact_url_credentials(source)
    return _HTTP_USERINFO.sub(lambda match: f"{match.group('prefix')}{REDACTED}@", redacted)


def _is_local_source(source: str) -> bool:
    """
    Report whether a source names a directory on this machine.

    Decided by :func:`noust.validators.source.validate_source`, the same
    classification :meth:`noust.managers.source_manager.SourceManager.fetch`
    acts on, so the answer here is what the build would actually do.

    Args:
        source: The source as the client sent it.

    Returns:
        True for a local path. A source that is not valid at all is not
        local: the fetch refuses it on its own, with its own reason.
    """
    try:
        kind, _normalized = validate_source(source)
    except SourceError:
        return False
    return kind == "local"


def _require_local_source_privilege(
    request: Request, session: Mapping[str, Any], source: str
) -> None:
    """
    Refuse a local-path source to anyone but the operator in person.

    A build runs as root and copies the directory it is given into an
    application that is then served, so a local path reaches every file on
    the machine. The master token is root by definition; a console session
    may, once it has confirmed in sudo mode; an API token never may, whatever
    its scope, because it is a standing credential held by a script. Checked
    here, at the two endpoints that fetch a source over HTTP; the CLI runs as
    root already and is not asked.

    Args:
        request: The incoming request, for the audit record.
        session: The authenticated payload.
        source: The source as the client sent it.

    Raises:
        HTTPException: 403 ``elevation_required`` for a console session that
            has not confirmed recently, 403 ``forbidden`` for any other
            credential.
    """
    if not _is_local_source(source):
        return

    kind = session.get("type")
    if kind == "master":
        return
    if kind == "session":
        ensure_elevated(request, dict(session))
        return

    audit = get_audit_logger()
    if audit:
        audit.record(
            action="apps.source",
            result="denied",
            client_ip=get_client_ip(request),
            actor=actor_label(session),
            resource=request.url.path,
            detail="local path source refused to a non-interactive credential",
        )
    raise HTTPException(
        status_code=403,
        detail={
            "error": "forbidden",
            "detail": "Deploying from a local path needs the master token or a console session",
            "hint": (
                "API tokens deploy from a repository URL. To deploy a directory on "
                "this machine, use the console (it asks you to confirm it's you) or "
                "'noust create' on the server."
            ),
            "fields": {"source": "local paths are not accepted from an API token"},
        },
    )


def _to_app_info(
    app: App,
    state: AppState,
    status: dict[str, Any],
    service: Service | None,
    *,
    webhook_enabled: bool,
    last_deployment: DeploymentRecord | None,
) -> AppInfo:
    """
    Combine a stored application with its resolved state and live status.

    Args:
        app: The stored application.
        state: What :func:`noust.core.app_state.resolve_state` decided is
            true about it.
        status: The systemd status ``state`` was resolved from, empty for a
            static application, which is never queried.
        service: The application's service record, when it has one.
        webhook_enabled: Whether a webhook secret is set for it.
        last_deployment: Its most recent deployment history row, if any.

    Returns:
        The API representation.
    """
    pid = status.get("pid")
    return AppInfo(
        name=app.domain,
        domain=app.domain,
        status=_STATUS_LABELS.get(state.label, state.label.lower()),
        active=bool(status.get("active", False)),
        enabled=bool(status.get("enabled", False)),
        pid=int(pid) if pid and str(pid) != "0" else None,
        uptime=str(status["uptime"]) if status.get("uptime") else None,
        port=app.port,
        app_type=app.app_type,
        path=app.app_path,
        source=public_source(app.source),
        branch=app.branch,
        follow_tags=getattr(app, "follow_tags", None),
        layout=app.layout,
        keep_releases=app.keep_releases,
        start_command=service.command if service is not None and service.command else None,
        memory_max_mb=app.memory_max_mb,
        cpu_quota_percent=app.cpu_quota_percent,
        tasks_max=app.tasks_max,
        health_path=app.health_path,
        health_expect=app.health_expect,
        health_timeout=app.health_timeout,
        webhook_enabled=webhook_enabled,
        unit=service.name if service is not None else None,
        run_as=service.user if service is not None else None,
        last_deployment=_last_deployment_out(last_deployment),
        zero_downtime=bool(getattr(app, "zero_downtime", False)),
        preview_parent=getattr(app, "preview_parent", None),
        backup_before_update=bool(app.backup_before_update),
    )


def _deployer_build_command(app: App) -> list[str]:
    """
    Read the build command a fresh deploy of this application would run.

    Instantiates the deployer and reads its ``get_build_command()`` the same
    way :func:`noust.deployers.inspect.inspect_source` does for a checkout
    that has not been deployed yet - ``configure()`` and nothing past it, no
    install, no build, no network. Cheap enough for one application, which is
    why only the detail endpoint calls this: the list endpoint would pay this
    once per application shown, and :func:`_to_app_info` already gives every
    caller a ``build_command`` (empty) without it.

    Args:
        app: The stored application.

    Returns:
        Argv the deployer would run to build the project. Empty when the
        type is not registered, has nothing to build, or the computation
        itself failed - this is a convenience for the detail view, not a
        fact worth failing the request over.
    """
    deployer_class = DeployerRegistry.get(app.app_type)
    if deployer_class is None or not issubclass(deployer_class, BaseDeployer):
        return []
    try:
        instance = deployer_class(verbose=False)
        instance.configure(
            domain=app.domain,
            source=app.source,
            branch=app.branch,
            app_path=Path(app.app_path) if app.app_path else None,
        )
        return instance.get_build_command()
    except (NoustError, OSError):
        return []


def _redact_env(
    values: Mapping[str, str], marks: Mapping[str, bool] | None = None
) -> dict[str, str]:
    """
    Replace every secret value with the fixed REDACTED placeholder.

    Uses :func:`~noust.core.secret_detection.classify`, honouring the
    application's own marks: a variable the operator marked secret is
    redacted even if nothing about its name or value would otherwise say so,
    and one marked not secret is shown even if it would. A value that is
    secret only because of a credential embedded in it - ``DATABASE_URL``, a
    connection string no name marks as a secret - keeps the rest of the
    value readable, exactly as ``noust env show`` does on the terminal. The
    placeholder is fixed width, so a response never reveals the length of a
    secret or whether one is set at all.

    Args:
        values: The environment as read from the .env file.
        marks: The application's operator overrides, from
            :attr:`~noust.core.store.App.env_secret_marks`.

    Returns:
        A new mapping safe to send to a browser.
    """
    redacted: dict[str, str] = {}
    for key, value in values.items():
        text = str(value)
        verdict = classify(key, text, marks)
        if not verdict.secret:
            redacted[key] = text
        elif verdict.reason == "url credentials":
            redacted[key] = redact_url_credentials(text)
        else:
            redacted[key] = REDACTED
    return redacted


def _secrets_map(
    values: Mapping[str, str],
    marks: Mapping[str, bool] | None = None,
    *,
    reveal_kind: bool = True,
) -> dict[str, EnvSecrecyOut]:
    """
    Classify every variable of an environment for the API response.

    Args:
        values: The environment as read from the .env file.
        marks: The application's operator overrides.
        reveal_kind: Whether a value-based verdict may name the vendor kind
            it matched (``"value: stripe"``). False collapses every such
            reason to the generic ``"value"``: the vendor a secret belongs to
            is itself information about its content, which a credential
            below admin scope has no business learning about a variable it
            cannot unmask. Every other reason (``"name"``, ``"marked
            secret"``, ...) already says nothing about the value and is
            never collapsed.

    Returns:
        Variable name to its classification.
    """

    def _out(verdict: Secrecy) -> EnvSecrecyOut:
        reason = verdict.reason
        if not reveal_kind and reason.startswith("value: "):
            reason = "value"
        return EnvSecrecyOut(secret=verdict.secret, reason=reason, marked=verdict.marked)

    return {key: _out(classify(key, value, marks)) for key, value in values.items()}


def _mark_change_detail(marks: Mapping[str, bool | None]) -> str:
    """
    Render an audit-safe summary of a marks change: every name and its new direction.

    Naming the keys alone (the previous shape of this line) let an auditor
    see that ``APP_NAME`` changed but not whether it was marked secret,
    marked not secret, or returned to automatic classification - the
    distinction that matters when the audit log is the record of who told
    Noust a variable was safe to display. Never carries a value.

    Args:
        marks: The marks the request asked to change, exactly as
            :class:`UpdateEnvMarksRequest` carries them: true (secret), false
            (not secret) or null (back to automatic).

    Returns:
        ``"no keys changed"`` when empty, otherwise one ``name -> direction``
        pair per change, sorted by name.
    """
    if not marks:
        return "no keys changed"
    direction = {True: "secret", False: "not secret", None: "automatic"}
    changes = ", ".join(f"{name} -> {direction[marks[name]]}" for name in sorted(marks))
    return f"changed: {changes}"


def _env_app(domain: str) -> App:
    """
    Look up the application whose ``.env`` file a request is about.

    Args:
        domain: Domain from the request.

    Returns:
        The stored application record.

    Raises:
        HTTPException: 404 when nothing is deployed at the domain.
    """
    validated = strict_domain(domain)
    app = get_store().get_app(validated)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return app


@router.get("", response_model=AppListResponse)
def list_apps(session: Annotated[dict, Depends(get_current_session)]) -> AppListResponse:
    """
    List every deployed application.

    Every application's service record, webhook flag and last deployment
    come from one store query each, and every application's systemd status
    is read concurrently through :func:`~noust.core.app_state.resolve_states_with_status`
    - so this endpoint costs a handful of queries and one round of systemctl
    calls, not four times the number of applications deployed.

    Args:
        session: The authenticated session.

    Returns:
        The applications, each with the live state of its service.
    """
    store = get_store()
    manager = ServiceManager(verbose=False)

    apps = store.list_apps()
    domains = [app.domain for app in apps]

    services_by_app_id = {
        service.app_id: service for service in store.list_services() if service.app_id is not None
    }
    webhook_flags = store.list_webhook_flags(domains)
    last_deployments = store.get_latest_deployments(domains)
    states = resolve_states_with_status(apps, manager)

    result = []
    for app in apps:
        state, status = states[app.domain]
        service = services_by_app_id.get(app.id) if app.id is not None else None
        result.append(
            _to_app_info(
                app,
                state,
                status,
                service,
                webhook_enabled=webhook_flags.get(app.domain, False),
                last_deployment=last_deployments.get(app.domain),
            )
        )

    return AppListResponse(apps=result, total=len(result))


def _check_initial_health(path: str | None, expect: str | None, timeout: int | None) -> None:
    """
    Refuse health settings the gate could not use, naming the field.

    Args:
        path: Path to probe, or None.
        expect: Accepted statuses, or None.
        timeout: Seconds to wait, or None.

    Raises:
        ValidationError: A value is not usable; ``field`` names it.
    """
    from noust.validators.health import (
        check_health_expect,
        check_health_path,
        check_health_timeout,
    )

    checks: tuple[tuple[str, Callable[[Any], Any], Any], ...] = (
        ("health_path", check_health_path, path),
        ("health_expect", check_health_expect, expect),
        ("health_timeout", check_health_timeout, timeout),
    )
    for field, check, value in checks:
        if value is None:
            continue
        try:
            check(value)
        except ValidationError as exc:
            exc.field = field
            raise


@router.post("", response_model=JobAcceptedResponse, status_code=202)
def create_app(
    body: CreateAppRequest,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Queue the deployment of a new application.

    Admin scope, through the blanket policy: the build runs as root. A local
    path as the source is further reserved to the operator in person; see
    :func:`_require_local_source_privilege`.

    Args:
        body: The deployment request.
        request: The incoming request, for the audit record of a refusal.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 409 when the domain is already deployed, 503 when no
            port is free, 403 when the source is a local path and the
            credential may not deploy one.
        PortError: When the requested port is not usable.
        DomainError: When the domain is not acceptable.
        ValidationError: A resource limit is out of range (400, with the
            range) - the same check ``PATCH .../limits`` runs, so a limit
            given at creation cannot be more permissive than one set later -
            or ``package_manager`` names one Noust does not drive, or neither a
            source nor a recipe was given.
        RecipeError: The recipe does not exist or is not available, or a
            source or a type was given with it (400).
    """
    domain = strict_domain(body.domain)
    recipe = get_recipe(body.recipe) if body.recipe is not None else None
    if recipe is not None:
        refuse_conflicts(source=body.source, app_type=body.app_type)
        if not recipe.available:
            raise RecipeError(
                f"{recipe.title} is not available in this release",
                details=recipe.unavailable_reason or "See GET /api/recipes",
            )
    elif not body.source:
        raise ValidationError(
            "A source is required", details="Give source, or a recipe from GET /api/recipes."
        )
    else:
        _require_local_source_privilege(request, session, body.source)
    app_type = recipe.app_type if recipe is not None else body.app_type
    if body.database is not None and (recipe is not None or app_type in OWN_DATABASE_TYPES):
        raise ValidationError(
            "A database cannot be created with "
            + ("a recipe" if recipe is not None else f"a {app_type} application"),
            details="A recipe, a monorepo and a Docker Compose project provision their own "
            "databases. Leave database out, or create one after the deploy with POST "
            "/api/apps/{domain}/databases.",
            field="database",
        )

    if get_store().get_app(domain):
        raise HTTPException(status_code=409, detail=f"Application already exists: {domain}")

    if body.port is not None:
        port: int | None = validate_port(body.port)
    else:
        port = find_available_port(
            preferred=(recipe.port if recipe is not None and recipe.port else DEFAULT_PORT),
            exclude=get_store().ports_owned_by_apps(),
        )
        if port is None:
            raise HTTPException(status_code=503, detail="No available port found")

    ResourceLimits(
        memory_max_mb=body.memory_max_mb,
        cpu_quota_percent=body.cpu_quota_percent,
        tasks_max=body.tasks_max,
    ).validated()
    # Checked before the job is queued, the same way PATCH .../health checks
    # them: a value the gate cannot use is a 400 now, not a failed deploy.
    _check_initial_health(body.health_path, body.health_expect, body.health_timeout)

    if body.package_manager is not None and body.package_manager not in SUPPORTED_PACKAGE_MANAGERS:
        raise ValidationError(
            f"Unsupported package manager: {body.package_manager!r}",
            details=f"Choose one of: {', '.join(SUPPORTED_PACKAGE_MANAGERS)}, "
            "or omit it to detect automatically from the project's lock file.",
        )

    job = get_job_manager().create_job(
        job_type=JobType.DEPLOY,
        name=f"Deploy {domain}",
        description=(
            f"Deploying {recipe.title} to {domain}"
            if recipe is not None
            else f"Deploying a {app_type} application to {domain}"
        ),
        func=deploy_app_job,
        kwargs={
            "domain": domain,
            "source": body.source or "",
            "app_type": app_type,
            "recipe": recipe.name if recipe is not None else None,
            "port": port,
            "branch": body.branch,
            "env_vars": body.env_vars,
            "webserver": body.webserver,
            "ssl": body.ssl,
            "subdomain_overrides": body.subdomain_overrides,
            "workspace_filter": body.workspace_filter,
            "skip_database": body.skip_database,
            "compose_file": body.compose_file,
            "compose_profiles": body.compose_profiles,
            "layout": body.layout,
            "include_www": body.include_www,
            "persistent_paths": body.persistent_paths,
            "memory_max_mb": body.memory_max_mb,
            "cpu_quota_percent": body.cpu_quota_percent,
            "tasks_max": body.tasks_max,
            "package_manager": body.package_manager,
            "github_installation_id": body.github_installation_id,
            "health_path": body.health_path,
            "health_expect": body.health_expect,
            "health_timeout": body.health_timeout,
            "database": dump_model(body.database) if body.database is not None else None,
        },
        metadata={
            "domain": domain,
            "app_type": app_type,
            "port": port,
            **({"recipe": recipe.name} if recipe is not None else {}),
        },
        actor=actor_label(session),
    )

    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Deployment queued for {domain}",
        job=job.to_dict(),
    )


class InspectSourceRequest(BaseModel):
    """Request to preview what a repository is before deploying it."""

    source: str = Field(..., description="Git URL, archive URL or local path")
    branch: str | None = Field(default=None, description="Git branch to inspect")
    github_installation_id: int | None = Field(
        default=None,
        description="GitHub App installation to read a private github.com repository with, "
        "as the repository list returned it. Omitted: the installation on the owner's account",
    )


class EnvKeyResponse(BaseModel):
    """One environment variable discovered in a repository's .env.example."""

    name: str
    default: str | None = None
    secret: bool
    required: bool


class SourceInspectionResponse(BaseModel):
    """
    What a repository is, discovered before anything is deployed from it.

    Attributes:
        app_type: The application type the new-app wizard would deploy as;
            the first entry of ``detected_types``.
        detected_types: Every registered application type that recognised
            the repository, most specific first (registry priority order).
        package_manager: The Node package manager the repository's lock file
            implies, or None when it is not a Node project.
        install_command: Argv the chosen deployer would run to install
            dependencies. Empty when the type has none.
        build_command: Argv the chosen deployer would run to build the
            project. Empty when there is nothing to build.
        start_command: Shell command the chosen deployer would run as the
            service's ``ExecStart``. Empty for a static site.
        default_port: Port the chosen deployer uses when none is requested.
        env_keys: Environment variables discovered from ``.env.example``.
        branch: The branch inspected, or the checkout's current branch when
            none was requested.
        commit: Short commit hash of the checkout, empty when the source is
            not a Git repository.
        compatible: Whether this server can deploy it as ``app_type`` as it
            is: false when a program the type needs is missing.
        verdict: What Noust found, in a sentence.
        suggestion: What to do before deploying, when there is something.
    """

    app_type: str
    detected_types: list[str]
    package_manager: str | None
    install_command: list[str]
    build_command: list[str]
    start_command: str
    default_port: int
    env_keys: list[EnvKeyResponse]
    branch: str
    commit: str
    compatible: bool | None = Field(
        default=None, description="Whether this server can deploy it as app_type as it is"
    )
    verdict: str | None = Field(default=None, description="What Noust found, in a sentence")
    suggestion: str | None = Field(
        default=None, description="What to do before deploying, when there is something"
    )
    platform_proposal: PlatformProposalResponse | None = Field(
        default=None,
        description="What the repository's Vercel, Railway, Render or Heroku configuration "
        "proposes, for the wizard to prefill; null when it carries none",
    )


#: How often the inspect endpoint asks whether its client is still there.
_DISCONNECT_POLL_SECONDS = 0.25

#: Not a status a client ever reads: nginx's "client closed request", so the
#: access log tells a cancelled inspection from a failed one.
_CLIENT_CLOSED_REQUEST = 499

_T = TypeVar("_T")


async def _run_until_disconnected(
    request: Request, cancel: threading.Event, work: Callable[[], _T]
) -> _T:
    """
    Run blocking work in a worker thread, cancelling it if the client leaves.

    The console's Cancel aborts the browser's fetch; uvicorn then answers
    ``http.disconnect`` on the request, which is polled here while the work
    runs. On a disconnect ``cancel`` is set, which the command runner
    watches, and this still waits for the worker to return, so everything
    the work cleans up on its way out is gone before the request ends.

    Polled with ``asyncio.wait`` rather than a task group: a task group
    reports the work's own exception inside an ``ExceptionGroup``, which the
    API's error boundary would not recognise as the ``NoustError`` it is.

    Args:
        request: The request whose client is watched.
        cancel: Set when the client disconnects, or when this coroutine is
            itself cancelled (the server shutting down).
        work: What to run; it must honour ``cancel``.

    Returns:
        What ``work`` returned.

    Raises:
        Exception: Whatever ``work`` raised, as it raised it.
    """
    task = asyncio.ensure_future(run_in_threadpool(work))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=_DISCONNECT_POLL_SECONDS)
            if not task.done() and not cancel.is_set() and await request.is_disconnected():
                cancel.set()
    finally:
        if not task.done():
            cancel.set()
    return task.result()


# NOTE: declared before GET /{domain} and its siblings so a request for
# /api/apps/inspect is never shadowed by a route that treats "inspect" as a
# domain.
@router.post("/inspect", response_model=SourceInspectionResponse)
async def inspect_app_source(
    body: InspectSourceRequest,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> SourceInspectionResponse | Response:
    """
    Preview what a repository is before deploying it.

    Fetches what detection needs (for git, ``ls-remote`` and a sparse,
    blobless checkout of the files the detectors read), detects the
    application type, and reports the commands, port and environment
    variables a deployment would use and whether this server can deploy it,
    so the new-app wizard has something real to show instead of a guess.
    Nothing is written outside the scratch checkout, which is removed before
    this returns, and no application, domain or unit is created.

    A client that disconnects (the wizard's Cancel aborts the fetch) cancels
    the inspection: the git command running is killed and the checkout
    removed at once, instead of the clone running on to its deadline.

    Admin scope, like creating one: fetching runs as root and reads back what
    it fetched. A local path is reserved to the operator in person, exactly
    as it is for ``POST /api/apps``.

    Args:
        body: The source to inspect and the branch to check out.
        request: The incoming request, for the audit record of a refusal.
        session: The authenticated session.

    Returns:
        The inspection result, or an empty 499 once a cancelled inspection
        has stopped (nobody is there to read it).

    Raises:
        HTTPException: 403 when the source is a local path and the credential
            may not read one.
        SourceError: The source is invalid, or fetching it failed. Answered
            as 400: the operator gave a source Noust cannot reach, not a
            server fault.
        ValidationError: The checkout matches no registered application
            type. ``inspect_source`` raises ``DeploymentError`` for this -
            right for the CLI, where it means the whole operation failed -
            but here it is the wizard's input that could not be classified,
            so it is translated to the API's validation-error contract
            (400 with details) instead of the 500 an unqualified
            ``DeploymentError`` would answer.
    """
    cancel = threading.Event()

    def work() -> SourceInspection:
        _require_local_source_privilege(request, session, body.source)
        return inspect_source(
            body.source,
            branch=body.branch,
            cancel=cancel,
            github_installation_id=body.github_installation_id,
        )

    try:
        result = await _run_until_disconnected(request, cancel, work)
    except DeploymentError as exc:
        raise ValidationError(exc.message, details=exc.details) from exc
    except CommandCancelled:
        _logger.info("Inspection of %s cancelled: the client went away", body.source)
        return Response(status_code=_CLIENT_CLOSED_REQUEST)
    return SourceInspectionResponse(
        app_type=result.app_type,
        detected_types=result.detected_types,
        package_manager=result.package_manager,
        install_command=result.install_command,
        build_command=result.build_command,
        start_command=result.start_command,
        default_port=result.default_port,
        env_keys=[
            EnvKeyResponse(
                name=key.name,
                default=key.default,
                secret=key.secret,
                required=key.required,
            )
            for key in result.env_keys
        ],
        branch=result.branch,
        commit=result.commit,
        compatible=result.compatible,
        verdict=result.verdict or None,
        suggestion=result.suggestion,
        platform_proposal=platform_proposal_response(getattr(result, "platform_proposal", None)),
    )


class AppTypeInfo(BaseModel):
    """One application type the new-app wizard may offer."""

    type: str
    name: str
    default_port: int


class AppTypesResponse(BaseModel):
    """Every application type Noust can deploy."""

    types: list[AppTypeInfo]


# NOTE: declared before GET /{domain} for the same reason /inspect is: a
# request for /api/apps/types must not be read as a request for the
# application whose domain is literally "types".
@router.get("/types", response_model=AppTypesResponse)
def list_app_types(session: Annotated[dict, Depends(get_current_session)]) -> AppTypesResponse:
    """
    List the application types Noust can deploy.

    :func:`~noust.deployers.registry.available_types` is the one source of
    truth - the CLI's ``--type`` choices come from it too - so a deployer
    registered with :meth:`~noust.deployers.registry.DeployerRegistry.register`
    reaches the wizard the moment it exists, instead of needing a second,
    hand-kept copy of the list in the console.

    Args:
        session: The authenticated session.

    Returns:
        Every registered type, most specific first, ``auto`` last.
    """
    return AppTypesResponse(
        types=[
            AppTypeInfo(type=entry["type"], name=entry["name"], default_port=entry["default_port"])
            for entry in available_types()
        ]
    )


@router.get("/{domain}", response_model=AppInfo)
def get_app(domain: str, session: Annotated[dict, Depends(get_current_session)]) -> AppInfo:
    """
    Describe one application.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The application description.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = strict_domain(domain)

    store = get_store()
    app = store.get_app(validated)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")

    service = store.get_service_by_app_id(app.id) if app.id else None
    manager = ServiceManager(verbose=False)
    state, status = resolve_state_with_status(app, manager)

    recent = store.list_deployments(domain=app.domain, limit=1)

    info = _to_app_info(
        app,
        state,
        status,
        service,
        webhook_enabled=store.get_webhook_secret(app.domain) is not None,
        last_deployment=recent[0] if recent else None,
    )
    # Only the detail view pays for this: one deployer instantiation, not one
    # per application in the list.
    info.build_command = _deployer_build_command(app)
    return info


def _service_action(domain: str, action: str, past_tense: str) -> AppActionResponse:
    """
    Run one systemctl verb against an application's unit.

    Args:
        domain: Domain of the application, as supplied by the client.
        action: ServiceManager method to call.
        past_tense: Word used in the response message.

    Returns:
        The action outcome.

    Raises:
        HTTPException: 404 when the application has no unit.
        ServiceError: When systemd refuses the operation.
        DeploymentError: When a PHP application's pool cannot be controlled.
    """
    validated = strict_domain(domain)
    app_name = domain_to_app_name(validated)

    app = get_store().get_app(validated)
    if app is not None and is_php_fpm(app):
        # Its pool, not a unit: the same control the CLI uses.
        detail = control_pool(app, action)
        return AppActionResponse(
            success=True,
            message=f"Application {past_tense}: {validated}. {detail}",
            domain=validated,
        )

    manager = ServiceManager(verbose=False)
    if not manager.get_status(app_name).get("exists"):
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")

    getattr(manager, action)(app_name)

    return AppActionResponse(
        success=True, message=f"Application {past_tense}: {validated}", domain=validated
    )


@router.post("/{domain}/start", response_model=AppActionResponse)
def start_app(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppActionResponse:
    """
    Start an application.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The action outcome.
    """
    return _service_action(domain, "start", "started")


@router.post("/{domain}/stop", response_model=AppActionResponse)
def stop_app(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppActionResponse:
    """
    Stop an application.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The action outcome.
    """
    return _service_action(domain, "stop", "stopped")


@router.post("/{domain}/restart", response_model=AppActionResponse)
def restart_app(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppActionResponse:
    """
    Restart an application.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The action outcome.
    """
    return _service_action(domain, "restart", "restarted")


@router.get("/{domain}/logs", response_model=AppLogsResponse)
def get_app_logs(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    lines: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> AppLogsResponse:
    """
    Read journal output for an application.

    Args:
        domain: Domain of the application.
        lines: How many lines to return.
        session: The authenticated session.

    Returns:
        The log output.
    """
    validated = strict_domain(domain)
    app_name = domain_to_app_name(validated)

    logs = ServiceManager(verbose=False).logs(app_name, lines=lines) or "No logs available"

    return AppLogsResponse(domain=validated, logs=logs, lines=lines)


@router.get("/{domain}/env", response_model=AppEnvResponse)
def get_app_env(
    domain: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    unmask: Annotated[bool, Query()] = False,
) -> AppEnvResponse:
    """
    Read an application's environment from its ``.env`` file.

    This reads the file :mod:`noust.deployers.helpers.app_env` writes, the
    same one ``noust env show`` reads on the terminal - not the snapshot the
    store recorded at deploy time, which can drift the moment anyone edits
    the file by hand. On the release layout that is ``shared/.env``.

    Args:
        domain: Domain of the application.
        request: The incoming request, for the audit record.
        session: The authenticated session.
        unmask: When true, values are returned in clear instead of redacted.
            Every such read is audited, naming the caller but never a value.

    Returns:
        The stored variables, redacted unless unmask was asked for.

    Raises:
        HTTPException: 403 when unmasking with less than an admin credential,
            404 when the application is unknown.
    """
    if unmask:
        # Here, in the function that produces the secrets, and not in the URL
        # policy: the panel's reveal and edit pages call this function too, and
        # a guard keyed on the /api path let a read token through them.
        ensure_scope(request, session, "admin")
        ensure_elevated(request, session)

    app = _env_app(domain)
    values = read_app_env(app)
    marks = app.env_secret_marks
    # A read-scope token can reach this endpoint (GET only ever needs
    # "read") and, unmasked or not, must not learn which vendor a secret's
    # shape matched - that is information about the value itself.
    admin = scope_satisfies(str(session.get("scope") or "read"), "admin")
    secrets = _secrets_map(values, marks, reveal_kind=admin)

    if unmask:
        audit = get_audit_logger()
        if audit:
            audit.record(
                action="apps.env.reveal",
                result="success",
                client_ip=get_client_ip(request),
                actor=actor_label(session),
                resource=f"/api/apps/{app.domain}/env",
                detail=f"revealed {len(values)} variable(s) in clear",
            )
        return AppEnvResponse(
            domain=app.domain, variables=dict(values), unmasked=True, secrets=secrets
        )

    return AppEnvResponse(
        domain=app.domain, variables=_redact_env(values, marks), unmasked=False, secrets=secrets
    )


@router.put("/{domain}/env", response_model=AppEnvUpdateResponse)
def update_app_env(
    domain: str,
    body: UpdateAppEnvRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> AppEnvUpdateResponse:
    """
    Replace an application's ``.env`` file wholesale.

    Every name and value is validated against what can safely reach a
    systemd unit (:mod:`noust.validators.environment`) before anything is
    written, so a rejected variable leaves the file on disk untouched. The
    write goes through :func:`~noust.deployers.helpers.app_env.write_app_env`,
    the same function ``noust env configure`` uses, so the file lands 0600,
    owned by the service account, in ``shared/`` on the release layout - and
    a mark on a name the write drops is pruned there too, not just here.

    The application is not restarted: a process already running keeps the
    environment it started with until it is, so the caller is told a restart
    is required rather than one being queued silently underneath it.

    Args:
        domain: Domain of the application.
        body: The complete name to value mapping to write.
        request: The incoming request, for the audit record.
        session: The authenticated session.

    Returns:
        Confirmation that a restart is required to pick up the change.

    Raises:
        HTTPException: 404 when the application is unknown, 422 when a name
            or a value is not safe to write into a systemd unit, or when the
            request tries to set PORT or NODE_ENV, which the unit sets inline
            and are refused by :func:`~noust.deployers.helpers.app_env.write_app_env`.
    """
    app = _env_app(domain)
    before = read_app_env(app)

    try:
        write_app_env(app, body.variables)
    except EnvironmentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    after = read_app_env(app)
    changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
    audit = get_audit_logger()
    if audit:
        audit.record(
            action="apps.env.update",
            result="success",
            client_ip=get_client_ip(request),
            actor=actor_label(session),
            resource=f"/api/apps/{app.domain}/env",
            detail=f"changed keys: {', '.join(changed)}" if changed else "no keys changed",
        )

    return AppEnvUpdateResponse(domain=app.domain, restart_required=True)


@router.put("/{domain}/env/marks", response_model=AppEnvSecretsResponse)
def update_app_env_marks(
    domain: str,
    body: UpdateEnvMarksRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> AppEnvSecretsResponse:
    """
    Set or clear operator overrides on an application's environment variables.

    A mark always wins over the automatic classification, in both
    directions: it is how an operator corrects a false positive (``KEYBOARD_LAYOUT``
    is not a secret) or a false negative (``SESSION`` holding a value nothing
    about its name suggests is one). Marks merge with what is already
    stored - a request need only name the variables it changes - and a
    ``null`` removes a mark rather than setting one, going back to the
    automatic classification. Needs sudo mode, like writing the environment
    itself: it changes what the console and the audit log will treat as
    safe to display.

    Args:
        domain: Domain of the application.
        body: The marks to set or clear.
        request: The incoming request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The application's full secrecy classification after the change.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A variable name is not a valid environment variable
            name (400).
    """
    app = _env_app(domain)
    marks = dict(app.env_secret_marks)
    for name, mark in body.marks.items():
        if mark is None:
            marks.pop(name, None)
        else:
            marks[name] = mark

    get_store().set_env_secret_marks(app.domain, marks)

    audit = get_audit_logger()
    if audit:
        audit.record(
            action="apps.env.marks",
            result="success",
            client_ip=get_client_ip(request),
            actor=actor_label(session),
            resource=f"/api/apps/{app.domain}/env/marks",
            detail=_mark_change_detail(body.marks),
        )

    values = read_app_env(app)
    return AppEnvSecretsResponse(domain=app.domain, secrets=_secrets_map(values, marks))


@router.delete("/{domain}", response_model=JobAcceptedResponse, status_code=202)
def delete_app(
    domain: str,
    session: Annotated[dict, Depends(require_elevated)],
    remove_files: Annotated[bool, Query()] = False,
    remove_ssl: Annotated[bool, Query()] = False,
    remove_adopted_directory: Annotated[
        str | None,
        Query(
            description="The application's directory, named exactly, when it is outside "
            "Noust's apps directory (an adopted stack): only then is it removed with the files"
        ),
    ] = None,
) -> JobAcceptedResponse:
    """
    Queue the removal of an application.

    Deletion stops a unit, rewrites the web server configuration, may call
    certbot and may delete a large directory, so it runs as a job rather than
    on the request path. A directory outside Noust's apps directory (an
    adopted stack's) is kept, files or not, unless it is named.

    Args:
        domain: Domain of the application.
        remove_files: Also delete the application directory.
        remove_ssl: Also delete the certificate.
        remove_adopted_directory: The directory named for removal when it is
            outside the apps directory.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The directory named is not the application's (400).
    """
    validated = strict_domain(domain)

    app = get_store().get_app(validated)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    # Answered now, while the operator can correct it; the job asks again.
    check_adopted_directory(
        validated, app_root(app), remove_files=remove_files, named=remove_adopted_directory
    )

    job = get_job_manager().create_job(
        job_type=JobType.DELETE,
        name=f"Delete {validated}",
        description=f"Deleting the application at {validated}",
        func=delete_app_job,
        kwargs={
            "domain": validated,
            "remove_files": remove_files,
            "remove_ssl": remove_ssl,
            "remove_adopted_directory": remove_adopted_directory,
        },
        metadata={"domain": validated},
        actor=actor_label(session),
    )

    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Deletion queued for {validated}",
        job=job.to_dict(),
    )


class RollbackPointOut(BaseModel):
    """One backup an application can be rolled back to."""

    id: str
    created_at: str
    description: str
    size_bytes: int
    git_commit: str | None = None

    _iso_timestamps = iso_offset_validator("created_at")


class RollbackPointsResponse(BaseModel):
    """The backups an application can return to, newest first."""

    items: list[RollbackPointOut]
    total: int


@router.get("/{domain}/rollback-points", response_model=RollbackPointsResponse)
def list_rollback_points(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> RollbackPointsResponse:
    """
    List the backups an application can be rolled back to.

    Deliberately not gated on the application still being deployed: a backup
    for a domain Noust no longer serves is still a rollback point until it is
    pruned, the same reasoning that keeps deployment history around after an
    application is deleted (see :mod:`noust.web.views.deployments`).

    Args:
        domain: Domain whose rollback points are asked for.
        session: The authenticated session.

    Returns:
        The points, newest first, from
        :meth:`~noust.managers.backup_manager.RollbackManager.list_rollback_points`
        - the one implementation, shared with ``noust backup rollback --list``.
    """
    validated = strict_domain(domain)
    points = RollbackManager(verbose=False).list_rollback_points(validated)
    items = [
        RollbackPointOut(
            id=point.id,
            created_at=point.created_at,
            description=point.description,
            size_bytes=point.size_bytes,
            git_commit=point.git_commit,
        )
        for point in points
    ]
    return RollbackPointsResponse(items=items, total=len(items))


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------


class ReleaseOut(BaseModel):
    """
    One release of an application on the release layout.

    Attributes:
        id: Release id, the directory name under ``releases/``.
        commit: Short commit it was built from; None for a non-git source.
        created_at: When it was created, ISO 8601 with an explicit UTC offset.
        activated_at: When it last became active, if it ever did.
        status: ``active``, ``superseded``, ``rolled_back``, ``failed`` or
            ``built``.
        active: Whether it is the one serving.
        on_disk: Whether it can be activated. A failed release is listed for
            a while after its directory was removed.
    """

    id: str
    commit: str | None = None
    created_at: str
    activated_at: str | None = None
    status: str
    active: bool
    on_disk: bool

    _iso_timestamps = iso_offset_validator("created_at", "activated_at")


class ReleasesResponse(BaseModel):
    """The releases of an application, newest first."""

    domain: str
    items: list[ReleaseOut]
    total: int


class ReleaseActivationResponse(BaseModel):
    """
    The outcome of activating a release.

    Attributes:
        domain: The application's domain.
        release_id: The release now serving.
        previous_id: The release that served before, if any.
        changed: False when the release was already active and nothing was done.
        rolled_back: Whether the release activated is older than the one it
            replaced.
        deployment_id: The deployment history row that records it.
    """

    domain: str
    release_id: str
    previous_id: str | None = None
    changed: bool
    rolled_back: bool
    deployment_id: int | None = None


def _release_app_or_error(domain: str) -> App:
    """
    Look up an application whose releases a request is about.

    Args:
        domain: Domain from the request.

    Returns:
        The stored application, on the release layout.

    Raises:
        HTTPException: 404 when it is unknown, 409 when it is deployed in
            place: it has no releases until it is migrated.
    """
    app = _env_app(domain)
    if app.layout != RELEASES:
        raise HTTPException(
            status_code=409,
            detail=f"{app.domain} is deployed in place and has no releases. Migrate it to "
            f"releases first (POST /api/apps/{app.domain}/migrate), or roll back to a backup.",
        )
    return app


@router.get("/{domain}/releases", response_model=ReleasesResponse)
def get_app_releases(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ReleasesResponse:
    """
    List an application's releases, newest first.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The releases, from :func:`noust.deployers.lifecycle.list_releases`,
        the same listing ``noust releases list`` prints.

    Raises:
        HTTPException: 404 when the application is unknown, 409 when it is
            deployed in place.
    """
    app = _release_app_or_error(domain)
    items = [
        ReleaseOut(
            id=release.id,
            commit=release.commit,
            created_at=release.created_at,
            activated_at=release.activated_at,
            status=release.status,
            active=release.active,
            on_disk=release.on_disk,
        )
        for release in list_releases(app.domain)
    ]
    return ReleasesResponse(domain=app.domain, items=items, total=len(items))


@router.post("/{domain}/releases/{release_id}/activate", response_model=ReleaseActivationResponse)
def activate_app_release(
    domain: str,
    release_id: str,
    session: Annotated[dict, Depends(get_current_session)],
    schema_changed_ok: Annotated[
        bool,
        Query(description="Go back even past deployments that changed the database schema"),
    ] = False,
) -> ReleaseActivationResponse:
    """
    Make a release the one that serves: an instant rollback, or a roll forward.

    Needs the ``deploy`` scope, like queueing an update: it changes what code
    runs, and nothing else. The release passes the same health gate as a
    deploy; one that does not is recorded as failed and the release that was
    serving is put back before this answers.

    Args:
        domain: Domain of the application.
        release_id: The release to activate.
        session: The authenticated session.
        schema_changed_ok: The operator confirmed going back past deployments
            that changed the database's schema.

    Returns:
        What was done.

    Raises:
        HTTPException: 400 for something that is not a release id, 404 for an
            unknown application or a release that is not on disk, 409 for an
            application deployed in place.
        SchemaChangedError: 409, naming the deployments, when going back past
            a schema change was not confirmed.
        DeploymentError: The release did not pass its health check; the
            details carry the probe's and the journal's own output.
    """
    app = _release_app_or_error(domain)
    if not is_release_id(release_id):
        raise HTTPException(status_code=400, detail=f"Not a release id: {release_id!r}")
    if not any(r.id == release_id and r.on_disk for r in list_releases(app.domain)):
        raise HTTPException(
            status_code=404, detail=f"Release {release_id} of {app.domain} is not on disk"
        )

    outcome = activate_release(
        app.domain,
        release_id,
        trigger=DeploymentTrigger.PANEL.value,
        schema_changed_ok=schema_changed_ok,
    )
    return ReleaseActivationResponse(
        domain=outcome.domain,
        release_id=outcome.release.id,
        previous_id=outcome.previous.id if outcome.previous is not None else None,
        changed=outcome.changed,
        rolled_back=outcome.went_back,
        deployment_id=outcome.deployment_id,
    )


# ---------------------------------------------------------------------------
# Migration to the release layout
# ---------------------------------------------------------------------------


class MigrationPlanOut(BaseModel):
    """
    What migrating an in-place application to releases would do.

    Attributes:
        domain: The application's domain.
        app_path: Its directory.
        release_id: The name the first release would get (a forecast).
        commit: The commit the tree is at, when it is a git checkout.
        persistent: Paths that move to ``shared/`` and are linked into every
            release from now on.
        persistent_source: ``git`` (what git does not track), ``explicit``
            (as named) or ``common`` (the usual upload directories).
        env_files: Environment files that move to ``shared/``.
        unit: The unit that runs it, or None for a site.
        unit_rewrite: Whether the unit is rewritten to run from ``current``.
        site_rewrite: Whether the site is rewritten to serve ``current``.
        untracked_files: Files that stay in the first release only.
        warnings: What the operator should read before going ahead.
        files: Regular files the directory holds; all of them are kept.
        bytes: Their total size.
    """

    domain: str
    app_path: str
    release_id: str
    commit: str | None = None
    persistent: list[str]
    persistent_source: str
    env_files: list[str]
    unit: str | None = None
    unit_rewrite: bool
    site_rewrite: bool
    untracked_files: list[str]
    warnings: list[str]
    files: int
    bytes: int


class MigrateRequest(BaseModel):
    """Request to migrate an application to the release layout."""

    persist: list[str] | None = Field(
        default=None,
        description="Paths to keep in shared/, relative to the application. "
        "Omitted: what git does not track, or the usual upload directories",
    )


def _migratable(domain: str) -> App:
    """
    Look up an application a migration request is about.

    Args:
        domain: Domain from the request.

    Returns:
        The stored application, deployed in place.

    Raises:
        HTTPException: 404 when it is unknown, 409 when it is on releases
            already.
    """
    app = _env_app(domain)
    if app.layout == RELEASES:
        raise HTTPException(
            status_code=409, detail=f"{app.domain} is on the release layout already"
        )
    return app


def _plan_out(plan: MigrationPlan) -> MigrationPlanOut:
    """
    Translate a plan to its API model.

    Args:
        plan: The plan.

    Returns:
        The model.
    """
    return MigrationPlanOut(
        domain=plan.domain,
        app_path=plan.app_path,
        release_id=plan.release_id,
        commit=plan.commit,
        persistent=list(plan.persistent),
        persistent_source=plan.persistent_source,
        env_files=list(plan.env_files),
        unit=plan.unit,
        unit_rewrite=plan.unit_rewrite,
        site_rewrite=plan.site_rewrite,
        untracked_files=list(plan.untracked_files),
        warnings=list(plan.warnings),
        files=plan.count.files,
        bytes=plan.count.bytes,
    )


@router.get("/{domain}/migrate/plan", response_model=MigrationPlanOut)
def get_migration_plan(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    persist: Annotated[list[str] | None, Query()] = None,
) -> MigrationPlanOut:
    """
    Show what migrating an in-place application to releases would do. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.
        persist: Paths to keep in ``shared/``; repeat the parameter for each.

    Returns:
        The plan, from :func:`noust.deployers.migrate.plan_migration`.

    Raises:
        HTTPException: 404 when the application is unknown, 409 when it is on
            releases already.
        ValidationError: A path in ``persist`` is not inside the application.
        DeploymentError: Its type cannot use releases.
    """
    app = _migratable(domain)
    return _plan_out(plan_migration(app.domain, persist))


@router.post("/{domain}/migrate", response_model=JobAcceptedResponse, status_code=202)
def migrate_app(
    domain: str,
    body: MigrateRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue the move of an in-place application onto the release layout.

    Rewrites the unit and the site and moves the whole application tree, so
    it needs sudo mode. It runs as a job, like an update: the unit is stopped
    while the tree moves and the health check waits for it on the new layout,
    and the job keeps its log whatever happens to the browser. The job's
    result has the first release, what moved to ``shared/`` and the file
    counts before and after.

    The plan is worked out here only to refuse a request that cannot run (an
    application on releases already, a path that is not inside it); the job
    plans again when it runs, so what is migrated is what is on disk then,
    never a plan the client sent.

    Args:
        domain: Domain of the application.
        body: Paths to keep in ``shared/``, if the detection is not wanted.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown, 409 when it is on
            releases already.
        ValidationError: A path in ``persist`` is not inside the application.
        DeploymentError: Its type cannot use releases, or the paths named
            leave a SQLite database out.
    """
    app = _migratable(domain)
    plan_migration(app.domain, body.persist)
    job = get_job_manager().create_job(
        job_type=JobType.MIGRATE,
        name=f"Migrate {app.domain}",
        description=f"Moving {app.domain} onto the release layout",
        func=migrate_app_job,
        kwargs={"domain": app.domain, "persist": body.persist},
        metadata={"domain": app.domain},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Migration of {app.domain} to releases queued",
        job=job.to_dict(),
    )


# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------


class UpdateLimitsRequest(BaseModel):
    """
    The memory, CPU and task limits an application's unit must have.

    The three are set together: a field left out or null removes that limit.
    """

    memory_max_mb: int | None = Field(
        default=None, description="MemoryMax, in MB; at least 64. Null: no limit"
    )
    cpu_quota_percent: int | None = Field(
        default=None,
        description="CPUQuota, in percent of one CPU (200 is two CPUs); 1 to 100 per CPU. "
        "Null: no limit",
    )
    tasks_max: int | None = Field(
        default=None, description="TasksMax, processes and threads; at least 16. Null: no limit"
    )
    restart: bool = Field(
        default=False, description="Restart now, so the processes run under the new limits"
    )


class LimitsResponse(BaseModel):
    """
    The limits an application has now.

    Attributes:
        domain: The application's domain.
        memory_max_mb: Memory limit in MB, or None.
        cpu_quota_percent: CPU quota in percent of one CPU, or None.
        tasks_max: Task limit, or None.
        units: The units rewritten.
        restarted: Whether they were restarted.
        restart_required: Whether the running processes still have the old
            limits until they are restarted.
    """

    domain: str
    memory_max_mb: int | None = None
    cpu_quota_percent: int | None = None
    tasks_max: int | None = None
    units: list[str]
    restarted: bool
    restart_required: bool


@router.patch("/{domain}/limits", response_model=LimitsResponse)
def update_app_limits(
    domain: str,
    body: UpdateLimitsRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> LimitsResponse:
    """
    Set the memory, CPU and task limits of an application's unit.

    Rewrites the unit (every unit, for a monorepo) and reloads systemd, which
    is a change to what runs as root's configuration, so it needs sudo mode
    like editing a unit by hand does. The values are validated where every
    caller's are, in the service manager.

    Args:
        domain: Domain of the application.
        body: The limits, and whether to restart now.
        session: The authenticated, elevated session.

    Returns:
        The limits the application has now.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A limit is out of range (400, with the range).
        DeploymentError: Nothing runs as a unit for it.
    """
    app = _env_app(domain)
    change = set_resource_limits(
        app.domain,
        ResourceLimits(
            memory_max_mb=body.memory_max_mb,
            cpu_quota_percent=body.cpu_quota_percent,
            tasks_max=body.tasks_max,
        ),
        restart=body.restart,
    )
    return LimitsResponse(
        domain=change.domain,
        memory_max_mb=change.limits.memory_max_mb,
        cpu_quota_percent=change.limits.cpu_quota_percent,
        tasks_max=change.limits.tasks_max,
        units=list(change.units),
        restarted=change.restarted,
        restart_required=not change.restarted,
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class UpdateHealthRequest(BaseModel):
    """
    What the health gate must ask of an application.

    The three are set together, like the limits: a field left out or null
    goes back to its default.
    """

    path: str | None = Field(
        default=None, description="Path to probe on 127.0.0.1, such as /healthz. Null: /"
    )
    expect: str | None = Field(
        default=None,
        description="Statuses that mean up, 100 to 599: 200-399, or 200,204. "
        "Null: any status below 500",
    )
    timeout: int | None = Field(
        default=None, description="Seconds it gets to answer, 5 to 600. Null: 30"
    )


class HealthCheckResponse(BaseModel):
    """
    The health check an application has now.

    Attributes:
        domain: The application's domain.
        path: The path it set, or None for the default.
        expect: The statuses it set, or None for the default.
        timeout: The seconds it set, or None for the default.
        effective_path: The path the gate requests.
        effective_expect: The statuses the gate accepts, as an operator
            reads them: the list, or "any status below 500".
        effective_timeout: The seconds the gate waits.
    """

    domain: str
    path: str | None = None
    expect: str | None = None
    timeout: int | None = None
    effective_path: str
    effective_expect: str
    effective_timeout: int


@router.patch("/{domain}/health", response_model=HealthCheckResponse)
def update_app_health(
    domain: str,
    body: UpdateHealthRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> HealthCheckResponse:
    """
    Set the path, the accepted statuses and the timeout of an application's health check.

    The health gate decides which release may serve, so changing what it
    asks needs sudo mode, like the limits. The values are validated where
    they are stored; nothing restarts.

    Args:
        domain: Domain of the application.
        body: The three settings.
        session: The authenticated, elevated session.

    Returns:
        The settings the application has now, and what the gate asks.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A value is not one the gate can use (400).
        DeploymentError: It is a static site, checked by its files.
    """
    app = _env_app(domain)
    app = set_health_check(app.domain, path=body.path, expect=body.expect, timeout=body.timeout)
    check = HealthCheck.for_app(app)
    return HealthCheckResponse(
        domain=app.domain,
        path=app.health_path,
        expect=app.health_expect,
        timeout=app.health_timeout,
        effective_path=check.path,
        effective_expect=check.describe_expect(),
        effective_timeout=check.seconds,
    )


# ---------------------------------------------------------------------------
# The branch it deploys from
# ---------------------------------------------------------------------------


class UpdateBranchRequest(BaseModel):
    """Pin the branch an application deploys from, or unpin it."""

    branch: str | None = Field(
        ...,
        description="The branch to pin; it must exist on the remote. Null: any push deploys",
    )


class BranchResponse(BaseModel):
    """
    The branch an application deploys from now.

    Attributes:
        domain: The application's domain.
        branch: The pinned branch, or None when any push deploys.
        pinned: Whether a branch is pinned.
        commit: The branch's head on the remote when it was pinned.
        previous: The branch it had before.
    """

    domain: str
    branch: str | None = None
    pinned: bool
    commit: str | None = None
    previous: str | None = None


@router.patch("/{domain}/branch", response_model=BranchResponse)
def update_app_branch(
    domain: str,
    body: UpdateBranchRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> BranchResponse:
    """
    Pin the branch an application deploys from, or unpin it.

    With a branch pinned, the webhook ignores pushes to any other branch and
    every update builds it; unpinned, any push deploys. The branch is checked
    on the remote first; nothing is fetched or rebuilt until the next update.
    Changing what deploys needs sudo mode, like the health check and the limits.

    Args:
        domain: Domain of the application.
        body: The branch, or null.
        session: The authenticated, elevated session.

    Returns:
        The branch it deploys from now, its head, and the one before.

    Raises:
        HTTPException: 404 when the application is unknown.
        SourceError: Not deployed from git, not a branch name, no such branch
            on the remote, or the remote cannot be read (400, git's words in
            ``output``).
    """
    app = _env_app(domain)
    pin = set_branch(app.domain, body.branch)
    return BranchResponse(
        domain=pin.domain,
        branch=pin.branch,
        pinned=pin.branch is not None,
        commit=pin.commit,
        previous=pin.previous,
    )


# ---------------------------------------------------------------------------
# The tags it deploys
# ---------------------------------------------------------------------------


class UpdateFollowTagsRequest(BaseModel):
    """Make an application deploy the tags that match a pattern, or stop."""

    pattern: str | None = Field(
        ...,
        description="A glob over tag names such as 'v*'. Null: follow a branch again",
    )


class FollowTagsResponse(BaseModel):
    """
    The tags an application deploys now.

    Attributes:
        domain: The application's domain.
        follow_tags: The glob it follows, or None when it follows a branch.
        following: Whether it follows tags.
        previous: The glob it followed before.
    """

    domain: str
    follow_tags: str | None = None
    following: bool
    previous: str | None = None


@router.patch("/{domain}/follow-tags", response_model=FollowTagsResponse)
def update_app_follow_tags(
    domain: str,
    body: UpdateFollowTagsRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> FollowTagsResponse:
    """
    Make an application deploy the tags that match a pattern, or stop.

    Following tags replaces following a branch: the webhook deploys the tag a
    release or a tag push names, in version order and never an older one, and
    ignores pushes to branches; an update with no tag deploys the newest tag
    that matches. Nothing is fetched or rebuilt until the next release or
    update. Changing what deploys needs sudo mode, like the branch.

    Args:
        domain: Domain of the application.
        body: The pattern, or null.
        session: The authenticated, elevated session.

    Returns:
        What it follows now and what it followed before.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The pattern is not a tag glob, or a branch is pinned
            (400, ``follow_tags`` in ``fields``).
        SourceError: Not deployed from git.
    """
    app = _env_app(domain)
    change = set_follow_tags(app.domain, body.pattern)
    return FollowTagsResponse(
        domain=change.domain,
        follow_tags=change.pattern,
        following=change.pattern is not None,
        previous=change.previous,
    )


# ---------------------------------------------------------------------------
# Release retention
# ---------------------------------------------------------------------------


class UpdateRetentionRequest(BaseModel):
    """How many releases an application keeps."""

    keep: int = Field(
        ...,
        description="Releases to keep, 1 to 50. The active one and the rollback target "
        "are always kept",
    )


class RetentionResponse(BaseModel):
    """
    The retention an application has now.

    Attributes:
        domain: The application's domain.
        keep_releases: Releases it keeps.
        pruned: Ids of the releases removed to honour it, oldest first.
    """

    domain: str
    keep_releases: int
    pruned: list[str]


@router.patch("/{domain}/releases/retention", response_model=RetentionResponse)
def update_release_retention(
    domain: str,
    body: UpdateRetentionRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> RetentionResponse:
    """
    Set how many releases an application keeps, and prune to it now.

    Pruning deletes release directories, so it needs sudo mode. The number
    is validated where it is stored.

    Args:
        domain: Domain of the application.
        body: The retention.
        session: The authenticated, elevated session.

    Returns:
        The retention it has now and what was removed.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The number is out of range (400).
        DeploymentError: It is deployed in place, with no releases.
    """
    app = _env_app(domain)
    change = set_release_retention(app.domain, body.keep)
    return RetentionResponse(
        domain=change.domain, keep_releases=change.keep_releases, pruned=list(change.pruned)
    )
