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

from typing import Literal

from pydantic import BaseModel, Field

from noust.core.server_text import ServerText
from noust.deployers.how_it_runs import HowItRuns, Way
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


class HowItRunsSentence(CodedText):
    """
    A sentence about how an application runs: its code and values, and its English beside them.

    Attributes:
        text: The sentence in English, which is always sent.
        hint: How to fix it, when the sentence has a fix (what a way that cannot be needs).
    """

    text: str
    hint: str | None = None


class RunWayResponse(BaseModel):
    """
    One form an application can run in, on this server.

    Attributes:
        form: ``server`` (directly on the server, a service of the system with an account of its
            own) or ``container``.
        status: ``yes``: it can be. ``no``: it cannot yet, and ``reason`` says why and what would
            make it possible (add a Dockerfile, install Railpack or Docker). ``hidden``: there is
            nothing to fix, so it is not offered: this machine can never run this form (one that
            only has Docker has no systemd), or nothing in the source runs that way.
        app_type: The ``app_type`` to create the application with to run it this way; null when it
            cannot be.
        via: For a container, how it is built: ``dockerfile``, ``railpack`` (no Dockerfile, with
            ``container.method`` ``railpack``) or ``compose`` (a Compose file). Null for ``server``.
        reason: Why it cannot be, when ``status`` is not ``yes``.
        port: For a container of a repository that has a Dockerfile, the port the Dockerfile
            exposes, when it says one (``container.container_port`` is empty to use it), even where
            this machine cannot run a container yet. Null otherwise: what a service listens on is
            the type's own, which ``default_port`` says.
    """

    form: Literal["server", "container"]
    status: Literal["yes", "no", "hidden"]
    app_type: str | None = None
    via: Literal["dockerfile", "railpack", "compose"] | None = None
    reason: HowItRunsSentence | None = None
    port: int | None = None


class HowItRunsResponse(BaseModel):
    """
    How a source can run on this server, and which way Noust proposes.

    What is proposed is what Noust has always deployed (the first of ``detected_types``): only what
    this machine cannot do moves it.

    Attributes:
        choice: Both ways can be, so there is something to choose.
        proposed: The form Noust proposes: ``server`` or ``container``.
        why: The sentence for the proposal.
        server: Directly on the server.
        container: In a container.
    """

    choice: bool
    proposed: Literal["server", "container"]
    why: HowItRunsSentence
    server: RunWayResponse
    container: RunWayResponse


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
    ways: HowItRunsResponse | None = Field(
        default=None,
        description="How it can run on this server: directly on it (a service of the system) or "
        "in a container, which Noust proposes and why, and why the other cannot be when it "
        "cannot. Absent from a Noust older than 4.3",
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


def _sentence(sentence: ServerText) -> HowItRunsSentence:
    """
    Args:
        sentence: A sentence of the server's catalog.

    Returns:
        It as the API says it: the code and values, with the English and its fix beside them.
    """
    return HowItRunsSentence(**sentence.to_dict(), text=sentence.text, hint=sentence.hint)


def _way(way: Way) -> RunWayResponse:
    """
    Args:
        way: One form of the decision.

    Returns:
        It for the API.
    """
    return RunWayResponse(
        form=way.form,  # type: ignore[arg-type]
        status=way.status,  # type: ignore[arg-type]
        app_type=way.app_type,
        via=way.via,  # type: ignore[arg-type]
        reason=_sentence(way.reason) if way.reason is not None else None,
        port=way.port,
    )


def ways_response(ways: HowItRuns | None) -> HowItRunsResponse | None:
    """
    Describe how a source can run for the API.

    Args:
        ways: The decision of :func:`noust.deployers.how_it_runs.decide`, or None.

    Returns:
        The response, or None when there is no decision.
    """
    if ways is None:
        return None
    return HowItRunsResponse(
        choice=ways.choice,
        proposed=ways.proposed,  # type: ignore[arg-type]
        why=_sentence(ways.why),
        server=_way(ways.server),
        container=_way(ways.container),
    )


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
        ways=ways_response(getattr(result, "ways", None)),
    )
