# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/replicas``: read replicas of the host's PostgreSQL, MySQL and MariaDB (checklist C-14).

A client of the replication managers, like ``noust db replica``
(:func:`~noust.managers.database.replication_select.replication_for` picks the class for both):
the same plan, the same refusals, in the same words. Reading the state and planning are requests.
Making room for a replica on this primary, showing its join code again, promoting a replica and
taking one away are requests too, with sudo mode: each is quick, and the first two have to answer
with the join code, which holds the replication password and is therefore never the result of a job
(a job's result is kept). Joining a primary copies its data, so it is a job.

What costs something is refused without ``confirm`` before anything is done, as
``confirmation_required`` with every warning: a restart of the engine, a data directory replaced by
the primary's copy, a replica that stops following its primary.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.exceptions import ConfirmationRequired, DatabaseError, ValidationError
from noust.managers.database.replication import ReplicationManager
from noust.managers.database.replication_mysql import MySqlReplication
from noust.managers.database.replication_select import REPLICA_ENGINES, replication_for
from noust.managers.database.replication_token import parse as parse_code
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue, service
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobContext, JobType

router = APIRouter(route_class=NoustErrorRoute)


# ------------------------------------------------------------------- models


class ReplicaPrepareRequest(BaseModel):
    """
    Where a replica will connect from, and what to do about the primary's address.

    Attributes:
        name: The replica's name; it names its account and its slot.
        source: Where the replica connects from: an address or a network.
        host: The name or address it connects to; the engine's certificate must cover it.
        listen: Make the engine listen here too (an address of this server, or ``*``), needed when
            it listens on the loopback only; restarts it once.
        ca_pem: The authority that signed the engine's certificate, PEM, when the replica does not
            trust it already (a private authority).
        open_firewall: Allow the engine's port from ``source`` in the firewall.
        confirm: The operator accepted the engine's restart, where there is one.
    """

    name: str = Field(..., min_length=1, max_length=63)
    source: str = Field(..., min_length=1, max_length=255)
    host: str = Field(..., min_length=1, max_length=253)
    listen: str | None = Field(default=None, max_length=255)
    ca_pem: str = Field(default="", max_length=64_000)
    open_firewall: bool = False
    confirm: bool = False


class ReplicaPlanResponse(BaseModel):
    """
    What making room for a replica would do. Nothing was changed.

    Attributes:
        name: The replica.
        account: The replication account.
        slot: The slot that keeps the log it has not read (PostgreSQL); empty for MySQL.
        host: The name the replica connects to.
        port: The engine's port.
        source: Where the replica connects from.
        listen: The address the engine is told to listen on, or null when it reaches the replica.
        restart: Whether the engine restarts.
        firewall: Whether a firewall rule is added.
        settings: The settings the engine's configuration is given (MySQL and MariaDB).
        steps: What will be done, in order.
        problems: What stops it; nothing is done while there are any.
        warnings: What the operator should know.
        ok: There are no problems.
    """

    name: str
    account: str
    slot: str = ""
    host: str
    port: int
    source: str
    listen: str | None = None
    restart: bool = False
    firewall: bool = False
    settings: dict[str, str] = Field(default_factory=dict)
    steps: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ok: bool = True


class ReplicaPreparedResponse(BaseModel):
    """
    A replica made room for, with the code the replica joins with.

    Attributes:
        token: The join code. It holds the replication password: carry it over a channel you
            trust, and do not keep it.
        plan: What was done.
        record: What Noust recorded.
    """

    token: str | None = None
    plan: dict[str, Any] | None = None
    record: dict[str, Any] | None = None


class ReplicaTokenResponse(BaseModel):
    """
    The join code of a replica prepared here.

    Attributes:
        name: The replica.
        token: The join code, a secret.
    """

    name: str
    token: str


class ReplicaStatusResponse(BaseModel):
    """
    How the replicas of one engine, and the engine as a replica, stand.

    Attributes:
        engine: The engine.
        role: ``primary``, ``replica`` or ``standalone``.
        replicas: One entry per replica prepared here, with what the engine says about its
            connection (state, TLS, lag, the log held for it).
        follows: The primary this server follows, when it is a replica.
        warnings: What the operator should know.
    """

    engine: str
    role: str
    replicas: list[dict[str, Any]] = Field(default_factory=list)
    follows: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class ReplicaJoinRequest(BaseModel):
    """
    The join code of a primary, and what the operator accepts.

    Attributes:
        token: The join code the primary gave. A secret.
        replace_data: The operator accepts that this server's data is replaced by the primary's.
            Without it the answer is a 400 ``confirmation_required`` and nothing is done.
        discard_previous: Delete this server's old data directory once the replica streams.
    """

    token: str = Field(..., min_length=1, max_length=8192)
    replace_data: bool = False
    discard_previous: bool = False


class ReplicaJoinPlanRequest(BaseModel):
    """
    A join code to check.

    Attributes:
        token: The join code the primary gave. A secret.
    """

    token: str = Field(..., min_length=1, max_length=8192)


class ReplicaJoinPlanResponse(BaseModel):
    """
    What joining a primary would do to this server. Nothing was changed.

    Attributes:
        name: The replica's name.
        primary: The primary, ``host:port``.
        account: The replication account.
        slot: The slot (PostgreSQL).
        data_directory: The data directory that is replaced or read.
        saved_as: Where this server's own data is set aside.
        databases: The databases on this server that the join replaces.
        free_bytes: Free space where the copy is made.
        needed_bytes: What the primary said it holds, with a margin.
        capacity: The settings the replica starts with to match its primary.
        problems: What stops it.
        warnings: What the operator should know.
        ok: There are no problems.
    """

    name: str
    primary: str
    account: str
    slot: str = ""
    data_directory: str
    saved_as: str
    databases: list[str] = Field(default_factory=list)
    free_bytes: int = 0
    needed_bytes: int = 0
    capacity: dict[str, int] = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ok: bool = True


class ReplicaConfirmRequest(BaseModel):
    """
    The operator's acceptance of a promotion.

    Attributes:
        confirm: The operator accepts that this replica stops following its primary.
    """

    confirm: bool = False


# ------------------------------------------------------------------- helpers


def _engine_name(engine: str) -> str:
    """
    Check the engine a path names, before anything else is looked at.

    Args:
        engine: The path's ``{engine}``.

    Returns:
        It, unchanged.

    Raises:
        ValidationError: 400 when the engine has no replicas to offer.
    """
    if engine not in REPLICA_ENGINES:
        raise ValidationError(
            f"{engine} has no replicas to offer",
            details=f"Replicas are for: {', '.join(REPLICA_ENGINES)}.",
            field="engine",
        )
    return engine


Engine = Annotated[str, Depends(_engine_name)]


def _replication(session: dict[str, Any], engine: str) -> ReplicationManager | MySqlReplication:
    """
    Build the manager every route here uses.

    Args:
        session: The authenticated session.
        engine: ``postgresql``, or ``mysql`` for MySQL and MariaDB.

    Returns:
        The replication manager over the server's engine.

    Raises:
        ValidationError: When the engine has no replicas to offer.
    """
    return replication_for(service(session).manager(_engine_name(engine)))


def _engine_of(token: str) -> str:
    """
    Read which engine a join code is for.

    Args:
        token: The join code.

    Returns:
        The engine its replica is of.

    Raises:
        ValidationError: When it is not a join code.
    """
    return parse_code(token).engine


def _need(confirmed: bool, *warnings: str) -> None:
    """
    Refuse what has not been accepted.

    Args:
        confirmed: The operator accepted it.
        *warnings: What they accept by confirming.

    Raises:
        ConfirmationRequired: When ``confirmed`` is false.
    """
    if not confirmed:
        raise ConfirmationRequired(list(warnings))


# ------------------------------------------------------------------- routes


@router.get("/replicas", response_model=list[ReplicaStatusResponse])
def list_status(
    session: Annotated[dict, Depends(get_current_session)],
) -> list[ReplicaStatusResponse]:
    """
    Say how the replicas of every engine installed here stand.

    Args:
        session: The authenticated session.

    Returns:
        One entry per engine that is installed; one that is not has no replicas to show.
    """
    views = []
    for engine in REPLICA_ENGINES:
        try:
            views.append(ReplicaStatusResponse(**_replication(session, engine).status()))
        except (DatabaseError, ValidationError):
            continue
    return views


@router.post("/replicas/join/plan", response_model=ReplicaJoinPlanResponse)
def plan_join(
    request: ReplicaJoinPlanRequest, session: Annotated[dict, Depends(get_current_session)]
) -> ReplicaJoinPlanResponse:
    """
    Say what joining a primary would do to this server. Changes nothing.

    The join code is a secret: it is checked, its password is not in the answer.

    Args:
        request: The join code.
        session: The authenticated session.

    Returns:
        The plan: where the copy goes, what is replaced, the space, and what stops it.

    Raises:
        ValidationError: When it is not a join code, or for an engine that has no replicas.
    """
    manager = _replication(session, _engine_of(request.token))
    return ReplicaJoinPlanResponse(**manager.plan_join(request.token).to_dict())


def replica_join_job(
    token: str,
    discard_previous: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Make this server a replica of a primary.

    Copies the primary's cluster, then stops the engine once, sets its own data aside and puts the
    copy in its place; when the replica does not stream, the previous data is put back.

    Args:
        token: The join code (a secret the job's log is scrubbed of).
        discard_previous: Delete the old data directory once the replica streams.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        The primary, the replica's name and where the old data is.
    """
    from noust.web.api.databases.jobs import _done, _service

    databases = _service(job_context, "Joining a primary", actor)
    manager = replication_for(databases.manager(_engine_of(token)))
    result = manager.join(token, confirmed=True, discard_previous=discard_previous)
    _done(job_context)
    return {key: value for key, value in result.items() if key != "token"}


@router.post("/replicas/join", response_model=JobAcceptedResponse, status_code=202)
def join(
    request: ReplicaJoinRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue making this server a replica of a primary, with sudo mode.

    Checked before the job exists: the code is one, the plan has no problems, and the operator
    accepted that this server's data is replaced.

    Args:
        request: The join code and the confirmation.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: 400 when the code is not one or the plan has problems.
        ConfirmationRequired: 400 ``confirmation_required`` when the data replaced is not accepted.
    """
    manager = _replication(session, _engine_of(request.token))
    plan = manager.plan_join(request.token)
    if plan.problems:
        raise ValidationError(
            "This server cannot join that primary yet",
            details="\n".join(f"- {problem}" for problem in plan.problems),
            field="token",
        )
    view = plan.to_dict()
    _need(
        request.replace_data,
        f"The {manager.DISPLAY} data of this server is replaced by a copy of {view['primary']}: "
        f"the old data is set aside at {view['saved_as']}, and put back if the replica does not "
        "start streaming.",
    )
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Join {view['primary']} as a replica",
        description=f"Making this server a replica of {view['primary']}",
        func=replica_join_job,
        kwargs={"token": request.token, "discard_previous": request.discard_previous},
        metadata={"action": "replica-join", "primary": view["primary"], "replica": view["name"]},
        message=f"This server will follow {view['primary']}",
    )


@router.get("/replicas/{engine}", response_model=ReplicaStatusResponse)
def get_status(
    engine: Engine, session: Annotated[dict, Depends(get_current_session)]
) -> ReplicaStatusResponse:
    """
    Say how the replicas of one engine, or the engine as a replica, stand.

    Args:
        engine: ``postgresql``, or ``mysql`` for MySQL and MariaDB.
        session: The authenticated session.

    Returns:
        The role, the replicas prepared here with their connection, and the primary it follows.

    Raises:
        ValidationError: 400 for an engine with no replicas.
        DatabaseEngineError: When the engine is not installed or not running.
    """
    return ReplicaStatusResponse(**_replication(session, engine).status())


@router.post("/replicas/{engine}/plan", response_model=ReplicaPlanResponse)
def plan_prepare(
    engine: Engine,
    request: ReplicaPrepareRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> ReplicaPlanResponse:
    """
    Check everything and say what making room for a replica would do. Changes nothing.

    Before anything is changed the certificate the engine presents is checked the way the replica
    will check it, for the name in ``host``.

    Args:
        engine: ``postgresql`` or ``mysql``.
        request: Where the replica connects from and to.
        session: The authenticated session.

    Returns:
        The plan, with what stops it.

    Raises:
        ValidationError: 400 for an engine with no replicas or a name that is not allowed.
    """
    manager = _replication(session, engine)
    plan = manager.plan_prepare(
        request.name,
        source=request.source,
        host=request.host,
        listen=request.listen,
        ca_pem=request.ca_pem,
        open_firewall=request.open_firewall,
    )
    return ReplicaPlanResponse(**plan.to_dict())


@router.post("/replicas/{engine}", response_model=ReplicaPreparedResponse)
def prepare(
    engine: Engine,
    request: ReplicaPrepareRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ReplicaPreparedResponse:
    """
    Make room for a replica on this server and give back its join code, with sudo mode.

    It creates an account that can only replicate, a slot that keeps the log the replica has not
    read (PostgreSQL), and the line that lets that account in from ``source`` over TLS. The answer
    is the only place the join code is given besides ``.../token``: it is not the result of a job.

    Args:
        engine: ``postgresql`` or ``mysql``.
        request: Where the replica connects from and to, and the restart accepted.
        session: The authenticated, elevated session.

    Returns:
        The join code, what was done and what was recorded.

    Raises:
        ValidationError: 400 when the plan has problems or the name is not allowed.
        ConfirmationRequired: 400 ``confirmation_required`` when the engine restarts and the
            operator has not accepted it.
    """
    manager = _replication(session, engine)
    plan = manager.plan_prepare(
        request.name,
        source=request.source,
        host=request.host,
        listen=request.listen,
        ca_pem=request.ca_pem,
        open_firewall=request.open_firewall,
    )
    if plan.problems:
        raise ValidationError(
            "A replica cannot be prepared on this server yet",
            details="\n".join(f"- {problem}" for problem in plan.problems),
            field="plan",
        )
    if plan.restart:
        _need(
            request.confirm,
            f"Preparing this replica restarts {manager.DISPLAY}: every connection is dropped for a "
            f"few seconds, because it only listens on {plan.listen} after a restart.",
        )
    result = manager.prepare(
        request.name,
        source=request.source,
        host=request.host,
        listen=request.listen,
        ca_pem=request.ca_pem,
        open_firewall=request.open_firewall,
        confirmed=True,
    )
    return ReplicaPreparedResponse(**result)


@router.post("/replicas/{engine}/promote")
def promote(
    engine: Engine,
    request: ReplicaConfirmRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> dict[str, Any]:
    """
    Stop following the primary: this server becomes a primary and accepts writes. Sudo mode.

    Args:
        engine: ``postgresql`` or ``mysql``.
        request: The operator's acceptance.
        session: The authenticated, elevated session.

    Returns:
        What was done, with the note about pointing applications at this server.

    Raises:
        ConfirmationRequired: 400 ``confirmation_required`` without ``confirm``.
    """
    _need(
        request.confirm,
        "This replica stops following its primary and accepts writes. The two diverge from "
        "here: the primary's changes no longer reach it.",
    )
    return _replication(session, engine).promote(confirmed=True)


@router.post("/replicas/{engine}/{name}/token", response_model=ReplicaTokenResponse)
def show_token(
    engine: Engine, name: str, session: Annotated[dict, Depends(require_elevated)]
) -> ReplicaTokenResponse:
    """
    Show the join code of a replica prepared here again, with sudo mode. It is audited.

    Args:
        engine: ``postgresql`` or ``mysql``.
        name: The replica.
        session: The authenticated, elevated session.

    Returns:
        The join code, a secret.

    Raises:
        ValidationError: 400 when no replica of that name was prepared here.
    """
    code = _replication(session, engine).token(name)
    service(session).audit("db.replica.token", f"{engine}/{name}")
    return ReplicaTokenResponse(name=name, token=code)


@router.delete("/replicas/{engine}/{name}")
def remove(
    engine: Engine,
    name: str,
    session: Annotated[dict, Depends(require_elevated)],
    confirm: Annotated[bool, Query(description="Accept that it stops streaming for good")] = False,
    restore_listen: Annotated[
        bool, Query(description="Put the address the engine listens on back (restarts it)")
    ] = False,
) -> dict[str, Any]:
    """
    Take a replica away: its account, its slot and its access line go. Sudo mode.

    Args:
        engine: ``postgresql`` or ``mysql``.
        name: The replica.
        session: The authenticated, elevated session.
        confirm: The operator accepts that the replica stops streaming for good.
        restore_listen: Put the address the engine listens on back as it was before.

    Returns:
        What was removed, step by step.

    Raises:
        ConfirmationRequired: 400 ``confirmation_required`` without ``confirm``.
    """
    _need(confirm, f"The replica {name} stops streaming for good and its access is removed.")
    return _replication(session, engine).remove(name, confirmed=True, restore_listen=restore_listen)
