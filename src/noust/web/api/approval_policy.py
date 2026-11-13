# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/config/approval-policy``: what a token and the built-in assistant may do alone (item 184).

The table is :class:`~noust.core.caller_policy.CallerPolicy`'s, and the guard of the API
(:func:`noust.web.api.approvals.require_approval`) is what applies it: nothing here decides a call. This
route reads the table, with what the release ships, what the operator chose and what the floor fixes for each
cell, so that a screen never offers what cannot be, and changes it.

Changing it is ``security.manage`` and sudo mode, and giving a token or the assistant more freedom than it
has waits for a second person where the server asks for them (a rule of
:mod:`noust.core.accounts.approvals`); taking freedom away does not, since it can only make the server
safer.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.audit import record as record_event
from noust.core.caller_policy import (
    ASSISTANT_ALONE_PERMISSIONS,
    ENVIRONMENTS,
    VALUES,
    CallerPolicy,
    Cell,
    Row,
)
from noust.core.callers import POLICED
from noust.core.operation_classes import CHANGE_PERMISSIONS, classify_schema
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.permissions.principal import audit_actor
from noust.web.pydantic_compat import bounded_list, dump_model

router = APIRouter(route_class=NoustErrorRoute)

#: Cells a body may name at once: the whole table is a hundred and sixty-eight.
MAX_CELLS = 400

_CLASSES_ATTRIBUTE = "noust_operation_classes"


class PolicyCellInfo(BaseModel):
    """
    One cell of the table: what happens to a call of this permission, from this caller, in this environment.

    Attributes:
        caller: ``token`` or ``assistant``.
        environment: ``production`` or ``other``.
        value: What judges a call now: ``alone``, ``propose`` or ``never``.
        default: What the release ships here (the security profile included).
        chosen: What the operator chose, or None when nobody did.
        offered: The values this cell may be given: a screen offers these and no other.
        fixed: Only one value is offered.
        reason: Why fewer than three are offered: ``assistant_floor`` (what the assistant never does: the
            padlock), ``confirm`` (the row asks you to confirm it is you: the key), ``assistant_narrow``
            (it is left alone only where it is narrow and reversible) or ``profile_floor`` (the security
            profile asks that a token's changes in production wait for a person); None when all three
            are.
        legacy: ``alone`` is what ships for a token here, as it always was, though the row does not offer
            it (the key): a token issued to act in sudo mode keeps doing what it did until a value that
            is offered is chosen. A screen shows it as what it is and offers only ``offered``.
    """

    caller: str
    environment: str
    value: str
    default: str
    chosen: str | None = None
    offered: list[str]
    fixed: bool
    reason: str | None = None
    legacy: bool = False


class PolicyOperations(BaseModel):
    """
    How many operations of the API a row governs.

    Attributes:
        total: Operations that change something with this permission.
        strong: Of those, the ones that ask for sudo mode or for a second person every time: the built-in
            assistant never does them, whatever its cell says, and a token does them only as its own sudo
            mode allows.
    """

    total: int
    strong: int


class PolicyRowInfo(BaseModel):
    """
    One permission that changes something and what the table says of it.

    Attributes:
        permission: The permission.
        area: The part before the dot, as the table of roles groups them.
        description: What it lets one do, in a sentence.
        key: Most of what it does asks you to confirm it is you (sudo mode) or a second person every time:
            ``alone`` is never offered in its cells. The key of the table.
        changed: Somebody chose some cell of the row (the filter of changed rows).
        operations: How many operations it governs.
        cells: The four cells: each class of caller in each environment.
    """

    permission: str
    area: str
    description: str
    key: bool
    changed: bool
    operations: PolicyOperations
    cells: list[PolicyCellInfo]


class PolicyFloor(BaseModel):
    """
    What no table lifts.

    Attributes:
        assistant_never: The permissions the built-in assistant never uses.
        assistant_alone: The only permissions its cell may be ``alone`` for.
    """

    assistant_never: list[str]
    assistant_alone: list[str]


class PolicyProfile(BaseModel):
    """
    What the security profile asks of the table.

    Attributes:
        token_production_approval: Whether what a token changes in production waits for a person (the
            control of that name, by the operator or by a framework such as ENS): ``propose`` is then
            the least a token's cell in production can be.
    """

    token_production_approval: bool


class ApprovalPolicyTable(BaseModel):
    """
    The approval policy for a caller that is not a person.

    Attributes:
        values: The three values, from the one that asks for nothing to the strictest.
        callers: The classes of caller the table has cells for.
        environments: ``production`` and ``other``.
        profile: What the security profile asks.
        floor: What no table lifts.
        rows: A row for each permission that changes something.
        chosen: How many cells the operator chose.
    """

    values: list[str]
    callers: list[str]
    environments: list[str]
    profile: PolicyProfile
    floor: PolicyFloor
    rows: list[PolicyRowInfo]
    chosen: int


class PolicyCellChange(BaseModel):
    """
    A cell to give a value.

    Attributes:
        caller: ``token`` or ``assistant``.
        environment: ``production`` or ``other``.
        permission: A permission that changes something.
        value: ``alone``, ``propose`` or ``never``, as the cell offers.
    """

    caller: str = Field(max_length=16)
    environment: str = Field(max_length=16)
    permission: str = Field(max_length=64)
    value: str = Field(max_length=16)


class PolicyCellRef(BaseModel):
    """
    A cell to put back to what the release ships.

    Attributes:
        caller: ``token`` or ``assistant``.
        environment: ``production`` or ``other``.
        permission: A permission that changes something.
    """

    caller: str = Field(max_length=16)
    environment: str = Field(max_length=16)
    permission: str = Field(max_length=64)


class PolicyChange(BaseModel):
    """
    A change of the table: some cells given a value, some put back. All or nothing.

    Attributes:
        cells: The cells to give a value.
        reset: The cells to put back to what the release ships.
    """

    cells: list[PolicyCellChange] = bounded_list(MAX_CELLS)
    reset: list[PolicyCellRef] = bounded_list(MAX_CELLS)


def _operations(request: Request) -> dict[str, tuple[int, int]]:
    """
    Count what each permission governs, from the API's own schema, once per application.

    Args:
        request: The request, for its application.

    Returns:
        Permission to ``(operations that change something, of which strong)``.
    """
    app = request.app
    schema = app.openapi()
    cached = getattr(app.state, _CLASSES_ATTRIBUTE, None)
    if cached is None or cached[0] is not schema:
        counts: dict[str, tuple[int, int]] = {}
        for found in classify_schema(schema).values():
            if found.changes and found.permission in CHANGE_PERMISSIONS:
                total, strong = counts.get(found.permission, (0, 0))
                counts[found.permission] = (total + 1, strong + int(found.strong))
        cached = (schema, counts)
        setattr(app.state, _CLASSES_ATTRIBUTE, cached)
    counted: dict[str, tuple[int, int]] = cached[1]
    return counted


def _cell(cell: Cell) -> PolicyCellInfo:
    """
    Args:
        cell: A cell of the policy.

    Returns:
        How the API shows it.
    """
    return PolicyCellInfo(
        caller=cell.caller,
        environment=cell.environment,
        value=cell.value,
        default=cell.default,
        chosen=cell.chosen,
        offered=list(cell.offered),
        fixed=cell.fixed,
        reason=cell.reason,
        legacy=cell.legacy,
    )


def _row(row: Row, counts: dict[str, tuple[int, int]]) -> PolicyRowInfo:
    """
    Args:
        row: A row of the policy.
        counts: What each permission governs.

    Returns:
        How the API shows it.
    """
    total, strong = counts.get(row.permission, (0, 0))
    return PolicyRowInfo(
        permission=row.permission,
        area=row.area,
        description=row.description,
        key=row.key,
        changed=any(cell.chosen is not None for cell in row.cells),
        operations=PolicyOperations(total=total, strong=strong),
        cells=[_cell(cell) for cell in row.cells],
    )


def table(request: Request, policy: CallerPolicy | None = None) -> ApprovalPolicyTable:
    """
    Read the table as it is now.

    Args:
        request: The request, for the schema the operations are counted from.
        policy: The policy to read; the server's by default.

    Returns:
        The whole table, with what the release ships and what the floor fixes.
    """
    found = policy or CallerPolicy()
    rows = found.rows()
    counts = _operations(request)
    return ApprovalPolicyTable(
        values=list(VALUES),
        callers=list(POLICED),
        environments=list(ENVIRONMENTS),
        profile=PolicyProfile(token_production_approval=found.floor_on),
        floor=PolicyFloor(
            assistant_never=sorted(ASSISTANT_FLOOR & CHANGE_PERMISSIONS),
            assistant_alone=sorted(ASSISTANT_ALONE_PERMISSIONS),
        ),
        rows=[_row(row, counts) for row in rows],
        chosen=sum(1 for row in rows for cell in row.cells if cell.chosen is not None),
    )


@router.get("", response_model=ApprovalPolicyTable)
def get_approval_policy(
    request: Request, session: dict[str, Any] = Depends(get_current_session)
) -> ApprovalPolicyTable:
    """
    Read what a token and the built-in assistant may do alone, propose or never.

    Args:
        request: The request.
        session: The authenticated session.

    Returns:
        The table: a row for each permission that changes something, each with its four cells, and for
        each cell what judges a call, what the release ships, what the operator chose and which values it
        may be given.
    """
    return table(request)


@router.put("", response_model=ApprovalPolicyTable)
def put_approval_policy(
    body: PolicyChange,
    request: Request,
    session: dict[str, Any] = Depends(require_elevated),
) -> ApprovalPolicyTable:
    """
    Change the table: give some cells a value and put others back, all or nothing.

    A change that gives a token or the assistant more freedom than it has waits for a second person where
    the server asks for them (answered 202 ``approval_required``); one that only takes it away does not.

    Args:
        body: The cells to give a value and the cells to put back.
        request: The request.
        session: The authenticated session, in sudo mode.

    Returns:
        The table as it is now.

    Raises:
        ValidationError: 400 for a cell that does not exist or a value the cell may not have, naming
            what it may.
    """
    policy = CallerPolicy()
    actor = audit_actor(session)
    who = str(actor.name or actor.id or actor.kind)
    resets = [(c.caller, c.environment, c.permission) for c in body.reset]
    for caller, environment, permission in resets:
        policy.cell(caller, environment, permission)
    changes = [(c.caller, c.environment, c.permission, c.value) for c in body.cells]
    written = policy.set_cells(changes, by=who) if changes else []
    removed = policy.reset_cells(resets) if resets else 0
    record_event(
        "approval.policy",
        actor=actor,
        target="approval-policy",
        details={
            "cells": [
                {
                    "caller": c.caller,
                    "environment": c.environment,
                    "permission": c.permission,
                    "value": c.value,
                }
                for c in written
            ],
            "reset": removed,
        },
    )
    return table(request, policy)


# ------------------------------------------------------------------ what saving would change


class PreviewChange(BaseModel):
    """
    A cell whose value would change.

    Attributes:
        caller: ``token`` or ``assistant``.
        environment: ``production`` or ``other``.
        permission: The permission.
        before: What judges a call now.
        after: What would judge it.
        loosens: It gives more freedom than it does now (a second person is asked for where the server
            asks for them).
        reset: The change puts the cell back to what the release ships.
    """

    caller: str
    environment: str
    permission: str
    before: str
    after: str
    loosens: bool
    reset: bool = False


class PreviewEffect(BaseModel):
    """
    What a credential would find different.

    Attributes:
        permission: The permission.
        environment: ``production`` or ``other``.
        before: What it is judged by now.
        after: What it would be judged by.
    """

    permission: str
    environment: str
    before: str
    after: str


class PreviewCredential(BaseModel):
    """
    A credential whose behaviour changes.

    Attributes:
        kind: ``token`` or ``assistant`` (the built-in assistant, whatever number of tokens it uses).
        id: The token's record; None for the assistant.
        name: The token's name; None for the assistant.
        owner: The account the token acts for.
        effects: What changes for it.
    """

    kind: str
    id: int | None = None
    name: str | None = None
    owner: str | None = None
    effects: list[PreviewEffect]


class PolicyPreview(BaseModel):
    """
    What saving a change would do, before it is saved.

    Attributes:
        changes: The cells whose value would change, in the order they were named.
        commands: The commands that make the same change, in order: what the preview says it will do.
        credentials: The credentials that would behave differently: the tokens that hold a permission of a
            cell that changes, and the assistant when one of its cells does. Nothing is said of what they
            will ask tomorrow: only that they would be judged differently.
        waiting: How many requests already wait for a person; they do not change.
        needs_second_person: Saving gives more freedom than the policy has now, and this server asks
            another person to approve that.
    """

    changes: list[PreviewChange]
    commands: list[str]
    credentials: list[PreviewCredential]
    waiting: int
    needs_second_person: bool


def _holders(permission: str) -> list[dict[str, Any]]:
    """
    Args:
        permission: A permission.

    Returns:
        The live tokens that hold it and are judged as tokens: not revoked, not expired, not a central's
        and not the assistant's.
    """
    import time

    from noust.web.permissions.roles import permissions_for_scope
    from noust.web.server import get_token_manager

    now = time.time()
    found = []
    for record in get_token_manager().list_api_tokens(None):
        if record.get("revoked_at") is not None or record.get("scope") == "fleet":
            continue
        if record.get("caller") == "assistant":
            continue
        expires = record.get("expires_at")
        if expires is not None and float(expires) <= now:
            continue
        held = record.get("permissions")
        if permission in (held if held is not None else permissions_for_scope(record.get("scope"))):
            found.append(record)
    return found


def _owner_of(record: dict[str, Any]) -> str | None:
    """
    Args:
        record: A token's record.

    Returns:
        The username of the account it acts for.
    """
    from noust.web.server import get_token_manager

    if record.get("owner_account_id") is None:
        return None
    account = get_token_manager().accounts.get(int(record["owner_account_id"]))
    return account.username if account is not None else None


@router.post("/preview", response_model=PolicyPreview)
def preview_approval_policy(
    body: PolicyChange, session: dict[str, Any] = Depends(get_current_session)
) -> PolicyPreview:
    """
    Say what saving a change would do, and to which credentials, without saving it.

    It is the simulation of ``PUT``: the same validation (a cell that does not exist or a value it may not
    have is a 400 naming what it may be), and nothing written.

    Args:
        body: The same body ``PUT`` takes.
        session: The authenticated session.

    Returns:
        The cells that would change, the commands that make the change, the credentials that would behave
        differently, how many requests already wait, and whether saving asks for a second person.

    Raises:
        ValidationError: 400 as ``PUT`` would.
    """
    from noust.core.accounts.approvals import ApprovalManager
    from noust.core.accounts.policy_requests import loosens_policy

    policy = CallerPolicy()
    changes = [(c.caller, c.environment, c.permission, c.value) for c in body.cells]
    resets = [(c.caller, c.environment, c.permission) for c in body.reset]
    planned = [item for item in policy.plan(changes, resets) if item.changes]
    effects: dict[tuple[str, int | None], PreviewCredential] = {}
    commands = []
    for item in planned:
        cell = item.cell
        commands.append(
            f"noust approvals policy reset {cell.permission} {cell.caller} {cell.environment}"
            if item.reset
            else f"noust approvals policy set {cell.permission} {cell.caller} "
            f"{cell.environment} {item.value}"
        )
        effect = PreviewEffect(
            permission=cell.permission,
            environment=cell.environment,
            before=cell.value,
            after=item.value,
        )
        if cell.caller == "assistant":
            effects.setdefault(("assistant", None), PreviewCredential(kind="assistant", effects=[]))
            effects[("assistant", None)].effects.append(effect)
            continue
        for record in _holders(cell.permission):
            key = ("token", int(record["id"]))
            if key not in effects:
                effects[key] = PreviewCredential(
                    kind="token",
                    id=int(record["id"]),
                    name=str(record["name"]),
                    owner=_owner_of(record),
                    effects=[],
                )
            effects[key].effects.append(effect)
    manager = ApprovalManager()
    waiting = [r for r in manager.list(state="requested") if r.origin in ("policy", "both")]
    return PolicyPreview(
        changes=[
            PreviewChange(
                caller=i.cell.caller,
                environment=i.cell.environment,
                permission=i.cell.permission,
                before=i.cell.value,
                after=i.value,
                loosens=i.loosens,
                reset=i.reset,
            )
            for i in planned
        ],
        commands=commands,
        credentials=list(effects.values()),
        waiting=len(waiting),
        needs_second_person=bool(planned)
        and manager.policy().enabled
        and loosens_policy(dump_model(body), {}),
    )
