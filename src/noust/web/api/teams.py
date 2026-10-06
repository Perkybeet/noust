# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Teams, their members, projects and environments, over HTTP.

A client of :class:`~noust.core.teams.TeamStore`, like ``noust team``, ``noust
project`` and ``noust env``: the same rules (slugs, the last owner, what a team
that still holds resources may not do) are the store's, not repeated here. This
module only holds the principal to its scope (:mod:`noust.web.api.scope`
already did, for the routes addressed to one team) and records what changed.

A team is a boundary of permissions and of organisation, not of execution: the
applications of two teams still share this server's kernel, which the console
says wherever it lets someone create one.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from noust.core.audit import record as audit_record
from noust.core.exceptions import ValidationError
from noust.core.teams import (
    Project,
    Target,
    Team,
    TeamNotFoundError,
    TeamStore,
    current_scope,
)
from noust.core.teams.model import TEAM_ROLES
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.api.scope import ensure_permission_in_team
from noust.web.auth import actor_label
from noust.web.permissions import DESCRIPTIONS, Permission
from noust.web.permissions.principal import actor_of
from noust.web.permissions.team_roles import TEAM_ROLE_PERMISSIONS
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)

#: Applications, databases, sites and backup schedules are the four things a
#: project holds; the console shows them as counts.
Counts = dict[str, int]


class TeamOut(BaseModel):
    """
    A team, as the principal asking sees it.

    Attributes:
        id: Store id.
        slug: Unique, typed in commands and URLs.
        name: Display name.
        role: The principal's role in it, or None when it sees it without
            belonging to it (an administrator, an auditor).
        created_at: When it was created, UNIX seconds.
        created_by: Who created it.
    """

    id: int
    slug: str
    name: str
    role: str | None = None
    created_at: float
    created_by: str | None = None


class TeamDetailOut(TeamOut):
    """
    A team with what it holds.

    Attributes:
        resources: Applications, sites, databases and backup schedules in its
            projects, by kind.
        members: How many people belong to it.
        projects: How many projects it has.
    """

    resources: Counts = Field(default_factory=dict)
    members: int = 0
    projects: int = 0


class TeamListResponse(BaseModel):
    """The teams a principal sees."""

    teams: list[TeamOut]
    total: int


class MineResponse(BaseModel):
    """
    What the principal asking belongs to.

    Attributes:
        teams: Every team it sees, with its role in each (None where it sees
            a team without belonging to it).
        active: The slug of the team it works in, when it chose one.
        sees_all: Whether no team is hidden from it.
    """

    teams: list[TeamOut]
    active: str | None = None
    sees_all: bool


class CreateTeamRequest(BaseModel):
    """Request to create a team."""

    name: str = Field(..., description="Display name")
    slug: str | None = Field(default=None, description="Its slug; made from the name when omitted")


class RenameTeamRequest(BaseModel):
    """Request to rename a team, a project or an environment."""

    name: str = Field(..., description="The new display name")


class ActiveTeamRequest(BaseModel):
    """Request to choose the team one works in."""

    team: str | None = Field(
        default=None, description="The team's slug, or null to stop preferring one"
    )


class MemberOut(BaseModel):
    """
    One person's place in a team.

    Attributes:
        username: The account.
        role: Its role in the team.
        instance_role: Its role on the server, which is the floor of what it may do.
        created_at: When it joined, UNIX seconds.
        created_by: Who added it.
    """

    username: str
    role: str
    instance_role: str
    created_at: float
    created_by: str | None = None


class MemberListResponse(BaseModel):
    """A team's members."""

    team: str
    members: list[MemberOut]


class AddMemberRequest(BaseModel):
    """Request to add an account to a team, or change its role there."""

    username: str = Field(..., description="The account's name")
    role: str = Field(..., description="viewer, operator, admin or owner")


class SetRoleRequest(BaseModel):
    """Request to change a member's role in a team."""

    role: str = Field(..., description="viewer, operator, admin or owner")


class ProjectOut(BaseModel):
    """
    A project.

    Attributes:
        id: Store id.
        team: The team's slug.
        slug: Unique inside the team.
        name: Display name.
        environments: Its environments' slugs.
        created_at: When it was created, UNIX seconds.
        created_by: Who created it.
    """

    id: int
    team: str
    slug: str
    name: str
    environments: list[str] = Field(default_factory=list)
    created_at: float
    created_by: str | None = None


class ProjectListResponse(BaseModel):
    """The projects a principal sees."""

    projects: list[ProjectOut]
    total: int


class CreateProjectRequest(BaseModel):
    """Request to create a project."""

    name: str = Field(..., description="Display name")
    slug: str | None = Field(default=None, description="Its slug; made from the name when omitted")


class EnvironmentOut(BaseModel):
    """
    An environment.

    Attributes:
        id: Store id.
        team: The team's slug.
        project: The project's slug.
        slug: Unique inside the project.
        name: Display name.
        created_at: When it was created, UNIX seconds.
        created_by: Who created it.
    """

    id: int
    team: str
    project: str
    slug: str
    name: str
    created_at: float
    created_by: str | None = None


class EnvironmentListResponse(BaseModel):
    """The environments a principal sees."""

    environments: list[EnvironmentOut]
    total: int


class CreateEnvironmentRequest(BaseModel):
    """Request to create an environment."""

    name: str = Field(..., description="Display name")
    slug: str | None = Field(default=None, description="Its slug; made from the name when omitted")


class ActionResponse(BaseModel):
    """The answer to a change that has nothing else to say."""

    message: str


class MoveAppRequest(BaseModel):
    """
    Request to move an application to another project or environment.

    Whatever is left out stays as the application has it: moving to another
    environment of the same project names the environment only; moving to
    another project names the project (and the team, when it is another one).
    """

    team: str | None = Field(default=None, description="The team, when it is another one")
    project: str | None = Field(default=None, description="The project to move to")
    environment: str | None = Field(default=None, description="The environment to move to")


class MoveAppResponse(BaseModel):
    """
    What moved.

    Attributes:
        domain: The application.
        team: Where it is now.
        project: Where it is now.
        environment: Where it is now.
        moved: Rows moved, by kind: the application, its sites, its databases and
            its backup schedule move together.
    """

    domain: str
    team: str
    project: str
    environment: str
    moved: Counts


def _store() -> TeamStore:
    """
    Returns:
        The team store over the process's store.
    """
    return TeamStore()


def _record(session: dict[str, Any], event: str, target: str, **details: Any) -> None:
    """
    Put a change on the audit trail, as whoever made it.

    Args:
        session: The authenticated payload.
        event: The catalog name, such as ``team.create``.
        target: What it was done to.
        **details: Context, never a credential.
    """
    audit_record(event, actor=actor_of(session), target=target, details=details or None)


def _actor(session: dict[str, Any]) -> str:
    """
    Args:
        session: The authenticated payload.

    Returns:
        The label stored as ``created_by``.
    """
    return actor_label(session)


def _team_out(team: Team, roles: dict[int, str]) -> TeamOut:
    """
    Args:
        team: The team.
        roles: The principal's role in each team it belongs to.

    Returns:
        The team as the principal sees it.
    """
    return TeamOut(
        id=team.id,
        slug=team.slug,
        name=team.name,
        role=roles.get(team.id),
        created_at=team.created_at,
        created_by=team.created_by,
    )


def _roles() -> dict[int, str]:
    """
    Returns:
        The asking principal's role in each team it belongs to.
    """
    return dict(current_scope().team_roles)


def _project_out(project: Project, environments: dict[int, list[str]]) -> ProjectOut:
    """
    Args:
        project: The project.
        environments: Environment slugs by project id.

    Returns:
        The project.
    """
    return ProjectOut(
        id=project.id,
        team=project.team,
        slug=project.slug,
        name=project.name,
        environments=environments.get(project.id, []),
        created_at=project.created_at,
        created_by=project.created_by,
    )


def _environments_by_project() -> dict[int, list[str]]:
    """
    Returns:
        Every environment's slug, grouped by project id.
    """
    grouped: dict[int, list[str]] = {}
    for environment in _store().list_environments():
        grouped.setdefault(environment.project_id, []).append(environment.slug)
    return grouped


# --------------------------------------------------------------------- teams


@router.get("", response_model=TeamListResponse)
def list_teams(session: Annotated[dict, Depends(get_current_session)]) -> TeamListResponse:
    """
    List the teams the principal sees.

    Args:
        session: The authenticated session.

    Returns:
        Every team for a principal that sees them all, otherwise its own.
    """
    roles = _roles()
    teams = [_team_out(team, roles) for team in _store().list_teams(current_scope())]
    return TeamListResponse(teams=teams, total=len(teams))


@router.post("", response_model=TeamOut, status_code=201)
def create_team(
    body: CreateTeamRequest, session: Annotated[dict, Depends(get_current_session)]
) -> TeamOut:
    """
    Create a team, with a default project and its production environment.

    Only a principal that sees every team may: a team is created by whoever
    administers the server, then handed to its owners.

    Args:
        body: The team's name and, optionally, its slug.
        session: The authenticated session.

    Returns:
        The team.

    Raises:
        TeamConflictError: 409 when the slug is taken.
    """
    team = _store().create_team(body.name, slug=body.slug, created_by=_actor(session))
    _record(session, "team.create", f"team:{team.slug}", name=team.name)
    return _team_out(team, _roles())


@router.get("/mine", response_model=MineResponse)
def my_teams(session: Annotated[dict, Depends(get_current_session)]) -> MineResponse:
    """
    Say which teams the principal belongs to and which one it works in.

    Args:
        session: The authenticated session.

    Returns:
        Its teams with its role in each, its active team and whether it sees
        them all.
    """
    scope = current_scope()
    store = _store()
    roles = _roles()
    active = store.active_team(scope.account_id)
    return MineResponse(
        teams=[_team_out(team, roles) for team in store.list_teams(scope)],
        active=active.slug if active is not None and scope.sees_team(active.id) else None,
        sees_all=scope.sees_all,
    )


@router.put("/active", response_model=MineResponse)
def choose_active_team(
    body: ActiveTeamRequest, session: Annotated[dict, Depends(get_current_session)]
) -> MineResponse:
    """
    Choose the team the principal works in.

    It is where a new application, database or site goes when the request does
    not name a project, and what the console opens on. It narrows nothing: the
    principal still sees every team it belongs to.

    Args:
        body: The team's slug, or null to stop preferring one.
        session: The authenticated session.

    Returns:
        What :func:`my_teams` answers, after the change.

    Raises:
        ValidationError: 400 when the principal is not an account, which has
            nowhere to remember it.
        TeamNotFoundError: 404 when the team does not exist or is not one the
            principal sees.
    """
    scope = current_scope()
    store = _store()
    if scope.account_id is None:
        raise ValidationError(
            "Only an account has a team to work in",
            details="Sign in with an account, or name the team in each request.",
            field="team",
        )
    if body.team is None:
        store.set_active_team(scope.account_id, None)
    else:
        team = store.get_team(body.team)
        if team is None or not scope.sees_team(team.id):
            raise TeamNotFoundError(
                f"No team named {body.team!r}",
                details="List the teams with GET /api/teams/mine.",
                field="team",
            )
        store.set_active_team(scope.account_id, team.id)
    return my_teams(session)


class TeamRoleOut(BaseModel):
    """
    One role of a team and what it holds there.

    Attributes:
        role: ``viewer``, ``operator``, ``admin`` or ``owner``.
        permissions: The permissions it holds on the team's resources.
    """

    role: str
    permissions: list[str]


class TeamRolesResponse(BaseModel):
    """What each role of a team holds, weakest first."""

    roles: list[TeamRoleOut]
    descriptions: dict[str, str]


@router.get("/roles", response_model=TeamRolesResponse)
def list_team_roles(session: Annotated[dict, Depends(get_current_session)]) -> TeamRolesResponse:
    """
    Say what each role of a team may do.

    The instance role is the floor (what the account holds everywhere in the
    teams it sees); a team role raises it inside that team.

    Args:
        session: The authenticated session.

    Returns:
        The four roles with their permissions, and what each permission means.
    """
    held = {name for permissions in TEAM_ROLE_PERMISSIONS.values() for name in permissions}
    return TeamRolesResponse(
        roles=[
            TeamRoleOut(role=role, permissions=sorted(TEAM_ROLE_PERMISSIONS[role]))
            for role in TEAM_ROLES
        ],
        descriptions={name: DESCRIPTIONS.get(name, "") for name in sorted(held)},
    )


@router.get("/{team}", response_model=TeamDetailOut)
def get_team(team: str, session: Annotated[dict, Depends(get_current_session)]) -> TeamDetailOut:
    """
    Read a team, with what it holds.

    Args:
        team: The team's slug.
        session: The authenticated session.

    Returns:
        The team.

    Raises:
        TeamNotFoundError: 404 when there is no such team.
    """
    store = _store()
    found = store.require_team(team)
    return TeamDetailOut(
        **dump_model(_team_out(found, _roles())),
        resources=store.resource_counts(team_id=found.id),
        members=len(store.list_members(found.slug)),
        projects=len(store.list_projects(team=found.slug)),
    )


@router.patch("/{team}", response_model=TeamOut)
def rename_team(
    team: str,
    body: RenameTeamRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> TeamOut:
    """
    Rename a team.

    Args:
        team: The team's slug.
        body: The new name.
        session: The authenticated session.

    Returns:
        The team.

    Raises:
        TeamNotFoundError: 404 when there is no such team.
    """
    renamed = _store().rename_team(team, body.name)
    _record(session, "team.update", f"team:{renamed.slug}", name=renamed.name)
    return _team_out(renamed, _roles())


@router.delete("/{team}", response_model=ActionResponse)
def delete_team(team: str, session: Annotated[dict, Depends(require_elevated)]) -> ActionResponse:
    """
    Delete a team that holds nothing.

    Args:
        team: The team's slug.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        TeamConflictError: 409 for the default team, or one that still holds
            applications, sites, databases or backup schedules.
    """
    removed = _store().delete_team(team)
    _record(session, "team.delete", f"team:{removed.slug}")
    return ActionResponse(message=f"Deleted the team {removed.slug}")


# ------------------------------------------------------------------- members


def _member_out(member: Any) -> MemberOut:
    return MemberOut(
        username=member.username,
        role=member.role,
        instance_role=member.instance_role,
        created_at=member.created_at,
        created_by=member.created_by,
    )


@router.get("/{team}/members", response_model=MemberListResponse)
def list_members(
    team: str, session: Annotated[dict, Depends(get_current_session)]
) -> MemberListResponse:
    """
    List a team's members.

    Args:
        team: The team's slug.
        session: The authenticated session.

    Returns:
        The members, strongest role first.
    """
    return MemberListResponse(
        team=team, members=[_member_out(m) for m in _store().list_members(team)]
    )


@router.post("/{team}/members", response_model=MemberOut, status_code=201)
def add_member(
    team: str,
    body: AddMemberRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> MemberOut:
    """
    Add an account to a team, or change its role there.

    Only accounts that exist: a new person is invited by a security officer
    (``POST /api/auth/invitations`` with ``team``), because creating a
    credential is the security role's decision, not a team's.

    Args:
        team: The team's slug.
        body: The account and its role.
        session: The authenticated, elevated session.

    Returns:
        The membership.

    Raises:
        TeamNotFoundError: 404 when the account does not exist.
        ValidationError: 400 for an unknown role, or a security or auditor
            account, which never joins a team.
        TeamConflictError: 409 when it would demote the team's last owner.
    """
    member = _store().add_member(team, body.username, body.role, created_by=_actor(session))
    _record(
        session,
        "team.member.add",
        f"team:{member.team}",
        account=member.username,
        role=member.role,
    )
    return _member_out(member)


@router.patch("/{team}/members/{username}", response_model=MemberOut)
def set_member_role(
    team: str,
    username: str,
    body: SetRoleRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> MemberOut:
    """
    Change a member's role in a team.

    Args:
        team: The team's slug.
        username: The member.
        body: The new role.
        session: The authenticated, elevated session.

    Returns:
        The membership.

    Raises:
        TeamNotFoundError: 404 when the account is not a member.
        TeamConflictError: 409 when it would demote the team's last owner.
    """
    store = _store()
    store.member(team, username)
    member = store.add_member(team, username, body.role, created_by=_actor(session))
    _record(
        session,
        "team.member.add",
        f"team:{member.team}",
        account=member.username,
        role=member.role,
    )
    return _member_out(member)


@router.delete("/{team}/members/{username}", response_model=ActionResponse)
def remove_member(
    team: str, username: str, session: Annotated[dict, Depends(require_elevated)]
) -> ActionResponse:
    """
    Take an account out of a team.

    Args:
        team: The team's slug.
        username: The member.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        TeamNotFoundError: 404 when the account is not a member.
        TeamConflictError: 409 when it is the team's last owner.
    """
    member = _store().remove_member(team, username)
    _record(session, "team.member.remove", f"team:{member.team}", account=member.username)
    return ActionResponse(message=f"{member.username} left the team {member.team}")


# ------------------------------------------------------- projects, environments


@router.get("/{team}/projects", response_model=ProjectListResponse)
def list_team_projects(
    team: str, session: Annotated[dict, Depends(get_current_session)]
) -> ProjectListResponse:
    """
    List a team's projects.

    Args:
        team: The team's slug.
        session: The authenticated session.

    Returns:
        Its projects, with their environments.
    """
    store = _store()
    store.require_team(team)
    environments = _environments_by_project()
    projects = [_project_out(p, environments) for p in store.list_projects(team=team)]
    return ProjectListResponse(projects=projects, total=len(projects))


@router.post("/{team}/projects", response_model=ProjectOut, status_code=201)
def create_project(
    team: str,
    body: CreateProjectRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> ProjectOut:
    """
    Create a project in a team, with its production environment.

    Args:
        team: The team's slug.
        body: The project's name and, optionally, its slug.
        session: The authenticated session.

    Returns:
        The project.

    Raises:
        TeamConflictError: 409 when the team already has it.
    """
    project = _store().create_project(team, body.name, slug=body.slug, created_by=_actor(session))
    _record(session, "project.create", f"project:{project.team}/{project.slug}")
    return _project_out(project, _environments_by_project())


@router.patch("/{team}/projects/{project}", response_model=ProjectOut)
def rename_project(
    team: str,
    project: str,
    body: RenameTeamRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> ProjectOut:
    """
    Rename a project. Its slug, which commands and links name it by, stays.

    Args:
        team: The team's slug.
        project: The project's slug.
        body: The new name.
        session: The authenticated session.

    Returns:
        The project.

    Raises:
        TeamNotFoundError: 404 when there is no such project.
    """
    renamed = _store().rename_project(team, project, body.name)
    _record(
        session,
        "project.update",
        f"project:{renamed.team}/{renamed.slug}",
        name=renamed.name,
    )
    return _project_out(renamed, _environments_by_project())


@router.delete("/{team}/projects/{project}", response_model=ActionResponse)
def delete_project(
    team: str, project: str, session: Annotated[dict, Depends(require_elevated)]
) -> ActionResponse:
    """
    Delete a project that holds nothing.

    Args:
        team: The team's slug.
        project: The project's slug.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        TeamConflictError: 409 for the default project, or one that still
            holds resources.
    """
    removed = _store().delete_project(team, project)
    _record(session, "project.delete", f"project:{removed.team}/{removed.slug}")
    return ActionResponse(message=f"Deleted the project {removed.team}/{removed.slug}")


def _environment_out(environment: Any) -> EnvironmentOut:
    return EnvironmentOut(
        id=environment.id,
        team=environment.team,
        project=environment.project,
        slug=environment.slug,
        name=environment.name,
        created_at=environment.created_at,
        created_by=environment.created_by,
    )


@router.get("/{team}/projects/{project}/environments", response_model=EnvironmentListResponse)
def list_project_environments(
    team: str, project: str, session: Annotated[dict, Depends(get_current_session)]
) -> EnvironmentListResponse:
    """
    List a project's environments.

    Args:
        team: The team's slug.
        project: The project's slug.
        session: The authenticated session.

    Returns:
        Its environments.
    """
    store = _store()
    store.require_project(team, project)
    environments = [
        _environment_out(e) for e in store.list_environments(team=team, project=project)
    ]
    return EnvironmentListResponse(environments=environments, total=len(environments))


@router.post(
    "/{team}/projects/{project}/environments", response_model=EnvironmentOut, status_code=201
)
def create_environment(
    team: str,
    project: str,
    body: CreateEnvironmentRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> EnvironmentOut:
    """
    Create an environment in a project.

    Args:
        team: The team's slug.
        project: The project's slug.
        body: The environment's name and, optionally, its slug.
        session: The authenticated session.

    Returns:
        The environment.

    Raises:
        TeamConflictError: 409 when the project already has it.
    """
    environment = _store().create_environment(
        team, project, body.name, slug=body.slug, created_by=_actor(session)
    )
    _record(
        session,
        "environment.create",
        f"environment:{environment.team}/{environment.project}/{environment.slug}",
    )
    return _environment_out(environment)


@router.patch(
    "/{team}/projects/{project}/environments/{environment}", response_model=EnvironmentOut
)
def rename_environment(
    team: str,
    project: str,
    environment: str,
    body: RenameTeamRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> EnvironmentOut:
    """
    Rename an environment. Its slug, which commands and links name it by, stays.

    Args:
        team: The team's slug.
        project: The project's slug.
        environment: The environment's slug.
        body: The new name.
        session: The authenticated session.

    Returns:
        The environment.

    Raises:
        TeamNotFoundError: 404 when there is no such environment.
    """
    renamed = _store().rename_environment(team, project, environment, body.name)
    _record(
        session,
        "environment.update",
        f"environment:{renamed.team}/{renamed.project}/{renamed.slug}",
        name=renamed.name,
    )
    return _environment_out(renamed)


@router.delete(
    "/{team}/projects/{project}/environments/{environment}", response_model=ActionResponse
)
def delete_environment(
    team: str,
    project: str,
    environment: str,
    session: Annotated[dict, Depends(require_elevated)],
) -> ActionResponse:
    """
    Delete an environment that holds nothing.

    Args:
        team: The team's slug.
        project: The project's slug.
        environment: The environment's slug.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        TeamConflictError: 409 for the last environment of a project, or one
            that still holds resources.
    """
    removed = _store().delete_environment(team, project, environment)
    _record(
        session,
        "environment.delete",
        f"environment:{removed.team}/{removed.project}/{removed.slug}",
    )
    return ActionResponse(
        message=f"Deleted the environment {removed.team}/{removed.project}/{removed.slug}"
    )


# ------------------------------------------------------------- flat listings

flat_router = APIRouter(route_class=NoustErrorRoute)


@flat_router.get("/projects", response_model=ProjectListResponse)
def list_projects(
    session: Annotated[dict, Depends(get_current_session)],
    team: Annotated[str | None, Query(description="Only this team's projects")] = None,
) -> ProjectListResponse:
    """
    List every project the principal sees, whatever its team.

    Args:
        session: The authenticated session.
        team: Only this team's projects.

    Returns:
        The projects, with their environments.
    """
    environments = _environments_by_project()
    projects = [
        _project_out(p, environments) for p in _store().list_projects(current_scope(), team=team)
    ]
    return ProjectListResponse(projects=projects, total=len(projects))


@flat_router.get("/environments", response_model=EnvironmentListResponse)
def list_environments(
    session: Annotated[dict, Depends(get_current_session)],
    team: Annotated[str | None, Query(description="Only this team's environments")] = None,
    project: Annotated[str | None, Query(description="Only this project's environments")] = None,
) -> EnvironmentListResponse:
    """
    List every environment the principal sees, whatever its project.

    Args:
        session: The authenticated session.
        team: Only this team's environments.
        project: Only the projects with this slug.

    Returns:
        The environments.
    """
    environments = [
        _environment_out(e)
        for e in _store().list_environments(current_scope(), team=team, project=project)
    ]
    return EnvironmentListResponse(environments=environments, total=len(environments))


# ------------------------------------------------------------ moving an app

move_router = APIRouter(route_class=NoustErrorRoute)


@move_router.post("/{domain}/move", response_model=MoveAppResponse)
def move_app(
    domain: str,
    body: MoveAppRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> MoveAppResponse:
    """
    Move an application, with its sites, databases and backup schedule, to
    another project or environment.

    The application must be one the principal sees and may manage, and so must
    be the place it moves to: a principal cannot hand an application to a team
    it has no part in.

    Args:
        domain: The application's domain.
        body: Where to; what is left out stays as it is.
        request: The incoming request, for the permission check's audit record.
        session: The authenticated, elevated session.

    Returns:
        Where it is now, and what moved.

    Raises:
        ValidationError: 400 when the request names no destination.
        ResourceNotFoundError: 404 when the application is not one the principal
            sees.
        TeamNotFoundError: 404 when the team, project or environment does not
            exist or is not one the principal sees.
        PermissionDenied: 403 when the principal may not manage applications
            in the team it moves to.
    """

    def authorize(target: Target) -> None:
        ensure_permission_in_team(request, session, target.team_id, Permission.APPS_MANAGE)

    result = _store().move_app_to(
        current_scope(),
        domain.strip().lower(),
        team=body.team,
        project=body.project,
        environment=body.environment,
        authorize=authorize,
    )
    _record(
        session,
        "apps.move",
        f"app:{result.domain}",
        to=f"{result.team}/{result.project}/{result.environment}",
        moved=result.moved,
    )
    return MoveAppResponse(
        domain=result.domain,
        team=result.team,
        project=result.project,
        environment=result.environment,
        moved=result.moved,
    )
