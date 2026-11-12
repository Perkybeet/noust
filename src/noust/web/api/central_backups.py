# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/central-backups``: the copies this central holds of its servers (item 195).

A central comes for the copies of the servers it manages, through the tunnel it already has
(:mod:`noust.fleet.collect`), and keeps them in its own backup directory. This is how a person sees
them, turns collecting on for the fleet or for one server, asks for a pass now, lets a copy go and
gives one back (to its server, or to another of the fleet):

- ``GET /api/central-backups`` lists the copies held, and ``/status`` says how collecting is going
  for each server: whether it is followed (the fleet's answer, or its own), how its last pass ended,
  what is held, what is moving and, for the instance, the knobs and the disk. A person of a team
  sees the servers assigned to their teams and what is held of them, never the central's disk.
- ``PUT /settings`` and ``PUT /nodes/{node}`` change who is followed and how (the instance's, with
  sudo mode), ``POST /collect`` asks for a pass now.
- ``DELETE /{node}/{kind}?item=`` lets a copy go; ``POST /{node}/{kind}/restore`` gives it back as a
  job. A copy is named by its server and its kind in the path and by the identifier the server gave
  it in the query or the body, because that identifier is the server's and may be anything printable.

Nothing here reads or writes a file of a copy: that is the collector's and the restore's. A copy is
never offered for download to a browser: it is every application's data.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.core.store import get_store
from noust.core.teams import scoped_store
from noust.core.teams.model import ResourceNotFoundError
from noust.fleet.collect.collector import NodeCollector
from noust.fleet.collect.layout import Layout
from noust.fleet.collect.records import HELD, CollectedStore
from noust.fleet.collect.restore import GiveBack, RestoreError
from noust.fleet.collect.settings import KEYS, Follows, SettingsStore
from noust.fleet.collect.status import status_of
from noust.fleet.collect.throttle import Throttle
from noust.fleet.collect.worker import get_worker
from noust.managers.backup_catalogue import KINDS
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.api.fleet import asker_for
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager
from noust.web.pydantic_compat import dump_set_fields

router = APIRouter(route_class=NoustErrorRoute)


class CollectedFileOut(BaseModel):
    """One file of a copy the central holds."""

    name: str
    size: int
    sha256: str
    role: str


class CollectedOut(BaseModel):
    """A copy the central holds of a server."""

    node: str
    kind: str
    id: str = Field(description="The server's identifier of it")
    owner: str = Field(description="What it is a copy of")
    origin: str = Field(description="`scheduled`, `manual` or `safety`")
    created_at: str = Field(description="When the server took it")
    collected_at: str = Field(description="When the central finished taking it")
    size: int
    files: list[CollectedFileOut]
    verified: str = Field(description="`digest` (matched the server's) or `size`")
    state: str
    meta: dict[str, str] = Field(default_factory=dict)


class CollectedListOut(BaseModel):
    """The copies the central holds."""

    items: list[CollectedOut]
    total: int


class FollowingOut(BaseModel):
    """How many servers are collected from."""

    following: int
    of: int
    exceptions: list[str] = Field(default_factory=list)


class NodeStatusOut(BaseModel):
    """How collecting is going with one server."""

    node: str
    follow: bool | None = Field(description="Its own answer; none follows the fleet's")
    following: bool = Field(description="Whether it is collected from")
    last_pass_at: str | None = None
    last_ok_at: str | None = None
    last_error: str | None = None
    last_error_code: str | None = None
    failures: int = 0
    support: dict[str, Any] = Field(
        default_factory=dict, description="What it can be collected for"
    )
    held: dict[str, int] = Field(description="`count` and `bytes` the central holds of it")
    newest: str | None = None
    moving: dict[str, Any] | None = Field(default=None, description="What is being taken now")


class StatusOut(BaseModel):
    """How collecting is going."""

    following: FollowingOut
    nodes: list[NodeStatusOut]
    settings: dict[str, Any] | None = Field(default=None, description="The knobs; the instance's")
    space: dict[str, Any] | None = Field(default=None, description="The disk; the instance's")
    directory: str | None = Field(default=None, description="Where the copies are; the instance's")


class SettingsIn(BaseModel):
    """Change how collecting works. A knob not named is left as it is."""

    enabled: bool | None = Field(default=None, description="Collect the fleet")
    kinds: list[str] | None = Field(default=None, description="Which kinds of copy are taken")
    rate_limit_kbps: int | None = Field(default=None, description="Kilobytes a second; 0 for none")
    poll_seconds: int | None = Field(default=None, description="How often each server is looked at")
    pass_hours: int | None = Field(default=None, description="Hours between whole lists")
    retention_count: int | None = Field(
        default=None, description="Scheduled copies kept; 0 for no limit"
    )
    retention_days: int | None = Field(
        default=None, description="Days a scheduled copy is kept; 0 for no limit"
    )
    retention_gb: float | None = Field(
        default=None, description="Gigabytes kept per application; 0 for no limit"
    )
    min_free_gb: float | None = Field(default=None, description="Room always left on the disk")


class NodeFollowIn(BaseModel):
    """A server's own answer to being collected from."""

    follow: bool | None = Field(description="True or false for its own; null to follow the fleet")


class CollectIn(BaseModel):
    """Ask for a pass now."""

    node: str | None = Field(default=None, description="Only this server; every followed one")
    force: bool = Field(default=True, description="Read the whole list, not only what changed")


class GiveBackIn(BaseModel):
    """Give a copy back."""

    item: str = Field(..., description="The server's identifier of the copy")
    target_node: str | None = Field(
        default=None, description="The server it goes to; the one it came from when omitted"
    )
    target: dict[str, Any] = Field(
        default_factory=dict,
        description="Where it goes on that server: `domain`, or `engine`, `database`, `new_name`",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="`restore_env`, `safety_backup`, `drop_existing`, `schema_changed_ok`",
    )


def _visible() -> set[str] | None:
    """
    Returns:
        The servers the caller sees; None when it sees every one.
    """
    scoped = scoped_store()
    if scoped.scope.sees_all:
        return None
    return {node.name for node in scoped.list_nodes()}


def _sees_all() -> bool:
    """Returns: Whether the caller sees every team: the instance's own."""
    return bool(scoped_store().scope.sees_all)


def _kind(kind: str) -> str:
    """
    Args:
        kind: A kind of copy from a path.

    Returns:
        The kind.

    Raises:
        ValidationError: When it is not one.
    """
    if kind not in KINDS:
        raise ValidationError(
            f"Unknown kind of copy: {kind!r}",
            details=f"The kinds are: {', '.join(KINDS)}.",
            field="kind",
        )
    return kind


def _copy(node: str, kind: str, item: str) -> Any:
    """
    Find a copy the central holds.

    Args:
        node: The server.
        kind: The kind of copy.
        item: The server's identifier of it.

    Returns:
        The copy.

    Raises:
        ResourceNotFoundError: The central holds no such copy (a 404, as one that is not there).
    """
    found = CollectedStore().get(node, _kind(kind), item)
    if found is None or found.state != HELD:
        raise ResourceNotFoundError(f"The central holds no copy {item!r} of {node}")
    return found


@router.get("", response_model=CollectedListOut)
def list_copies(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    node: Annotated[str | None, Query(description="Only this server's")] = None,
    kind: Annotated[str | None, Query(description="Only this kind")] = None,
    owner: Annotated[str | None, Query(description="Only copies of this")] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> CollectedListOut:
    """
    List the copies the central holds.

    A person of a team sees the copies of the servers assigned to their teams, and a server that is
    not one of those is as if it had none.

    Args:
        session: The authenticated session.
        node: Only this server's.
        kind: Only this kind.
        owner: Only copies of this application, database or ``noust``.
        limit: The most to return.

    Returns:
        The copies, newest first.
    """
    visible = _visible()
    if node is not None and visible is not None and node not in visible:
        return CollectedListOut(items=[], total=0)
    copies = CollectedStore().list(node=node, nodes=visible, kind=_kind(kind) if kind else None)
    if owner:
        copies = [c for c in copies if c.owner == owner]
    return CollectedListOut(
        items=[CollectedOut(**c.to_dict()) for c in copies[:limit]], total=len(copies)
    )


@router.get("/status", response_model=StatusOut)
def collecting_status(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> StatusOut:
    """
    Say how collecting is going.

    Args:
        session: The authenticated session.

    Returns:
        Who is followed (``N of M``), how each server is doing and what is moving; for the instance,
        also the knobs and the disk.
    """
    return StatusOut(**status_of(visible=_visible(), instance=_sees_all()))


@router.put("/settings", response_model=StatusOut)
def change_settings(
    body: SettingsIn, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> StatusOut:
    """
    Change how collecting works.

    A limit given as 0 is taken off. Turning ``enabled`` on is what makes the central come for the
    copies of every server that has no answer of its own, so it asks for sudo mode, and the audit
    trail records who did.

    Args:
        body: The knobs to change.
        session: The authenticated, elevated session.

    Returns:
        How collecting is going now.

    Raises:
        ValidationError: A knob is not right; nothing was changed.
    """
    changes = {key: value for key, value in dump_set_fields(body).items() if key in KEYS}
    for key in ("rate_limit_kbps", "retention_count", "retention_days", "retention_gb"):
        if changes.get(key) == 0:
            changes[key] = None
    SettingsStore().set(**changes)
    audit.record(
        "central_backup.settings",
        actor=session_actor(session),
        target="central",
        details={key: changes[key] for key in changes},
    )
    get_worker().wake()
    return StatusOut(**status_of(visible=None, instance=True))


@router.put("/nodes/{node}", response_model=StatusOut)
def follow_node(
    node: str, body: NodeFollowIn, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> StatusOut:
    """
    Give a server its own answer to being collected from, or take it back to the fleet's.

    Args:
        node: The server.
        body: ``true`` or ``false`` for its own; ``null`` to follow the fleet.
        session: The authenticated, elevated session.

    Returns:
        How collecting is going now.

    Raises:
        ValidationError: The server is not one this central manages.
    """
    Follows().set_follow(node, body.follow)
    audit.record(
        "central_backup.settings",
        actor=session_actor(session),
        target=f"node:{node}",
        details={"follow": body.follow},
    )
    get_worker().wake(node)
    return StatusOut(**status_of(visible=None, instance=True))


@router.post("/collect", response_model=StatusOut, status_code=202)
def collect_now(
    body: CollectIn, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> StatusOut:
    """
    Ask for a pass now: what is followed is looked at at once, instead of at its turn.

    The pass runs in the background: what it takes shows in the status while it does.

    Args:
        body: Which server, or every followed one.
        session: The authenticated session.

    Returns:
        How collecting is going.

    Raises:
        ValidationError: The server is not one this central manages.
    """
    if body.node is not None and (
        not scoped_store().sees_node(body.node) or get_store().get_node(body.node) is None
    ):
        raise ValidationError(
            f"No server named {body.node} is managed by this central", field="node"
        )
    get_worker().wake(body.node, force=body.force)
    return StatusOut(**status_of(visible=_visible(), instance=_sees_all()))


@router.delete("/{node}/{kind}", response_model=CollectedListOut)
def delete_copy(
    node: str,
    kind: str,
    item: Annotated[str, Query(description="The server's identifier of the copy")],
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> CollectedListOut:
    """
    Let a copy go: its files are deleted from the central, and the server is not asked for it again.

    Args:
        node: The server it is a copy of.
        kind: The kind of copy.
        item: The server's identifier of it.
        session: The authenticated, elevated session.

    Returns:
        What the central holds of that server now.

    Raises:
        ResourceNotFoundError: The central holds no such copy.
    """
    copy = _copy(node, kind, item)
    NodeCollector().let_go(copy, reason="operator")
    audit.record(
        "central_backup.delete",
        actor=session_actor(session),
        target=f"node:{node}",
        details={
            "kind": copy.kind,
            "copy": copy.item_id,
            "owner": copy.owner,
            "reason": "operator",
        },
    )
    held = CollectedStore().list(node=node)
    return CollectedListOut(items=[CollectedOut(**c.to_dict()) for c in held], total=len(held))


def give_back_job(
    node: str,
    kind: str,
    item: str,
    target_node: str,
    target: dict[str, Any],
    options: dict[str, Any],
    asker: dict[str, Any],
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Give a copy back, as a job.

    Args:
        node: The server the copy is of.
        kind: The kind of copy.
        item: The server's identifier of it.
        target_node: The server it goes to.
        target: Where it goes on that server.
        options: What the operator confirmed.
        asker: Who asked, as the central says it to the server.
        job_context: Injected by the job manager.

    Returns:
        What the server's restore did.

    Raises:
        RestoreError: The copy is damaged, the server cannot receive it, or the restore failed.
    """
    from noust.fleet.aggregate import Asker

    copy = _copy(node, kind, item)
    settings = SettingsStore().get()

    def step(what: str, percent: int) -> None:
        if job_context is not None:
            job_context.update(what, percent)

    return GiveBack(
        copy=copy,
        target=target_node,
        layout=Layout.default(),
        asker=Asker(**asker),
        throttle=Throttle(settings.rate_bytes_per_second),
        log=(lambda message: job_context.log(message))
        if job_context is not None
        else (lambda _m: None),
        step=step,
    ).run(target_args=target, options=options)


@router.post("/{node}/{kind}/restore", response_model=JobAcceptedResponse, status_code=202)
def restore_copy(
    node: str,
    kind: str,
    body: GiveBackIn,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Give a copy back to a server and restore it there, as a job.

    To the server it came from, or to another of the fleet. The copy is checked whole on the central
    before a byte leaves, the file travels as an upload that resumes, and the server restores it with
    the manager of its kind, as the person who asked. Moving a copy between two servers passes it
    through this central's line; that is why it is asked for and never done by itself.

    Args:
        node: The server the copy is of.
        kind: The kind of copy.
        body: Which copy, where it goes and what is confirmed.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ResourceNotFoundError: The central holds no such copy, or the target is not a server the
            caller sees.
    """
    copy = _copy(node, kind, body.item)
    target_node = body.target_node or node
    if not scoped_store().sees_node(target_node) or get_store().get_node(target_node) is None:
        raise ResourceNotFoundError(f"No server named {target_node} is managed by this central")
    audit.record(
        "central_backup.restore",
        actor=session_actor(session),
        target=f"node:{target_node}",
        details={"from": node, "kind": copy.kind, "copy": copy.item_id, "owner": copy.owner},
    )
    job = get_job_manager().create_job(
        job_type=JobType.RESTORE,
        name=f"Give a {copy.kind} copy of {copy.owner} back to {target_node}",
        description=f"Giving the central's copy of {copy.owner} (from {node}) back to {target_node}",
        func=give_back_job,
        kwargs={
            "node": node,
            "kind": copy.kind,
            "item": copy.item_id,
            "target_node": target_node,
            "target": body.target,
            "options": body.options,
            "asker": asdict(asker_for(session)),
        },
        metadata={"node": target_node, "backup_id": copy.item_id, "from_central": True},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Giving the copy back to {target_node} queued",
        job=job.to_dict(),
    )


__all__ = ["RestoreError", "give_back_job", "router"]
