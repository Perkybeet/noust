# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``POST /api/apps/inspect`` answers: the models of the response and how an inspection becomes one.

Kept beside :mod:`noust.web.api.apps`, whose router serves the route, so that what the wizard is
told about a source (its verdict, what to do about it, each with its code for the console to
translate) is read in one place and the router, which is past the size a module should have, does
not grow with it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from noust.deployers.inspect import SourceInspection
from noust.web.api.deps import CodedText
from noust.web.api.platform_proposal import (
    PlatformProposalResponse,
    platform_proposal_response,
)


class EnvKeyResponse(BaseModel):
    """One environment variable discovered in a repository's .env.example."""

    name: str
    default: str | None = None
    secret: bool
    required: bool


class SourceInspectionResponse(BaseModel):
    """
    What a repository is, discovered before anything is deployed from it.

    Attributes:
        app_type: The application type the new-app wizard would deploy as;
            the first entry of ``detected_types``.
        detected_types: Every registered application type that recognised
            the repository, most specific first (registry priority order).
        package_manager: The Node package manager the repository's lock file
            implies, or None when it is not a Node project.
        install_command: Argv the chosen deployer would run to install
            dependencies. Empty when the type has none.
        build_command: Argv the chosen deployer would run to build the
            project. Empty when there is nothing to build.
        start_command: Shell command the chosen deployer would run as the
            service's ``ExecStart``. Empty for a static site.
        default_port: Port the chosen deployer uses when none is requested.
        env_keys: Environment variables discovered from ``.env.example``.
        branch: The branch inspected, or the checkout's current branch when
            none was requested.
        commit: Short commit hash of the checkout, empty when the source is
            not a Git repository.
        compatible: Whether this server can deploy it as ``app_type`` as it
            is: false when a program the type needs is missing.
        verdict: What Noust found, in a sentence.
        suggestion: What to do before deploying, when there is something.
        verdict_coded: ``verdict`` as a code and its values, for the console to translate.
        suggestion_coded: ``suggestion`` as a code and its values, when it is a sentence of
            its own (what to install); null when there is none.
    """

    app_type: str
    detected_types: list[str]
    package_manager: str | None
    install_command: list[str]
    build_command: list[str]
    start_command: str
    default_port: int
    env_keys: list[EnvKeyResponse]
    branch: str
    commit: str
    compatible: bool | None = Field(
        default=None, description="Whether this server can deploy it as app_type as it is"
    )
    verdict: str | None = Field(default=None, description="What Noust found, in a sentence")
    suggestion: str | None = Field(
        default=None, description="What to do before deploying, when there is something"
    )
    verdict_coded: CodedText | None = Field(
        default=None, description="The verdict as a code and its values"
    )
    suggestion_coded: CodedText | None = Field(
        default=None, description="The suggestion as a code and its values, when it has one"
    )
    platform_proposal: PlatformProposalResponse | None = Field(
        default=None,
        description="What the repository's Vercel, Railway, Render or Heroku configuration "
        "proposes, for the wizard to prefill; null when it carries none",
    )


def _coded(sentence: object) -> CodedText | None:
    """
    Args:
        sentence: A sentence of the server's catalog, or None.

    Returns:
        Its code and values for the response, or None.
    """
    to_dict = getattr(sentence, "to_dict", None)
    return CodedText(**to_dict()) if to_dict is not None else None


def inspection_response(result: SourceInspection) -> SourceInspectionResponse:
    """
    Describe an inspection for the API.

    Args:
        result: What the inspection found.

    Returns:
        The response, with the verdict and the suggestion beside their codes.
    """
    return SourceInspectionResponse(
        app_type=result.app_type,
        detected_types=result.detected_types,
        package_manager=result.package_manager,
        install_command=result.install_command,
        build_command=result.build_command,
        start_command=result.start_command,
        default_port=result.default_port,
        env_keys=[
            EnvKeyResponse(
                name=key.name,
                default=key.default,
                secret=key.secret,
                required=key.required,
            )
            for key in result.env_keys
        ],
        branch=result.branch,
        commit=result.commit,
        compatible=result.compatible,
        verdict=result.verdict or None,
        suggestion=result.suggestion,
        verdict_coded=_coded(result.verdict_coded),
        suggestion_coded=_coded(result.suggestion_coded),
        platform_proposal=platform_proposal_response(getattr(result, "platform_proposal", None)),
    )
