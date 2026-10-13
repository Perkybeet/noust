# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Previewing a manifest from the console: ``POST /api/manifest/plan`` (J-15).

The same engine the command line uses (:mod:`noust.manifest`), run on the
server as a client of its own API: every read it makes is a request to the
console's routes carrying the caller's own token, so what the plan can see is
what the caller can see, and an application of another team is simply absent
from it. A preview changes nothing and carries no value of any variable.

Applying is deliberately not here. A manifest lives in git and is applied by
whoever owns the repository's pipeline (``noust --context prod manifest
apply``), with a token that can be revoked and a log of who applied what; a
button in a browser would make the console a second place to keep the truth.
Variables that a manifest reads from the environment or from a file are refused
here: a server does not read its own environment on behalf of a document it was
sent.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.client.api import refusal
from noust.client.errors import RemoteError
from noust.manifest import RefusingResolver, parse, plan
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute
from noust.web.auth import bearer_token, get_client_ip, is_secure_request
from noust.web.mcp.gateway import call_api

router = APIRouter(route_class=NoustErrorRoute)


class GatewayTransport:
    """
    The server's own API, called as the person asking, from a worker thread.

    It only reads: a preview changes nothing, and a signed-in browser session
    (which has no bearer token) can be forwarded for a read but not for a write.

    Args:
        request: The request being served, whose application is called back.
        token: The caller's bearer token, or None for a browser session.
        loop: The event loop the application runs on, which the worker thread
            hands each call back to.
    """

    #: The longest one read of the API may take.
    TIMEOUT = 60.0

    def __init__(
        self, request: Request, token: str | None, loop: asyncio.AbstractEventLoop
    ) -> None:
        self._request = request
        self._token = token
        self._loop = loop

    def _call(self, method: str, path: str, body: Any = None, **query: Any) -> Any:
        """
        Args:
            method: The HTTP method.
            path: A route below ``/api``.
            body: A JSON body, or None.
            **query: Query parameters.

        Returns:
            The decoded answer.

        Raises:
            RemoteError: When the route refuses the call.
        """
        request = self._request
        pending = asyncio.run_coroutine_threadsafe(
            call_api(
                request.app,
                method,
                "/api" + (path if path.startswith("/") else "/" + path),
                query=query,
                body=body,
                token=self._token,
                cookie=None if self._token else request.headers.get("cookie"),
                client_host=get_client_ip(request),
                host=request.headers.get("host") or "localhost",
                scheme="https" if is_secure_request(request) else "http",
            ),
            self._loop,
        )
        response = pending.result(timeout=self.TIMEOUT)
        decoded = response.json()
        if response.status >= 400:
            raise refusal(response.status, decoded)
        return decoded

    def get(self, path: str, **query: Any) -> Any:
        """
        Args:
            path: A route.
            **query: Query parameters.

        Returns:
            The decoded body.
        """
        return self._call("GET", path, **query)

    def post(self, path: str, body: Any = None, **query: Any) -> Any:
        """
        Args:
            path: A route.
            body: The JSON body.
            **query: Query parameters.

        Raises:
            RuntimeError: Always: a preview only reads.
        """
        raise RuntimeError("a preview reads and writes nothing")

    def put(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A route.
            body: The JSON body.

        Raises:
            RuntimeError: Always: a preview only reads.
        """
        raise RuntimeError("a preview reads and writes nothing")

    def patch(self, path: str, body: Any) -> Any:
        """
        Args:
            path: A route.
            body: The JSON body.

        Raises:
            RuntimeError: Always: a preview only reads.
        """
        raise RuntimeError("a preview reads and writes nothing")


class PlanRequest(BaseModel):
    """Request to preview a manifest."""

    manifest: str = Field(..., max_length=1024 * 1024, description="The manifest, as YAML or JSON")


class ActionOut(BaseModel):
    """
    One change a manifest asks for.

    Attributes:
        kind: ``project.create``, ``environment.create``, ``app.create``, ``app.move``,
            ``app.branch``, ``app.limits``, ``app.health``, ``app.domain``, ``app.database``,
            ``app.env`` or ``app.backups``.
        target: What it is done to.
        summary: What it does, with no value of any variable.
        elevated: Applying it needs sudo mode.
        unverified: Whether it is needed could not be checked.
    """

    kind: str
    target: str
    summary: str
    elevated: bool = False
    unverified: bool = False


class PlanResponse(BaseModel):
    """
    What applying a manifest would change.

    Attributes:
        empty: Nothing would.
        actions: The changes, in the order they would run.
        warnings: Differences that are not reconciled, and what could not be checked.
        unmanaged: Applications the caller sees that the manifest does not name.
        counts: How many actions of each kind.
    """

    empty: bool
    actions: list[ActionOut]
    warnings: list[str]
    unmanaged: list[str]
    counts: dict[str, int]


@router.post("/plan", response_model=PlanResponse)
async def preview(
    request: Request,
    body: PlanRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> PlanResponse:
    """
    Compare a manifest with the server, as the caller, and change nothing.

    Args:
        request: The request.
        body: The manifest.
        session: The authenticated session.

    Returns:
        The plan.

    Raises:
        ManifestError: 400, naming the field of the manifest that is not valid.
        RemoteError: When the caller may not read something the plan needs.
    """
    manifest = parse(body.manifest)
    transport = GatewayTransport(request, bearer_token(request), asyncio.get_running_loop())
    try:
        # The plan makes several reads, one after another, each a request to this very
        # application: they run in a worker thread so the loop stays free to answer them.
        result = await run_in_threadpool(
            plan, manifest, transport, RefusingResolver(), compare_env=False
        )
    except RemoteError as exc:
        # The route the plan read refused the caller: say so in its own words and status.
        raise HTTPException(
            status_code=exc.status or 502,
            detail={
                "error": exc.code,
                "detail": exc.message,
                "hint": exc.details or None,
                "fields": None,
            },
        ) from exc
    return PlanResponse(**result.to_dict())
