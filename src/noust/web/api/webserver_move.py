# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which web server serves each site, and moving them to the other one (backlog item 105).

A client of :mod:`noust.deployers.webserver_move_plan` and :mod:`noust.deployers.webserver_move`,
like ``noust webserver``:

- ``GET /api/webserver/status`` says what is behind each web server and which one holds ports 80
  and 443.
- ``GET /api/webserver/migration/plan`` and ``GET /api/apps/{domain}/webserver/plan`` say what
  moving would do, site by site and line by line, and change nothing.
- ``POST /api/webserver/migrate`` and ``POST /api/apps/{domain}/webserver/migrate`` queue the move
  as a job: the new server tests each translated site, takes over, and every site is asked for
  through it before anything is final. They are root-equivalent (they write what a web server
  reads, as root) and need sudo mode.

Ports 80 and 443 belong to one web server at a time. When the server a site moves to is not
running, moving a single site is refused (``409 webserver_move``) with the command that moves them
all; the plan says the same before anything is asked.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.store import App
from noust.core.webserver_switch import usage
from noust.deployers import webserver_move_plan as planning
from noust.deployers.recorder import CapturingLogger
from noust.deployers.webserver_move import move, require_ready
from noust.deployers.webserver_move_plan import (
    MovePlan,
    display,
    other_than,
    plan_move,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    known_app_row,
    require_elevated,
)
from noust.web.api.scope import require_instance_scope
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.permissions import Permission

#: Mounted at ``/api/webserver``.
router = APIRouter(route_class=NoustErrorRoute)

#: Mounted at ``/api/apps``: the routes about one application.
app_router = APIRouter(route_class=NoustErrorRoute)

WebServerName = Literal["nginx", "apache"]


class WebserverSentenceOut(BaseModel):
    """
    A sentence of the server's, as a code and its values, with its English beside it.

    Attributes:
        code: The key of the sentence in ``serverText``, which the console translates.
        params: The values it is said with.
        text: The sentence in English.
        hint: How to fix it in English, when it has a fix.
    """

    code: str
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    text: str
    hint: str | None = None


class WebserverFindingOut(BaseModel):
    """
    Something about the old site that the new one does not carry over as it was.

    Attributes:
        reason: ``unsupported``, ``protection``, ``included``, ``variable``, ``approximate``,
            ``tuning`` or ``unreadable``.
        severity: ``blocks`` (cannot move until it is fixed), ``loses`` (the new site will not do
            something the old one did; needs ``accept_losses``) or ``notes`` (carried over in
            another form, or a knob that changes no answer).
        directive: The directive as the old web server names it.
        line: Its line in the old file; None for the file as a whole.
        protection: Whether what is lost was a protection (a login, a limit, a firewall).
        code: The key of its sentence.
        params: The values it is said with.
        text: The sentence in English.
        hint: How to fix it in English, when it has a fix.
    """

    reason: str
    severity: Literal["blocks", "loses", "notes"]
    directive: str
    line: int | None = None
    protection: bool
    code: str
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    text: str
    hint: str | None = None


class WebserverSitePlanOut(BaseModel):
    """
    One site, and what moving it would do.

    Attributes:
        domain: The application's domain, or the site's.
        kind: ``app`` or ``site`` (one that belongs to no application).
        source: The web server it is behind now.
        target: The one it would move to.
        source_path: Its file now.
        target_path: Where its file would be.
        operator_site: Whether the file now is the operator's, which is never rewritten.
        keeps_operator_site: Whether the operator already wrote its equivalent, which is kept.
        movable: Whether nothing stops it.
        blockers: What stops it.
        findings: What the new site does not carry over as it was.
        modules: The Apache modules the new site needs.
        text: The translated site, when asked for with ``text=true``.
    """

    domain: str
    kind: Literal["app", "site"]
    source: WebServerName
    target: WebServerName
    source_path: str
    target_path: str
    operator_site: bool
    keeps_operator_site: bool
    movable: bool
    blockers: list[WebserverSentenceOut] = Field(default_factory=list)
    findings: list[WebserverFindingOut] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    text: str | None = None


class WebserverMovePlanOut(BaseModel):
    """
    What moving a set of sites to a web server would do.

    Attributes:
        source: The web server they are behind.
        target: The one they would move to.
        mode: ``live`` when the target is serving (each site moves on its own) or ``handover``
            when it is not (the ports pass from one server to the other, for every site at once).
        source_running: Whether the old server is running.
        target_installed: Whether the new one is installed.
        target_running: Whether the new one is running.
        install_command: How to install the new one when it is not.
        sites: What each site becomes.
        blockers: What stops the move as a whole.
        skipped: Sites asked for that are behind the target already.
        left_behind: Sites of the old server that are not moving and would stop being served.
        blocked: Whether anything stops the move, a site's or the whole's.
        modules: The Apache modules the sites need.
        losses: How many things the new sites will not do that the old ones did.
        confirmation: What the operator agrees to by going ahead.
    """

    source: WebServerName
    target: WebServerName
    mode: Literal["live", "handover"]
    source_running: bool
    target_installed: bool
    target_running: bool
    install_command: str = ""
    sites: list[WebserverSitePlanOut]
    blockers: list[WebserverSentenceOut] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    left_behind: list[str] = Field(default_factory=list)
    blocked: bool
    modules: list[str] = Field(default_factory=list)
    losses: int
    confirmation: WebserverSentenceOut


class WebserverServerOut(BaseModel):
    """
    One web server of the machine.

    Attributes:
        installed: Whether it is installed.
        running: Whether it is running, which is what holds ports 80 and 443.
        starts_at_boot: Whether it starts at boot.
        version: Its version, when it could be read.
        applications: How many applications are behind it.
        sites: How many sites of no application are behind it.
    """

    installed: bool
    running: bool
    starts_at_boot: bool
    version: str | None = None
    applications: int
    sites: int


class WebserverStatusOut(BaseModel):
    """
    The web servers of the machine.

    Attributes:
        webserver: The setting: the one new applications are put behind.
        holds_ports: The one that is running (and so holds ports 80 and 443), None when both or
            neither are.
        servers: Each web server, by name.
    """

    webserver: WebServerName
    holds_ports: WebServerName | None = None
    servers: dict[str, WebserverServerOut]


class WebserverMoveRequest(BaseModel):
    """
    Move sites to a web server.

    Attributes:
        to: The web server to move to.
        domains: The sites to move; leave out and use ``all`` for every one behind the other.
        all: Every application and site behind the other web server: what a handover moves.
        accept_losses: Go ahead although the new sites will not do everything the old ones did
            (the plan lists each thing).
        confirm: Go ahead with the move itself. Without it the answer is the confirmation to
            ask for, and nothing changes.
    """

    to: WebServerName
    domains: list[str] | None = None
    all: bool = False
    accept_losses: bool = False
    confirm: bool = False


class WebserverAppMoveRequest(BaseModel):
    """
    Move one application's site to a web server.

    Attributes:
        to: The web server to move to; the other one than it is behind now when left out.
        accept_losses: Go ahead although the new site will not do everything the old one did.
        confirm: Go ahead with the move itself.
    """

    to: WebServerName | None = None
    accept_losses: bool = False
    confirm: bool = False


def _plan_out(plan: MovePlan, *, with_text: bool = False) -> WebserverMovePlanOut:
    """
    Args:
        plan: The plan.
        with_text: Include the translated site of each.

    Returns:
        It as the API sends it.
    """
    data = plan.to_dict()
    data["sites"] = [site.to_dict(with_text=with_text) for site in plan.sites]
    return WebserverMovePlanOut(**data)


def _planned(
    target: WebServerName, domains: list[str] | None, everything: bool
) -> tuple[MovePlan, list[str] | None]:
    """
    Plan what a request asks for.

    Args:
        target: The web server to move to.
        domains: The sites named.
        everything: Whether every site was asked for.

    Returns:
        The plan and the domains it was made for (None for every site).

    Raises:
        NoustError: Neither sites nor ``all`` were given, or both, or a site is unknown.
    """
    from noust.core.exceptions import ValidationError

    if everything == bool(domains):
        raise ValidationError(
            "Name the sites to move, or ask for all of them",
            details=f"Send domains, or all: true for every one behind "
            f"{display(other_than(target))}.",
            field="domains",
        )
    names = None if everything else [name.strip().lower() for name in domains or []]
    return plan_move(target, names), names


def webserver_move_job(
    target: str,
    domains: list[str] | None,
    accept_losses: bool,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Move sites to the other web server, as a background job.

    The plan is worked out again when the job runs, under the applications' locks, and the move
    refuses to go ahead if it is not the one that was confirmed.

    Args:
        target: The web server to move to.
        domains: The sites to move; None for every one behind the other web server.
        accept_losses: Whether the operator accepted what the new sites will not do.
        job_context: Injected by the job manager.

    Returns:
        What moved, what was enabled and what did not go as it should without undoing the move.

    Raises:
        WebserverMoveError: The new server refused a site, did not start, or a site did not
            answer through it. Everything was put back; the details say what, if anything, could
            not be.
        AppBusyError: Another operation is running on one of the applications.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.update("Planning the move", 10)
        logger.attach_sink(job_context.log)
    plan = plan_move(target, domains)
    if job_context is not None:
        job_context.set_metadata("sites", [site.domain for site in plan.sites])
        job_context.update(f"Moving to {display(target)}", 30)
    result = move(plan, accept_losses=accept_losses, confirm=True, logger=logger)
    if job_context is not None:
        job_context.update("Done", 100)
    return {
        "source": result.source,
        "target": result.target,
        "mode": result.mode,
        "sites": [
            {"domain": site.domain, "kind": site.kind, "written": site.written}
            for site in result.sites
        ],
        "modules": list(result.modules),
        "losses": result.losses,
        "warnings": list(result.warnings),
    }


def _queue(
    plan: MovePlan,
    names: list[str] | None,
    body_losses: bool,
    body_confirm: bool,
    session: dict[str, Any],
    description: str,
) -> JobAcceptedResponse:
    """
    Check a plan is ready and queue its move.

    Args:
        plan: What the request asks for.
        names: The domains it was made for (None for every site).
        body_losses: Whether the request accepted the losses.
        body_confirm: Whether the request confirmed the move.
        session: The authenticated session.
        description: What the job is called.

    Returns:
        The queued job.

    Raises:
        ValidationError: There is nothing to move.
        WebserverMoveError: Something stops the move.
        ConfirmationRequired: The losses or the move are not agreed to yet.
    """
    require_ready(plan, accept_losses=body_losses, confirm=body_confirm)
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SITE_ACTION,
        name=f"Move to {display(plan.target)}",
        description=description,
        func=webserver_move_job,
        kwargs={"target": plan.target, "domains": names, "accept_losses": body_losses},
        metadata={"webserver": plan.target, "domains": [site.domain for site in plan.sites]},
        actor=actor,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=description,
        job=job.to_dict(),
    )


# -- The machine ----------------------------------------------------------------------------


@router.get("/status", response_model=WebserverStatusOut)
def get_status(session: Annotated[dict, Depends(get_current_session)]) -> WebserverStatusOut:
    """
    Show the web servers of this machine and what is behind each.

    ``webserver`` is the one **new** applications are put behind; each application keeps the one
    it was deployed behind. The server that is running holds ports 80 and 443.

    Args:
        session: The authenticated session.

    Returns:
        The setting, the server that holds the ports and each server's state and counts.
    """
    both = planning.managers_between("nginx", "apache")
    counts = usage()
    servers: dict[str, WebserverServerOut] = {}
    for manager in (both.source, both.target):
        status = manager.get_status()
        name = manager.backend.name
        servers[name] = WebserverServerOut(
            installed=status.installed,
            running=status.active,
            starts_at_boot=status.enabled,
            version=status.version,
            applications=counts.applications.get(name, 0),
            sites=counts.sites.get(name, 0),
        )
    running = [name for name, item in servers.items() if item.running]
    setting = str(Config().get("webserver", DEFAULT_CONFIG["webserver"]))
    return WebserverStatusOut(
        webserver="apache" if setting == "apache" else "nginx",
        holds_ports="apache"
        if running == ["apache"]
        else ("nginx" if running == ["nginx"] else None),
        servers=servers,
    )


@router.get("/migration/plan", response_model=WebserverMovePlanOut)
def get_migration_plan(
    session: Annotated[dict, Depends(get_current_session)],
    to: WebServerName,
    domain: Annotated[list[str] | None, Query()] = None,
    all: bool = False,
    text: bool = False,
) -> WebserverMovePlanOut:
    """
    Show what moving sites to a web server would do. Changes nothing.

    Args:
        session: The authenticated session.
        to: The web server to move to.
        domain: The sites to move; repeat the parameter for each.
        all: Every application and site behind the other web server.
        text: Include the translated site of each in the answer.

    Returns:
        The plan, from :func:`~noust.deployers.webserver_move_plan.plan_move`.

    Raises:
        ValidationError: Neither sites nor ``all`` were given, or both, or a site is unknown.
    """
    require_instance_scope(Permission.APPS_READ)
    plan, _ = _planned(to, domain, all)
    return _plan_out(plan, with_text=text)


@router.post("/migrate", response_model=JobAcceptedResponse, status_code=202)
def post_migrate(
    body: WebserverMoveRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue the move of sites to a web server.

    Needs sudo mode. Without ``confirm`` the answer is the confirmation to ask the operator for,
    and without ``accept_losses`` the list of what the new sites will not do; neither changes
    anything. The job tests every translated site in the new server, takes over (ports 80 and 443
    too, when it is not serving) and asks for every site through it; if one does not answer
    everything is put back.

    Args:
        body: Where to, which sites, and what the operator agreed to.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ConfirmationRequired: The losses or the move are not agreed to yet (400 with the warnings).
        WebserverMoveError: Something stops it (409 ``webserver_move``).
        ValidationError: Nothing to move, or neither sites nor ``all`` were given.
    """
    require_instance_scope(Permission.ROOT_EQUIVALENT)
    plan, names = _planned(body.to, body.domains, body.all)
    return _queue(
        plan,
        names,
        body.accept_losses,
        body.confirm,
        session,
        f"Moving {len(plan.sites)} site(s) from {display(plan.source)} to {display(plan.target)}",
    )


# -- One application -------------------------------------------------------------------------


def _target_of(app: App, to: str | None) -> WebServerName:
    """
    Args:
        app: The application.
        to: The web server asked for, None for the other one than it is behind.

    Returns:
        The web server to move it to.
    """
    if to is not None:
        return "apache" if to == "apache" else "nginx"
    return "apache" if other_than(app.webserver) == "apache" else "nginx"


@app_router.get("/{domain}/webserver/plan", response_model=WebserverMovePlanOut)
def get_app_plan(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    to: WebServerName | None = None,
    text: bool = False,
) -> WebserverMovePlanOut:
    """
    Show what moving one application's site to the other web server would do. Changes nothing.

    When the web server it would move to is not running, the plan is blocked and its blocker
    names the command that moves every site together: ports 80 and 443 belong to one server.

    Args:
        domain: Domain of the application.
        session: The authenticated session.
        to: The web server to move to; the other one than it is behind when left out.
        text: Include the translated site in the answer.

    Returns:
        The plan.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The application is behind that web server already.
    """
    require_instance_scope(Permission.APPS_READ)
    app = known_app_row(domain)
    plan, _ = _planned(_target_of(app, to), [app.domain], False)
    return _plan_out(plan, with_text=text)


@app_router.post("/{domain}/webserver/migrate", response_model=JobAcceptedResponse, status_code=202)
def post_app_migrate(
    domain: str,
    session: Annotated[dict, Depends(require_elevated)],
    body: WebserverAppMoveRequest | None = None,
) -> JobAcceptedResponse:
    """
    Queue the move of one application's site to the other web server.

    Needs sudo mode. The same checks and the same put-back as moving every site; when the web
    server it moves to is not running the move is refused (409 ``webserver_move``) with the
    command that moves them all.

    Args:
        domain: Domain of the application.
        session: The authenticated, elevated session.
        body: Where to, and what the operator agreed to.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
        ConfirmationRequired: The losses or the move are not agreed to yet.
        WebserverMoveError: Something stops it.
    """
    require_instance_scope(Permission.ROOT_EQUIVALENT)
    app = known_app_row(domain)
    validated = app.domain
    asked = body or WebserverAppMoveRequest()
    plan, names = _planned(_target_of(app, asked.to), [validated], False)
    return _queue(
        plan,
        names,
        asked.accept_losses,
        asked.confirm,
        session,
        f"Moving {validated} from {display(plan.source)} to {display(plan.target)}",
    )
