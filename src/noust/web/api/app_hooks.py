# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's deploy hooks: see them, set the operator's own, clear them.

A client of :mod:`noust.deployers.helpers.hooks`, like ``noust app hooks``:
``GET`` says which hooks the next deployment runs and where they come from
(the operator's, which win whole, or the running code's ``noust.yaml``);
``PUT`` stores the operator's document, validated first, and ``DELETE``
removes it so the repository's apply again. A hook is code that runs with the
application's identity and its secrets, so writing one is root-equivalent and
asks for sudo mode; the module records every change in the audit trail.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.store import get_store
from noust.deployers.helpers.hooks import AppHooks, Hook, describe_hooks, set_operator_hooks
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)

#: The largest document accepted; a declaration of hooks is a few lines.
MAX_DOCUMENT = 64 * 1024


class HookModel(BaseModel):
    """One hook, as declared."""

    run: list[str] = Field(description="The program and its arguments; never given to a shell")
    service: str | None = Field(
        default=None, description="Docker Compose only: the service whose new image it runs in"
    )
    workdir: str | None = Field(default=None, description="Where it runs")
    timeout: int = Field(description="Seconds it may take, 1-3600")
    migrates: bool = Field(description="It changes the database's schema")


class AppHooksResponse(BaseModel):
    """The hooks an application's next deployment runs, and where they come from."""

    domain: str
    source: str = Field(
        description="operator: the operator's own, replacing noust.yaml; repo: the "
        "noust.yaml of the code that runs now; none"
    )
    pre_deploy: list[HookModel] = Field(description="Run in order before the new version serves")
    post_deploy: list[HookModel] = Field(description="Run in order once it passed its health gate")
    document: str | None = Field(
        default=None, description="The operator's document as written, when there is one"
    )
    repository_error: str | None = Field(
        default=None,
        description="Why the running code's noust.yaml is not valid, when it is not; the next "
        "deployment fails with it",
    )


class AppHooksRequest(BaseModel):
    """The operator's hooks: a YAML document with ``hooks:`` and its phases."""

    document: str = Field(
        min_length=1,
        max_length=MAX_DOCUMENT,
        description="The shape of a noust.yaml, holding only 'hooks'",
    )


def _hook(hook: Hook) -> HookModel:
    """
    Args:
        hook: A hook.

    Returns:
        Its API model.
    """
    return HookModel(
        run=list(hook.run),
        service=hook.service,
        workdir=hook.workdir,
        timeout=hook.timeout,
        migrates=hook.migrates,
    )


def _response(described: AppHooks) -> AppHooksResponse:
    """
    Args:
        described: What applies.

    Returns:
        The API model.
    """
    return AppHooksResponse(
        domain=described.domain,
        source=described.hooks.source,
        pre_deploy=[_hook(hook) for hook in described.hooks.pre_deploy],
        post_deploy=[_hook(hook) for hook in described.hooks.post_deploy],
        document=described.document,
        repository_error=described.repository_error,
    )


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    if get_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/hooks", response_model=AppHooksResponse)
def get_app_hooks(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppHooksResponse:
    """
    Show the hooks the next deployment of an application runs. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The hooks and where they come from.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _response(describe_hooks(_known(domain)))


@router.put("/{domain}/hooks", response_model=AppHooksResponse)
def put_app_hooks(
    domain: str,
    body: AppHooksRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> AppHooksResponse:
    """
    Set the operator's hooks, replacing the repository's noust.yaml whole.

    The document is validated before anything is stored; a refusal names the
    field (``hooks.pre_deploy[0].timeout``). A hook runs with the
    application's identity and secrets, so this needs sudo mode.

    Args:
        domain: Domain of the application.
        body: The document.
        session: The authenticated, elevated session.

    Returns:
        The hooks now in force.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The document is not valid, or the application's
            type has no hooks (400, with the field).
    """
    validated = _known(domain)
    set_operator_hooks(validated, body.document, actor=actor_label(session))
    return _response(describe_hooks(validated))


@router.delete("/{domain}/hooks", response_model=AppHooksResponse)
def delete_app_hooks(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppHooksResponse:
    """
    Remove the operator's hooks, so the repository's noust.yaml applies again.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The hooks now in force.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    set_operator_hooks(validated, None, actor=actor_label(session))
    return _response(describe_hooks(validated))
