# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
GitLab connections: tokens or OAuth, and the project and branch picker.

A client of :mod:`noust.integrations.gitlab.service`, like ``noust gitlab``. Adding a connection,
registering an OAuth application and signing in change what this server trusts, so they need sudo
mode; listing connections, projects, groups and branches do not. The sign-in as the console runs it:

1. ``POST /oauth/authorize`` with the connection's name and host: the URL to send the browser to.
2. GitLab sends the browser to ``<console>/integrations/gitlab/callback?code=&state=``.
3. The console calls ``POST /oauth/callback`` with both.

Tokens and the application's secret are never in an answer.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.integrations.gitlab.service import GitLabService
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class ConnectionOut(BaseModel):
    """A GitLab connection."""

    name: str
    host: str
    kind: str = Field(description="token or oauth")
    username: str | None = None
    created_at: str
    created_by: str | None = None
    updated_at: str
    last_checked_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None


class OAuthApplicationOut(BaseModel):
    """An OAuth application registered in GitLab, without its secret."""

    host: str
    client_id: str
    created_at: str
    created_by: str | None = None


class GitLabStatusOut(BaseModel):
    """What this server has of GitLab."""

    connections: list[ConnectionOut]
    oauth_apps: list[OAuthApplicationOut]
    redirect_uri: str | None = Field(
        description="Where GitLab sends the browser back; null when the console has no public URL"
    )


class ConnectRequest(BaseModel):
    """A connection that signs in with an access token."""

    name: str
    host: str
    token: str = Field(
        description="A personal, group or project access token (read_api, read_repository)"
    )


class OAuthApplicationRequest(BaseModel):
    """The OAuth application registered in GitLab."""

    host: str
    client_id: str
    client_secret: str


class OAuthStartRequest(BaseModel):
    """Begin signing in with GitLab."""

    name: str = Field(description="What to call the connection")
    host: str


class OAuthStartOut(BaseModel):
    """Where to send the browser."""

    authorize_url: str
    state: str


class OAuthCallbackRequest(BaseModel):
    """What GitLab's redirect carried."""

    code: str
    state: str


class ProjectOut(BaseModel):
    """A project the connection's user can read."""

    id: int
    path: str | None = None
    default_branch: str | None = None
    clone_url: str | None = None
    web_url: str | None = None


class ProjectListOut(BaseModel):
    """Projects, most recently active first."""

    items: list[ProjectOut]
    total: int


class GroupOut(BaseModel):
    """A group the connection's user belongs to."""

    id: int
    full_path: str
    name: str | None = None


class GroupListOut(BaseModel):
    """Groups, by path."""

    items: list[GroupOut]
    total: int


class BranchOut(BaseModel):
    """A branch of a project."""

    name: str
    default: bool
    commit: str | None = None


class BranchListOut(BaseModel):
    """A project's branches."""

    items: list[BranchOut]
    total: int


class RemovalOut(BaseModel):
    """Whether there was something to remove."""

    removed: bool


def _service() -> GitLabService:
    """
    Returns:
        The service over this server's store and secrets.
    """
    return GitLabService()


def _status() -> GitLabStatusOut:
    """
    Returns:
        What this server has of GitLab.
    """
    service = _service()
    try:
        redirect: str | None = service.redirect_uri()
    except ValidationError:
        redirect = None
    return GitLabStatusOut(
        connections=[ConnectionOut(**item.to_dict()) for item in service.connections.list()],
        oauth_apps=[
            OAuthApplicationOut(**item.to_dict())
            for item in service.connections.oauth_applications()
        ],
        redirect_uri=redirect,
    )


@router.get("/gitlab", response_model=GitLabStatusOut)
def gitlab_status(session: Annotated[dict, Depends(get_current_session)]) -> GitLabStatusOut:
    """
    List the GitLab connections and OAuth applications.

    Args:
        session: The authenticated session.

    Returns:
        The connections, the applications and the redirect URI an application must allow.
    """
    return _status()


@router.post("/gitlab/connections", response_model=ConnectionOut, status_code=201)
def gitlab_connect(
    body: ConnectRequest, session: Annotated[dict, Depends(require_elevated)]
) -> ConnectionOut:
    """
    Add a connection that signs in with an access token.

    GitLab is asked who the token belongs to before it is kept; the token is never returned.

    Args:
        body: The name, the host and the token.
        session: An elevated session.

    Returns:
        The connection.

    Raises:
        ValidationError: The name or host is not valid, or the name is taken.
        IntegrationError: GitLab could not be reached or refused the token.
    """
    connection = _service().connect_with_token(
        body.name, body.host, body.token, actor=actor_label(session)
    )
    return ConnectionOut(**connection.to_dict())


@router.post("/gitlab/connections/{name}/test", response_model=ConnectionOut)
def gitlab_test(name: str, session: Annotated[dict, Depends(get_current_session)]) -> ConnectionOut:
    """
    Ask GitLab who a connection is, and record whether it still works.

    Args:
        name: The connection.
        session: The authenticated session.

    Returns:
        The connection, with its new status.
    """
    return ConnectionOut(**_service().check(name).to_dict())


@router.delete("/gitlab/connections/{name}", response_model=RemovalOut)
def gitlab_remove(name: str, session: Annotated[dict, Depends(require_elevated)]) -> RemovalOut:
    """
    Forget a connection and its credentials.

    Args:
        name: The connection.
        session: An elevated session.

    Returns:
        Whether there was one.
    """
    return RemovalOut(removed=_service().remove(name))


@router.get("/gitlab/connections/{name}/groups", response_model=GroupListOut)
def gitlab_groups(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> GroupListOut:
    """
    List the groups a connection's user belongs to.

    Args:
        name: The connection.
        session: The authenticated session.

    Returns:
        The groups, by path.
    """
    items = [GroupOut(**item) for item in _service().groups(name)]
    return GroupListOut(items=items, total=len(items))


@router.get("/gitlab/connections/{name}/projects", response_model=ProjectListOut)
def gitlab_projects(
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    group: str | None = None,
    search: str | None = None,
) -> ProjectListOut:
    """
    List the projects a connection's user can read.

    Args:
        name: The connection.
        session: The authenticated session.
        group: A group's full path: only its projects and its subgroups'.
        search: Part of a project's name.

    Returns:
        The projects, most recently active first.
    """
    items = [ProjectOut(**item) for item in _service().projects(name, group=group, search=search)]
    return ProjectListOut(items=items, total=len(items))


@router.get("/gitlab/connections/{name}/projects/{project}/branches", response_model=BranchListOut)
def gitlab_branches(
    name: str, project: str, session: Annotated[dict, Depends(get_current_session)]
) -> BranchListOut:
    """
    List a project's branches.

    Args:
        name: The connection.
        project: The project's numeric id.
        session: The authenticated session.

    Returns:
        The branches.

    Raises:
        HTTPException: 400 when ``project`` is not a number.
    """
    if not project.isdigit():
        raise HTTPException(status_code=400, detail="The project is its numeric id")
    items = [BranchOut(**item) for item in _service().branches(name, project)]
    return BranchListOut(items=items, total=len(items))


@router.put("/gitlab/oauth-apps", response_model=OAuthApplicationOut)
def gitlab_oauth_app(
    body: OAuthApplicationRequest, session: Annotated[dict, Depends(require_elevated)]
) -> OAuthApplicationOut:
    """
    Record the OAuth application registered in GitLab for a host.

    Args:
        body: The host, the application id and its secret.
        session: An elevated session.

    Returns:
        The application, without its secret.
    """
    application = _service().save_oauth_application(
        body.host, body.client_id, body.client_secret, actor=actor_label(session)
    )
    return OAuthApplicationOut(**application.to_dict())


@router.delete("/gitlab/oauth-apps/{host}", response_model=RemovalOut)
def gitlab_oauth_app_remove(
    host: str, session: Annotated[dict, Depends(require_elevated)]
) -> RemovalOut:
    """
    Forget the OAuth application of a host.

    Args:
        host: The GitLab host.
        session: An elevated session.

    Returns:
        Whether there was one.
    """
    return RemovalOut(removed=_service().remove_oauth_application(host))


@router.post("/gitlab/oauth/authorize", response_model=OAuthStartOut)
def gitlab_oauth_start(
    body: OAuthStartRequest, session: Annotated[dict, Depends(require_elevated)]
) -> OAuthStartOut:
    """
    Begin signing in to GitLab.

    Args:
        body: What to call the connection, and the host.
        session: An elevated session.

    Returns:
        The address to send the browser to, and the state GitLab sends back.

    Raises:
        ValidationError: The host has no OAuth application, the name is taken, or the console
            has no public address.
    """
    started: dict[str, Any] = _service().oauth_start(body.name, body.host)
    return OAuthStartOut(**started)


@router.post("/gitlab/oauth/callback", response_model=ConnectionOut, status_code=201)
def gitlab_oauth_callback(
    body: OAuthCallbackRequest, session: Annotated[dict, Depends(require_elevated)]
) -> ConnectionOut:
    """
    Finish signing in: keep the tokens GitLab hands back as a connection.

    Args:
        body: The ``code`` and ``state`` of GitLab's redirect.
        session: An elevated session.

    Returns:
        The connection.

    Raises:
        ValidationError: The state was not issued here, expired or was used.
        IntegrationError: GitLab refused the code.
    """
    return ConnectionOut(**_service().oauth_finish(body.code, body.state).to_dict())
