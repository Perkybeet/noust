# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/users`` and a database's access: accounts, profiles, passwords.

Accounts get one of three profiles on a database - owner, read-write,
read-only - computed from what the engine says they can do. Read-only is the
engine's own ``SELECT``-only grant on that database, never PostgreSQL's
``pg_read_all_data``, which reads every database in the cluster.

The engine's own accounts and the read-only console's ``wasm_ro_`` accounts
are listed and marked ``internal``; the service refuses to change them,
whatever the request says.

A password is handed back once, when an account is created. A rotation is a
job (it restarts the applications that sign in as the account), and its new
password is only shown through the sudo-mode reveal endpoint.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.web.api.auth import get_current_session
from noust.web.api.databases import jobs
from noust.web.api.databases.common import ActionResponse, queue, service
from noust.web.api.databases.databases import AccessEntryResponse
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobType

router = APIRouter(route_class=NoustErrorRoute)


class UserInfoResponse(BaseModel):
    """
    One database user.

    Attributes:
        internal: The engine's own account or Noust's read-only console's.
    """

    username: str
    engine: str
    host: str = "localhost"
    databases: list[str] = Field(default_factory=list)
    privileges: list[str] = Field(default_factory=list)
    internal: bool = False


class UserListResponse(BaseModel):
    """Response for listing users."""

    users: list[UserInfoResponse]
    total: int


class CreateUserRequest(BaseModel):
    """
    Request to create a database user.

    Attributes:
        database: A database to give it access to.
        profile: ``owner``, ``read_write`` or ``read_only`` on that database;
            the engine's full privileges when a database is given without one.
    """

    username: str = Field(..., description="Username")
    engine: str = Field(..., description="Database engine")
    password: str | None = Field(default=None, description="Password, generated when omitted")
    database: str | None = Field(default=None, description="Grant access to this database")
    host: str = Field(default="localhost", description="Host restriction")
    profile: str | None = Field(default=None, description="owner, read_write or read_only")


class CreateUserResponse(BaseModel):
    """
    Response after creating a user.

    The password is returned here, once. Noust keeps it in its secret store so
    the account can be linked to an application and shown again with sudo
    mode; it is absent from every listing.
    """

    username: str
    password: str
    message: str


class GrantPrivilegesRequest(BaseModel):
    """Request to grant or revoke privileges."""

    username: str = Field(..., description="Username")
    database: str = Field(..., description="Database name")
    engine: str = Field(..., description="Database engine")
    privileges: list[str] | None = Field(default=None, description="Privileges to act on")
    host: str = Field(default="localhost", description="Host restriction")


class AccessListResponse(BaseModel):
    """Who can reach a database."""

    access: list[AccessEntryResponse]


class SetProfileRequest(BaseModel):
    """
    Request to give an account one profile on a database.

    Attributes:
        profile: ``owner``, ``read_write`` or ``read_only``.
    """

    profile: str = Field(..., description="owner, read_write or read_only")
    host: str = Field(default="localhost", description="Host restriction")


class RotatePasswordRequest(BaseModel):
    """
    Request to rotate an account's password.

    Attributes:
        propagate: Give the new password to the applications that sign in as
            the account, restarting each behind its health gate; anything
            that does not come back undoes the whole rotation.
    """

    propagate: bool = Field(default=True, description="Rewrite and restart the applications")
    host: str = Field(default="localhost", description="Host restriction")


class PasswordResponse(BaseModel):
    """An account's password, shown once to an elevated session."""

    username: str
    password: str


@router.post("/users", response_model=CreateUserResponse)
def create_user(
    request: CreateUserRequest, session: Annotated[dict, Depends(get_current_session)]
) -> CreateUserResponse:
    """
    Create a database user, optionally with a profile on one database.

    Args:
        request: The create request.
        session: The authenticated session.

    Returns:
        The user and its password, shown this once.
    """
    user, password = service(session).create_user(
        request.engine,
        request.username,
        password=request.password,
        host=request.host,
        database=request.database,
        profile=request.profile,
    )
    return CreateUserResponse(
        username=user.username, password=password, message=f"User '{user.username}' created"
    )


@router.post("/users/grant", response_model=ActionResponse)
def grant_privileges(
    request: GrantPrivilegesRequest, session: Annotated[dict, Depends(get_current_session)]
) -> ActionResponse:
    """
    Grant privileges on a database.

    Declared before ``/users/{engine}`` so the literal path wins.

    Args:
        request: The grant request.
        session: The authenticated session.

    Returns:
        The action outcome.
    """
    service(session).grant(
        request.engine,
        request.username,
        request.database,
        privileges=request.privileges,
        host=request.host,
    )
    return ActionResponse(
        success=True, message=f"Privileges granted to '{request.username}' on '{request.database}'"
    )


@router.post("/users/revoke", response_model=ActionResponse)
def revoke_privileges(
    request: GrantPrivilegesRequest, session: Annotated[dict, Depends(get_current_session)]
) -> ActionResponse:
    """
    Revoke privileges on a database.

    Args:
        request: The revoke request.
        session: The authenticated session.

    Returns:
        The action outcome.
    """
    service(session).revoke(
        request.engine,
        request.username,
        request.database,
        privileges=request.privileges,
        host=request.host,
    )
    return ActionResponse(
        success=True,
        message=f"Privileges revoked from '{request.username}' on '{request.database}'",
    )


@router.get("/users/{engine}", response_model=UserListResponse)
def list_users(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> UserListResponse:
    """
    List the users of an engine, internal ones marked.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        The users. Passwords are never part of this response.
    """
    users = service(session).list_users(engine)
    return UserListResponse(
        users=[
            UserInfoResponse(
                username=user.username,
                engine=user.engine,
                host=user.host,
                databases=user.databases,
                privileges=user.privileges,
                internal=bool(user.extra.get("internal")),
            )
            for user in users
        ],
        total=len(users),
    )


@router.delete("/users/{engine}/{username}", response_model=ActionResponse)
def delete_user(
    engine: str,
    username: str,
    session: Annotated[dict, Depends(require_elevated)],
    host: Annotated[str, Query()] = "localhost",
) -> ActionResponse:
    """
    Delete a database user, with sudo mode.

    Refused for an internal account and for one an application signs in as.

    Args:
        engine: Engine name.
        username: User to delete.
        host: Host restriction the user was created with.
        session: The authenticated, elevated session.

    Returns:
        The action outcome.
    """
    service(session).drop_user(engine, username, host=host)
    return ActionResponse(success=True, message=f"User '{username}' deleted")


@router.post(
    "/users/{engine}/{username}/password", response_model=JobAcceptedResponse, status_code=202
)
def rotate_password(
    engine: str,
    username: str,
    request: RotatePasswordRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue a password rotation, with sudo mode.

    A job, because each application that signs in as the account restarts
    behind its gate. The new password is kept in Noust's secret store; read
    it with ``POST .../password/reveal``.

    Args:
        engine: Engine name.
        username: The account; ``default`` for Redis's ``requirepass``.
        request: Whether to give it to the applications.
        session: The authenticated, elevated session.

    Returns:
        The queued job.
    """
    manager = service(session).running(engine)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Rotate the password of {username}",
        description=f"Rotating the {manager.DISPLAY_NAME} password of {username}",
        func=jobs.rotate_job,
        kwargs={
            "engine": manager.ENGINE_NAME,
            "username": username,
            "host": request.host,
            "propagate": request.propagate,
        },
        metadata={"engine": manager.ENGINE_NAME, "username": username},
        message=f"Rotating the password of {username}",
    )


@router.post("/users/{engine}/{username}/password/reveal", response_model=PasswordResponse)
def reveal_password(
    engine: str, username: str, session: Annotated[dict, Depends(require_elevated)]
) -> PasswordResponse:
    """
    Show the password Noust keeps for an account, with sudo mode, audited.

    Args:
        engine: Engine name.
        username: The account.
        session: The authenticated, elevated session.

    Returns:
        The password.
    """
    return PasswordResponse(
        username=username, password=service(session).reveal_password(engine, username)
    )


@router.get("/databases/{engine}/{name}/access", response_model=AccessListResponse)
def list_access(
    engine: str, name: str, session: Annotated[dict, Depends(get_current_session)]
) -> AccessListResponse:
    """
    List who can reach a database, with each account's profile.

    Args:
        engine: Engine name.
        name: Database name.
        session: The authenticated session.

    Returns:
        The accounts.
    """
    return AccessListResponse(
        access=[
            AccessEntryResponse(**entry.to_dict())
            for entry in service(session).access(engine, name)
        ]
    )


@router.put("/databases/{engine}/{name}/access/{username}", response_model=ActionResponse)
def set_profile(
    engine: str,
    name: str,
    username: str,
    request: SetProfileRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> ActionResponse:
    """
    Give an account one access profile on a database.

    Args:
        engine: Engine name.
        name: Database name.
        username: The account.
        request: The profile.
        session: The authenticated session.

    Returns:
        The action outcome.
    """
    service(session).set_profile(engine, name, username, request.profile, host=request.host)
    return ActionResponse(
        success=True, message=f"'{username}' now has the {request.profile} profile on '{name}'"
    )
