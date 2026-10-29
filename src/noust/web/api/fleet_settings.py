# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/settings``: what the central wants every server to have (item 90).

A thin layer over :class:`~noust.fleet.settings.desired.FleetSettings` (what is wanted, and who
may set what), :mod:`noust.fleet.settings.status` (where each server stands) and
:mod:`noust.web.fleet_settings_actions` (applying it), which is a bulk action like the others:
its progress, per server, is ``GET /api/fleet/jobs/{id}``.

Setting one needs sudo mode and the permission of the thing set (the permission its route on a
single server asks), and is recorded as a **change** with an id; the pushes that apply it carry
that change and the person who approved it, also later, when a server comes back, with nobody in
front of the console. A secret goes in and never comes out: it is kept in the secret store and
shown as ``***``. Removing a setting from the fleet never deletes it from the servers: they keep
what was last applied as their own.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.core.server_text import say
from noust.core.teams.model import ResourceNotFoundError
from noust.fleet.client import actor_label as fleet_actor_label
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.settings.kinds import KINDS, NODE_SCOPE_PREFIX, PLANNED, check_scope, kind_of
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute, ensure_elevated, require_auth, require_elevated
from noust.web.api.fleet import _plan_and_start, asker_for
from noust.web.auth import actor_label
from noust.web.permissions.principal import permissions_of

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(require_auth)]
Elevated = Annotated[dict[str, Any], Depends(require_elevated)]


class FleetSettingKindOut(BaseModel):
    """
    One kind of setting.

    Attributes:
        key: Its name (``channel``).
        title: What it is called.
        available: Whether this release manages it from the central.
        permission: The permission a person needs to set one for the fleet.
        operations: The routes a server must offer to take one.
        reason: Why it is not available: a sentence with its code, when it is not.
    """

    key: str
    title: str
    available: bool
    permission: str | None = None
    operations: list[str] = Field(default_factory=list)
    reason: dict[str, Any] | None = None


class FleetSettingOut(BaseModel):
    """
    One setting of the fleet, in one scope.

    Attributes:
        resource: The kind.
        name: The setting.
        scope: ``fleet``, or ``node:<server>`` for an exception.
        node: The server of an exception.
        version: The row's version: one counter for every setting, never reused.
        value: The value, with ``***`` where a secret is stored.
        secrets: The paths of the value that hold a secret.
        change: The change that wrote it.
        changed_by: The person who approved that change.
        changed_at: When.
    """

    resource: str
    name: str
    scope: str
    node: str | None = None
    version: int
    value: dict[str, Any]
    secrets: list[str]
    change: str
    changed_by: str
    changed_at: str


class FleetSettingsOut(BaseModel):
    """
    Everything the fleet's settings hold.

    Attributes:
        kinds: Every kind there is, and whether it can be set.
        items: Every setting in every scope.
    """

    kinds: list[FleetSettingKindOut]
    items: list[FleetSettingOut]


class FleetSettingIn(BaseModel):
    """
    A setting to set.

    Attributes:
        value: The body of the server's own route for it. A secret sent back as ``***`` keeps
            the one stored.
        scope: ``fleet`` for every server, or ``node:<server>`` for an exception.
        apply: Apply it to the servers now (the default).
    """

    value: dict[str, Any]
    scope: str = "fleet"
    apply: bool = True


class FleetSettingsAppliedOut(BaseModel):
    """
    The job that applies a change.

    Attributes:
        job_id: The job: ``GET /api/fleet/jobs/{id}`` says how each server is doing.
        nodes: The servers it runs on.
    """

    job_id: str
    nodes: list[str]


class FleetSettingSavedOut(BaseModel):
    """
    A setting that was set.

    Attributes:
        setting: It, as stored.
        apply: The job applying it; null when none was started.
    """

    setting: FleetSettingOut
    apply: FleetSettingsAppliedOut | None = None


class FleetSettingRemovedOut(BaseModel):
    """
    A setting that was taken away.

    Attributes:
        removed: Whether there was one.
        apply: The job that lets the servers go of it; null when none was started.
    """

    removed: bool
    apply: FleetSettingsAppliedOut | None = None


class FleetSettingsModeIn(BaseModel):
    """
    How a server follows the fleet's settings.

    Attributes:
        mode: ``managed`` (it follows them) or ``independent`` (it is left out).
    """

    mode: str


class FleetSettingsModeOut(BaseModel):
    """
    A server's mode.

    Attributes:
        node: The server.
        mode: Its mode.
        apply: The job that settles it, when one was started.
    """

    node: str
    mode: str
    apply: FleetSettingsAppliedOut | None = None


class FleetSettingsApplyIn(BaseModel):
    """
    Apply the fleet's settings to the servers.

    Attributes:
        nodes: The servers; every one when empty.
        adopt: Take over a setting a server already has that is not the fleet's.
        restore: ``kind/name`` to put back where a server's operator changed it.
        restore_all: Put back every one of them.
        plan: Only say what would run.
    """

    nodes: list[str] = Field(default_factory=list)
    adopt: bool = False
    restore: list[str] = Field(default_factory=list)
    restore_all: bool = False
    plan: bool = False


def _settings() -> FleetSettings:
    """
    Returns:
        The settings over the central's store and secret store (the node registry's).
    """
    from noust.fleet.nodes import NodeManager

    manager = NodeManager()
    return FleetSettings(manager.store, manager.secrets)


def _out(settings: FleetSettings, setting: Any) -> FleetSettingOut:
    """
    Args:
        settings: The settings.
        setting: A row.

    Returns:
        Its API shape.
    """
    return FleetSettingOut(**settings.shown(setting))


def _person(session: dict[str, Any]) -> str:
    """
    Args:
        session: The authenticated payload.

    Returns:
        Who approves the change, as a label a push can be signed with.
    """
    return fleet_actor_label(actor_label(session))


def _missing(resource: str, name: str) -> ResourceNotFoundError:
    """
    Args:
        resource: The kind.
        name: The setting.

    Returns:
        The not found for it.
    """
    return ResourceNotFoundError.from_text(
        say("fleet_settings.no_such_setting", kind=resource, name=name)
    )


async def _queue(
    session: dict[str, Any], nodes: list[str] | None, options: dict[str, Any] | None = None
) -> FleetSettingsAppliedOut | None:
    """
    Start applying the fleet's settings.

    Args:
        session: The authenticated payload: the reads are made as them.
        nodes: The servers; all when None.
        options: The action's options.

    Returns:
        The job; None when there is no server.
    """
    from noust.fleet.nodes import NodeManager
    from noust.web.fleet_settings_actions import queue_apply

    targets = nodes if nodes is not None else [record.name for record in NodeManager().list()]
    if not targets:
        return None
    job = await run_in_threadpool(
        lambda: queue_apply(
            targets, options=options, asker=asker_for(session), actor=actor_label(session)
        )
    )
    return FleetSettingsAppliedOut(job_id=job.id, nodes=targets) if job is not None else None


def _kind_out(kind: Any) -> FleetSettingKindOut:
    """
    Args:
        kind: A kind of setting.

    Returns:
        Its API shape.
    """
    return FleetSettingKindOut(
        key=kind.key,
        title=kind.title,
        available=True,
        permission=kind.permission,
        operations=[f"{method} {template}" for method, template in kind.operations],
    )


@router.get("/settings", response_model=FleetSettingsOut)
def list_settings(session: Session) -> FleetSettingsOut:
    """
    List what the fleet's settings hold, in every scope, and the kinds there are.

    Args:
        session: The authenticated session.

    Returns:
        The kinds, and every setting with its secrets shown as ``***``.
    """
    settings = _settings()
    kinds = [_kind_out(kind) for kind in KINDS.values()]
    kinds += [
        FleetSettingKindOut(
            key=key,
            title=key.replace("_", " ").capitalize(),
            available=False,
            reason=say("fleet_settings.kind_not_yet", kind=key).to_dict(),
        )
        for key in PLANNED
    ]
    return FleetSettingsOut(kinds=kinds, items=[_out(settings, row) for row in settings.list()])


@router.get("/settings/status")
async def settings_status(
    session: Session,
    refresh: Annotated[
        bool, Query(description="Ask the servers even if they were just asked")
    ] = True,
) -> dict[str, Any]:
    """
    Say, for each setting, where each server stands.

    Every server is asked what it manages (it looks at what it holds first, so a setting its own
    operator changed shows as such). A server that cannot be read is said so with the reason, not
    guessed at. Nothing secret is read.

    Args:
        session: The authenticated session.
        refresh: Ask even a server whose answer is a few seconds old.

    Returns:
        ``servers`` (how each answered, its mode, whether a look is waiting), ``settings`` (each
        with its fleet row, its exceptions and a cell per server: ``origin``, ``sync`` and
        ``reach``, whether a channel's route can match anything on that server), ``released`` (what a server still manages that the fleet no longer wants), and
        ``partial``.
    """
    from noust.fleet.settings.status import read

    settings = _settings()
    asker = asker_for(session)
    return await run_in_threadpool(lambda: read(settings, asker=asker, refresh=refresh))


@router.get("/settings/{resource}", response_model=FleetSettingsOut)
def list_kind(resource: str, session: Session) -> FleetSettingsOut:
    """
    List the settings of one kind, in every scope.

    Args:
        resource: The kind.
        session: The authenticated session.

    Returns:
        The kind, and its settings.

    Raises:
        ValidationError: 400 for a kind that is unknown, or not managed from the central yet.
    """
    kind = kind_of(resource)
    settings = _settings()
    return FleetSettingsOut(
        kinds=[_kind_out(kind)],
        items=[_out(settings, row) for row in settings.list(resource)],
    )


@router.get("/settings/{resource}/{name}", response_model=FleetSettingsOut)
def get_setting(resource: str, name: str, session: Session) -> FleetSettingsOut:
    """
    Show one setting: the fleet's value and every exception.

    Args:
        resource: The kind.
        name: The setting.
        session: The authenticated session.

    Returns:
        Its rows, one per scope, with secrets shown as ``***``.

    Raises:
        ResourceNotFoundError: 404 when there is no such setting.
    """
    kind = kind_of(resource)
    settings = _settings()
    rows = [row for row in settings.list(resource) if row.name == name]
    if not rows:
        raise _missing(resource, name)
    return FleetSettingsOut(
        kinds=[_kind_out(kind)],
        items=[_out(settings, row) for row in rows],
    )


@router.put("/settings/{resource}/{name}", response_model=FleetSettingSavedOut)
async def set_setting(
    resource: str, name: str, body: FleetSettingIn, session: Elevated
) -> FleetSettingSavedOut:
    """
    Set a setting of the fleet, and apply it to the servers.

    Needs sudo mode and the permission the server's own route for it asks. It is recorded as a
    change with an id; the pushes that apply it carry the person who approves it and that change.

    Args:
        resource: The kind.
        name: The setting.
        body: Its value, the scope it is set in and whether to apply it now.
        session: The elevated session.

    Returns:
        The setting as stored, and the job applying it.

    Raises:
        ValidationError: 400 for a name, scope or value that is not acceptable, naming the field.
        NoustPermissionError: 403 without the permission of that kind.
    """
    settings = _settings()
    saved = await run_in_threadpool(
        lambda: settings.save(
            resource,
            name,
            body.value,
            scope=body.scope,
            person=_person(session),
            held=permissions_of(session),
        )
    )
    # What changed, never a value: a value may hold credentials.
    audit.record(
        "fleet.setting.set",
        actor=session_actor(session),
        target=f"{resource}:{name}",
        details={
            "scope": saved.scope,
            "version": saved.version,
            "change": saved.change_id,
            "secrets": len(saved.secret_paths),
        },
    )
    applying = None
    if body.apply:
        nodes = [saved.node] if saved.node else None
        applying = await _queue(session, nodes)
    return FleetSettingSavedOut(setting=_out(settings, saved), apply=applying)


@router.delete("/settings/{resource}/{name}", response_model=FleetSettingRemovedOut)
async def remove_setting(
    resource: str,
    name: str,
    session: Elevated,
    scope: Annotated[str, Query(description="fleet, or node:<server> for an exception")] = "fleet",
    apply: Annotated[bool, Query(description="Let the servers go of it now")] = True,
) -> FleetSettingRemovedOut:
    """
    Take a setting away from the fleet. The servers keep what they applied, as their own.

    Args:
        resource: The kind.
        name: The setting.
        session: The elevated session.
        scope: ``fleet`` or ``node:<server>``.
        apply: Let the servers go of it now.

    Returns:
        Whether there was one, and the job that lets the servers go of it.

    Raises:
        NoustPermissionError: 403 without the permission of that kind.
    """
    scope = check_scope(scope)
    settings = _settings()
    removed = await run_in_threadpool(
        lambda: settings.remove(
            resource, name, scope, person=_person(session), held=permissions_of(session)
        )
    )
    if removed:
        audit.record(
            "fleet.setting.remove",
            actor=session_actor(session),
            target=f"{resource}:{name}",
            details={"scope": scope},
        )
    applying = None
    if removed and apply:
        node = scope[len(NODE_SCOPE_PREFIX) :] if scope != "fleet" else None
        applying = await _queue(session, [node] if node else None)
    return FleetSettingRemovedOut(removed=removed, apply=applying)


@router.put("/servers/{node}/settings-mode", response_model=FleetSettingsModeOut)
async def set_mode(node: str, body: FleetSettingsModeIn, session: Elevated) -> FleetSettingsModeOut:
    """
    Make a server follow the fleet's settings, or leave it out of them.

    An independent server is pushed nothing and is let go of what it had (it keeps it, as its own);
    it still appears in every fleet view. A server that follows them again is applied everything.

    Args:
        node: The server.
        body: The mode.
        session: The elevated session.

    Returns:
        The mode and the job that settles it.

    Raises:
        ValidationError: 400 for a mode that is not one, or a server that is not in the fleet.
    """
    settings = _settings()
    mode = await run_in_threadpool(
        lambda: settings.set_mode(node, body.mode, person=_person(session))
    )
    audit.record(
        "fleet.setting.mode",
        actor=session_actor(session),
        target=f"node:{node}",
        details={"mode": mode},
    )
    return FleetSettingsModeOut(node=node, mode=mode, apply=await _queue(session, [node]))


@router.post("/settings/apply")
async def apply_settings(
    body: FleetSettingsApplyIn, request: Request, session: Session
) -> dict[str, Any]:
    """
    Apply the fleet's settings to the servers: "Apply to the fleet".

    A bulk action like the others: a plan first (which servers run, which are skipped and why),
    batches, and a record per server with each setting's result - ``created``, ``updated``,
    ``in_sync``, ``overridden`` (its operator changed it; left unless asked), ``restored``,
    ``released``, ``conflict`` (it exists there and is not the fleet's; left unless asked to adopt
    it), ``unsupported`` or ``failed`` with the server's own words. Poll it with
    ``GET /api/fleet/jobs/{id}``.

    Args:
        body: The servers, and what to do about conflicts and changed settings.
        request: The request.
        session: The authenticated session.

    Returns:
        ``plan`` and, unless only planning, ``job``.

    Raises:
        ValidationError: An unknown server or option, or no server to apply to.
        HTTPException: 403 ``elevation_required`` for ``adopt``, ``restore`` and ``restore_all``
            without sudo mode: they take over what a server has or undo what its operator did.
    """
    from noust.fleet.nodes import NodeManager
    from noust.web.fleet_jobs import FleetRequest
    from noust.web.fleet_settings_actions import ACTION

    if not body.plan and (body.adopt or body.restore or body.restore_all):
        # Applying what somebody already approved asks nothing more of whoever presses the
        # button; taking over what a server has, or undoing what its own operator did, is a
        # decision of theirs.
        ensure_elevated(request, session)
    names = list(body.nodes) or [record.name for record in NodeManager().list()]
    if not names:
        raise ValidationError(
            "There is no server to apply to",
            details="Add one first: noust node add.",
            field="nodes",
        )
    fleet_request = FleetRequest(
        ACTION,
        nodes=names,
        options={
            "adopt": body.adopt,
            "restore": list(body.restore),
            "restore_all": body.restore_all,
        },
    )
    return await _plan_and_start(request, session, fleet_request, body.plan)


@router.post(
    "/servers/{node}/settings/{resource}/{name}/restore", response_model=FleetSettingsAppliedOut
)
async def restore_setting(
    node: str, resource: str, name: str, session: Elevated
) -> FleetSettingsAppliedOut:
    """
    Put a setting back as the fleet's on a server whose operator changed it: "back to the fleet's".

    Args:
        node: The server.
        resource: The kind.
        name: The setting.
        session: The elevated session.

    Returns:
        The job that does it.

    Raises:
        ResourceNotFoundError: 404 when the server is not given that setting by the fleet.
    """
    kind_of(resource)
    settings = _settings()
    wanted = [s for s in settings.effective_for(node) if (s.resource, s.name) == (resource, name)]
    if not wanted:
        raise _missing(resource, name)
    applying = await _queue(session, [node], {"restore": [f"{resource}/{name}"]})
    if applying is None:
        raise _missing(resource, name)
    return applying
