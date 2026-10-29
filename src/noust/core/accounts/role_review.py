# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a framework makes of the roles a server has: which join the duties it keeps apart.

A framework (ENS, ISO 27001, ...) used to cut the administrator behind the roles' backs
(``roles_split``). It does not any more: it judges the roles as they are defined, and this is the
judgement, one for the compliance plan, the ENS check and report, and the console:

- which roles join governing the server with running it, so the operator knows what to split;
- which accounts hold roles that together do, though no role does alone;
- how many owners the server has. The owner holds everything by design, so it is listed as
  protected and never counted among the roles that join them.

It reads; nothing here changes a role or an account.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core import compliance
from noust.core.access.builtin import ADMIN, ROLE_ADMIN, ROLE_OWNER, separation_conflict
from noust.core.access.roles import RoleStore
from noust.core.accounts.manager import AccountManager

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: The controls that ask for governing and running to be kept apart.
DUTY_CONTROLS: tuple[str, ...] = ("roles_split", "separation_of_duties", "single_role_per_account")


@dataclass(frozen=True)
class RoleJudgement:
    """
    One role, judged.

    Attributes:
        name: The role.
        protected: Whether it is the owner, which holds everything by design.
        holders: How many accounts hold it.
        governing: The permissions that govern the server it holds, when it also runs it.
        running: The permissions that run the server it holds, when it also governs it.
    """

    name: str
    protected: bool
    holders: int
    governing: tuple[str, ...] = ()
    running: tuple[str, ...] = ()

    @property
    def joins_duties(self) -> bool:
        """Whether it joins governing the server with running it (never the protected one)."""
        return not self.protected and bool(self.governing and self.running)


@dataclass(frozen=True)
class AccountJudgement:
    """
    An account whose roles together join the duties, though none does alone.

    Attributes:
        username: The account.
        roles: Its roles.
        governing: What it holds that governs.
        running: What it holds that runs.
    """

    username: str
    roles: tuple[str, ...]
    governing: tuple[str, ...]
    running: tuple[str, ...]


@dataclass(frozen=True)
class RolesReview:
    """
    The roles of a server, judged against what the frameworks in force ask.

    Attributes:
        controls: The controls in force that keep governing and running apart.
        roles: Every role, judged.
        accounts: The accounts whose roles together join the duties.
        owners: The accounts that hold the owner role and can sign in.
        suggestion: A command that splits the administrator the way the ENS review decided, when
            the administrator joins the duties.
    """

    controls: tuple[str, ...]
    roles: tuple[RoleJudgement, ...]
    accounts: tuple[AccountJudgement, ...]
    owners: int
    suggestion: str | None = None

    @property
    def duties_apart(self) -> bool:
        """Whether a control that keeps the duties apart is in force."""
        return bool(self.controls)

    @property
    def joining(self) -> tuple[RoleJudgement, ...]:
        """The roles that join the duties."""
        return tuple(role for role in self.roles if role.joins_duties)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The review as the API and ``--json`` show it.
        """
        return {
            "duties_apart": self.duties_apart,
            "controls": list(self.controls),
            "owners": self.owners,
            "suggestion": self.suggestion,
            "roles": [
                {
                    "name": role.name,
                    "protected": role.protected,
                    "holders": role.holders,
                    "joins_duties": role.joins_duties,
                    "governing": list(role.governing),
                    "running": list(role.running),
                }
                for role in self.roles
            ],
            "accounts": [
                {
                    "username": account.username,
                    "roles": list(account.roles),
                    "governing": list(account.governing),
                    "running": list(account.running),
                }
                for account in self.accounts
            ],
        }


def split_administrator_command() -> str:
    """
    Returns:
        The command that saves the administrator as the ENS review decided it: without the
        permissions of governing the server. It is the same set an upgrade saved for a server
        that ran ``roles_split``.
    """
    flags = " ".join(f"--permission {name}" for name in sorted(ADMIN - {"self"}))
    return f"noust access role set {ROLE_ADMIN} {flags}"


def review(
    store: NoustStore | None = None, requires: Callable[[str], bool] | None = None
) -> RolesReview:
    """
    Judge the roles of a server.

    Args:
        store: The store; the process-wide one by default.
        requires: Whether a control is in force; the configuration's by default.

    Returns:
        The review.
    """
    asked = requires or compliance.is_on
    roles = RoleStore(store)
    table = roles.permission_map()
    judged = []
    for record in roles.list_roles():
        conflict = None if record.protected else separation_conflict(record.held)
        governing, running = conflict if conflict is not None else ([], [])
        judged.append(
            RoleJudgement(
                name=record.name,
                protected=record.protected,
                holders=len(roles.holders(record.name)),
                governing=tuple(governing),
                running=tuple(running),
            )
        )
    accounts = []
    for account in AccountManager(store).list_all():
        if ROLE_OWNER in account.roles or len(account.roles) < 2:
            continue
        held: set[str] = set()
        for name in account.roles:
            held |= table.get(name, frozenset())
        conflict = separation_conflict(held)
        alone = any(
            separation_conflict(table.get(name, frozenset())) is not None for name in account.roles
        )
        if conflict is not None and not alone:
            accounts.append(
                AccountJudgement(
                    account.username, account.roles, tuple(conflict[0]), tuple(conflict[1])
                )
            )
    joins_administrator = any(role.name == ROLE_ADMIN and role.joins_duties for role in judged)
    return RolesReview(
        controls=tuple(control for control in DUTY_CONTROLS if asked(control)),
        roles=tuple(judged),
        accounts=tuple(accounts),
        owners=len(roles.usable_owners()),
        suggestion=split_administrator_command() if joins_administrator else None,
    )
