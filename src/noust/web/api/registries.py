# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Container registry logins: the accounts Noust pulls and builds private images with.

A client of :class:`noust.managers.registry_logins.RegistryManager`, like
``noust registry``. A login is a name, a kind of registry (Docker Hub, GHCR,
GitLab, Amazon ECR, Azure ACR, Google Artifact Registry or any other host), a
user and a password. The password goes in and never comes out: no response
carries it, it is kept in the secret store, and writing, testing or removing a
login asks for sudo mode. Which applications use which login is
``/api/apps/{domain}/registry-logins``, and it is the instance's to set: a
login is a credential of the server, not of a team.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.core.registry_store import KINDS, RegistryLogin
from noust.core.teams import scoped_store
from noust.managers.registry_logins import RegistryManager
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)

#: Mounted under ``/apps`` for the one route that is about an application.
apps_router = APIRouter(route_class=NoustErrorRoute)


class RegistryLoginResponse(BaseModel):
    """One registry account. Never its password."""

    name: str
    kind: str = Field(description="dockerhub, ghcr, gitlab, ecr, acr, gar or custom")
    server: str = Field(description="The registry host")
    username: str = Field(description="The account (for ECR, the AWS access key id)")
    created_at: str
    created_by: str | None = None
    updated_at: str
    last_tested_at: str | None = None
    last_status: str | None = Field(default=None, description="ok or failed after a test")
    last_error: str | None = Field(
        default=None, description="What the registry said when it failed"
    )
    apps: list[str] = Field(default_factory=list, description="The applications that use it")


class RegistryLoginListResponse(BaseModel):
    """Every account, and the kinds of registry there are."""

    logins: list[RegistryLoginResponse]
    kinds: list[str]


class RegistryLoginRequest(BaseModel):
    """A new account."""

    name: str = Field(max_length=63)
    kind: str = Field(max_length=16)
    username: str = Field(max_length=256)
    password: str = Field(
        description="The registry's password or token; for ECR, the AWS secret access key. "
        "Not returned by any call",
        max_length=8192,
    )
    server: str | None = Field(
        default=None, max_length=300, description="The registry host; the kind's own if it has one"
    )


class RegistryLoginChange(BaseModel):
    """What to change about an account. A field left out keeps its value."""

    kind: str | None = Field(default=None, max_length=16)
    server: str | None = Field(default=None, max_length=300)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=8192)


class RegistryLoginRemoved(BaseModel):
    """An account that is gone."""

    name: str
    apps: list[str] = Field(
        description="The applications that used it and now pull without an account"
    )


class AppRegistryLogins(BaseModel):
    """The accounts an application pulls and builds with."""

    domain: str
    logins: list[str]


class AppRegistryLoginsRequest(BaseModel):
    """The accounts to use; an empty list uses none."""

    logins: list[str] = Field(default_factory=list)


def _response(login: RegistryLogin) -> RegistryLoginResponse:
    """
    Args:
        login: An account.

    Returns:
        Its API shape.
    """
    return RegistryLoginResponse(**login.to_dict())


def _manager() -> RegistryManager:
    """
    Returns:
        The manager, over the process-wide store and runner.
    """
    return RegistryManager()


@router.get("", response_model=RegistryLoginListResponse)
def list_registry_logins(
    session: Annotated[dict, Depends(get_current_session)],
) -> RegistryLoginListResponse:
    """
    List the registry accounts, without their passwords.

    Args:
        session: The authenticated session.

    Returns:
        The accounts and the kinds of registry.
    """
    return RegistryLoginListResponse(
        logins=[_response(login) for login in _manager().accounts.list()], kinds=list(KINDS)
    )


@router.post("", response_model=RegistryLoginResponse, status_code=201)
def add_registry_login(
    body: RegistryLoginRequest, session: Annotated[dict, Depends(require_elevated)]
) -> RegistryLoginResponse:
    """
    Add a registry account.

    Args:
        body: The account and its password.
        session: An elevated session: a credential travels in.

    Returns:
        The account.

    Raises:
        ValidationError: A value is not valid, or the name is taken (400, with
            the field).
    """
    login = _manager().add(
        body.name,
        body.kind,
        body.username,
        body.password,
        server=body.server,
        actor=actor_label(session),
    )
    return _response(login)


def _known(name: str) -> RegistryLogin:
    """
    Read an account or answer 404.

    Args:
        name: Its name.

    Returns:
        The account.

    Raises:
        HTTPException: 404.
    """
    login = _manager().accounts.get(name)
    if login is None:
        raise HTTPException(status_code=404, detail=f"No registry login named {name}")
    return login


@router.get("/{name}", response_model=RegistryLoginResponse)
def get_registry_login(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> RegistryLoginResponse:
    """
    Show one account.

    Args:
        name: Its name.
        session: The authenticated session.

    Returns:
        The account.

    Raises:
        HTTPException: 404.
    """
    return _response(_known(name))


@router.patch("/{name}", response_model=RegistryLoginResponse)
def change_registry_login(
    name: str,
    body: RegistryLoginChange,
    session: Annotated[dict, Depends(require_elevated)],
) -> RegistryLoginResponse:
    """
    Change an account's kind, host, user or password.

    Args:
        name: Its name.
        body: What to change.
        session: An elevated session.

    Returns:
        The account; a change forgets the last test.

    Raises:
        HTTPException: 404.
        ValidationError: A value is not valid (400, with the field).
    """
    _known(name)
    return _response(
        _manager().update(
            name,
            kind=body.kind,
            server=body.server,
            username=body.username,
            password=body.password,
        )
    )


@router.delete("/{name}", response_model=RegistryLoginRemoved)
def remove_registry_login(
    name: str, session: Annotated[dict, Depends(require_elevated)]
) -> RegistryLoginRemoved:
    """
    Remove an account, its password and its assignments.

    Args:
        name: Its name.
        session: An elevated session.

    Returns:
        What was removed and the applications that now pull without it.

    Raises:
        HTTPException: 404.
    """
    _known(name)
    return RegistryLoginRemoved(name=name, apps=_manager().remove(name))


@router.post("/{name}/test", response_model=RegistryLoginResponse)
async def test_registry_login(
    name: str, session: Annotated[dict, Depends(require_elevated)]
) -> RegistryLoginResponse:
    """
    Try an account against its registry.

    It logs in to a configuration directory of its own and removes it, so
    nothing of it stays on the server. The outcome is kept on the account
    (``last_status``, ``last_error``), and a registry that refuses it is a
    result, not an error.

    Args:
        name: Its name.
        session: An elevated session.

    Returns:
        The account, with how the test went.

    Raises:
        HTTPException: 404.
    """
    _known(name)
    return _response(await run_in_threadpool(_manager().test, name))


@apps_router.get("/{domain}/registry-logins", response_model=AppRegistryLogins)
def get_app_registry_logins(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppRegistryLogins:
    """
    Say which registry accounts an application pulls and builds with.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The names of its accounts.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return AppRegistryLogins(
        domain=validated,
        logins=[login.name for login in _manager().accounts.assigned_to(validated)],
    )


@apps_router.put("/{domain}/registry-logins", response_model=AppRegistryLogins)
def set_app_registry_logins(
    domain: str,
    body: AppRegistryLoginsRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> AppRegistryLogins:
    """
    Set the registry accounts an application pulls and builds with.

    Takes effect on the next deployment or update. A login is the server's
    credential, so this is the instance's to set.

    Args:
        domain: Domain of the application.
        body: The accounts' names; an empty list uses none.
        session: An elevated session.

    Returns:
        The names of its accounts now.

    Raises:
        HTTPException: 404 when the application is unknown.
        NoustError: An account is unknown.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    _manager().attach(validated, body.logins, actor=actor_label(session))
    return AppRegistryLogins(domain=validated, logins=list(dict.fromkeys(body.logins)))
