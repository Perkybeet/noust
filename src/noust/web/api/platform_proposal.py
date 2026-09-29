# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The API shape of another platform's configuration, read from a repository.

What :func:`noust.deployers.importers.propose` answers, for the inspection the
new-app wizard runs (``POST /api/apps/inspect``, its ``platform_proposal``):
a module of its own so the inspection's response can use it without importing
the export endpoints, which import the applications router.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from noust.deployers.importers import Proposal


class ProposedEnvResponse(BaseModel):
    """One environment variable the platform's configuration declares."""

    name: str
    value: str | None = Field(
        default=None, description="The default it gives; null for a secret or none"
    )
    secret: bool = False
    generated: bool = Field(
        default=False, description="The platform generates it; Noust generates one in its place"
    )
    required: bool = Field(
        default=False, description="A value must be given: the configuration has none"
    )
    note: str | None = Field(default=None, description="Where the value came from there")


class PlatformProposalResponse(BaseModel):
    """What another platform's configuration says, in Noust's terms."""

    platform: str = Field(description="vercel, railway, render or heroku")
    files: list[str] = Field(default_factory=list, description="The files read")
    app_type: str | None = Field(default=None, description="Null: detection decides")
    install_command: str | None = None
    build_command: str | None = None
    start_command: str | None = None
    output_directory: str | None = None
    port: int | None = None
    health_path: str | None = None
    health_timeout: int | None = None
    env: list[ProposedEnvResponse] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list, description="Engines it needs")
    domains: list[str] = Field(default_factory=list)
    persistent_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list, description="What has no equivalent, and what to do instead"
    )


def platform_proposal_response(proposal: Proposal | None) -> PlatformProposalResponse | None:
    """
    Translate a proposal to its API model.

    Args:
        proposal: The proposal, or None when the repository carries none.

    Returns:
        The model, or None.
    """
    if proposal is None:
        return None
    return PlatformProposalResponse(
        platform=proposal.platform,
        files=list(proposal.files),
        app_type=proposal.app_type,
        install_command=proposal.install_command,
        build_command=proposal.build_command,
        start_command=proposal.start_command,
        output_directory=proposal.output_directory,
        port=proposal.port,
        health_path=proposal.health_path,
        health_timeout=proposal.health_timeout,
        env=[
            ProposedEnvResponse(
                name=variable.name,
                value=variable.value,
                secret=variable.secret,
                generated=variable.generated,
                required=variable.required,
                note=variable.note,
            )
            for variable in proposal.env
        ],
        databases=list(proposal.databases),
        domains=list(proposal.domains),
        persistent_paths=list(proposal.persistent_paths),
        warnings=list(proposal.warnings),
    )
