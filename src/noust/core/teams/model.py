# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a team, a project and an environment are, and the rules between the names.

Pure data and validation, with no store behind it, so the CLI, the API and the
store itself agree on one definition. What each team role may *do* is the
console's business and lives in :mod:`noust.web.permissions.team_roles`; this
module only knows the names and which accounts may join a team at all.

The shape is Coolify's (team, project, environment, resources), with one
difference the 4.0 design sets down: the instance roles of
:mod:`noust.core.accounts.model` stay what they were, and some of them see
every team. ``admin`` does (it is the instance's administrator), and so do
``security`` and ``auditor``, which are instance-only roles and never join a
team: an auditor that belonged to one team would be an auditor of one team.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import NoustError, ValidationError

# The instance roles, spelled out: importing noust.core.accounts from here would
# start the accounts manager, which imports the store, which imports this
# package. A test holds each to noust.core.accounts.model.ROLES.
_INSTANCE_VIEWER = "viewer"
_INSTANCE_OPERATOR = "operator"
_INSTANCE_ADMIN = "admin"
_INSTANCE_SECURITY = "security"
_INSTANCE_AUDITOR = "auditor"

#: Team roles, weakest first. They map onto the permission sets of the
#: instance roles (``noust.web.permissions.team_roles``): a viewer reads, an
#: operator also runs what exists, an admin also creates and deletes inside
#: the team, an owner also governs the team's members, projects and environments.
TEAM_ROLE_VIEWER = "viewer"
TEAM_ROLE_OPERATOR = "operator"
TEAM_ROLE_ADMIN = "admin"
TEAM_ROLE_OWNER = "owner"
TEAM_ROLES: tuple[str, ...] = (
    TEAM_ROLE_VIEWER,
    TEAM_ROLE_OPERATOR,
    TEAM_ROLE_ADMIN,
    TEAM_ROLE_OWNER,
)

#: Instance roles that see every team's resources, whatever they belong to.
INSTANCE_WIDE_ROLES: frozenset[str] = frozenset(
    {_INSTANCE_ADMIN, _INSTANCE_SECURITY, _INSTANCE_AUDITOR}
)

#: Instance roles that never join a team (I-10). ``security`` governs who may
#: run the infrastructure and ``auditor`` checks everybody: both are only
#: meaningful across teams, and a membership would let a team's owner give a
#: team-sized view of what is meant to be the whole of it.
INSTANCE_ONLY_ROLES: frozenset[str] = frozenset({_INSTANCE_SECURITY, _INSTANCE_AUDITOR})

#: The team role a migrated account is given, by its instance role: the one
#: whose permission set is the same, so an upgrade changes nobody's powers.
TEAM_ROLE_FOR_INSTANCE_ROLE: dict[str, str] = {
    _INSTANCE_VIEWER: TEAM_ROLE_VIEWER,
    _INSTANCE_OPERATOR: TEAM_ROLE_OPERATOR,
    _INSTANCE_ADMIN: TEAM_ROLE_ADMIN,
}

#: What an existing server is migrated into, and where new rows go when nobody
#: says otherwise. ``production`` is the environment every project starts with.
DEFAULT_SLUG = "default"
DEFAULT_NAME = "Default"
PRODUCTION_SLUG = "production"
PRODUCTION_NAME = "Production"

#: The tables whose rows belong to a project and an environment, in the order a
#: move updates them.
OWNED_TABLES: tuple[str, ...] = ("apps", "sites", "databases", "backup_schedules")

#: A slug: lower-case letters, digits and dashes, 1 to 40 characters, never
#: starting or ending with a dash. It is typed in commands and URLs.
SLUG_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")

#: Longest display name kept; it is shown, never parsed.
MAX_NAME = 80


class TeamError(NoustError):
    """A team, project or environment operation was refused; the message says why."""


class TeamNotFoundError(TeamError):
    """No team, project, environment or member has the name asked for."""


class TeamConflictError(TeamError):
    """The operation clashes with what exists: a name taken, a team that still holds resources."""


class ResourceNotFoundError(NoustError):
    """
    A resource is not there, or not there for the principal that asked.

    One error for both on purpose (I-04): a team member who asks for another
    team's application is answered exactly as if it did not exist, so the
    answer never reveals that it does. The API maps it to 404, never 403.
    """


def slugify(value: str) -> str:
    """
    Turn a display name into a slug.

    Args:
        value: A name such as ``Acme Corp``.

    Returns:
        ``acme-corp``; empty when nothing usable is left, which the caller
        refuses by asking for a slug.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug[:40].strip("-")


def validate_slug(value: str, *, what: str = "name") -> str:
    """
    Check a slug.

    Args:
        value: The slug as typed.
        what: What it names, for the message.

    Returns:
        The slug, trimmed and lower-cased.

    Raises:
        ValidationError: When it is not a slug.
    """
    slug = (value or "").strip().lower()
    if not SLUG_PATTERN.fullmatch(slug):
        raise ValidationError(
            f"Invalid {what}: {value!r}",
            details=(
                "Use 1 to 40 lower-case letters, digits or '-', starting and ending with a "
                "letter or digit, such as 'acme' or 'shop-eu'."
            ),
            field=what,
        )
    return slug


def clean_name(value: str | None, *, fallback: str) -> str:
    """
    Trim a display name and refuse control characters.

    Args:
        value: The name as typed, or None.
        fallback: What to use when it is empty.

    Returns:
        The trimmed name.

    Raises:
        ValidationError: When it is too long or carries control characters.
    """
    name = (value or "").strip() or fallback
    if len(name) > MAX_NAME or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValidationError(
            "Invalid name",
            details=f"Use at most {MAX_NAME} printable characters.",
            field="name",
        )
    return name


def validate_team_role(value: str) -> str:
    """
    Check a team role.

    Args:
        value: The role as typed.

    Returns:
        The role, lower-cased.

    Raises:
        ValidationError: When it is not one of :data:`TEAM_ROLES`.
    """
    role = (value or "").strip().lower()
    if role not in TEAM_ROLES:
        raise ValidationError(
            f"Unknown team role: {value!r}",
            details=f"Use one of: {', '.join(TEAM_ROLES)}.",
            field="role",
        )
    return role


def refuse_instance_only_member(username: str, instance_role: str) -> None:
    """
    Refuse to put an instance-only account in a team.

    Args:
        username: The account, for the message.
        instance_role: Its instance role.

    Raises:
        ValidationError: When the account is a security officer or an auditor.
    """
    if instance_role in INSTANCE_ONLY_ROLES:
        raise ValidationError(
            f"{username!r} is a {instance_role} account, and those never join a team",
            details=(
                f"The {instance_role} role works across every team of the server. Invite the "
                "person's other account (one account, one role), or change this account's role."
            ),
            field="username",
        )


@dataclass(frozen=True)
class Team:
    """
    A group of people and the projects they own.

    Attributes:
        id: Store id.
        slug: Unique, typed in commands and URLs.
        name: Display name.
        created_at: When it was created, UNIX seconds.
        created_by: Who created it.
    """

    id: int
    slug: str
    name: str
    created_at: float
    created_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The team as the API and ``--json`` show it.
        """
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class Project:
    """
    A group of applications, databases and sites inside a team.

    Attributes:
        id: Store id.
        team_id: The team it belongs to.
        team: That team's slug.
        slug: Unique inside the team.
        name: Display name.
        created_at: When it was created, UNIX seconds.
        created_by: Who created it.
    """

    id: int
    team_id: int
    team: str
    slug: str
    name: str
    created_at: float
    created_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The project as the API and ``--json`` show it.
        """
        return {
            "id": self.id,
            "team": self.team,
            "team_id": self.team_id,
            "slug": self.slug,
            "name": self.name,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class ProjectEnvironment:
    """
    A stage of a project: production, staging...

    Attributes:
        id: Store id.
        project_id: The project it belongs to.
        project: That project's slug.
        team: The slug of the project's team.
        slug: Unique inside the project.
        name: Display name.
        created_at: When it was created, UNIX seconds.
        created_by: Who created it.
    """

    id: int
    project_id: int
    project: str
    team: str
    slug: str
    name: str
    created_at: float
    created_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The environment as the API and ``--json`` show it.
        """
        return {
            "id": self.id,
            "project": self.project,
            "project_id": self.project_id,
            "team": self.team,
            "slug": self.slug,
            "name": self.name,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class Membership:
    """
    One account's place in one team.

    Attributes:
        team_id: The team.
        team: Its slug.
        account_id: The account.
        username: Its name.
        role: One of :data:`TEAM_ROLES`.
        instance_role: The account's instance role, for display.
        created_at: When it joined, UNIX seconds.
        created_by: Who added it.
    """

    team_id: int
    team: str
    account_id: int
    username: str
    role: str
    instance_role: str
    created_at: float
    created_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The membership as the API and ``--json`` show it.
        """
        return {
            "team": self.team,
            "team_id": self.team_id,
            "account_id": self.account_id,
            "username": self.username,
            "role": self.role,
            "instance_role": self.instance_role,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class Target:
    """
    Where a resource lives: its team, project and environment.

    Attributes:
        team_id: The team.
        project_id: The project.
        environment_id: The environment.
    """

    team_id: int
    project_id: int
    environment_id: int


def now() -> float:
    """
    Returns:
        The current time, UNIX seconds; one place so tests can replace it.
    """
    return time.time()
