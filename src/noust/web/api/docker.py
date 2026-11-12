# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/docker``: what Docker has on this machine, whoever made it, and the few things that change it.

A client of one class, :class:`~noust.core.docker.gate.DockerGate` (rule 3): a request becomes a look at
the machine or an action on one object, and the answer is what the gate resolved. The gate is where
every object gets its owner (an application of Noust, a database engine, Noust itself, or nobody) and
where an action is judged as (owner, action, object) before a process is started (rule 4); nothing in
this module asks Docker for anything or decides who may change what.

What the routes add is HTTP:

- **The zone is the machine's.** Every route is held to the instance (a person of a team is refused),
  to ``docker.read`` or ``docker.manage``, and what changes something to sudo mode. What an
  application owns is seen from the application.
- **A machine without Docker answers.** A listing says ``available: false`` with the doctor's report
  (what is missing and how to install it) and no rows; an action is a 409 with the same words. The
  doctor is asked once for a few seconds, not for every request.
- **Every row says what it may be offered**, by the judgement an action goes through, for whoever asks.
- **A cleanup is a plan** that is shown, and applied as a job with the token it was shown with.

Literal paths (``summary``, ``engine``, ``restart-loss``, ``volumes/sizes``, ``cleanup``) come before
the parametrised ones.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from noust.core import audit
from noust.core.docker.engine import EngineReport
from noust.core.docker.errors import DockerOwnedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import Container, OwnerKind
from noust.core.exceptions import DockerError, ValidationError
from noust.core.runner import CommandRunner
from noust.terminal.targets import KIND_CONTAINER
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.api.docker_models import (
    DockerActionOut,
    DockerAttentionOut,
    DockerCanOut,
    DockerCleanupPlanOut,
    DockerCleanupRequest,
    DockerContainerDetailOut,
    DockerContainersOut,
    DockerDiskOut,
    DockerEngineOut,
    DockerImagesOut,
    DockerListOut,
    DockerLogsOut,
    DockerLostOut,
    DockerNetworksOut,
    DockerOriginOut,
    DockerRemoveVolumeRequest,
    DockerRestartLossOut,
    DockerRestartPolicyOut,
    DockerStackDetailOut,
    DockerStacksOut,
    DockerStateOut,
    DockerSummaryOut,
    DockerTouchesOut,
    DockerUsageOut,
    DockerVolumeSizesOut,
    DockerVolumesOut,
    DockerWhyOut,
    container_out,
    engine_out,
    image_out,
    network_out,
    owner_out,
    plan_out,
    stack_out,
    volume_out,
)
from noust.web.api.terminal import TerminalRequest, TerminalTicketResponse, mint_ticket
from noust.web.auth import actor_label, get_client_ip
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.permissions import Permission
from noust.web.permissions.docker_console import console_permission
from noust.web.permissions.enforce import check_permission
from noust.web.permissions.principal import audit_actor, permissions_of

logger = logging.getLogger(__name__)

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(get_current_session)]
Elevated = Annotated[dict[str, Any], Depends(require_elevated)]

#: Seconds the engine's doctor is trusted for. It costs four processes, and a page of the console asks
#: for several things at once.
ENGINE_TTL_SECONDS = 10.0

_engine_lock = threading.Lock()
_engine_seen: list[tuple[float, CommandRunner, EngineReport]] = []


def reset_engine_cache() -> None:
    """Forget what the doctor said: the next request asks the engine again."""
    with _engine_lock:
        _engine_seen.clear()


def _gate() -> DockerGate:
    """
    Returns:
        The gate, as whoever is asking: it reads the principal's scope from the request.
    """
    return DockerGate()


def _engine(gate: DockerGate, *, fresh: bool = False) -> EngineReport:
    """
    Ask the engine's doctor, at most once in :data:`ENGINE_TTL_SECONDS`.

    Args:
        gate: The gate.
        fresh: Ask again whatever was said a moment ago (the Engine page is the doctor).

    Returns:
        The report.
    """
    runner = gate.runner
    now = time.monotonic()
    with _engine_lock:
        if (
            not fresh
            and _engine_seen
            and _engine_seen[0][1] is runner
            and now - _engine_seen[0][0] < ENGINE_TTL_SECONDS
        ):
            return _engine_seen[0][2]
    report = gate.doctor()
    with _engine_lock:
        _engine_seen[:] = [(now, runner, report)]
    return report


def _may_manage(session: dict[str, Any]) -> bool:
    """
    Args:
        session: The authenticated payload.

    Returns:
        Whether it holds the permission to change Docker.
    """
    return Permission.DOCKER_MANAGE in permissions_of(session)


def _can(gate: DockerGate, thing: Any, session: dict[str, Any]) -> DockerCanOut:
    """
    Args:
        gate: The gate.
        thing: A container, an image, a volume or a network.
        session: The authenticated payload.

    Returns:
        What may be done to it by whoever asks, as the API says it: what the gate lets through for
        its owner, and only if the credential holds the permission to change Docker (and, for a shell
        in a container, the one that shell needs for whose container it is).
    """
    held = permissions_of(session)
    console = console_permission(thing.owner) in held if isinstance(thing, Container) else False
    return DockerCanOut(
        **gate.can(thing, manage=Permission.DOCKER_MANAGE in held, console=console).to_dict()
    )


def _owner_kind(value: str | None) -> OwnerKind | None:
    """
    Args:
        value: What the query asked to be cut by.

    Returns:
        The kind of owner, or None for no cut.

    Raises:
        ValidationError: It is not a kind of owner.
    """
    if not value:
        return None
    try:
        return OwnerKind(value)
    except ValueError:
        raise ValidationError(
            f"Not a kind of owner: {value!r}",
            details=f"Use one of: {', '.join(kind.value for kind in OwnerKind)}.",
            field="owner",
        ) from None


def _list_head(model: type[DockerListOut], report: EngineReport, manage: bool) -> dict[str, Any]:
    """
    Args:
        model: The listing's model.
        report: The engine's report.
        manage: Whether the principal may change Docker.

    Returns:
        The fields every listing carries when Docker cannot be read.
    """
    del model
    return {"available": False, "engine": engine_out(report), "can_manage": manage}


# ----------------------------------------------------------------------------- summary and engine


@router.get("/summary", response_model=DockerSummaryOut)
def get_summary(session: Session) -> DockerSummaryOut:
    """
    Say what asks for the operator on this machine, worst first, and how much there is of each thing.

    A container that keeps falling, one that is stopped when it should run, and the disk Docker lives on
    running out; the counters of every kind of thing; and the engine's doctor. Without Docker the
    answer is the doctor's, with nothing counted.

    Args:
        session: The authenticated session.

    Returns:
        The summary.
    """
    gate = _gate()
    report = _engine(gate)
    manage = _may_manage(session)
    if not report.usable:
        return DockerSummaryOut(available=False, engine=engine_out(report), can_manage=manage)
    found = gate.summary(report)
    return DockerSummaryOut(
        available=True,
        engine=engine_out(found.engine),
        counts={kind: dict(values) for kind, values in found.counts.items()},
        attention=[DockerAttentionOut(**item.to_dict()) for item in found.attention],
        disk=DockerDiskOut(**found.disk.to_dict()) if found.disk else None,
        can_manage=manage,
    )


@router.get("/engine", response_model=DockerEngineOut)
def get_engine(session: Session) -> DockerEngineOut:
    """
    Say which Docker there is and what does not fit in it: the engine's doctor.

    Asked of the engine every time, not from what was said a moment ago: this is the page that says it.

    Args:
        session: The authenticated session.

    Returns:
        The facts and the limits, each as a sentence with a code and its English, and what Docker said.
    """
    return engine_out(_engine(_gate(), fresh=True))


@router.get("/restart-loss", response_model=DockerRestartLossOut)
def get_restart_loss(session: Session) -> DockerRestartLossOut:
    """
    List what a restart of the machine would leave down, and how each is fixed.

    Args:
        session: The authenticated session.

    Returns:
        The containers that would not come back, by name.
    """
    gate = _gate()
    report = _engine(gate)
    if not report.usable:
        return DockerRestartLossOut(available=False, engine=engine_out(report))
    return DockerRestartLossOut(
        items=[
            DockerLostOut(**{**lost.to_dict(), "owner": owner_out(lost.owner)})
            for lost in gate.restart_loss()
        ]
    )


# ----------------------------------------------------------------------------- listings


@router.get("/stacks", response_model=DockerStacksOut)
def list_stacks(
    session: Session,
    offset: Annotated[int, Query(description="Where the page starts")] = 0,
    limit: Annotated[
        int | None, Query(description="How many it holds at most (1 to 1000); all when absent")
    ] = None,
    owner: Annotated[
        str | None,
        Query(description="Only those of this kind of owner: app, database, noust, nobody"),
    ] = None,
    search: Annotated[
        str | None,
        Query(
            max_length=200,
            description="Only those whose name, directory or containers contain this",
        ),
    ] = None,
) -> DockerStacksOut:
    """
    List the Compose projects, with their state, their containers and whose each is.

    Args:
        session: The authenticated session.
        offset: Where the page starts.
        limit: How many at most.
        owner: Only this kind of owner.
        search: Only what contains this text.

    Returns:
        The page, by name.

    Raises:
        ValidationError: 400 for a window or an owner that makes no sense.
    """
    gate = _gate()
    report = _engine(gate)
    manage = _may_manage(session)
    if not report.usable:
        return DockerStacksOut(**_list_head(DockerStacksOut, report, manage))
    page = gate.projects(offset=offset, limit=limit, owner=_owner_kind(owner), search=search)
    return DockerStacksOut(
        total=page.total,
        offset=page.offset,
        limit=page.limit,
        can_manage=manage,
        items=[stack_out(project) for project in page.items],
    )


@router.get("/stacks/{name}", response_model=DockerStackDetailOut)
def get_stack(name: str, session: Session) -> DockerStackDetailOut:
    """
    Read one Compose project with its containers in full.

    Args:
        name: The project's name.
        session: The authenticated session.

    Returns:
        The stack and its containers, each with its owner and what may be done to it.

    Raises:
        DockerNotFoundError: 404 when there is none.
    """
    gate = _gate()
    report = _engine(gate)
    manage = _may_manage(session)
    if not report.usable:
        return DockerStackDetailOut(available=False, engine=engine_out(report), can_manage=manage)
    project, members = gate.project(name)
    return DockerStackDetailOut(
        stack=stack_out(project),
        containers=[container_out(c, _can(gate, c, session)) for c in members],
        can_manage=manage,
    )


@router.get("/containers", response_model=DockerContainersOut)
def list_containers(
    session: Session,
    offset: Annotated[int, Query(description="Where the page starts")] = 0,
    limit: Annotated[
        int | None, Query(description="How many it holds at most (1 to 1000); all when absent")
    ] = None,
    owner: Annotated[
        str | None,
        Query(description="Only those of this kind of owner: app, database, noust, nobody"),
    ] = None,
    app: Annotated[
        str | None, Query(description="Only those of the application with this domain")
    ] = None,
    state: Annotated[
        str | None, Query(description="Only those in this Docker state: running, exited...")
    ] = None,
    project: Annotated[str | None, Query(description="Only those of this Compose project")] = None,
    search: Annotated[
        str | None,
        Query(max_length=200, description="Only those whose name or image contains this"),
    ] = None,
) -> DockerContainersOut:
    """
    List the containers, running or not, each with its owner, its state as one word and what may be
    done to it.

    Args:
        session: The authenticated session.
        offset: Where the page starts.
        limit: How many at most.
        owner: Only this kind of owner.
        app: Only the containers of this application.
        state: Only this Docker state.
        project: Only this Compose project.
        search: Only what contains this text.

    Returns:
        The page, by name.

    Raises:
        ValidationError: 400 for a window or an owner that makes no sense.
    """
    gate = _gate()
    report = _engine(gate)
    manage = _may_manage(session)
    if not report.usable:
        return DockerContainersOut(**_list_head(DockerContainersOut, report, manage))
    page = gate.containers(
        offset=offset,
        limit=limit,
        owner=_owner_kind(owner),
        app=app,
        state=state,
        project=project,
        search=search,
    )
    return DockerContainersOut(
        total=page.total,
        offset=page.offset,
        limit=page.limit,
        can_manage=manage,
        items=[container_out(c, _can(gate, c, session)) for c in page.items],
    )


@router.get("/containers/{ref}", response_model=DockerContainerDetailOut)
def get_container(ref: str, session: Session) -> DockerContainerDetailOut:
    """
    Look at one container from the inside.

    Why it is the way it is (how its last run ended, whether memory ran out, how often it restarted
    lately, its last lines as it wrote them), where it comes from and what it touches, its restart policy
    and limits, and what it uses now: one reading of ``docker stats`` without a stream, or the reason
    there is none (it is not running, it is restarting, it started a moment ago). Its environment is
    never read, and the credentials in what it wrote are masked.

    Args:
        ref: Its name, or the id Docker shows.
        session: The authenticated session.

    Returns:
        The container.

    Raises:
        DockerNotFoundError: 404 when there is none.
    """
    gate = _gate()
    report = _engine(gate)
    manage = _may_manage(session)
    if not report.usable:
        return DockerContainerDetailOut(
            available=False, engine=engine_out(report), can_manage=manage
        )
    found = gate.container_detail(ref)
    body = found.to_dict()
    return DockerContainerDetailOut(
        container=container_out(found.container, _can(gate, found.container, session)),
        state=DockerStateOut(**body["state"]),
        owner=owner_out(found.container.owner),
        job=body["job"],
        why=DockerWhyOut(**body["why"]),
        origin=DockerOriginOut(**body["origin"]),
        touches=DockerTouchesOut(**body["touches"]),
        restart_policy=DockerRestartPolicyOut(**body["restart_policy"]),
        cpu_limit=body["cpu_limit"],
        usage=DockerUsageOut(**body["usage"]),
        can_manage=manage,
    )


@router.get("/containers/{ref}/logs", response_model=DockerLogsOut)
def get_container_logs(
    ref: str,
    session: Session,
    tail: Annotated[int, Query(description="How many lines from the end (1 to 5000)")] = 200,
) -> DockerLogsOut:
    """
    Read the end of a container's log, as it wrote it with the time of each line.

    The live follow is the WebSocket ``/ws/docker/logs/{ref}``.

    Args:
        ref: Its name, or the id Docker shows.
        session: The authenticated session.
        tail: How many lines from the end.

    Returns:
        The lines, with the credentials in them masked and nothing else changed.

    Raises:
        ValidationError: 400 for a tail that makes no sense.
        DockerNotFoundError: 404 when there is none.
    """
    gate = _gate()
    report = _engine(gate)
    if not report.usable:
        return DockerLogsOut(available=False, engine=engine_out(report))
    container = gate.container(ref)
    return DockerLogsOut(
        container=container.name, tail=tail, logs=gate.container_logs(container.id, tail=tail)
    )


@router.get("/images", response_model=DockerImagesOut)
def list_images(
    session: Session,
    offset: Annotated[int, Query(description="Where the page starts")] = 0,
    limit: Annotated[
        int | None, Query(description="How many it holds at most (1 to 1000); all when absent")
    ] = None,
    owner: Annotated[
        str | None,
        Query(description="Only those of this kind of owner: app, database, noust, nobody"),
    ] = None,
    search: Annotated[
        str | None,
        Query(max_length=200, description="Only those whose name, id or users contain this"),
    ] = None,
) -> DockerImagesOut:
    """
    List the images, each once with every name it has, who runs it and whether it is an application's
    way back.

    Args:
        session: The authenticated session.
        offset: Where the page starts.
        limit: How many at most.
        owner: Only this kind of owner.
        search: Only what contains this text.

    Returns:
        The page, by name.

    Raises:
        ValidationError: 400 for a window or an owner that makes no sense.
    """
    gate = _gate()
    report = _engine(gate)
    manage = _may_manage(session)
    if not report.usable:
        return DockerImagesOut(**_list_head(DockerImagesOut, report, manage))
    page = gate.images(offset=offset, limit=limit, owner=_owner_kind(owner), search=search)
    return DockerImagesOut(
        total=page.total,
        offset=page.offset,
        limit=page.limit,
        can_manage=manage,
        items=[image_out(i, _can(gate, i, session)) for i in page.items],
    )


@router.get("/volumes", response_model=DockerVolumesOut)
def list_volumes(
    session: Session,
    offset: Annotated[int, Query(description="Where the page starts")] = 0,
    limit: Annotated[
        int | None, Query(description="How many it holds at most (1 to 1000); all when absent")
    ] = None,
    owner: Annotated[
        str | None,
        Query(description="Only those of this kind of owner: app, database, noust, nobody"),
    ] = None,
    search: Annotated[
        str | None, Query(max_length=200, description="Only those whose name or users contain this")
    ] = None,
) -> DockerVolumesOut:
    """
    List the named volumes, with who owns each and who mounts it. A listing does not measure them
    (``GET /volumes/sizes`` does: Docker walks every volume to say it).

    Args:
        session: The authenticated session.
        offset: Where the page starts.
        limit: How many at most.
        owner: Only this kind of owner.
        search: Only what contains this text.

    Returns:
        The page, by name, every ``size_bytes`` null.

    Raises:
        ValidationError: 400 for a window or an owner that makes no sense.
    """
    gate = _gate()
    report = _engine(gate)
    manage = _may_manage(session)
    if not report.usable:
        return DockerVolumesOut(**_list_head(DockerVolumesOut, report, manage))
    page = gate.volumes(offset=offset, limit=limit, owner=_owner_kind(owner), search=search)
    return DockerVolumesOut(
        total=page.total,
        offset=page.offset,
        limit=page.limit,
        can_manage=manage,
        items=[volume_out(v, _can(gate, v, session)) for v in page.items],
    )


@router.get("/volumes/sizes", response_model=DockerVolumeSizesOut)
def get_volume_sizes(session: Session) -> DockerVolumeSizesOut:
    """
    Measure the volumes: a call of its own, because Docker walks each one and it takes a while.

    Args:
        session: The authenticated session.

    Returns:
        The size in bytes of each volume the principal sees that Docker could measure.
    """
    gate = _gate()
    report = _engine(gate)
    if not report.usable:
        return DockerVolumeSizesOut(available=False, engine=engine_out(report))
    return DockerVolumeSizesOut(sizes=gate.volume_sizes())


@router.get("/networks", response_model=DockerNetworksOut)
def list_networks(
    session: Session,
    offset: Annotated[int, Query(description="Where the page starts")] = 0,
    limit: Annotated[
        int | None, Query(description="How many it holds at most (1 to 1000); all when absent")
    ] = None,
    owner: Annotated[
        str | None,
        Query(description="Only those of this kind of owner: app, database, noust, nobody"),
    ] = None,
    search: Annotated[
        str | None,
        Query(max_length=200, description="Only those whose name or containers contain this"),
    ] = None,
) -> DockerNetworksOut:
    """
    List the networks, with who is on each, whose it is and whether Docker made it itself.

    Args:
        session: The authenticated session.
        offset: Where the page starts.
        limit: How many at most.
        owner: Only this kind of owner.
        search: Only what contains this text.

    Returns:
        The page, by name.

    Raises:
        ValidationError: 400 for a window or an owner that makes no sense.
    """
    gate = _gate()
    report = _engine(gate)
    manage = _may_manage(session)
    if not report.usable:
        return DockerNetworksOut(**_list_head(DockerNetworksOut, report, manage))
    page = gate.networks(offset=offset, limit=limit, owner=_owner_kind(owner), search=search)
    return DockerNetworksOut(
        total=page.total,
        offset=page.offset,
        limit=page.limit,
        can_manage=manage,
        items=[network_out(n, _can(gate, n, session)) for n in page.items],
    )


# ----------------------------------------------------------------------------- actions


def _acted(
    session: dict[str, Any],
    event: str,
    kind: str,
    ref: str,
    action: str,
    do: Any,
) -> DockerActionOut:
    """
    Do one action through the gate and put it on record, whatever comes of it.

    Args:
        session: The authenticated payload.
        event: The audit event of a success.
        kind: ``container``, ``image``, ``volume`` or ``network``.
        ref: What the person named.
        action: ``start``, ``stop``, ``restart`` or ``remove``.
        do: Does it through the gate and returns the object that was acted on (a container) or what
            was removed.

    Returns:
        What was done.

    Raises:
        DockerUnavailableError: 409 when Docker is not there or is below what Noust speaks.
        DockerOwnedError: 409 when an application, an engine or Noust owns it; the error says which
            door to use, and the refusal is recorded.
        DockerRefusedError: 409 when Docker refused, with its own words in ``output``.
    """
    gate = _gate()
    gate.require_usable(_engine(gate))
    actor = audit_actor(session)
    target = f"{kind}:{ref}"
    try:
        done = do(gate)
    except DockerOwnedError as exc:
        audit.record(
            "docker.denied",
            actor=actor,
            target=target,
            outcome="denied",
            details={"action": action, "owner": exc.owner_kind, "subject": exc.subject},
        )
        raise
    except DockerError as exc:
        audit.record(
            event,
            actor=actor,
            target=target,
            outcome="failure",
            details={"action": action, "reason": exc.message},
        )
        raise
    name = done.name
    size = getattr(done, "size_bytes", None) if not isinstance(done, Container) else None
    audit.record(
        event,
        actor=actor,
        target=f"{kind}:{name}",
        details={"action": action, **({"size_bytes": size} if size is not None else {})},
    )
    return DockerActionOut(kind=kind, name=name, action=action, size_bytes=size)


@router.post("/containers/{ref}/start", response_model=DockerActionOut)
def start_container(ref: str, session: Elevated) -> DockerActionOut:
    """
    Start a container nobody of Noust owns.

    What belongs to an application, a database engine or Noust is refused (409 ``docker_owned``) with
    the door to use: a stack is not changed from under its health gate. Needs sudo mode and
    ``docker.manage``.

    Args:
        ref: Its name, or the id Docker shows.
        session: The authenticated session, in sudo mode.

    Returns:
        What was done.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when it is somebody's.
    """
    return _acted(
        session, "docker.action", "container", ref, "start", lambda g: g.start_container(ref)
    )


@router.post("/containers/{ref}/stop", response_model=DockerActionOut)
def stop_container(ref: str, session: Elevated) -> DockerActionOut:
    """
    Stop a container nobody of Noust owns, giving it 30 seconds before Docker kills it.

    Args:
        ref: Its name, or the id Docker shows.
        session: The authenticated session, in sudo mode.

    Returns:
        What was done.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when it is somebody's.
    """
    return _acted(
        session, "docker.action", "container", ref, "stop", lambda g: g.stop_container(ref)
    )


@router.post("/containers/{ref}/restart", response_model=DockerActionOut)
def restart_container(ref: str, session: Elevated) -> DockerActionOut:
    """
    Restart a container nobody of Noust owns.

    Args:
        ref: Its name, or the id Docker shows.
        session: The authenticated session, in sudo mode.

    Returns:
        What was done.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when it is somebody's.
    """
    return _acted(
        session, "docker.action", "container", ref, "restart", lambda g: g.restart_container(ref)
    )


@router.delete("/containers/{ref}", response_model=DockerActionOut)
def remove_container(
    ref: str,
    session: Elevated,
    force: Annotated[bool, Query(description="Remove it even if it is running")] = False,
) -> DockerActionOut:
    """
    Remove a container nobody of Noust owns. Its volumes are left where they are.

    Args:
        ref: Its name, or the id Docker shows.
        session: The authenticated session, in sudo mode.
        force: Remove it even if it is running.

    Returns:
        What was removed.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when it is somebody's.
        DockerRefusedError: 409 when Docker refused (it is running and ``force`` was not given).
    """
    return _acted(
        session,
        "docker.remove",
        "container",
        ref,
        "remove",
        lambda g: g.remove_container(ref, force=force),
    )


@router.delete("/images/{image_id}", response_model=DockerActionOut)
def remove_image(image_id: str, session: Elevated) -> DockerActionOut:
    """
    Remove an image nobody keeps. One a container uses stays, and so does an application's way back.

    Args:
        image_id: The image's id, as the listing gives it.
        session: The authenticated session, in sudo mode.

    Returns:
        What was removed, with its size.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when an application keeps it or an engine owns it.
        ValidationError: 400 when a container still uses it.
    """
    return _acted(
        session, "docker.remove", "image", image_id, "remove", lambda g: g.remove_image(image_id)
    )


@router.delete("/networks/{ref}", response_model=DockerActionOut)
def remove_network(ref: str, session: Elevated) -> DockerActionOut:
    """
    Remove a network nobody of Noust owns and Docker did not make itself.

    Args:
        ref: Its name, or its id.
        session: The authenticated session, in sudo mode.

    Returns:
        What was removed.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when it is somebody's, or Docker's own.
        DockerRefusedError: 409 when Docker refused (a container is on it).
    """
    return _acted(
        session, "docker.remove", "network", ref, "remove", lambda g: g.remove_network(ref)
    )


@router.delete("/volumes/{name}", response_model=DockerActionOut)
def remove_volume(
    name: str, session: Elevated, body: DockerRemoveVolumeRequest | None = None
) -> DockerActionOut:
    """
    Remove a volume. Its name is typed a second time, and what it holds is said before it goes.

    Args:
        name: The volume's name.
        session: The authenticated session, in sudo mode.
        body: ``confirm_name``, the name again as the person typed it.

    Returns:
        What was removed, with the size it held.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when an application or an engine owns it.
        ConfirmationRequired: 400 ``confirmation_required`` when the name was not typed again; the
            warning says how much data is at stake.
        ValidationError: 400 when a container still mounts it.
    """
    typed = body.confirm_name if body is not None else None
    return _acted(
        session,
        "docker.remove",
        "volume",
        name,
        "remove",
        lambda g: g.remove_volume(name, confirm_name=typed),
    )


# ----------------------------------------------------------------------------- console


@router.post("/containers/{ref}/terminal", response_model=TerminalTicketResponse)
def open_container_terminal(
    ref: str, body: TerminalRequest, request: Request, session: Elevated
) -> TerminalTicketResponse:
    """
    Get the ticket that opens a console in a container, whoever made it.

    The terminal's own machinery (a single-use ticket, a session, a recording, the audit trail) with a
    target of its own: ``docker exec`` of the id Docker listed, with a shell asked for by name. The
    container is resolved to its owner first, and the permission asked is the one its owner asks for:
    ``apps.terminal`` for a container of an application, ``server.terminal`` for any other, on top of
    ``docker.manage`` and sudo mode. What Noust runs for itself is never opened (409 ``docker_owned``),
    and a container that does not run has no console (400). The terminals' switch is the one of
    application terminals. Open the WebSocket ``/ws/terminal?terminal_ticket=<ticket>`` within
    ``expires_in`` seconds; the socket judges it all again.

    Args:
        ref: The container's name, or the id Docker shows.
        body: The shell and the size.
        request: The request.
        session: The authenticated session, in sudo mode.

    Returns:
        The ticket.

    Raises:
        DockerNotFoundError: 404 when there is none.
        DockerOwnedError: 409 when it is part of Noust.
        PermissionDenied: 403 when the credential lacks what a shell in this container needs.
        TerminalDisabledError: 403 when terminals are off here.
        TerminalLimitError: 429 when a limit is reached.
    """
    gate = _gate()
    gate.require_usable(_engine(gate))
    container = gate.container_for_console(ref)
    needed = console_permission(container.owner)
    if needed not in permissions_of(session):
        reason = f"This needs the '{needed}' permission to open a console in this container"
        audit.record(
            "terminal.denied",
            actor=audit_actor(session),
            target=f"container:{container.name}",
            outcome="denied",
            details={"reason": reason, "client_ip": get_client_ip(request)},
        )
        check_permission(session, needed)
    domain = container.owner.subject if container.owner.kind is OwnerKind.APP else None
    return mint_ticket(
        request, session, kind=KIND_CONTAINER, domain=domain, body=body, container=container
    )


# ----------------------------------------------------------------------------- cleanup


@router.get("/cleanup/plan", response_model=DockerCleanupPlanOut)
def get_cleanup_plan(session: Session) -> DockerCleanupPlanOut:
    """
    Say what a cleanup of Docker would remove, without removing anything.

    The images nobody names or uses, the stopped containers nobody made, the unused volumes (never
    ticked, each named before it goes, with how much it holds), the networks nobody is on and the old
    build cache, each with whose it is and how big; and what stays and why (the images an application
    keeps to go back to are never offered). The ``token`` is what the plan is applied with.

    Args:
        session: The authenticated session.

    Returns:
        The plan.
    """
    gate = _gate()
    report = _engine(gate)
    if not report.usable:
        return DockerCleanupPlanOut(available=False, engine=engine_out(report))
    return plan_out(gate.cleanup_plan())


def docker_cleanup_job(
    token: str,
    select: list[str],
    confirm_names: dict[str, str],
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Apply a cleanup plan as it was shown.

    Args:
        token: The token of the plan that was shown.
        select: The ids of what to remove.
        confirm_names: For each volume chosen, its name as typed.
        job_context: Injected by the job manager.

    Returns:
        What was removed, what Docker refused and what was freed.
    """
    if job_context is None:
        raise ValidationError("A job runs with its context")
    job_context.update("Cleaning up Docker", 10)
    gate = _gate()
    result = gate.apply_cleanup(token, select, confirm_names=confirm_names, on_line=job_context.log)
    # Who queued it is the actor the job manager bound for the job.
    audit.record(
        "docker.cleanup",
        target="docker",
        outcome="ok" if not result.failed else "failure",
        details={
            "stage": "finished",
            "removed": len(result.removed),
            "failed": len(result.failed),
            "freed_bytes": result.freed_bytes,
        },
    )
    job_context.update("Done", 100)
    return result.to_dict()


@router.post("/cleanup", response_model=JobAcceptedResponse, status_code=202)
def apply_cleanup(body: DockerCleanupRequest, session: Elevated) -> JobAcceptedResponse:
    """
    Apply a cleanup plan exactly as it was shown, as a job.

    Everything is checked before a job is queued: the plan must still be the one the ``token`` names
    (409 ``plan_changed`` with the plan as it is now, to be shown again), what is chosen must be in it, and
    each volume chosen must have its name typed (400 ``confirmation_required``). The job removes each
    thing through the gate, which judges its owner once more, and tells what Docker refused without
    stopping the rest. Needs sudo mode and ``docker.manage``.

    Args:
        body: The token, the ids chosen and the volume names typed.
        session: The authenticated session, in sudo mode.

    Returns:
        The queued job, whose result is what was removed and what failed.

    Raises:
        DockerPlanChangedError: 409 when the plan is not the one that was shown.
        ValidationError: 400 for something that is not in the plan, or nothing chosen.
        ConfirmationRequired: 400 when a volume was not named.
    """
    gate = _gate()
    gate.require_usable(_engine(gate))
    chosen = gate.prepare_cleanup(body.token, body.select, confirm_names=body.confirm_names)
    actor = audit_actor(session)
    job = get_job_manager().create_job(
        job_type=JobType.CLEANUP,
        name="Clean up Docker",
        description=f"Removing {len(chosen)} things from Docker, as the plan showed",
        func=docker_cleanup_job,
        kwargs={
            "token": body.token,
            "select": [item.id for item in chosen],
            "confirm_names": dict(body.confirm_names),
        },
        metadata={"action": "docker", "items": len(chosen)},
        actor=actor_label(session),
    )
    audit.record(
        "docker.cleanup",
        actor=actor,
        target="docker",
        details={"stage": "queued", "job": job.id, "items": len(chosen)},
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Cleanup queued",
        job=job.to_dict(),
    )


__all__ = ["docker_cleanup_job", "reset_engine_cache", "router"]
