# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which permissions each role holds, and each credential that is not an account.

The role matrix is the one in the ENS review (``research/3.1/ens.md`` §4.2.2),
decided in the 3.1 spec (§2.1):

- ``viewer`` sees everything, secrets redacted, and changes nothing.
- ``operator`` also runs what exists: restarts, certificate renewals, cron
  runs, backups, and deploys (update, rollback, activate, rebuild).
- ``admin`` also creates, configures and deletes, reads secrets and does what
  is root-equivalent. Under the ``ens-medium`` profile it does not govern
  security, accounts or the audit log; outside it, it does (item 83: a person
  running their own servers needs one account, not three).
- ``security`` sees everything and governs security settings, accounts and the
  audit log; it deploys and changes nothing else.
- ``auditor`` sees everything, the account list and the whole audit log.

``ROLE_PERMISSIONS`` is the matrix the ENS review decided, which is what the
profile enforces. What a role holds on *this* server is
:func:`permissions_for_role`, the one place the node's own profile is read, so a
central forwarding a role name gets exactly what the node decides.

The credentials that are not accounts are here too, so there is one table:
the master token (the whole console while no account exists, break-glass
afterwards, account recovery only under the ENS profile), the scopes of API
tokens issued before accounts, and the scope a 3.0 central forwards.
"""

from __future__ import annotations

from noust.core import compliance
from noust.web.permissions import ALL_PERMISSIONS, Permission

P = Permission

VIEWER: frozenset[str] = frozenset(
    {
        P.SELF,
        P.APPS_READ,
        P.SERVER_READ,
        P.DATABASES_READ,
        P.BACKUPS_READ,
        P.FLEET_READ,
        P.SETTINGS_READ,
        P.TEAMS_READ,
    }
)
OPERATOR: frozenset[str] = VIEWER | {P.APPS_OPERATE, P.APPS_DEPLOY, P.BACKUPS_RUN}
ADMIN: frozenset[str] = OPERATOR | {
    P.APPS_MANAGE,
    # A shell as the application reads its secrets and changes what runs, so it is
    # an administrator's, like revealing them; the server's own is root.
    P.APPS_TERMINAL,
    P.SERVER_TERMINAL,
    P.SECRETS_REVEAL,
    P.ROOT_EQUIVALENT,
    P.SERVER_MANAGE,
    P.SERVER_HOST_ACCESS,
    P.DATABASES_WRITE,
    P.DATABASES_MANAGE,
    P.BACKUPS_MANAGE,
    P.FLEET_MANAGE,
    P.SETTINGS_MANAGE,
    P.COMPLIANCE_READ,
    P.TEAMS_MANAGE,
}
SECURITY: frozenset[str] = VIEWER | {
    P.SECURITY_MANAGE,
    P.ACCOUNTS_READ,
    P.ACCOUNTS_MANAGE,
    P.AUDIT_READ,
    P.AUDIT_MANAGE,
    P.COMPLIANCE_READ,
}
AUDITOR: frozenset[str] = VIEWER | {P.ACCOUNTS_READ, P.AUDIT_READ, P.COMPLIANCE_READ}

#: What an administrator holds outside the ENS profile: the three duties, which
#: the profile keeps in different hands, are one person's here. Everything there
#: is (a test holds it to ``ALL_PERMISSIONS``).
ADMIN_STANDARD: frozenset[str] = ADMIN | SECURITY | AUDITOR

#: Role name to what it holds under the ENS profile. The names are those of
#: :data:`noust.core.accounts.model.ROLES`, checked by a test.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": VIEWER,
    "operator": OPERATOR,
    "admin": ADMIN,
    "security": SECURITY,
    "auditor": AUDITOR,
}

#: The same table outside the profile: only the administrator differs.
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


def role_table() -> dict[str, frozenset[str]]:
    """
    Returns:
        The role matrix in force on this server: the ENS review's where the
        ``roles_split`` control is on (a framework asks for it, or
        ``security.roles_split``), the one with a whole-server administrator
        otherwise. Read on every call, so a change applies to the next request.
    """
    return ROLE_PERMISSIONS if compliance.is_on("roles_split") else STANDARD_ROLE_PERMISSIONS


def permissions_for_role(role: str | None) -> frozenset[str]:
    """
    Args:
        role: A role name.

    Returns:
        The permissions it holds on this server, under its profile; a
        viewer's for an unknown role, so a central newer than this node never
        gets more than reading by naming a role this node has not heard of. A
        fleet request is judged here, by the node's own profile, whatever the
        central's is: the central names a role, never what it holds.
    """
    return role_table().get(str(role or ""), FALLBACK)


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


def role_within(role: str | None, permissions: frozenset[str] | set[str]) -> str | None:
    """
    The role a central may name for a principal without widening it.

    A node grants what its own table gives the role it is told, so a token
    narrowed below its owner's role must not be named by that role: it is
    named by the widest role it holds every permission of instead.

    Args:
        role: The role of the account behind the principal, or None.
        permissions: What the principal actually holds on the central.

    Returns:
        ``role`` when the principal holds all of it; otherwise the widest role
        entirely within ``permissions``, and ``viewer`` - the least a node
        grants anyone - when none is. None when ``role`` is None (the master
        token, a token nobody owns), which the node narrows by scope.
    """
    if role is None:
        return None
    held = frozenset(permissions)
    table = role_table()
    if role in table and table[role] <= held:
        return role
    for candidate in _ROLES_BY_REACH:
        if table[candidate] <= held:
            return candidate
    return "viewer"
