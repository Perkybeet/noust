# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The roles Noust ships, and what governing and running the server mean.

The role matrix is the one in the ENS review (``research/3.1/ens.md`` §4.2.2), decided in the 3.1
spec (§2.1):

- ``viewer`` sees everything, secrets redacted, and changes nothing.
- ``operator`` also runs what exists: restarts, certificate renewals, cron runs, backups, and
  deploys (update, rollback, activate, rebuild).
- ``admin`` also creates, configures and deletes, reads secrets and does what is root-equivalent,
  and governs security, accounts and the audit log: a person running their own servers needs one
  account, not three (item 83).
- ``security`` sees everything and governs security settings, accounts and the audit log; it
  deploys and changes nothing else.
- ``auditor`` sees everything, the account list and the whole audit log.
- ``owner`` holds every permission there is, always (4.1, item 96): the one role nobody edits or
  deletes, so a server is never left without somebody who can do everything.

These are what a role is *seeded* with. Once seeded, a role is a row
(:mod:`noust.core.access.roles`) the operator edits, duplicates and puts back; what a framework
wants of the roles is a judgement of the rows (:func:`separation_conflict`), never a table that
cuts permissions behind the operator's back, which is what ``roles_split`` used to do.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from noust.core.permission_catalog import ALL_PERMISSIONS, Permission

P = Permission

VIEWER: frozenset[str] = frozenset(
    {
        P.SELF,
        P.APPS_READ,
        P.SERVER_READ,
        P.DOCKER_READ,
        P.DATABASES_READ,
        P.BACKUPS_READ,
        P.FLEET_READ,
        P.SETTINGS_READ,
        P.TEAMS_READ,
    }
)
OPERATOR: frozenset[str] = VIEWER | {
    P.APPS_OPERATE,
    P.APPS_DEPLOY,
    P.BACKUPS_RUN,
    P.ASSISTANT_USE,
}
ADMIN: frozenset[str] = OPERATOR | {
    P.APPS_MANAGE,
    # A shell as the application reads its secrets and changes what runs, so it is
    # an administrator's, like revealing them; the server's own is root.
    P.APPS_TERMINAL,
    P.SERVER_TERMINAL,
    P.SECRETS_REVEAL,
    P.ROOT_EQUIVALENT,
    P.SERVER_MANAGE,
    P.DOCKER_MANAGE,
    P.SERVER_HOST_ACCESS,
    P.DATABASES_WRITE,
    P.DATABASES_MANAGE,
    P.BACKUPS_MANAGE,
    P.BACKUPS_COLLECT,
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

#: What an administrator is seeded with: the three duties, which a framework keeps in different
#: hands, are one person's. Everything there is (a test holds it to ``ALL_PERMISSIONS``).
ADMIN_STANDARD: frozenset[str] = ADMIN | SECURITY | AUDITOR

#: The names of the roles every server has. The first five are the roles of 3.1 and later, in the
#: order the console lists them (weakest first); the last is the protected one.
ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
ROLE_SECURITY = "security"
ROLE_AUDITOR = "auditor"
ROLE_OWNER = "owner"
SEEDED_ROLES: tuple[str, ...] = (
    ROLE_VIEWER,
    ROLE_OPERATOR,
    ROLE_ADMIN,
    ROLE_SECURITY,
    ROLE_AUDITOR,
)
SYSTEM_ROLES: tuple[str, ...] = (*SEEDED_ROLES, ROLE_OWNER)

#: What decides who may run the server. Holding any of it with any of :data:`RUNNING` is the
#: combination the separation of duties forbids.
GOVERNANCE: frozenset[str] = frozenset({P.SECURITY_MANAGE, P.ACCOUNTS_MANAGE, P.AUDIT_MANAGE})

#: What runs the server and what is in it: everything an ``operator`` or an ``admin`` holds beyond
#: reading.
RUNNING: frozenset[str] = (ADMIN | OPERATOR) - VIEWER - {P.TEAMS_MANAGE, P.COMPLIANCE_READ}


@dataclass(frozen=True)
class Shipped:
    """
    A role as Noust ships it.

    Attributes:
        name: Its slug.
        label: What the console shows for it in English; the console translates the five by name.
        description: One sentence on what it is for.
        permissions: What it holds out of the box.
    """

    name: str
    label: str
    description: str
    permissions: frozenset[str]


#: The five roles a server is seeded with, by name. ``owner`` is not here: it holds whatever
#: ``ALL_PERMISSIONS`` is, so it has no list to keep in step with a release.
SHIPPED: dict[str, Shipped] = {
    ROLE_VIEWER: Shipped(
        ROLE_VIEWER,
        "Viewer",
        "Sees everything, with secrets redacted, and changes nothing.",
        VIEWER,
    ),
    ROLE_OPERATOR: Shipped(
        ROLE_OPERATOR,
        "Operator",
        "Runs what exists: restarts, renewals, backups and deployments.",
        OPERATOR,
    ),
    ROLE_ADMIN: Shipped(
        ROLE_ADMIN,
        "Administrator",
        "Creates, configures and deletes, and governs accounts, security and the audit log.",
        ADMIN_STANDARD,
    ),
    ROLE_SECURITY: Shipped(
        ROLE_SECURITY,
        "Security officer",
        "Governs security settings, accounts and the audit log; deploys and changes nothing else.",
        SECURITY,
    ),
    ROLE_AUDITOR: Shipped(
        ROLE_AUDITOR,
        "Auditor",
        "Sees everything, the accounts and the whole audit log.",
        AUDITOR,
    ),
}

#: What the owner is called and described as.
OWNER_LABEL = "Owner"
OWNER_DESCRIPTION = "Holds every permission there is. It cannot be edited or deleted."


def separation_conflict(permissions: Iterable[str]) -> tuple[list[str], list[str]] | None:
    """
    Say whether a set of permissions mixes governing the server with running it.

    Args:
        permissions: What a role, or an account's roles together, hold.

    Returns:
        ``(governing, running)`` - the offending permissions of each half - or None when the set
        keeps them apart.
    """
    held = set(permissions)
    governing = sorted(held & GOVERNANCE)
    running = sorted(held & RUNNING)
    return (governing, running) if governing and running else None


def everything() -> frozenset[str]:
    """
    Returns:
        Every permission there is: what the owner holds, whichever release wrote its row.
    """
    return ALL_PERMISSIONS
