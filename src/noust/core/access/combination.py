# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which roles one account may hold together, and what a role may hold, where a framework asks.

A framework (ENS, ISO 27001, ...) does not cut permissions behind the roles any more
(``roles_split`` used to); it asks that duties are not joined, and this module is the judgement
of the roles as they are defined:

- ``separation_of_duties``: no role is *defined* (or edited) joining governing the server with
  running it, and no account's roles together do although none does alone; an auditor holds
  nothing beyond what an auditor does. A role that already joins them, such as the administrator
  Noust ships, is not refused to the accounts that hold it: it is reported
  (:mod:`noust.core.accounts.role_review`) until the operator splits it, and nothing is cut
  behind their back.
- ``single_role_per_account``: an account has one function. Several roles are fine, as long as
  together they do not join governing with running (this is what the control used to refuse by
  counting roles, which also refused a viewer who was given a second reading role).

The owner is outside both: it is the role that keeps a server from being locked out, it holds
everything by design, and a report says so (:func:`noust.core.access.review.review`).

Everything that gives an account a role, and everything that changes what a role holds, asks here
(rule 4): the CLI, the API and the identity providers reach the same two functions.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from noust.core import compliance
from noust.core.access.builtin import ROLE_AUDITOR, ROLE_OWNER, separation_conflict
from noust.core.access.model import AccessError
from noust.core.permission_catalog import Permission
from noust.core.server_text import say

#: The question "is this control in force": the configuration's by default, and a policy's where
#: the caller was given one (the account manager judges by its policy, as for every other limit).
Requires = Callable[[str], bool]


def _in_force(control_id: str) -> bool:
    return compliance.is_on(control_id)


def duties_apply(requires: Requires = _in_force) -> bool:
    """
    Args:
        requires: Whether a control is in force.

    Returns:
        Whether a control that keeps governing and running apart is in force.
    """
    return requires("separation_of_duties") or requires("single_role_per_account")


def refuse_conflicting_definition(
    subject: str, permissions: Iterable[str], requires: Requires = _in_force
) -> None:
    """
    Refuse a role that would join governing the server with running it.

    Args:
        subject: What holds the permissions, for the message (``The role release-manager``).
        permissions: What the role would hold.
        requires: Whether a control is in force.

    Raises:
        AccessError: When ``separation_of_duties`` is in force and the set joins them.
    """
    if not requires("separation_of_duties"):
        return
    conflict = separation_conflict(permissions)
    if conflict is not None:
        governing, running = conflict
        raise AccessError.from_text(
            say(
                "roles.separation_role",
                subject=subject,
                governing=", ".join(governing),
                running=", ".join(running),
            ),
            field="permissions",
        )


def accounts_clash(
    first: Sequence[str], second: Sequence[str], table: Mapping[str, frozenset[str]]
) -> bool:
    """
    Whether two accounts of one person together join what a framework keeps apart.

    What each account holds alone is judged by :func:`refuse_conflicting_roles`; this is only
    about the two of them together, so a role that already joins both halves is not counted
    against every account the person has.

    Args:
        first: The roles of one account.
        second: The roles of another account of the same person.
        table: What every role holds now, by name.

    Returns:
        True when an auditor's person holds another kind of account, or when the two accounts
        together hold both governing and running although neither does alone. The owner is outside
        it, as everywhere here.
    """
    if ROLE_OWNER in first or ROLE_OWNER in second:
        return False
    if ROLE_AUDITOR in first or ROLE_AUDITOR in second:
        return set(first) != set(second)
    one: set[str] = set()
    other: set[str] = set()
    for name in first:
        one |= table.get(name, frozenset())
    for name in second:
        other |= table.get(name, frozenset())
    return (
        separation_conflict(one | other) is not None
        and separation_conflict(one) is None
        and separation_conflict(other) is None
    )


def refuse_conflicting_roles(
    username: str,
    names: Sequence[str],
    table: Mapping[str, frozenset[str]],
    requires: Requires = _in_force,
) -> None:
    """
    Refuse a set of roles one account would hold together.

    Args:
        username: The account, for the message.
        names: Its roles.
        table: What every role holds now, by name.
        requires: Whether a control is in force.

    Raises:
        AccessError: When a control that keeps duties apart is in force and the roles together
            join them, or an auditor would hold something beyond what an auditor does.
    """
    if not duties_apply(requires) or ROLE_OWNER in names:
        return
    held: set[str] = set()
    for name in names:
        held |= table.get(name, frozenset())
    if requires("separation_of_duties") and ROLE_AUDITOR in names:
        beyond = held - table.get(ROLE_AUDITOR, frozenset()) - {Permission.SELF}
        if beyond:
            raise AccessError.from_text(
                say("roles.auditor_exclusive", username=username), field="roles"
            )
    conflict = separation_conflict(held)
    if conflict is None:
        return
    if any(separation_conflict(table.get(name, frozenset())) for name in names):
        # One role joins them by itself, so it is the role that has to be split: the review says
        # which (:mod:`noust.core.accounts.role_review`), and a role cannot be defined or edited
        # to join them. Refusing the account would only make a server unable to have any.
        return
    governing, running = conflict
    raise AccessError.from_text(
        say(
            "roles.separation_account",
            username=username,
            governing=", ".join(governing),
            running=", ".join(running),
        ),
        field="roles",
    )
