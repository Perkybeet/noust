# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploy with ``git push``: the server's setup, an application's repository, who may push.

A client of :mod:`noust.pushdeploy`, like ``noust push``. ``/api/push`` is the server's: whether it
takes pushes, preparing it (a new account and sshd rules: host access and sudo mode), and the
applications that deploy this way. ``/api/apps/{domain}/push`` is one application's: how it stands,
turning it on or off, writing its hook again, and its keys. Turning it on and adding a key give
someone the power to deploy code to the application, so they ask for sudo mode; the modules record
every change in the audit trail.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.pushdeploy.host import PushHost
from noust.pushdeploy.repos import PushInfo, PushRepos
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label

#: ``/api/push``: the server's side.
router = APIRouter(route_class=NoustErrorRoute)

#: ``/api/apps/{domain}/push``: one application's.
apps_router = APIRouter(route_class=NoustErrorRoute)


class PushKeyOut(BaseModel):
    """A key that may push to an application's repository."""

    label: str
    key_type: str
    fingerprint: str = Field(description="SHA256:..., as ssh-keygen -l prints it")


class PushOut(BaseModel):
    """How an application's push deployment stands."""

    domain: str
    enabled: bool
    branch: str | None = Field(default=None, description="The branch a push deploys")
    repository: str | None = Field(default=None, description="The bare repository on the server")
    remote: str | None = Field(
        default=None, description="The remote to add: noust-git@host:domain.git"
    )
    keys: list[PushKeyOut]
    hook_ok: bool = Field(description="Whether the hook is the one Noust writes")
    host_ready: bool = Field(description="Whether the server takes pushes at all")
    problems: list[str] = Field(description="What stops a push from deploying, and the fix")
    last_push_at: str | None = None
    enabled_at: str | None = None


class PushHostOut(BaseModel):
    """Whether this server takes ``git push``."""

    ready: bool
    problems: list[str]
    account: bool = Field(description="Whether the account pushes log in as exists")
    ssh_port: int
    home: str = Field(description="The directory that holds the repositories")
    keys_file: str


class PushOverview(BaseModel):
    """The server's side, and every application that deploys from push."""

    host: PushHostOut
    apps: list[PushOut]


class PushSetupOut(BaseModel):
    """What preparing the server did."""

    account: str
    account_created: bool
    home: str
    sshd_policy: str
    keys_file: str
    changed: bool
    ssh_port: int


class PushEnableRequest(BaseModel):
    """Turn push on for an application."""

    branch: str | None = Field(
        default=None,
        max_length=200,
        description="The branch a push deploys; the pinned one, else main",
    )
    key: str | None = Field(
        default=None, max_length=8192, description="A public key that may push (its .pub line)"
    )
    label: str | None = Field(default=None, max_length=40, description="What to call that key")
    host: str | None = Field(
        default=None, max_length=253, description="The server's name for the remote's URL"
    )


class PushKeyRequest(BaseModel):
    """Let a key push to an application's repository."""

    label: str = Field(..., max_length=40, description="What to call the key: laptop, ci")
    key: str = Field(..., max_length=8192, description="The public key, as its .pub line")


def _out(info: PushInfo) -> PushOut:
    """
    Args:
        info: What the module says.

    Returns:
        The API model.
    """
    return PushOut(**info.to_dict())


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one the principal cannot see.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


def _repos(session: dict[str, Any]) -> PushRepos:
    """
    Args:
        session: The authenticated session.

    Returns:
        The push deployments of this server, acting for the session.
    """
    return PushRepos(actor=actor_label(session))


@router.get("", response_model=PushOverview)
def get_push(session: Annotated[dict, Depends(get_current_session)]) -> PushOverview:
    """
    Say whether this server takes git pushes, and which applications deploy that way.

    Args:
        session: The authenticated session.

    Returns:
        The server's status and the applications.
    """
    repos = PushRepos(actor=actor_label(session))
    return PushOverview(
        host=PushHostOut(**repos.host.status().to_dict()),
        apps=[_out(info) for info in repos.list()],
    )


@router.post("/setup", response_model=PushSetupOut)
def setup_push(session: Annotated[dict, Depends(require_elevated)]) -> PushSetupOut:
    """
    Prepare this server to take git pushes: the account, its directory and its sshd rules.

    Changes how the machine accepts SSH logins, so it needs sudo mode. sshd is checked before it
    is reloaded, and the change is undone when sshd does not apply it.

    Args:
        session: The authenticated, elevated session.

    Returns:
        What was done.
    """
    result = PushHost().setup(actor=actor_label(session))
    return PushSetupOut(**result.to_dict())


@apps_router.get("/{domain}/push", response_model=PushOut)
def get_app_push(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    host: Annotated[str | None, Query(max_length=253, description="The server's name")] = None,
) -> PushOut:
    """
    Show how an application's push deployment stands. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.
        host: The server's name for the remote's URL; the domain when omitted.

    Returns:
        The deployment; ``enabled`` is false for an application that does not deploy from push.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _out(_repos(session).describe(_known(domain), host=host))


@apps_router.put("/{domain}/push", response_model=PushOut)
def put_app_push(
    domain: str,
    body: PushEnableRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> PushOut:
    """
    Make an application deploy from git push, or change the branch it deploys.

    Whoever holds a key added here can deploy code to the application, so this needs sudo mode.

    Args:
        domain: Domain of the application.
        body: The branch, and optionally a first key and its label.
        session: The authenticated, elevated session.

    Returns:
        The deployment.

    Raises:
        HTTPException: 404 when the application is unknown.
        NoustError: The server is not set up, the application builds in place or is a stack,
            or the branch or the key is not valid (400, naming the field).
    """
    validated = _known(domain)
    info = _repos(session).enable(
        validated, branch=body.branch, key=body.key, label=body.label, host=body.host
    )
    return _out(info)


@apps_router.delete("/{domain}/push", response_model=PushOut)
def delete_app_push(
    domain: str,
    session: Annotated[dict, Depends(require_elevated)],
    delete_repository: Annotated[
        bool, Query(description="Also delete the repository, and the code pushed to it")
    ] = False,
) -> PushOut:
    """
    Make an application stop deploying from git push.

    Its source and pinned branch go back to what they were; the keys that could push are removed.

    Args:
        domain: Domain of the application.
        session: The authenticated, elevated session.
        delete_repository: Also delete the repository.

    Returns:
        The deployment, now not enabled.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _out(_repos(session).disable(_known(domain), delete_repository=delete_repository))


@apps_router.post("/{domain}/push/refresh", response_model=PushOut)
def refresh_app_push(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> PushOut:
    """
    Write an application's hook and its settings again.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The deployment.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _out(_repos(session).refresh(_known(domain)))


@apps_router.get("/{domain}/push/keys", response_model=list[PushKeyOut])
def list_app_push_keys(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> list[PushKeyOut]:
    """
    List the keys that may push to an application's repository.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The keys, public halves only.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return [PushKeyOut(**key.to_dict()) for key in _repos(session).keys.list(_known(domain))]


@apps_router.post("/{domain}/push/keys", response_model=list[PushKeyOut], status_code=201)
def add_app_push_key(
    domain: str,
    body: PushKeyRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> list[PushKeyOut]:
    """
    Let a key push to an application's repository.

    Args:
        domain: Domain of the application.
        body: The key and what to call it.
        session: The authenticated, elevated session.

    Returns:
        The application's keys.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The key or its label is not valid, or the key is already used (400).
    """
    keys = _repos(session).add_key(_known(domain), body.label, body.key)
    return [PushKeyOut(**key.to_dict()) for key in keys]


@apps_router.delete("/{domain}/push/keys/{label}", response_model=list[PushKeyOut])
def remove_app_push_key(
    domain: str,
    label: str,
    session: Annotated[dict, Depends(require_elevated)],
) -> list[PushKeyOut]:
    """
    Take a key's permission to push away.

    Args:
        domain: Domain of the application.
        label: The key's label or its fingerprint.
        session: The authenticated, elevated session.

    Returns:
        The keys that are left.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: No key of the application has that label (400).
    """
    return [
        PushKeyOut(**key.to_dict()) for key in _repos(session).remove_key(_known(domain), label)
    ]
