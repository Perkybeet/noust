# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which calls hand someone more than they had, and so wait for a second person.

:mod:`noust.core.accounts.approvals` holds the table of routes; the questions a route's body has
to answer before it counts (does this call change the roles of that account?) are here, each
reading the state it is judged against from the store, the way ``rule_for`` is asked on every
call. A question answers False for a call the handler is going to refuse (an account or a role that
does not exist, a value that is not valid), so the person is told what is wrong at once instead of
being sent to wait for a decision on a call that cannot run.

What counts, and why (the line the routes below were drawn by):

- **The roles of an account, whatever route sets them.** ``PATCH`` with ``role``, ``PATCH`` with
  ``roles`` and ``PUT .../roles`` are one change. Any difference counts, reordering and taking a
  role away included: the role an account is known by is what an approval judges a decider by, so
  none of those is a harmless edit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from noust.core.accounts import role_rules

#: The question a route's body has to answer: does this call, as it stands, need a second person.
BodyCheck = Callable[[Any, Mapping[str, str]], bool]


# ------------------------------------------------------------ roles of an account


def changes_account_roles(body: Any, params: Mapping[str, str]) -> bool:
    """
    Whether a call would leave an account with other roles than it holds.

    Both fields are read, because the handler of ``PATCH`` takes ``roles`` over ``role`` and a guard
    that read one could be passed by the other.

    Args:
        body: The call's JSON body: ``roles`` (a list), ``role`` (one), or both.
        params: The route's parameters: ``username``.

    Returns:
        True when the roles asked for are not the roles the account holds, in the same order.
    """
    if not isinstance(body, Mapping):
        return False
    from noust.core.accounts.manager import AccountManager

    account = AccountManager().find(str(params.get("username") or ""))
    if account is None:
        return False
    wanted: list[list[str]] = []
    listed = body.get("roles")
    if isinstance(listed, list) and all(isinstance(item, str) for item in listed):
        names = role_rules.normalised(listed)
        if names and all(names):
            wanted.append(names)
    one = body.get("role")
    if isinstance(one, str) and one.strip():
        wanted.append(role_rules.with_first_role(account.roles, one.strip().lower()))
    return any(tuple(names) != account.roles for names in wanted)
