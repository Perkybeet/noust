# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's HTTPS credential for its private repository: see it, set it, remove it.

A client of :class:`noust.managers.source_credentials.GitCredentialManager`,
like ``noust app git-credential``. The token goes in and never comes out: no
response carries it, and writing or removing one asks for sudo mode.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.audit import Actor
from noust.core.teams import scoped_store
from noust.managers.source_credentials import (
    MAX_SECRET,
    MAX_USERNAME,
    CredentialView,
    GitCredentialManager,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label
from noust.web.permissions.principal import actor_of

router = APIRouter(route_class=NoustErrorRoute)


class GitCredentialResponse(BaseModel):
    """
    What an application's repository is reached with over https. Never the token.

    Attributes:
        domain: The application.
        repository: Its repository as ``host/owner/repo``; null when its source
            is not a git repository.
        host: The code host.
        configured: Whether a credential is stored for the repository.
        username: The user it signs in as, when one is stored.
        source_is_https: Whether the application's source is an http(s) URL,
            the only kind a credential signs in to.
    """

    domain: str
    repository: str | None = None
    host: str | None = None
    configured: bool
    username: str | None = None
    source_is_https: bool


class GitCredentialRequest(BaseModel):
    """
    The credential to keep.

    Attributes:
        username: The user to sign in as: for Bitbucket Cloud your username or
            ``x-bitbucket-api-token-auth`` with an API token, ``x-token-auth``
            with a repository or workspace access token.
        secret: The token.
    """

    username: str = Field(min_length=1, max_length=MAX_USERNAME)
    secret: str = Field(min_length=1, max_length=MAX_SECRET)


def _manager(session: Mapping[str, Any]) -> GitCredentialManager:
    """
    Build the manager a request uses.

    Args:
        session: The authenticated session.

    Returns:
        A manager over the process-wide store.
    """
    built = actor_of(session)
    actor = built if isinstance(built, Actor) else Actor.from_label(actor_label(session))
    return GitCredentialManager(actor=actor)


def _response(view: CredentialView) -> GitCredentialResponse:
    """
    Args:
        view: What the manager found.

    Returns:
        Its response.
    """
    return GitCredentialResponse(
        domain=view.domain,
        repository=view.repository,
        host=view.host,
        configured=view.configured,
        username=view.username,
        source_is_https=view.source_is_https,
    )


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/git-credential", response_model=GitCredentialResponse)
def get_git_credential(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> GitCredentialResponse:
    """
    Say whether an application's repository has an https credential. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        Whether one is stored and the user it signs in as; never the token.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _response(_manager(session).view(_known(domain)))


@router.put("/{domain}/git-credential", response_model=GitCredentialResponse)
def put_git_credential(
    domain: str,
    body: GitCredentialRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> GitCredentialResponse:
    """
    Keep the user and token git signs in to the repository with, replacing any.

    A secret travels in, so this needs sudo mode. It is stored in the secret
    store, not in the application's source URL.

    Args:
        domain: Domain of the application.
        body: The user and the token.
        session: The authenticated, elevated session.

    Returns:
        The credential as the API shows it: no token.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The application has no git repository, or a value is
            not acceptable (400).
    """
    return _response(_manager(session).put(_known(domain), body.username, body.secret))


@router.delete("/{domain}/git-credential", response_model=GitCredentialResponse)
def delete_git_credential(
    domain: str, session: Annotated[dict, Depends(require_elevated)]
) -> GitCredentialResponse:
    """
    Forget the https credential of an application's repository.

    Args:
        domain: Domain of the application.
        session: The authenticated, elevated session.

    Returns:
        The credential, which is not configured now.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    manager = _manager(session)
    manager.remove(validated)
    return _response(manager.view(validated))
