# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Render: the ``render.yaml`` Blueprint.

A Blueprint can describe several services and databases; a WASM application
is one of them. The proposal is for the first web (or static) service, and
names the others in a warning so none is lost silently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wasm.core.exceptions import ValidationError
from wasm.deployers.importers.base import (
    Proposal,
    ProposedEnv,
    declared_env,
    int_value,
    read_text,
    text_value,
)

PLATFORM = "render"
FILES = ("render.yaml",)

#: Where Render checks a repository out; a disk mounted under it is a path
#: of the application.
_PROJECT_ROOT = "/opt/render/project/src/"

#: Render runtimes WASM has a type for. ``node`` is left to detection, which
#: tells Next.js, Vite and a plain server apart as Render's build does not.
_RUNTIME_TYPES: dict[str, str | None] = {
    "node": None,
    "python": "python",
    "static": "static",
}


def detect(root: Path) -> bool:
    """
    Report whether a repository carries a Render Blueprint.

    Args:
        root: The repository.

    Returns:
        True when ``render.yaml`` exists.
    """
    return (root / "render.yaml").exists()


def read(root: Path) -> Proposal:
    """
    Read the Blueprint into a proposal for its first web service.

    Args:
        root: The repository.

    Returns:
        The proposal.

    Raises:
        ValidationError: The file cannot be read, is not YAML, or is not a
            mapping.
    """
    proposal = Proposal(platform=PLATFORM)
    text = read_text(root, "render.yaml")
    if text is None:
        return proposal
    proposal.files.append("render.yaml")
    try:
        blueprint = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError("render.yaml is not valid YAML", details=str(exc)) from exc
    if blueprint is None:
        blueprint = {}
    if not isinstance(blueprint, dict):
        raise ValidationError("render.yaml does not hold a mapping")

    services = [s for s in blueprint.get("services") or [] if isinstance(s, dict)]
    databases = [d for d in blueprint.get("databases") or [] if isinstance(d, dict)]
    chosen = _choose_service(services, proposal)
    if chosen is not None:
        _service(chosen, proposal)
    elif services:
        proposal.warn("render.yaml has no web or static service to deploy as an application.")
    else:
        proposal.warn("render.yaml declares no services.")

    if databases:
        proposal.need_database("postgresql")
        names = ", ".join(text_value(d, "name") or "?" for d in databases)
        proposal.warn(
            f"render.yaml declares PostgreSQL database(s) {names}. Create them with "
            "'wasm db create' and give the application its connection string."
        )
    if blueprint.get("envVarGroups"):
        proposal.warn(
            "Environment groups (envVarGroups) live in Render's dashboard; copy their "
            "variables into the application's environment."
        )
    return proposal


def _choose_service(services: list[dict[str, Any]], proposal: Proposal) -> dict[str, Any] | None:
    """
    Pick the service that becomes the application.

    Args:
        services: Every service in the Blueprint.
        proposal: Where the others are named.

    Returns:
        The first web service (static sites included), or None.
    """
    web = [s for s in services if text_value(s, "type") in ("web", "static")]
    if not web:
        return None
    chosen = web[0]
    others = [s for s in services if s is not chosen]
    if others:
        described = ", ".join(
            f"{text_value(s, 'name') or '?'} ({text_value(s, 'type') or '?'})" for s in others
        )
        proposal.warn(
            f"render.yaml also declares {described}. Each web service is an application "
            "of its own on WASM; a worker or cron job is a 'wasm cron' job or a service "
            "you run yourself; a Key Value instance is 'wasm db create --engine redis'."
        )
    return chosen


def _service(service: dict[str, Any], proposal: Proposal) -> None:
    """
    Read one service.

    Args:
        service: The service chosen.
        proposal: Where everything goes.
    """
    runtime = text_value(service, "runtime") or text_value(service, "env")
    if text_value(service, "type") == "static":
        runtime = "static"
    if runtime in _RUNTIME_TYPES:
        proposal.app_type = _RUNTIME_TYPES[runtime]
    elif runtime in ("docker", "image"):
        proposal.warn(
            "The service runs a Docker image on Render. WASM runs containers through "
            "Docker Compose: commit a compose.yaml that builds it and deploy it as "
            "docker-compose."
        )
    elif runtime is not None:
        proposal.warn(
            f"The service's runtime is {runtime}, which WASM has no deployer for; build "
            "it into a container and deploy it as Docker Compose."
        )

    proposal.build_command = text_value(service, "buildCommand")
    proposal.start_command = text_value(service, "startCommand")
    proposal.output_directory = text_value(service, "staticPublishPath")
    proposal.health_path = text_value(service, "healthCheckPath")
    proposal.note_commands()
    if proposal.app_type == "static" and proposal.output_directory not in (None, ".", "./"):
        proposal.warn(
            f"Render publishes {proposal.output_directory}; WASM serves a static site "
            "from the root of the repository, or builds it with Vite."
        )

    domains = service.get("domains")
    if isinstance(domains, list):
        proposal.domains = [d.strip().lower() for d in domains if isinstance(d, str) and d.strip()]

    for variable in service.get("envVars") or []:
        if isinstance(variable, dict):
            _env_var(variable, proposal)

    _disk(service, proposal)
    _other_settings(service, proposal)


def _env_var(variable: dict[str, Any], proposal: Proposal) -> None:
    """
    Read one entry of ``envVars``.

    Args:
        variable: The entry.
        proposal: Where it goes.
    """
    if "fromGroup" in variable:
        proposal.warn(
            f"envVars pulls in the group {variable.get('fromGroup')}, which lives in "
            "Render's dashboard; copy its variables over."
        )
        return
    name = text_value(variable, "key")
    if name is None:
        return
    if name == "PORT":
        port = int_value(variable, "value")
        if port is not None:
            proposal.port = port
        return

    database = variable.get("fromDatabase")
    if isinstance(database, dict):
        proposal.need_database("postgresql")
        entry = ProposedEnv(
            name=name,
            secret=True,
            required=True,
            note=f"from database {text_value(database, 'name') or '?'}",
        )
        proposal.add_env(entry)
        return

    linked = variable.get("fromService")
    if isinstance(linked, dict):
        kind = text_value(linked, "type")
        if kind in ("keyvalue", "redis"):
            proposal.need_database("redis")
        proposal.add_env(
            ProposedEnv(
                name=name,
                secret=_is_credential(linked),
                required=True,
                note=f"from service {text_value(linked, 'name') or '?'}",
            )
        )
        return

    if variable.get("generateValue"):
        proposal.add_env(declared_env(name, None, generated=True))
        return
    if variable.get("sync") is False:
        # Render asks for the value in its dashboard when the Blueprint is
        # applied: it is a value the operator has, and must give here too.
        entry = declared_env(name, None, required=True)
        entry.note = "set in Render's dashboard (sync: false)"
        proposal.add_env(entry)
        return
    value = variable.get("value")
    text: str | None = None
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (str, int, float)):
        text = str(value)
    proposal.add_env(declared_env(name, text))


def _is_credential(linked: dict[str, Any]) -> bool:
    """
    Decide whether a value read from another service is a credential.

    Args:
        linked: The ``fromService`` mapping.

    Returns:
        True when it is a connection string or an environment value of that
        service, which is how Render hands over passwords.
    """
    prop = text_value(linked, "property") or ""
    return prop in ("connectionString", "") or "envVarKey" in linked


def _disk(service: dict[str, Any], proposal: Proposal) -> None:
    """
    Turn a persistent disk under the checkout into a persistent path.

    Args:
        service: The service.
        proposal: Where the path or the warning goes.
    """
    disk = service.get("disk")
    if not isinstance(disk, dict):
        return
    mount = text_value(disk, "mountPath")
    if mount is None:
        return
    if mount.startswith(_PROJECT_ROOT) and mount.rstrip("/") != _PROJECT_ROOT.rstrip("/"):
        proposal.persistent_paths.append(mount[len(_PROJECT_ROOT) :].strip("/"))
        return
    proposal.warn(
        f"Render mounts a persistent disk at {mount}, outside the application. On WASM "
        "keep a path of the application across releases with --persist, or point the "
        "application at a directory on this server."
    )


def _other_settings(service: dict[str, Any], proposal: Proposal) -> None:
    """
    Warn about service settings without an equivalent.

    Args:
        service: The service.
        proposal: Where the warnings go.
    """
    instances = int_value(service, "numInstances")
    if instances is not None and instances > 1:
        proposal.warn(
            f"Render runs {instances} instances; a WASM application runs one (two, "
            "briefly, with zero-downtime on)."
        )
    if isinstance(service.get("scaling"), dict):
        proposal.warn("Autoscaling has no equivalent; a WASM application runs one instance.")
    if text_value(service, "preDeployCommand"):
        proposal.warn("preDeployCommand has no equivalent; run migrations from the build script.")
    root_dir = text_value(service, "rootDir")
    if root_dir and root_dir not in (".", "./"):
        proposal.warn(
            f"The service builds from {root_dir}/; WASM deploys a repository from its "
            "root. Deploy from a repository of its own, or as a monorepo."
        )
    if isinstance(service.get("headers"), list) and service["headers"]:
        proposal.warn("Static site headers have no equivalent in WASM's generated site.")
    if isinstance(service.get("routes"), list) and service["routes"]:
        proposal.warn(
            "Static site redirect and rewrite routes have no equivalent; handle them in the "
            "application, or add a redirect domain with 'wasm domain add --kind redirect'."
        )
