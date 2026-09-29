# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Vercel: ``vercel.json`` and, when it was committed, ``.vercel/project.json``.

``vercel.json`` overrides the project settings ``vercel link`` writes to
``.vercel/project.json``, as it does on Vercel. Most projects have neither
checked in, because Vercel detects the framework itself; Noust detects it the
same way, so the proposal then only carries the warnings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from noust.deployers.importers.base import (
    Proposal,
    declared_env,
    read_json_object,
    text_value,
)

PLATFORM = "vercel"
FILES = ("vercel.json", ".vercel/project.json")

#: Vercel framework presets Noust has a deployer for.
FRAMEWORK_TYPES: dict[str, str] = {
    "nextjs": "nextjs",
    "vite": "vite",
}

#: Keys of vercel.json that shape routing at Vercel's edge, which the
#: application does not see and Noust's generated site does not reproduce.
_ROUTING_KEYS = ("rewrites", "redirects", "headers", "routes")


def detect(root: Path) -> bool:
    """
    Report whether a repository carries Vercel configuration.

    Args:
        root: The repository.

    Returns:
        True when either file exists.
    """
    return any((root / name).exists() for name in FILES)


def read(root: Path) -> Proposal:
    """
    Read Vercel's configuration into a proposal.

    Args:
        root: The repository.

    Returns:
        The proposal.

    Raises:
        ValidationError: A file cannot be read or is not a JSON object.
    """
    proposal = Proposal(platform=PLATFORM)
    project = read_json_object(root, ".vercel/project.json")
    config = read_json_object(root, "vercel.json")

    settings: dict[str, Any] = {}
    if project is not None:
        proposal.files.append(".vercel/project.json")
        raw = project.get("settings")
        if isinstance(raw, dict):
            settings.update(raw)
        root_directory = text_value(settings, "rootDirectory")
        if root_directory and root_directory not in (".", "./"):
            proposal.warn(
                f"The Vercel project builds from {root_directory}/; Noust deploys a "
                "repository from its root. Deploy from a repository of its own, or "
                "as a monorepo."
            )
    if config is not None:
        proposal.files.append("vercel.json")
        settings.update({key: value for key, value in config.items() if value is not None})

    proposal.install_command = text_value(settings, "installCommand")
    proposal.build_command = text_value(settings, "buildCommand")
    proposal.output_directory = text_value(settings, "outputDirectory")
    framework = text_value(settings, "framework")
    _choose_type(root, proposal, framework)
    proposal.note_commands()

    if config is not None:
        _routing(config, proposal)
        _env(config, proposal)
        _functions(config, proposal)
    return proposal


def _choose_type(root: Path, proposal: Proposal, framework: str | None) -> None:
    """
    Map Vercel's framework preset to a Noust type.

    Args:
        root: The repository.
        proposal: Where the type and any warning go.
        framework: The preset, or None for "Other" or none given.
    """
    if framework in FRAMEWORK_TYPES:
        proposal.app_type = FRAMEWORK_TYPES[framework]
        if framework == "vite" and proposal.output_directory not in (None, "dist"):
            proposal.warn(
                f"Vercel publishes {proposal.output_directory}/; Noust reads the output "
                "directory from the Vite configuration (build.outDir), so set it there."
            )
        return
    if framework is not None:
        proposal.warn(
            f"Vercel builds this as {framework}, which Noust has no deployer for; "
            "detection decides the type, or build it into a container and deploy it "
            "as Docker Compose."
        )
        return
    if not (root / "package.json").exists() and proposal.build_command is None:
        # Vercel's "Other" with nothing to build serves the files as they are.
        proposal.app_type = "static"
        if proposal.output_directory not in (None, ".", "./", "public"):
            proposal.warn(
                f"Vercel publishes {proposal.output_directory}/; Noust serves a static "
                "site from the root of the repository."
            )


def _routing(config: dict[str, Any], proposal: Proposal) -> None:
    """
    Warn about edge routing rules.

    Args:
        config: vercel.json.
        proposal: Where the warnings go.
    """
    for key in _ROUTING_KEYS:
        rules = config.get(key)
        if isinstance(rules, list) and rules:
            proposal.warn(
                f"vercel.json has {len(rules)} {key} rule(s), which Vercel applies at its "
                "edge. Noust has no equivalent: handle them in the application (Next.js "
                "and Vite have their own), or send a name elsewhere with a redirect "
                "domain ('noust domain add --kind redirect')."
            )
    for key in ("cleanUrls", "trailingSlash"):
        if key in config:
            proposal.warn(f"vercel.json sets {key}, which Noust does not reproduce.")
    crons = config.get("crons")
    if isinstance(crons, list) and crons:
        for cron in crons:
            if isinstance(cron, dict):
                path = text_value(cron, "path") or "?"
                schedule = text_value(cron, "schedule") or "?"
                proposal.warn(
                    f"Vercel cron '{schedule}' calls {path}. Recreate it with 'noust cron "
                    "create', running curl against the application on that schedule."
                )


def _env(config: dict[str, Any], proposal: Proposal) -> None:
    """
    Read the legacy ``env`` and ``build.env`` maps.

    A value written ``@name`` referred to a Vercel secret, which lives in
    Vercel and has to be given again here.

    Args:
        config: vercel.json.
        proposal: Where the variables go.
    """
    maps = [config.get("env")]
    build = config.get("build")
    if isinstance(build, dict):
        maps.append(build.get("env"))
    for values in maps:
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            if not isinstance(name, str):
                continue
            text = value if isinstance(value, str) else None
            if text is not None and text.startswith("@"):
                variable = declared_env(name, None, required=True)
                variable.secret = True
                variable.note = f"Vercel secret {text}"
                proposal.add_env(variable)
            else:
                proposal.add_env(declared_env(name, text))
    proposal.warn(
        "Vercel keeps most environment variables in the project's dashboard, not in the "
        "repository: copy them over ('vercel env pull' writes them to a file you can pass "
        "to --env-file)."
    )


def _functions(config: dict[str, Any], proposal: Proposal) -> None:
    """
    Warn about serverless and region settings.

    Args:
        config: vercel.json.
        proposal: Where the warnings go.
    """
    if isinstance(config.get("functions"), dict) and config["functions"]:
        proposal.warn(
            "vercel.json configures serverless functions (memory, duration); on Noust "
            "the application runs as one long-lived service, limited with "
            "'noust app limits'."
        )
    if config.get("regions"):
        proposal.warn("vercel.json picks regions; a Noust application runs on this server.")
