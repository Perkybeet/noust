# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The rules about the roles an account holds, which :class:`~noust.core.accounts.manager.AccountManager`
asks wherever it writes them (rule 4: none of the callers repeats one).

- **An account has one or more roles of the list**, and at least one. A name the list does not
  have is refused, whichever way the account arrives (an invitation, a command, a provider).
- **The owner is handed on by an owner.** Giving or taking the owner role is done by an account
  that holds it, or by whoever has no account to hold anything with (root on the server, the
  master token), which is how a server that lost its owners gets one back.
- **A server always keeps an owner it can reach.** What would leave the last account that can sign
  in without the owner role (taking it, disabling the account, deleting it) is refused.
- **The first account is the owner**, except where a framework keeps duties apart: it would join
  them, so nobody is handed it unasked.
"""

from __future__ import annotations

from collections.abc import Sequence

from noust.core.access.builtin import ROLE_OWNER
from noust.core.access.model import AccessError
from noust.core.access.roles import USABLE_STATUSES, RoleStore
from noust.core.accounts.model import Account
from noust.core.exceptions import ValidationError
from noust.core.server_text import say


def validated(roles: RoleStore, role: str | None, wanted: Sequence[str] | None) -> list[str]:
    """
    Check the roles an account is to hold.

    Args:
        roles: The list of roles.
        role: One role, when the caller names one (the older way).
        wanted: Several, when it names them; they take the place of ``role``.

    Returns:
        The roles, lower-cased, without repeats, in the order given.

    Raises:
        ValidationError: When there are none, or one is not in the list.
    """
    given = list(wanted) if wanted else ([role] if role else [])
    names = list(dict.fromkeys((str(name) or "").strip().lower() for name in given))
    if not names or not all(names):
        raise ValidationError.from_text(say("roles.no_roles"), field="roles")
    known = set(roles.names())
    for name in names:
        if name not in known:
            raise ValidationError.from_text(
                say("roles.unknown", name=name, command="noust access role list"), field="roles"
            )
    return names


def refuse_owner_hand_over(by: Account | None, before: Sequence[str], after: Sequence[str]) -> None:
    """
    Refuse giving or taking the owner role by somebody who does not hold it.

    Args:
        by: The account asking (a token's owner), or None for a principal that is no account
            (root, the master token).
        before: The account's roles now.
        after: Its roles if the change goes ahead.

    Raises:
        AccessError: When the owner role changes hands and the account asking is not an owner.
    """
    if by is None or (ROLE_OWNER in before) == (ROLE_OWNER in after):
        return
    if ROLE_OWNER not in by.roles:
        raise AccessError.from_text(say("roles.owner_by_owner"), field="roles")


def refuse_last_owner(
    roles: RoleStore,
    account: Account,
    after: Sequence[str] | None,
) -> None:
    """
    Refuse what would leave the server without an owner who can sign in.

    Args:
        roles: The list of roles.
        account: The account that would lose the role, by disabling, deleting or a change of roles.
        after: Its roles if the change goes ahead; None for an account that goes (disabled,
            deleted).

    Raises:
        AccessError: When it is the last account holding the owner role that can sign in.
    """
    if ROLE_OWNER not in account.roles or (after is not None and ROLE_OWNER in after):
        return
    if account.status not in USABLE_STATUSES:
        # It was not counted, so taking the role from it takes nobody away.
        return
    if any(holder.account_id != account.id for holder in roles.usable_owners()):
        return
    raise AccessError.from_text(say("roles.last_owner", username=account.username), field="roles")


def first_account_gets_the_owner(
    roles: RoleStore, names: Sequence[str], any_account: bool, duties_apart: bool
) -> bool:
    """
    Whether the account being made first is to be the owner.

    Args:
        roles: The list of roles.
        names: The roles it was asked for.
        any_account: Whether the server already has an account.
        duties_apart: Whether a control keeps governing and running apart here.

    Returns:
        True for the first account of a server with no owner, where no framework keeps duties
        apart and the owner is not already among its roles.
    """
    return (
        not any_account
        and ROLE_OWNER not in names
        and not roles.usable_owners()
        and not duties_apart
    )


def team_basis(names: Sequence[str], instance_only: frozenset[str], mirror: str) -> str:
    """
    The role the teams treat an account as having, when it holds several.

    Args:
        names: Its roles.
        instance_only: The roles that never join a team.
        mirror: What ``accounts.role`` says for them.

    Returns:
        A role that is only meaningful across teams when it holds one, so it leaves them as a
        security officer or an auditor does; its own role otherwise.
    """
    return next((name for name in names if name in instance_only), mirror)
