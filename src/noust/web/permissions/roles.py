# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which permissions each role holds on this server, and each credential that is not an account.

A server has one list of roles (4.1, item 96): the five of every release and the protected
``owner`` are seeded, and the operator edits them, duplicates them, puts them back and defines
others. What each holds is a row of the store (:mod:`noust.core.access.roles`); this module is the
one place the web layer asks for it, so a role the operator edited means the same thing in a
session, in a token and in a request a central forwards.

- :func:`permissions_for_role` is what one role holds *here*; a viewer's for a name this server has
  not heard of, so a name never gets more than reading by being unknown.
- :func:`permissions_of_account` is what an account holds: the union of its roles.
- :data:`ROLE_PERMISSIONS` and :data:`STANDARD_ROLE_PERMISSIONS` are what Noust ships and what
  the ENS review decided (``research/3.1/ens.md`` §4.2.2): the reference a framework's report and
  the documentation compare a server's roles with. They are not what is enforced and nothing cuts
  a role behind the operator's back any more: ``roles_split`` is judged on the roles as they are
  (:mod:`noust.core.access.combination`).

The credentials that are not accounts are here too, so there is one table: the master token (the
whole console while no account exists, break-glass afterwards, account recovery only under the ENS
profile), the scopes of API tokens issued before accounts, and the scope a 3.0 central forwards.

In a fleet a central forwards the *permissions* the person holds (``X-Noust-Actor-Permissions``),
not only a role's name: with roles the operator edits, a name does not mean the same thing on two
servers. The node's ceiling for centrals still decides what any of it grants
(:mod:`noust.fleet.policy`), which is where the boundary always was: a central could already claim
``admin``. A node before 4.1 reads the name, so :func:`role_within` still names the widest role
the person holds all of.
"""

from __future__ import annotations

from typing import Any

from noust.core.access.builtin import (
    ADMIN,
    ADMIN_STANDARD,
    AUDITOR,
    OPERATOR,
    ROLE_OWNER,
    SECURITY,
    VIEWER,
)
from noust.core.access.roles import FALLBACK_ROLE, RoleStore
from noust.web.permissions import ALL_PERMISSIONS, Permission

P = Permission

__all__ = [
    "ADMIN",
    "ADMIN_STANDARD",
    "AUDITOR",
    "BREAK_GLASS",
    "COMPAT",
    "FALLBACK",
    "GRANT_BREAK_GLASS",
    "GRANT_COMPAT",
    "GRANT_PERMISSIONS",
    "GRANT_RECOVERY",
    "LEGACY_SCOPES",
    "OPERATOR",
    "RECOVERY",
    "ROLE_PERMISSIONS",
    "SECURITY",
    "STANDARD_ROLE_PERMISSIONS",
    "TOKEN_SCOPES",
    "VIEWER",
    "instance_role_of",
    "legacy_scope",
    "permissions_for_role",
    "permissions_for_scope",
    "permissions_of_account",
    "role_table",
    "role_within",
]

#: What the ENS review decided each role holds: the administrator does not govern security,
#: accounts or the audit log. The reference for a framework that keeps duties apart (a server's
#: administrator that matches it is split; one that does not is reported), and what a server that
#: ran ``roles_split`` saved its administrator as when it moved to editable roles. Keyed by the
#: five roles of 3.1, whose names ``noust.core.accounts.model.ROLES`` holds a test to.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": VIEWER,
    "operator": OPERATOR,
    "admin": ADMIN,
    "security": SECURITY,
    "auditor": AUDITOR,
}

#: The same table with the administrator Noust ships: the three duties, which a framework keeps
#: in different hands, are one person's (item 83: a person running their own servers needs one
#: account, not three).
STANDARD_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    **ROLE_PERMISSIONS,
    "admin": ADMIN_STANDARD,
}

#: How the master token is holding the console right now.
GRANT_COMPAT = "compat"
GRANT_BREAK_GLASS = "break_glass"
GRANT_RECOVERY = "recovery"

#: The master token while no account exists: the whole console, as in 3.0.
COMPAT: frozenset[str] = ALL_PERMISSIONS
#: Once accounts exist: everything but managing the audit trail it leaves.
BREAK_GLASS: frozenset[str] = ALL_PERMISSIONS - {P.AUDIT_MANAGE}
#: Under the ENS profile: nothing but getting a person back in.
RECOVERY: frozenset[str] = frozenset({P.SELF, P.ACCOUNTS_READ, P.ACCOUNTS_MANAGE})

GRANT_PERMISSIONS: dict[str, frozenset[str]] = {
    GRANT_COMPAT: COMPAT,
    GRANT_BREAK_GLASS: BREAK_GLASS,
    GRANT_RECOVERY: RECOVERY,
}

#: The 3.0 token scopes, as permissions: ``read`` is a viewer, ``deploy`` a
#: viewer that may also deploy (exactly what 3.0's ``deploy`` allowed: no
#: restarts), ``admin`` the whole console, as the master token was.
LEGACY_SCOPES: dict[str, frozenset[str]] = {
    "read": VIEWER,
    "deploy": VIEWER | {P.APPS_DEPLOY},
    "admin": ALL_PERMISSIONS,
}

#: What a new token asked for by scope may hold, before its owner's cap.
TOKEN_SCOPES: dict[str, frozenset[str]] = {
    "read": VIEWER,
    "deploy": VIEWER | {P.APPS_DEPLOY},
    "admin": ADMIN,
}

#: What an unknown role or scope is worth: never more than reading.
FALLBACK: frozenset[str] = VIEWER


def role_table(store: Any = None) -> dict[str, frozenset[str]]:
    """
    Args:
        store: The store; the process-wide one by default.

    Returns:
        What every role of this server holds now, by name: the rows of the list, the ones a
        release follows as that release ships them. Read on every call, so a change applies to
        the next request.
    """
    return RoleStore(store).permission_map()


def permissions_for_role(role: str | None, store: Any = None) -> frozenset[str]:
    """
    Args:
        role: A role name.
        store: The store; the process-wide one by default.

    Returns:
        The permissions it holds on this server; a viewer's for a role this server does not
        have, so a central newer than this node never gets more than reading by naming a role
        this node has not heard of.
    """
    return role_table(store).get(str(role or ""), FALLBACK)


def permissions_of_account(account: Any, store: Any = None) -> frozenset[str]:
    """
    What an account holds on the server outside any team: the union of its roles.

    Args:
        account: The account (anything with ``roles``).
        store: The store; the process-wide one by default.

    Returns:
        What its roles hold together; a viewer's when none of them exists any more.
    """
    return RoleStore(store).permissions_for(account.roles)


def instance_role_of(account: Any) -> str:
    """
    The role the teams treat an account as holding, when it holds several.

    Which teams an account sees, and whether it joins one, depend on a role that is meaningful
    across the whole server (:data:`noust.core.teams.INSTANCE_WIDE_ROLES`). The owner sees what an
    administrator does; any other of the account's roles that sees every team makes it see every
    team; otherwise the role it is known by decides, as it always did.

    Args:
        account: The account (anything with ``role`` and ``roles``).

    Returns:
        A role name the team machinery knows.
    """
    from noust.core.teams.model import INSTANCE_WIDE_ROLES

    for name in account.roles:
        if name == ROLE_OWNER:
            return "admin"
        if name in INSTANCE_WIDE_ROLES:
            return str(name)
    return str(account.role)


def permissions_for_scope(scope: str | None) -> frozenset[str]:
    """
    Args:
        scope: A 3.0 token scope: ``read``, ``deploy`` or ``admin``.

    Returns:
        What it allowed in 3.0, as permissions; a viewer's for anything else.
    """
    return LEGACY_SCOPES.get(str(scope or ""), FALLBACK)


def legacy_scope(permissions: frozenset[str] | set[str]) -> str:
    """
    Name the 3.0 scope a set of permissions amounts to, at most.

    Code written against scopes (and a 3.0 node behind a central) still
    reads ``read``, ``deploy`` or ``admin``; this keeps that answer honest.

    Args:
        permissions: What a principal holds.

    Returns:
        ``admin`` when it may manage applications, ``deploy`` when it may
        deploy, ``read`` otherwise.
    """
    if P.APPS_MANAGE in permissions:
        return "admin"
    if P.APPS_DEPLOY in permissions:
        return "deploy"
    return "read"


#: Roles from the most a node grants to the least, for rounding a narrowed
#: credential down to one a node understands.
_ROLES_BY_REACH: tuple[str, ...] = ("admin", "security", "operator", "auditor", "viewer")


def role_within(
    role: str | None, permissions: frozenset[str] | set[str], store: Any = None
) -> str | None:
    """
    The name of one of the five roles of 3.1 a central may give a node that does not read
    permissions, without widening the person.

    A node before 4.1 grants what its own table gives the role it is told. A person is named by
    the widest of the five roles of 3.1 they hold every permission of (as this server defines
    them), whatever role their account is known by: an account with several roles, a token
    narrowed below its owner's and a team member all come out as the widest that fits. The owner
    is never a name: a node before 4.1 does not know it, and what it holds beyond the others is
    only reached by a node that reads the permissions.

    Args:
        role: The role of the account behind the principal, or None for a credential that is
            no account.
        permissions: What the principal actually holds on the central.
        store: The store; the process-wide one by default.

    Returns:
        The widest of the five entirely within ``permissions``, and ``viewer`` - the least a
        node grants anyone - when none is. None when ``role`` is None (the master token, a
        token nobody owns), which the node narrows by scope.
    """
    if role is None:
        return None
    held = frozenset(permissions)
    table = role_table(store)
    for candidate in _ROLES_BY_REACH:
        definition = table.get(candidate)
        if definition and definition <= held:
            return candidate
    return FALLBACK_ROLE
