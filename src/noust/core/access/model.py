# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a custom role and a role binding are, and the names they may carry.

Pure data and validation, with no store behind it. What a permission *means*
(the catalogue, what a team can own, separation of duties) is the console's
business and lives in :mod:`noust.web.permissions.access`; this module only
knows the shapes and the rules between the names, so the CLI, the API and the
store agree on one definition.

A **binding** gives an account a role at a scope (I-03): the whole server, a
team, a project or an environment. A **custom role** (I-07) is a named set of
the permissions that exist, defined by an administrator and bound like any
built-in role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import NoustError, ValidationError

#: Where a role is held. ``instance`` adds to the account's own role everywhere;
#: the others narrow what the role reaches to a team, a project or an environment.
SCOPE_INSTANCE = "instance"
SCOPE_TEAM = "team"
SCOPE_PROJECT = "project"
SCOPE_ENVIRONMENT = "environment"
SCOPE_KINDS: tuple[str, ...] = (SCOPE_INSTANCE, SCOPE_TEAM, SCOPE_PROJECT, SCOPE_ENVIRONMENT)

#: The scope id of the server, which has no row of its own.
INSTANCE_SCOPE_ID = 0

#: Names a custom role can never take: the roles that already mean something,
#: on the server (``ROLES``) and in a team (``TEAM_ROLES``).
BUILT_IN_ROLE_NAMES = frozenset({"viewer", "operator", "admin", "security", "auditor", "owner"})

#: A custom role's name: lower-case letters, digits and dashes, 2 to 40
#: characters, starting and ending with a letter or digit. It is typed in
#: commands and travels in URLs.
ROLE_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,38}[a-z0-9]")

#: Longest label, description and permission list kept.
MAX_LABEL = 60
MAX_DESCRIPTION = 300
MAX_PERMISSIONS = 64
#: Custom roles a server keeps at most: a catalogue, not a database.
MAX_ROLES = 100


class AccessError(NoustError):
    """A custom role or a binding was refused; the message says why."""


class AccessNotFoundError(AccessError):
    """No custom role or binding has the name or id asked for."""


class AccessConflictError(AccessError):
    """The operation clashes with what exists: a name taken, a role still in use."""


def validate_role_name(value: str) -> str:
    """
    Check the name of a custom role.

    Args:
        value: The name as typed.

    Returns:
        The name, trimmed and lower-cased.

    Raises:
        ValidationError: When it is not a slug, or is the name of a built-in role.
    """
    name = (value or "").strip().lower()
    if not ROLE_NAME_PATTERN.fullmatch(name):
        raise ValidationError(
            f"Invalid role name: {value!r}",
            details=(
                "Use 2 to 40 lower-case letters, digits or '-', starting and ending with a "
                "letter or digit, such as 'release-manager'."
            ),
            field="name",
        )
    if name in BUILT_IN_ROLE_NAMES:
        raise ValidationError(
            f"{name!r} is the name of a built-in role",
            details="Pick another name; the built-in roles cannot be redefined.",
            field="name",
        )
    return name


def clean_text(value: str | None, *, limit: int, what: str) -> str:
    """
    Trim a label or a description and refuse control characters.

    Args:
        value: The text as typed, or None.
        limit: The longest accepted.
        what: Which field it is, for the message.

    Returns:
        The trimmed text, possibly empty.

    Raises:
        ValidationError: When it is too long or carries control characters.
    """
    text = (value or "").strip()
    if len(text) > limit or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValidationError(
            f"Invalid {what}",
            details=f"Use at most {limit} printable characters.",
            field=what,
        )
    return text


def validate_scope_kind(value: str) -> str:
    """
    Check a scope kind.

    Args:
        value: ``instance``, ``team``, ``project`` or ``environment``.

    Returns:
        The kind, lower-cased.

    Raises:
        ValidationError: When it is none of them.
    """
    kind = (value or "").strip().lower()
    if kind not in SCOPE_KINDS:
        raise ValidationError(
            f"Unknown scope: {value!r}",
            details=f"Use one of: {', '.join(SCOPE_KINDS)}.",
            field="scope",
        )
    return kind


@dataclass(frozen=True)
class CustomRole:
    """
    A role defined by an administrator.

    Attributes:
        name: Its slug, unique, never a built-in role's.
        label: What the console shows.
        description: One sentence on what it is for.
        permissions: The permissions it holds, sorted.
        created_at: When it was made, UNIX seconds.
        created_by: Who made it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    label: str
    description: str
    permissions: tuple[str, ...]
    created_at: float
    created_by: str | None
    updated_at: float
    updated_by: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The role as the API and ``--json`` show it.
        """
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "permissions": list(self.permissions),
            "built_in": False,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


@dataclass(frozen=True)
class Binding:
    """
    One role given to one account at one scope.

    Attributes:
        id: Store id.
        account_id: The account.
        username: Its name.
        scope_kind: One of :data:`SCOPE_KINDS`.
        scope_id: The row the scope is (0 for the server).
        scope: The scope as people write it: ``acme``, ``acme/web``,
            ``acme/web/staging``, or empty for the server. Empty also for a
            scope that no longer exists, which holds nothing.
        role: A built-in team role or a custom role's name.
        created_at: When it was given, UNIX seconds.
        created_by: Who gave it.
    """

    id: int
    account_id: int
    username: str
    scope_kind: str
    scope_id: int
    scope: str
    role: str
    created_at: float
    created_by: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The binding as the API and ``--json`` show it.
        """
        return {
            "id": self.id,
            "account_id": self.account_id,
            "username": self.username,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "scope": self.scope,
            "role": self.role,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }
