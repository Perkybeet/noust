# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Railway: ``railway.toml`` or ``railway.json`` (config as code).

Both spell the same two sections, ``build`` and ``deploy``. Railway keeps
variables, domains and databases in its dashboard, so the proposal carries
the builder, the commands and the health check, and says so for the rest.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from noust.core.exceptions import ValidationError
from noust.deployers.importers.base import (
    Proposal,
    check_nesting,
    health_timeout,
    int_value,
    read_json_object,
    read_text,
    text_value,
    too_deep,
)
from noust.deployers.importers.toml_fallback import load_toml_fallback

PLATFORM = "railway"
FILES = ("railway.toml", "railway.json")


def detect(root: Path) -> bool:
    """
    Report whether a repository carries Railway configuration.

    Args:
        root: The repository.

    Returns:
        True when either file exists.
    """
    return any((root / name).exists() for name in FILES)


def read(root: Path) -> Proposal:
    """
    Read Railway's configuration into a proposal.

    ``railway.toml`` wins when both exist, as it would on Railway, which
    reads the first it finds.

    Args:
        root: The repository.

    Returns:
        The proposal.

    Raises:
        ValidationError: The file cannot be read or parsed.
    """
    proposal = Proposal(platform=PLATFORM)
    config: dict[str, Any] | None = None
    text = read_text(root, "railway.toml")
    if text is not None:
        proposal.files.append("railway.toml")
        config = load_toml(text, name="railway.toml", warn=proposal.warn)
    else:
        config = read_json_object(root, "railway.json")
        if config is not None:
            proposal.files.append("railway.json")
    config = config or {}

    build = _section(config, "build")
    deploy = _section(config, "deploy")

    _builder(build, proposal)
    proposal.build_command = text_value(build, "buildCommand")
    proposal.start_command = text_value(deploy, "startCommand")
    proposal.note_commands()

    proposal.health_path = text_value(deploy, "healthcheckPath")
    proposal.health_timeout = health_timeout(
        int_value(deploy, "healthcheckTimeout"), proposal, source="healthcheckTimeout"
    )
    _deploy_settings(deploy, proposal)

    if isinstance(config.get("environments"), dict) and config["environments"]:
        proposal.warn(
            "The configuration overrides settings per Railway environment; the proposal "
            "reads the base settings only."
        )
    proposal.warn(
        "Railway keeps variables, domains and databases in its dashboard, not in the "
        "repository: copy the variables over (to a file for --env-file) and create the "
        "databases with 'noust db create'."
    )
    return proposal


def _section(config: dict[str, Any], key: str) -> dict[str, Any]:
    """
    Read one section of the configuration.

    Args:
        config: The whole configuration.
        key: ``build`` or ``deploy``.

    Returns:
        The section, or an empty one when it is absent or not a table.
    """
    section = config.get(key)
    return section if isinstance(section, dict) else {}


def _builder(build: dict[str, Any], proposal: Proposal) -> None:
    """
    Map Railway's builder to a Noust type.

    Nixpacks and Railpack detect the stack from the repository the way Noust
    does, so the type is left to detection; a Dockerfile has no deployer of
    its own here.

    Args:
        build: The ``build`` section.
        proposal: Where the type and warnings go.
    """
    builder = (text_value(build, "builder") or "").upper()
    if builder == "DOCKERFILE" or text_value(build, "dockerfilePath"):
        proposal.warn(
            "Railway builds this from a Dockerfile. Noust runs containers through Docker "
            "Compose: commit a compose.yaml that builds it and deploy it as docker-compose."
        )
    if text_value(build, "nixpacksPlan") or isinstance(build.get("nixpacksPlan"), dict):
        proposal.warn("The Nixpacks plan has no equivalent; Noust detects the stack itself.")
    if isinstance(build.get("watchPatterns"), list) and build["watchPatterns"]:
        proposal.warn(
            "watchPatterns decide which pushes redeploy on Railway; a Noust webhook "
            "redeploys on every push to the branch."
        )


def _deploy_settings(deploy: dict[str, Any], proposal: Proposal) -> None:
    """
    Warn about deploy settings without an equivalent.

    Args:
        deploy: The ``deploy`` section.
        proposal: Where the warnings go.
    """
    policy = text_value(deploy, "restartPolicyType")
    if policy is not None:
        proposal.warn(
            f"restartPolicyType is {policy}; systemd restarts a Noust application whenever "
            "it exits with an error, without a retry limit."
        )
    replicas = int_value(deploy, "numReplicas")
    if replicas is not None and replicas > 1:
        proposal.warn(
            f"Railway runs {replicas} replicas; a Noust application runs one instance "
            "(two, briefly, with zero-downtime on)."
        )
    schedule = text_value(deploy, "cronSchedule")
    if schedule is not None:
        proposal.warn(
            f"The service runs as a cron job ({schedule}) on Railway. Create it with "
            "'noust cron create' instead of deploying it as an application."
        )
    if text_value(deploy, "preDeployCommand") or isinstance(deploy.get("preDeployCommand"), list):
        proposal.warn(
            "preDeployCommand has no equivalent; run migrations from the build script, or "
            "by hand after the deploy."
        )
    if deploy.get("sleepApplication"):
        proposal.warn("sleepApplication has no equivalent; a Noust application keeps running.")
    for key in ("region", "multiRegionConfig"):
        if deploy.get(key):
            proposal.warn(f"{key} has no equivalent; a Noust application runs on this server.")


# TOML ----------------------------------------------------------------------


def load_toml(text: str, *, name: str, warn: Callable[[str], None] | None = None) -> dict[str, Any]:
    """
    Parse a TOML file with the standard library, or Noust's own reader on 3.10.

    ``tomllib`` arrived in Python 3.11; Ubuntu 22.04 ships 3.10 and no TOML
    parser Noust may depend on, so there :func:`_load_simple_toml` reads it.

    Args:
        text: The file's text.
        name: The file's name, for the error.
        warn: Where the 3.10 reader reports what it leaves out.

    Returns:
        The document.

    Raises:
        ValidationError: The text is not TOML, or nests deeper than a
            parser follows.
    """
    try:
        if sys.version_info >= (3, 11):
            import tomllib

            try:
                return check_nesting(name, tomllib.loads(text))
            except tomllib.TOMLDecodeError as exc:
                raise ValidationError(f"{name} is not valid TOML", details=str(exc)) from exc
        return _load_simple_toml(text, name=name, warn=warn)
    except RecursionError as exc:
        raise too_deep(name) from exc


def _load_simple_toml(
    text: str, *, name: str, warn: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """
    Read TOML without ``tomllib``; see :mod:`noust.deployers.importers.toml_fallback`.

    Args:
        text: The file's text.
        name: The file's name, for the error.
        warn: Where arrays of tables and dates, which it leaves out, are reported.

    Returns:
        The document.

    Raises:
        ValidationError: The text is not TOML.
    """
    return load_toml_fallback(text, name=name, warn=warn)
