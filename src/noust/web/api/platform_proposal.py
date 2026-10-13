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


class ProposedCronResponse(BaseModel):
    """A scheduled task of the other platform."""

    name: str
    schedule: str = Field(description="A cron expression")
    command: str


class PlatformProposalResponse(BaseModel):
    """What another platform's configuration says, in Noust's terms."""

    platform: str = Field(description="vercel, railway, render, heroku, coolify or dokploy")
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
    name: str | None = Field(default=None, description="What the platform calls it (API imports)")
    source: str | None = Field(
        default=None, description="The repository it was pulled from, when the platform says"
    )
    branch: str | None = None
    base_directory: str | None = Field(
        default=None, description="The subdirectory of the repository the project lives in"
    )
    watch_paths: list[str] = Field(default_factory=list)
    image: str | None = Field(default=None, description="A registry image it ran instead")
    memory_max_mb: int | None = None
    cpu_quota_percent: int | None = None
    cron: list[ProposedCronResponse] = Field(default_factory=list)
    equivalent: str | None = Field(
        default=None,
        description="The noust command that creates what a proposal cannot deploy "
        "(an image, a Dockerfile, a Compose file)",
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
        name=proposal.name,
        source=proposal.source,
        branch=proposal.branch,
        base_directory=proposal.base_directory,
        watch_paths=list(proposal.watch_paths),
        image=proposal.image,
        memory_max_mb=proposal.memory_max_mb,
        cpu_quota_percent=proposal.cpu_quota_percent,
        cron=[
            ProposedCronResponse(name=job.name, schedule=job.schedule, command=job.command)
            for job in proposal.cron
        ],
        equivalent=proposal.equivalent,
    )
