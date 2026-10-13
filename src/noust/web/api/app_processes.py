# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The process types an application's code declares, and which of them a deployment uses.

A client of :mod:`noust.deployers.helpers.process_report`, like ``noust app process-types``.
Read-only: the process types are the repository's (``processes:`` in ``noust.yaml``, or a
``Procfile``), and what overrides them is the build block and the deploy hooks, each with its
own route.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.deployers.helpers.process_report import describe_processes
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain

router = APIRouter(route_class=NoustErrorRoute)


class ProcessEntry(BaseModel):
    """One process type."""

    name: str
    argv: list[str]
    command: str = Field(description="The argument vector as one quoted line")
    source: str = Field(description="noust.yaml or Procfile")
    started_by_noust: bool = Field(description="Whether a deployment starts or runs it itself")
    in_effect: bool = Field(description="Whether the next deployment uses it")


class ProcessesResponse(BaseModel):
    """An application's process types."""

    domain: str
    source: str = Field(description="Where they are declared: noust.yaml, Procfile or none")
    problem: str = Field(description="Why a Procfile that exists is not used; empty otherwise")
    procfile_honoured: bool = Field(description="Whether this application reads its Procfile")
    processes: list[ProcessEntry]
    notes: list[str] = Field(description="Why a declared process is not in effect, and what to do")


@router.get("/{domain}/process-types", response_model=ProcessesResponse)
def get_processes(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ProcessesResponse:
    """
    Show the process types an application declares and which ones a deployment uses.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The process types, read from the code that runs now.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return ProcessesResponse(**describe_processes(validated).to_dict())
