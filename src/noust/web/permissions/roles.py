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
  is root-equivalent; it does not govern security, accounts or the audit log.
- ``security`` sees everything and governs security settings, accounts and the
  audit log; it deploys and changes nothing else.
- ``auditor`` sees everything, the account list and the whole audit log.

The credentials that are not accounts are here too, so there is one table:
the master token (the whole console while no account exists, break-glass
afterwards, account recovery only under the ENS profile), the scopes of API
tokens issued before accounts, and the scope a 3.0 central forwards.
"""

from __future__ import annotations

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
    }
)
OPERATOR: frozenset[str] = VIEWER | {P.APPS_OPERATE, P.APPS_DEPLOY, P.BACKUPS_RUN}
ADMIN: frozenset[str] = OPERATOR | {
    P.APPS_MANAGE,
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

#: Role name to what it holds. The names are those of
#: :data:`noust.core.accounts.model.ROLES`, checked by a test.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": VIEWER,
    "operator": OPERATOR,
    "admin": ADMIN,
    "security": SECURITY,
    "auditor": AUDITOR,
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


def permissions_for_role(role: str | None) -> frozenset[str]:
    """
    Args:
        role: A role name.

    Returns:
        The permissions it holds; a viewer's for an unknown role, so a
        central newer than this node never gets more than reading by naming
        a role this node has not heard of.
    """
    return ROLE_PERMISSIONS.get(str(role or ""), FALLBACK)


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


def fleet_role_for(role: str | None, grant: str | None = None) -> str:
    """
    The role a central forwards for one of its principals.

    Args:
        role: The account's role, when it is an account.
        grant: How the master token is held, when it is the master token.

    Returns:
        A role name a node understands: the account's own, ``admin`` for the
        master token in full or break-glass, ``viewer`` otherwise.
    """
    if role in ROLE_PERMISSIONS:
        return str(role)
    if grant in (GRANT_COMPAT, GRANT_BREAK_GLASS):
        return "admin"
    return "viewer"
