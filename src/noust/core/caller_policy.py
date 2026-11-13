# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a token and the built-in assistant may do without a person, in production and elsewhere (item 184).

A person is held to the rules of the roles and of the four-eyes approval. A caller that is not a person
(:mod:`noust.core.callers`) is also held to this table: for each permission that changes something, in
production and in every other environment, one of three values.

- ``alone``: it does it, as before.
- ``propose``: the call waits as a request for a person to approve in the console, with the flow that
  already exists for a second person (:mod:`noust.core.accounts.approvals`); once approved, the same call,
  made again, runs once.
- ``never``: the call is refused with the sentence that says which policy forbids it and who changes it.

**What the release ships.** A token stays as it is today: ``alone`` on everything its permissions let it
do, so a pipeline that deployed yesterday deploys tomorrow. The built-in assistant proposes everything that
changes something, in both environments. A test walks the whole catalogue and holds those values.

**The floor, which no table lifts.** What the assistant never does (:data:`~noust.core.assistant_floor.ASSISTANT_FLOOR`: the secrets, the
terminals and the machine, the accounts, the audit, the configuration and the policy itself) is ``never`` and fixed. What asks for sudo mode or for a second person
every time (:class:`~noust.core.operation_classes.OperationClass` ``strong``) is never done by the
assistant, whatever a cell says: it prepares the call and a person makes it, in their own session. For a
token the sudo mode it was issued with (``allow_elevated``) is still what lets it pass a route that asks
for it: ``alone`` here means the table asks for nothing more, never that sudo mode is not needed.

**The profile.** The control ``token_production_approval`` (ENS asks for it) makes ``propose`` the least a
token's cell in production can be: it is the factory value there, and an operator can ask for more
(``never``) but not for less. A choice made before is kept and applies again when the profile goes away.

**The environment** is the resource's, read from the store with the scope of whoever asks
(:func:`environment_of`), never from a parameter a caller sends; what has none, or is not seen, is
production, the prudent one.

Nothing here knows HTTP: the guard of the API (:mod:`noust.web.api.caller_gate`), the MCP and the CLI all
ask the same :class:`CallerPolicy`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.callers import ASSISTANT, POLICED
from noust.core.exceptions import ValidationError
from noust.core.operation_classes import (
    CHANGE_PERMISSIONS,
    OWN,
    PUBLIC_CLASS,
    SAFE_METHODS,
    STRONG,
    SUDO_ROWS,
    OperationClass,
)
from noust.core.permission_catalog import DESCRIPTIONS, Permission
from noust.core.teams.model import PRODUCTION_SLUG
from noust.core.timeutil import utc_now_iso

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: The three values of a cell, from the one that asks for nothing to the strictest.
ALONE = "alone"
PROPOSE = "propose"
NEVER = "never"
VALUES: tuple[str, ...] = (ALONE, PROPOSE, NEVER)

#: Production (the environment ``production`` of a project) and every other environment.
PRODUCTION = "production"
OTHER = "other"
ENVIRONMENTS: tuple[str, ...] = (PRODUCTION, OTHER)

P = Permission

#: Where the assistant may be left to act without a person: narrow and reversible (restart, renew a
#: certificate, take a backup, deploy what a preview showed). Everywhere else its cell is ``propose`` or
#: ``never``.
ASSISTANT_ALONE_PERMISSIONS: frozenset[str] = frozenset(
    {P.APPS_OPERATE, P.APPS_DEPLOY, P.BACKUPS_RUN}
)

#: Why a cell offers fewer values than the three.
REASON_ASSISTANT_FLOOR = "assistant_floor"
REASON_ASSISTANT_NARROW = "assistant_narrow"
REASON_PROFILE_FLOOR = "profile_floor"
#: The row is one where most of what the permission does asks you to confirm it is you (sudo mode) or a
#: second person: ``alone`` is not offered there (:data:`~noust.core.operation_classes.SUDO_ROWS`).
REASON_CONFIRM = "confirm"

#: Why a call was judged as it was, beside ``table``.
VERDICT_PERSON = "person"
VERDICT_NOT_A_CHANGE = "not_a_change"
VERDICT_TABLE = "table"
VERDICT_ASSISTANT_FLOOR = "assistant_floor"
VERDICT_ASSISTANT_STRONG = "assistant_strong"
VERDICT_ASSISTANT_OWN = "assistant_own"

#: The verdicts that are the assistant's floor and not a cell of the table: no choice of the operator
#: changes them.
ASSISTANT_NEVER_REASONS: frozenset[str] = frozenset(
    {VERDICT_ASSISTANT_FLOOR, VERDICT_ASSISTANT_STRONG, VERDICT_ASSISTANT_OWN}
)

_RANK = {value: rank for rank, value in enumerate(VALUES)}


def strictest(*values: str) -> str:
    """
    Args:
        values: Values of a cell.

    Returns:
        The strictest of them (``never`` over ``propose`` over ``alone``).
    """
    return max(values, key=lambda value: _RANK[value])


def area_of(permission: str) -> str:
    """
    Args:
        permission: A permission.

    Returns:
        The part before the dot, as the table of roles groups them; a permission with no dot is its own
        area.
    """
    return permission.partition(".")[0]


@dataclass(frozen=True)
class Cell:
    """
    One cell of the table.

    Attributes:
        caller: ``token`` or ``assistant``.
        environment: ``production`` or ``other``.
        permission: A permission that changes something.
        value: What judges a call now: what the operator chose, raised to the floor.
        default: What the release ships here, the profile included.
        chosen: What the operator chose, or None when nobody did.
        offered: The values this cell may be given, from the least strict.
        fixed: Only one value is offered.
        reason: Why fewer than three are offered (``assistant_floor``, ``confirm``,
            ``assistant_narrow``, ``profile_floor``), or None.
        legacy: ``alone`` is what the release ships for a token here, as it always was, though a key row
            does not offer it: a token issued to act in sudo mode keeps doing what it did until somebody
            chooses a value that is offered.
    """

    caller: str
    environment: str
    permission: str
    value: str
    default: str
    chosen: str | None
    offered: tuple[str, ...]
    fixed: bool
    reason: str | None
    legacy: bool = False


@dataclass(frozen=True)
class Planned:
    """
    What a change would do to one cell.

    Attributes:
        cell: The cell as it is now.
        value: What the cell would judge with after the change.
        reset: The change puts it back to what the release ships.
    """

    cell: Cell
    value: str
    reset: bool

    @property
    def changes(self) -> bool:
        """Whether what a call is judged by changes."""
        return self.value != self.cell.value

    @property
    def loosens(self) -> bool:
        """Whether the cell gives more freedom than it does now."""
        return _RANK[self.value] < _RANK[self.cell.value]


@dataclass(frozen=True)
class Row:
    """
    What the table says of one permission.

    Attributes:
        permission: The permission.
        area: The part before the dot.
        description: What the permission lets one do, in a sentence.
        key: Most of what it does asks for sudo mode or a second person every time: ``alone`` is not
            offered in its cells.
        cells: One for each class of caller and environment.
    """

    permission: str
    area: str
    description: str
    key: bool
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class Verdict:
    """
    How the policy judges one call.

    Attributes:
        value: ``alone``, ``propose`` or ``never``.
        reason: ``person``, ``not_a_change``, ``table`` or one of the floors of the assistant.
        caller: The class of caller.
        environment: The environment judged.
        permission: The permission whose cell decided, when a cell did.
    """

    value: str
    reason: str
    caller: str
    environment: str
    permission: str | None = None


def environment_of(domain: str | None) -> str:
    """
    Say which environment the resource a call names is in.

    Read from the store with the scope of whoever is asking, as every other read of an application is:
    a resource that is not found, that the caller does not see or that has no environment is judged as
    production. There is no argument that carries an environment; a caller cannot say where it is.

    Args:
        domain: The application's domain (or an alias or a site's), when the call names one.

    Returns:
        ``production`` or ``other``.
    """
    if not domain:
        return PRODUCTION
    from noust.core.teams import TeamStore, scoped_store

    scoped = scoped_store()
    placement = scoped.placement_of_domain(domain.strip().lower())
    if placement is None or not placement.environment_id:
        return PRODUCTION
    environment = TeamStore(scoped.store).environment_by_id(placement.environment_id)
    if environment is None:
        return PRODUCTION
    return PRODUCTION if environment.slug == PRODUCTION_SLUG else OTHER


def environment_of_target(environment_id: int | None) -> str:
    """
    Say which environment a placement is in, for a resource that is not an application.

    Args:
        environment_id: The environment's id, as the store keeps it; 0 or None for none.

    Returns:
        ``production`` or ``other``; production for none.
    """
    if not environment_id:
        return PRODUCTION
    from noust.core.teams import TeamStore, scoped_store

    environment = TeamStore(scoped_store().store).environment_by_id(environment_id)
    if environment is None:
        return PRODUCTION
    return PRODUCTION if environment.slug == PRODUCTION_SLUG else OTHER


def _normalised(environment: str) -> str:
    """
    Args:
        environment: Whatever a caller of this module passed.

    Returns:
        The environment, and production for anything that is not one: the one that is judged.
    """
    return environment if environment in ENVIRONMENTS else PRODUCTION


def _profile_asks() -> bool:
    """
    Returns:
        Whether the control ``token_production_approval`` is on here, by the operator or by a framework.
    """
    from noust.core.compliance import resolve

    return resolve.is_on("token_production_approval")


class CallerPolicy:
    """
    Read, change and apply the table.

    Args:
        store: The store; the process-wide one by default.
        production_floor: Whether the profile makes ``propose`` the least a token's cell in production
            can be; read from the control ``token_production_approval`` on every use when None.
    """

    def __init__(
        self, store: NoustStore | None = None, *, production_floor: bool | None = None
    ) -> None:
        self._store = store
        self._production_floor = production_floor

    @property
    def store(self) -> NoustStore:
        """The store the choices live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    @property
    def floor_on(self) -> bool:
        """Whether the profile's floor for a token in production applies now."""
        return self._floor_on()

    def _floor_on(self) -> bool:
        """
        Returns:
            Whether the profile's floor for a token in production applies now.
        """
        return self._production_floor if self._production_floor is not None else _profile_asks()

    # ----------------------------------------------------------------- the cells

    def _chosen(self) -> dict[tuple[str, str, str], str]:
        """
        Returns:
            What the operator chose: ``(caller, environment, permission)`` to the value.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT caller, environment, permission, value FROM caller_policy")
            return {(str(r[0]), str(r[1]), str(r[2])): str(r[3]) for r in cursor.fetchall()}

    def _make(
        self,
        caller: str,
        environment: str,
        permission: str,
        chosen: str | None,
        *,
        floor_on: bool,
    ) -> Cell:
        """
        Args:
            caller: ``token`` or ``assistant``.
            environment: ``production`` or ``other``.
            permission: A permission that changes something.
            chosen: What the operator chose, or None.
            floor_on: Whether the profile's floor applies.

        Returns:
            The cell.
        """
        reason: str | None = None
        key = permission in SUDO_ROWS
        if caller == ASSISTANT:
            if permission in ASSISTANT_FLOOR:
                offered: tuple[str, ...] = (NEVER,)
                default = NEVER
                reason = REASON_ASSISTANT_FLOOR
            elif permission in ASSISTANT_ALONE_PERMISSIONS and not key:
                offered = VALUES
                default = PROPOSE
            else:
                offered = (PROPOSE, NEVER)
                default = PROPOSE
                reason = REASON_CONFIRM if key else REASON_ASSISTANT_NARROW
        elif environment == PRODUCTION and floor_on:
            offered = (PROPOSE, NEVER)
            default = PROPOSE
            reason = REASON_PROFILE_FLOOR
        else:
            offered = VALUES
            default = ALONE
        legacy = False
        if key and caller != ASSISTANT and ALONE in offered:
            # Not offered, because a key row asks you to confirm it is you; but a token keeps doing what it
            # does today until a value that is offered is chosen: a token issued to act in sudo mode is a
            # decision somebody already made.
            offered = tuple(value for value in offered if value != ALONE)
            reason = REASON_CONFIRM
            legacy = default == ALONE
        # What was chosen below what is offered now (a profile that arrived after it) is kept and raised,
        # so the operator's own value is back when the floor goes; what nobody chose is what ships.
        value = default
        if chosen is not None:
            value = chosen if chosen in offered else strictest(chosen, offered[0])
        return Cell(
            caller=caller,
            environment=environment,
            permission=permission,
            value=value,
            default=default,
            chosen=chosen,
            offered=offered,
            fixed=len(offered) == 1,
            reason=reason,
            legacy=legacy and chosen is None,
        )

    def cell(self, caller: str, environment: str, permission: str) -> Cell:
        """
        Args:
            caller: ``token`` or ``assistant``.
            environment: ``production`` or ``other``.
            permission: A permission that changes something.

        Returns:
            The cell, as it is now.

        Raises:
            ValidationError: When it names a cell that does not exist.
        """
        _check_cell(caller, environment, permission)
        chosen = self._chosen().get((caller, environment, permission))
        return self._make(caller, environment, permission, chosen, floor_on=self._floor_on())

    def rows(self) -> list[Row]:
        """
        Returns:
            The whole table: a row for each permission that changes something, grouped by area as the
            table of roles groups them.
        """
        chosen = self._chosen()
        floor_on = self._floor_on()
        rows = []
        for permission in sorted(CHANGE_PERMISSIONS):
            cells = tuple(
                self._make(
                    caller,
                    environment,
                    permission,
                    chosen.get((caller, environment, permission)),
                    floor_on=floor_on,
                )
                for caller in POLICED
                for environment in ENVIRONMENTS
            )
            rows.append(
                Row(
                    permission,
                    area_of(permission),
                    DESCRIPTIONS[permission],
                    permission in SUDO_ROWS,
                    cells,
                )
            )
        return rows

    def choices(self) -> list[Cell]:
        """
        Returns:
            Every cell the operator chose, in the table's order.
        """
        return [cell for row in self.rows() for cell in row.cells if cell.chosen is not None]

    # --------------------------------------------------------------- the choices

    def _check_values(self, cells: Sequence[tuple[str, str, str, str]]) -> None:
        """
        Refuse a cell that does not exist or a value it may not have.

        Args:
            cells: ``(caller, environment, permission, value)`` for each.

        Raises:
            ValidationError: Naming what is offered.
        """
        floor_on = self._floor_on()
        for caller, environment, permission, value in cells:
            _check_cell(caller, environment, permission)
            if value not in VALUES:
                raise ValidationError(
                    f"{value!r} is not a value of the approval policy",
                    details=f"Use one of: {', '.join(VALUES)}.",
                    field="value",
                )
            offered = self._make(caller, environment, permission, None, floor_on=floor_on).offered
            if value not in offered:
                raise ValidationError(
                    f"The policy does not let {permission} be {value} for the {caller} class in "
                    f"{environment}: it can only be {' or '.join(offered)}",
                    details=_why_not(caller, permission, offered),
                    field="value",
                )

    def plan(
        self,
        cells: Sequence[tuple[str, str, str, str]],
        resets: Sequence[tuple[str, str, str]] = (),
    ) -> list[Planned]:
        """
        Say what a change would do, writing nothing.

        Args:
            cells: ``(caller, environment, permission, value)`` to give a value.
            resets: ``(caller, environment, permission)`` to put back to what the release ships.

        Returns:
            For each cell the change names, as it is now and what it would judge with after.

        Raises:
            ValidationError: As :meth:`set_cells` and :meth:`reset_cells` would.
        """
        self._check_values(cells)
        planned: list[Planned] = []
        for caller, environment, permission, value in cells:
            planned.append(Planned(self.cell(caller, environment, permission), value, False))
        floor_on = self._floor_on()
        for caller, environment, permission in resets:
            _check_cell(caller, environment, permission)
            shipped = self._make(caller, environment, permission, None, floor_on=floor_on)
            planned.append(Planned(self.cell(caller, environment, permission), shipped.value, True))
        return planned

    def set_cells(self, cells: Sequence[tuple[str, str, str, str]], *, by: str) -> list[Cell]:
        """
        Choose the value of cells, all or none.

        Args:
            cells: ``(caller, environment, permission, value)`` for each.
            by: Who chose, for the record.

        Returns:
            The cells as they are now.

        Raises:
            ValidationError: When a cell does not exist or a value is not one the cell may have, naming
                what is offered. Nothing is written then.
        """
        self._check_values(cells)
        moment = utc_now_iso("seconds")
        with self.store._transaction() as cursor:
            for caller, environment, permission, value in cells:
                cursor.execute(
                    "INSERT INTO caller_policy (caller, environment, permission, value, "
                    "updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (caller, environment, permission) DO UPDATE SET "
                    "value = excluded.value, updated_at = excluded.updated_at, "
                    "updated_by = excluded.updated_by",
                    (caller, environment, permission, value, moment, by[:120]),
                )
        return [
            self.cell(caller, environment, permission)
            for caller, environment, permission, _ in cells
        ]

    def reset_cells(self, cells: Sequence[tuple[str, str, str]]) -> int:
        """
        Put cells back to what the release ships.

        Args:
            cells: ``(caller, environment, permission)`` for each.

        Returns:
            How many had been chosen, and are not any more.

        Raises:
            ValidationError: When a cell does not exist.
        """
        for caller, environment, permission in cells:
            _check_cell(caller, environment, permission)
        removed = 0
        with self.store._transaction() as cursor:
            for caller, environment, permission in cells:
                cursor.execute(
                    "DELETE FROM caller_policy WHERE caller = ? AND environment = ? "
                    "AND permission = ?",
                    (caller, environment, permission),
                )
                removed += cursor.rowcount
        return removed

    # ------------------------------------------------------------------ judging

    def decide(
        self,
        caller: str,
        environment: str,
        operation: OperationClass,
        needed: Iterable[str],
        *,
        escalated: str | None = None,
    ) -> Verdict:
        """
        Judge one call.

        Args:
            caller: The class of caller (:func:`noust.core.callers.caller_of`).
            environment: The environment of the resource (:func:`environment_of`).
            operation: What the operation is (:func:`noust.core.operation_classes.classify_parts`).
            needed: Every permission the call needs.
            escalated: The permission of the write, when a body made a read route a write
                (:func:`noust.core.operation_classes.escalated_permission`).

        Returns:
            What the policy says: ``alone``, ``propose`` or ``never``, and why.
        """
        where = _normalised(environment)
        if caller not in POLICED:
            return Verdict(ALONE, VERDICT_PERSON, caller, where)
        permissions = list(needed)
        if escalated is not None:
            permissions.append(escalated)
        if caller == ASSISTANT:
            floor = self._assistant_floor(operation, permissions)
            if floor is not None:
                return Verdict(NEVER, floor, caller, where, _first_forbidden(permissions))
        changes = operation.changes or escalated is not None
        if not changes:
            return Verdict(ALONE, VERDICT_NOT_A_CHANGE, caller, where)
        rows = [p for p in dict.fromkeys(permissions) if p in CHANGE_PERMISSIONS]
        if not rows:
            return Verdict(ALONE, VERDICT_NOT_A_CHANGE, caller, where)
        chosen = self._chosen()
        floor_on = self._floor_on()
        found = [
            self._make(caller, where, p, chosen.get((caller, where, p)), floor_on=floor_on)
            for p in rows
        ]
        decider = max(found, key=lambda cell: _RANK[cell.value])
        return Verdict(decider.value, VERDICT_TABLE, caller, where, decider.permission)

    @staticmethod
    def _assistant_floor(operation: OperationClass, permissions: list[str]) -> str | None:
        """
        Args:
            operation: What the operation is.
            permissions: Every permission the call needs, the escalated one included.

        Returns:
            The floor that forbids the call to the assistant, or None when none does.
        """
        if operation.kind == PUBLIC_CLASS:
            return None
        if any(permission in ASSISTANT_FLOOR for permission in permissions):
            return VERDICT_ASSISTANT_FLOOR
        if operation.kind == STRONG:
            return VERDICT_ASSISTANT_STRONG
        if operation.kind == OWN and operation.method not in SAFE_METHODS:
            return VERDICT_ASSISTANT_OWN
        return None


def _first_forbidden(permissions: Iterable[str]) -> str | None:
    """
    Args:
        permissions: Permissions a call needs.

    Returns:
        The first the floor forbids, or None.
    """
    return next((p for p in permissions if p in ASSISTANT_FLOOR), None)


def _check_cell(caller: str, environment: str, permission: str) -> None:
    """
    Refuse a cell that does not exist.

    Args:
        caller: A class of caller.
        environment: An environment.
        permission: A permission.

    Raises:
        ValidationError: Naming what is wrong and what exists.
    """
    if caller not in POLICED:
        raise ValidationError(
            f"{caller!r} is not a class of caller the policy has cells for",
            details=f"Use one of: {', '.join(POLICED)}. A person is held to the rules of the roles.",
            field="caller",
        )
    if environment not in ENVIRONMENTS:
        raise ValidationError(
            f"{environment!r} is not an environment of the policy",
            details=f"Use one of: {', '.join(ENVIRONMENTS)}.",
            field="environment",
        )
    if permission not in CHANGE_PERMISSIONS:
        raise ValidationError(
            f"{permission!r} is not a permission that changes something",
            details="Reading is not approved: the table has a row for each permission that changes "
            "something (GET /api/config/approval-policy lists them).",
            field="permission",
        )


def _why_not(caller: str, permission: str, offered: tuple[str, ...]) -> str:
    """
    Args:
        caller: The class of caller.
        permission: The permission.
        offered: What the cell may be.

    Returns:
        The sentence that says why the cell offers only those.
    """
    if caller == ASSISTANT and permission in ASSISTANT_FLOOR:
        return (
            f"The built-in assistant never does what {permission} lets one do: the floor of the "
            "policy is not configurable. A person does it from the console."
        )
    if permission in SUDO_ROWS:
        return (
            f"Most of what {permission} lets one do asks you to confirm it is you, or a second person: "
            "'alone' is not offered there, and a person does those from the console."
        )
    if caller == ASSISTANT:
        return (
            "The built-in assistant is left to act alone only where it is narrow and reversible: "
            "restarting, renewing a certificate, taking a backup and deploying."
        )
    return (
        "This server's security profile asks that what a token changes in production waits for a "
        "person (noust compliance controls); it can be stricter, never looser."
    )


__all__ = [
    "ALONE",
    "ASSISTANT_ALONE_PERMISSIONS",
    "ASSISTANT_FLOOR",
    "ASSISTANT_NEVER_REASONS",
    "ENVIRONMENTS",
    "NEVER",
    "OTHER",
    "PRODUCTION",
    "PROPOSE",
    "VALUES",
    "CallerPolicy",
    "Cell",
    "Planned",
    "Row",
    "Verdict",
    "area_of",
    "environment_of",
    "environment_of_target",
    "strictest",
]
