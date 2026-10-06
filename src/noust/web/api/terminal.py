# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The terminal and one-off commands: the HTTP half of opening a shell in the browser.

A client of :mod:`noust.terminal`, like ``noust app exec``. A terminal needs a
call and a socket, and this module is the call:

- ``POST /api/apps/{domain}/terminal`` and ``POST /api/server/terminal`` check
  everything a session will need (the permission, the team, sudo mode, the
  switch of the server's shell, the limits, that there is something to open),
  and mint the ticket the WebSocket (``/ws/terminal``) is opened with. They ask
  for sudo mode: a shell as an application reads its secrets, and the server's
  is root. The server's also asks for a second person when approvals are on.
- ``POST /api/apps/{domain}/exec`` runs one command as a job, whose output is
  the job's log and reaches the console through ``/ws/jobs/{id}`` like any job.
- ``GET /api/terminal/status``, ``GET /api/terminal/sessions`` and ``DELETE
  /api/terminal/sessions/{id}`` say what is allowed, who has what open, and end
  somebody's session; ``GET /api/terminal/recordings[/{id}]`` read what the
  sessions left, for whoever reads the audit.

Nothing in here starts a process or reads a recording's bytes itself: it is
HTTP in, a manager call, HTTP out (rule 3).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.terminal import execution
from noust.terminal import recording as recordings
from noust.terminal.sessions import (
    SessionInfo,
    TerminalDisabledError,
    TerminalGone,
    TerminalLimitError,
    get_registry,
)
from noust.terminal.settings import load_settings
from noust.terminal.targets import (
    KIND_APP,
    KIND_SERVER,
    SHELLS,
    TerminalTarget,
    plan_app_terminal,
    plan_server_terminal,
    validate_command,
    validate_service,
    validate_shell,
)
from noust.terminal.tickets import KIND_APP as TICKET_APP
from noust.terminal.tickets import KIND_SERVER as TICKET_SERVER
from noust.terminal.tickets import TICKET_TTL_SECONDS, TicketRefused, get_tickets
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    require_elevated,
    strict_domain,
)
from noust.web.api.scope import require_instance_scope
from noust.web.auth import actor_label, get_client_ip
from noust.web.jobs import JobType, exec_app_job, get_job_manager
from noust.web.permissions import Permission
from noust.web.permissions.enforce import check_permission
from noust.web.permissions.principal import audit_actor, scope_of

logger = logging.getLogger(__name__)

#: One router per prefix the routes live under: an application's, the server's
#: and the terminal's own.
apps_router = APIRouter(route_class=NoustErrorRoute)
server_router = APIRouter(route_class=NoustErrorRoute)
router = APIRouter(route_class=NoustErrorRoute)

#: Where the console opens the socket; a node's is reached through the central's
#: ``/ws/nodes/{node}`` prefix, which the console adds.
SOCKET_PATH = "/ws/terminal"


class TerminalRequest(BaseModel):
    """What the person asks of a terminal."""

    service: str | None = Field(
        default=None,
        max_length=64,
        description="The Compose service to open it in, for a stack that runs several",
    )
    shell: str | None = Field(
        default=None,
        max_length=16,
        description=f"The shell, by name: one of {', '.join(SHELLS)}; the default when absent",
    )
    rows: int = Field(default=24, ge=1, le=500, description="The terminal's height")
    cols: int = Field(default=80, ge=1, le=1000, description="The terminal's width")
    resume: str | None = Field(
        default=None,
        min_length=16,
        max_length=16,
        description="The session to go back to after the connection dropped, instead of a new one",
    )


class TerminalTargetModel(BaseModel):
    """Where a terminal is, as its header says it."""

    kind: str = Field(description="app or server")
    domain: str | None = None
    service: str | None = None
    label: str = Field(description="What the header shows")
    account: str = Field(
        description="Who the shell runs as: the application's account, root, or container"
    )


class TerminalTicketResponse(BaseModel):
    """The ticket that opens a terminal, and what it will open."""

    ticket: str = Field(description="Single use; valid for expires_in seconds; shown once")
    expires_in: int
    path: str = Field(description="The WebSocket to open it on, with ?terminal_ticket=<ticket>")
    session: str = Field(description="The session's identifier")
    resume: bool = Field(description="The ticket goes back to a session that is already open")
    target: TerminalTargetModel
    idle_seconds: int = Field(description="How long it may go with nothing typed")
    max_seconds: int = Field(description="How long it lasts, however busy")
    recorded: bool = Field(description="The session is kept in the audit trail")


class ExecRequest(BaseModel):
    """A command to run once in an application."""

    # Its length is judged by validate_command, which says what is wrong; pydantic 1
    # and 2 spell the limits of a list differently and the bridge does not cover them.
    argv: list[str] = Field(
        description="The program and its arguments, given to it as they are; never a shell line",
    )
    service: str | None = Field(
        default=None, max_length=64, description="The Compose service, for a stack"
    )
    timeout: int | None = Field(
        default=None, ge=1, le=86400, description="Seconds it may run; 300 when absent"
    )


class ServerShellModel(BaseModel):
    """Whether the server's own shell may be opened, and what to do when it may not."""

    allowed: bool
    enabled: bool = Field(description="terminal.server.enabled")
    reason: str | None = Field(default=None, description="Why it may not be opened")
    how: str | None = Field(default=None, description="What to change, verbatim")


class TerminalStatusResponse(BaseModel):
    """What the terminal may do on this server."""

    enabled: bool = Field(description="Application terminals are on")
    server: ServerShellModel
    idle_seconds: int
    max_seconds: int
    max_sessions: int
    max_sessions_per_user: int
    open_sessions: int
    exec_max_seconds: int
    recording_max_mb: int
    retention_days: int
    ens: bool
    shells: list[str]


class TerminalSessionModel(BaseModel):
    """One open terminal."""

    id: str
    kind: str
    domain: str | None = None
    service: str | None = None
    label: str
    account: str
    actor: str
    started_at: float
    ends_at: float
    idle_seconds: int
    idle_remaining: float
    rows: int
    cols: int
    attached: bool
    recording: str | None = None


class TerminalSessionsResponse(BaseModel):
    """The open terminals the caller may see."""

    sessions: list[TerminalSessionModel]


class SessionEndedResponse(BaseModel):
    """A terminal that was ended."""

    id: str
    ended: bool


class RecordingModel(BaseModel):
    """One recording of a session, as the list shows it."""

    id: str
    status: str = Field(description="recording, complete or interrupted")
    kind: str | None = None
    domain: str | None = None
    service: str | None = None
    label: str | None = None
    account: str | None = None
    actor: str | None = None
    client_ip: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    duration: float | None = None
    end_reason: str | None = None
    exit_code: int | None = None
    bytes: int | None = None
    sha256: str | None = None
    events: int | None = None
    truncated_output: bool | None = None
    truncated_input: bool | None = None
    failed: bool | None = None


class RecordingsResponse(BaseModel):
    """The recordings, newest first."""

    recordings: list[RecordingModel]


class RecordingResponse(BaseModel):
    """One recording, read back."""

    recording: RecordingModel
    intact: bool | None = Field(
        description="The cast still matches the digest the audit trail recorded; "
        "null while the session is still recording"
    )
    transcript: str = Field(description="Its output and its input as text, safe to print")
    events: list[list[Any]] = Field(description="[seconds, 'o'|'i'|'r'|'m', data] for each event")


#: What a recording's list entry carries, besides what is not the API's to show.
_RECORDING_FIELDS = frozenset(
    {
        "id",
        "status",
        "kind",
        "domain",
        "service",
        "label",
        "account",
        "actor",
        "client_ip",
        "started_at",
        "ended_at",
        "duration",
        "end_reason",
        "exit_code",
        "bytes",
        "sha256",
        "events",
        "truncated_output",
        "truncated_input",
        "failed",
    }
)


def _recording(info: recordings.RecordingInfo) -> RecordingModel:
    """
    Args:
        info: A recording as the list reads it.

    Returns:
        Its API model; fields the sidecar does not hold are left out.
    """
    values = {key: value for key, value in info.to_dict().items() if key in _RECORDING_FIELDS}
    return RecordingModel(**values)


def _session(info: SessionInfo) -> TerminalSessionModel:
    """
    Args:
        info: An open session.

    Returns:
        Its API model.
    """
    return TerminalSessionModel(**info.to_dict())


def _deny(session: dict[str, Any], request: Request, target: str, reason: str) -> None:
    """
    Put a refused terminal on record.

    Args:
        session: The caller.
        request: The request.
        target: What it asked for.
        reason: Why it was refused, one sentence.
    """
    audit.record(
        "terminal.denied",
        actor=audit_actor(session),
        target=target,
        outcome="denied",
        details={"reason": reason, "client_ip": get_client_ip(request)},
    )


def _mint(
    request: Request,
    session: dict[str, Any],
    *,
    kind: str,
    domain: str | None,
    body: TerminalRequest,
) -> TerminalTicketResponse:
    """
    Check that a terminal can be opened, and issue the ticket that opens it.

    Everything that can be known without the socket is judged here, so a
    refusal is an answer and not a socket that closes: the switch, the limits,
    that the application has something to open a terminal in, that the shell
    and the service exist. The socket judges them again, because what is true
    now is not necessarily true in a few seconds.

    Args:
        request: The request.
        session: The authenticated payload, in sudo mode.
        kind: ``app`` or ``server``.
        domain: The application, for an application's terminal.
        body: What was asked.

    Returns:
        The ticket and what it opens.

    Raises:
        TerminalDisabledError: 403, this kind of terminal is off here.
        TerminalLimitError: 429, a limit is reached.
        TerminalGone: 404, the session to resume is not open.
        ValidationError: 400, an unknown shell, service or application.
    """
    registry = get_registry()
    actor = actor_label(session)
    resource = f"app:{domain}" if domain else "server"
    sid = str(session.get("sid"))
    try:
        shell_name = validate_shell(body.shell)
        service = validate_service(body.service)
        if body.resume is not None:
            live = registry.resumable(body.resume, sid=sid, actor=actor)
            target: TerminalTarget = live.plan.target
            if (domain or None) != target.domain:
                raise TerminalGone(
                    "That terminal belongs to another application",
                    details="Open a new one.",
                )
        else:
            settings = registry.check_room(actor, kind=kind)
            runner = _runner()
            plan = (
                plan_app_terminal(
                    domain or "", service=service, shell_name=shell_name, runner=runner
                )
                if kind == KIND_APP
                else plan_server_terminal(shell_name=shell_name)
            )
            target = plan.target
            del settings
    except (TerminalDisabledError, TerminalLimitError, TerminalGone) as exc:
        _deny(session, request, resource, str(exc))
        raise
    try:
        ticket, grant = get_tickets().mint(
            kind=TICKET_APP if kind == KIND_APP else TICKET_SERVER,
            domain=domain,
            service=service,
            shell_name=shell_name,
            rows=body.rows,
            cols=body.cols,
            sid=sid,
            actor=actor,
            client_ip=get_client_ip(request),
            resume=body.resume,
        )
    except TicketRefused as exc:
        raise TerminalLimitError(str(exc), details=exc.details) from exc
    limits = load_settings()
    audit.record(
        "terminal.ticket",
        actor=audit_actor(session),
        target=resource,
        details={
            "session": grant.session_id,
            "resume": body.resume is not None,
            "expires_in": TICKET_TTL_SECONDS,
        },
    )
    return TerminalTicketResponse(
        ticket=ticket,
        expires_in=TICKET_TTL_SECONDS,
        path=SOCKET_PATH,
        session=grant.session_id,
        resume=body.resume is not None,
        target=TerminalTargetModel(**target.to_dict()),
        idle_seconds=limits.idle_seconds,
        max_seconds=limits.max_seconds,
        recorded=True,
    )


def _runner() -> Any:
    """
    Returns:
        The process-wide runner a plan asks where a stack's containers are.
    """
    from noust.core.runner import get_runner

    return get_runner()


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one the caller cannot see.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@apps_router.post("/{domain}/terminal", response_model=TerminalTicketResponse)
def open_app_terminal(
    domain: str,
    body: TerminalRequest,
    request: Request,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> TerminalTicketResponse:
    """
    Get the ticket that opens a terminal as an application.

    The shell runs as the application's own account, in its sandbox, with its
    environment; for a Compose or container application it is a shell in the
    container of the service asked for. Needs sudo mode and the
    ``apps.terminal`` permission in the application's team. Open the WebSocket
    ``/ws/terminal?terminal_ticket=<ticket>`` within ``expires_in`` seconds.

    Args:
        domain: Domain of the application.
        body: The service, the shell and the size.
        request: The request.
        session: The authenticated session, in sudo mode.

    Returns:
        The ticket.

    Raises:
        HTTPException: 404 when the application is unknown.
        TerminalDisabledError: 403 when terminals are off.
        TerminalLimitError: 429 when a limit is reached.
    """
    return _mint(request, session, kind=KIND_APP, domain=_known(domain), body=body)


@server_router.post("/terminal", response_model=TerminalTicketResponse)
def open_server_terminal(
    body: TerminalRequest,
    request: Request,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> TerminalTicketResponse:
    """
    Get the ticket that opens a root shell on the server itself.

    Off until an administrator turns it on (``terminal.server.enabled``), and
    under the ENS profile until it is forced too. Needs sudo mode, the
    ``server.terminal`` permission and a principal that sees every team; with
    four-eyes approval on, a second person decides first.

    Args:
        body: The shell and the size.
        request: The request.
        session: The authenticated session, in sudo mode.

    Returns:
        The ticket.

    Raises:
        TerminalDisabledError: 403 when the server's shell is off.
        TerminalLimitError: 429 when a limit is reached.
    """
    require_instance_scope(Permission.SERVER_TERMINAL)
    return _mint(request, session, kind=KIND_SERVER, domain=None, body=body)


@apps_router.post("/{domain}/exec", response_model=JobAcceptedResponse, status_code=202)
def exec_in_app(
    domain: str,
    body: ExecRequest,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Run one command in an application, as a job.

    The command is an argument vector, given to the program as it is. It runs
    as the application's own account in its sandbox (a container of the stack,
    for Compose), with the application's environment; its output is the job's
    log. A command that exits with anything but 0 is a failed job whose log
    says how. Needs sudo mode and ``apps.terminal`` in the application's team.

    Args:
        domain: Domain of the application.
        body: The command, the service and the time it may take.
        session: The authenticated session, in sudo mode.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: 400 for a command or a time that is not acceptable.
    """
    validated = _known(domain)
    command = list(validate_command(body.argv))
    service = validate_service(body.service)
    seconds = execution.clamp_timeout(body.timeout)
    label = " ".join(command)[:60]
    job = get_job_manager().create_job(
        job_type=JobType.APP_EXEC,
        name=f"Run in {validated}",
        description=f"Running a command in {validated}: {label}",
        func=exec_app_job,
        kwargs={"domain": validated, "argv": command, "service": service, "timeout": seconds},
        metadata={"domain": validated, "service": service, "timeout": seconds},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Command queued",
        job=job.to_dict(),
    )


@router.get("/status", response_model=TerminalStatusResponse)
def terminal_status(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> TerminalStatusResponse:
    """
    Say what the terminal may do on this server.

    What the console reads to offer a terminal, or to say why there is none:
    whether application terminals are on, whether the server's shell is, and
    what an administrator would change.

    Args:
        session: The authenticated session.

    Returns:
        The switches and the limits.
    """
    settings = load_settings()
    refusal = settings.server_refusal()
    return TerminalStatusResponse(
        enabled=settings.enabled,
        server=ServerShellModel(
            allowed=refusal is None,
            enabled=settings.server_enabled,
            reason=refusal[0] if refusal else None,
            how=refusal[1] if refusal else None,
        ),
        idle_seconds=settings.idle_seconds,
        max_seconds=settings.max_seconds,
        max_sessions=settings.max_sessions,
        max_sessions_per_user=settings.max_sessions_per_user,
        open_sessions=len(get_registry().list()),
        exec_max_seconds=settings.exec_max_seconds,
        recording_max_mb=settings.recording_max_bytes // (1024 * 1024),
        retention_days=settings.retention_days,
        ens=settings.ens,
        shells=list(SHELLS),
    )


def _visible(session: dict[str, Any], info: SessionInfo) -> bool:
    """
    Tell whether the caller may see, and end, a session.

    Args:
        session: The caller.
        info: An open session.

    Returns:
        True for a principal that sees every team; for one that sees some,
        only the sessions in applications it sees, never the server's.
    """
    scope = scope_of(session)
    if not scope.restricted:
        return True
    return info.kind == KIND_APP and scoped_store().sees_domain(info.domain)


@router.get("/sessions", response_model=TerminalSessionsResponse)
def list_sessions(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> TerminalSessionsResponse:
    """
    List the terminals that are open, in the caller's teams.

    Args:
        session: The authenticated session.

    Returns:
        The open sessions, oldest first.
    """
    return TerminalSessionsResponse(
        sessions=[_session(info) for info in get_registry().list() if _visible(session, info)]
    )


@router.delete("/sessions/{session_id}", response_model=SessionEndedResponse)
def end_session(
    session_id: str,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> SessionEndedResponse:
    """
    End somebody's terminal.

    The program is stopped and the recording closed. The server's own sessions
    need the ``server.terminal`` permission too.

    Args:
        session_id: The session.
        session: The authenticated session.

    Returns:
        That it ended.

    Raises:
        HTTPException: 404 when it is not open, or not the caller's to see.
    """
    registry = get_registry()
    live = registry.get(session_id)
    if live is None or not _visible(session, live.info()):
        raise HTTPException(status_code=404, detail=f"Terminal not found: {session_id}")
    if live.grant.kind == TICKET_SERVER:
        check_permission(session, Permission.SERVER_TERMINAL)
    ended = registry.kill(session_id, by=audit_actor(session))
    return SessionEndedResponse(id=session_id, ended=ended)


@router.get("/recordings", response_model=RecordingsResponse)
def list_recordings(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    domain: str | None = None,
    actor: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> RecordingsResponse:
    """
    List the recordings of terminal sessions, newest first.

    Args:
        session: The authenticated session.
        domain: Only the sessions in this application.
        actor: Only the sessions of this person.
        kind: Only ``app`` or ``server`` sessions.
        limit: The most to return, 1 to 500.

    Returns:
        The recordings.

    Raises:
        ValidationError: 400 for a kind that is neither.
    """
    if kind not in (None, KIND_APP, KIND_SERVER):
        raise ValidationError("kind is app or server", field="kind")
    found = recordings.list_recordings(
        domain=domain,
        actor=actor,
        kind=kind,
        active=get_registry().active_recordings(),
        limit=max(1, min(limit, 500)),
    )
    return RecordingsResponse(recordings=[_recording(info) for info in found])


@router.get("/recordings/{recording_id}", response_model=RecordingResponse)
def read_recording(
    recording_id: str,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> RecordingResponse:
    """
    Read one recording: what was printed, what was typed, and whether it is intact.

    Reading it is itself on record. Escape sequences of the programs are kept in
    ``events`` as they were and are removed from ``transcript``, which is what
    to print in a terminal.

    Args:
        recording_id: The recording's id.
        session: The authenticated session.

    Returns:
        The recording.

    Raises:
        ValidationError: 400 for an id that is not one.
        NoustError: 400 when there is no such recording or it is damaged.
    """
    found = recordings.read_recording(recording_id)
    audit.record(
        "terminal.recording.read",
        actor=audit_actor(session),
        target=f"recording:{recording_id}",
        details={
            "domain": found.info.meta.get("domain"),
            "opened_by": found.info.meta.get("actor"),
        },
    )
    return RecordingResponse(
        recording=_recording(found.info),
        intact=found.intact,
        transcript=recordings.transcript(found),
        events=[[seconds, kind, data] for seconds, kind, data in found.events],
    )


__all__ = ["apps_router", "router", "server_router"]
