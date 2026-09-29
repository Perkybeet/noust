# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Heroku: ``app.json`` (the app manifest) and the ``Procfile``.

``app.json`` is also the name Expo gives its configuration, so it only counts
as Heroku's when it carries one of the manifest's keys and not Expo's.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from noust.core.exceptions import ValidationError
from noust.deployers.importers.base import (
    Proposal,
    declared_env,
    read_json_object,
    read_text,
    text_value,
)

PLATFORM = "heroku"
FILES = ("app.json", "Procfile")

#: Keys only Heroku's app manifest has.
_MANIFEST_KEYS = frozenset(
    {"env", "formation", "addons", "buildpacks", "scripts", "stack", "environments", "success_url"}
)

#: Add-on name prefixes that are databases, and the engine they are.
_DATABASE_ADDONS: tuple[tuple[str, str], ...] = (
    ("heroku-postgresql", "postgresql"),
    ("heroku-redis", "redis"),
    ("rediscloud", "redis"),
    ("jawsdb", "mysql"),
    ("cleardb", "mysql"),
    ("mongolab", "mongodb"),
    ("ormongo", "mongodb"),
)

#: Buildpacks and the Noust type each implies; None leaves Node to detection.
_BUILDPACK_TYPES: dict[str, str | None] = {
    "heroku/nodejs": None,
    "heroku/python": "python",
}

_PROCFILE_LINE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.+)$")

#: ``$PORT``, ``${PORT}`` and ``${PORT:-3000}``, but not ``$PORTAL``.
_READS_PORT = re.compile(r"\$\{?PORT\b")


def is_manifest(data: dict[str, Any]) -> bool:
    """
    Tell Heroku's app manifest from another ``app.json``.

    Args:
        data: The parsed file.

    Returns:
        True when it has a manifest key and no ``expo`` section.
    """
    return "expo" not in data and bool(_MANIFEST_KEYS & set(data))


def detect(root: Path) -> bool:
    """
    Report whether a repository carries Heroku configuration.

    Args:
        root: The repository.

    Returns:
        True for a ``Procfile``, or an ``app.json`` that is Heroku's manifest.
    """
    if (root / "Procfile").is_file():
        return True
    if not (root / "app.json").is_file():
        return False
    try:
        data = read_json_object(root, "app.json")
    except ValidationError:
        return False
    return data is not None and is_manifest(data)


def read(root: Path) -> Proposal:
    """
    Read Heroku's configuration into a proposal.

    Args:
        root: The repository.

    Returns:
        The proposal.

    Raises:
        ValidationError: A file cannot be read, or ``app.json`` is not a
            JSON object.
    """
    proposal = Proposal(platform=PLATFORM)
    manifest = read_json_object(root, "app.json")
    if manifest is not None and is_manifest(manifest):
        proposal.files.append("app.json")
        _manifest(manifest, proposal)
    procfile = read_text(root, "Procfile")
    if procfile is not None:
        proposal.files.append("Procfile")
        _procfile(procfile, proposal)
    proposal.note_commands()
    if proposal.start_command and _READS_PORT.search(proposal.start_command):
        proposal.warn(
            "The web command reads $PORT; Noust sets PORT for the application, so it keeps working."
        )
    return proposal


def _manifest(manifest: dict[str, Any], proposal: Proposal) -> None:
    """
    Read ``app.json``.

    Args:
        manifest: The parsed file.
        proposal: Where everything goes.
    """
    env = manifest.get("env")
    if isinstance(env, dict):
        for name, spec in env.items():
            if isinstance(name, str):
                _env(name, spec, proposal)

    addons = manifest.get("addons")
    if isinstance(addons, list):
        for addon in addons:
            _addon(addon, proposal)
    elif isinstance(addons, dict):
        # Not the manifest's shape, but a natural one to write: name to plan.
        for name, spec in addons.items():
            _addon(spec if isinstance(spec, dict) and "plan" in spec else name, proposal)
    elif addons is not None:
        proposal.warn("addons in app.json is not a list of add-ons; it is not read.")

    packs = manifest.get("buildpacks")
    buildpacks = [
        text_value(pack, "url") if isinstance(pack, dict) else None
        for pack in (packs if isinstance(packs, list) else [])
    ]
    for url in filter(None, buildpacks):
        if url in _BUILDPACK_TYPES:
            if proposal.app_type is None:
                proposal.app_type = _BUILDPACK_TYPES[url]
        else:
            proposal.warn(
                f"The buildpack {url} has no Noust equivalent; build it into a container "
                "and deploy it as Docker Compose if detection does not recognise the project."
            )

    formation = manifest.get("formation")
    if isinstance(formation, dict):
        for process, spec in formation.items():
            quantity = spec.get("quantity") if isinstance(spec, dict) else None
            if process != "web":
                proposal.warn(
                    f"The {process} process has no equivalent; a Noust application runs its "
                    "web process. Run it as an application of its own or a 'noust cron' job."
                )
            elif isinstance(quantity, int) and quantity > 1:
                proposal.warn(
                    f"Heroku runs {quantity} web dynos; a Noust application runs one instance."
                )

    scripts = manifest.get("scripts")
    if isinstance(scripts, dict):
        for hook in scripts:
            proposal.warn(
                f"The {hook} script has no equivalent; run it by hand after the first deploy."
            )
    if isinstance(manifest.get("environments"), dict) and manifest["environments"]:
        proposal.warn(
            "Per-environment settings (review apps, CI) are not read; Noust previews are set "
            "with 'noust preview enable'."
        )


def _env(name: str, spec: Any, proposal: Proposal) -> None:
    """
    Read one entry of the manifest's ``env``.

    Args:
        name: Variable name.
        spec: A string (its value) or a mapping with ``value``, ``required``,
            ``generator`` and ``description``.
        proposal: Where it goes.
    """
    if isinstance(spec, str):
        proposal.add_env(declared_env(name, spec))
        return
    if not isinstance(spec, dict):
        return
    if spec.get("generator") == "secret":
        proposal.add_env(declared_env(name, None, generated=True))
        return
    value = spec.get("value")
    text = value if isinstance(value, str) else None
    required = spec.get("required")
    # Heroku's default: a variable is required unless it says otherwise.
    needed = (required is not False) and text is None
    proposal.add_env(declared_env(name, text, required=needed))


def _addon(addon: Any, proposal: Proposal) -> None:
    """
    Read one add-on.

    Args:
        addon: ``"heroku-postgresql"``, ``"heroku-postgresql:essential-0"``, or
            ``{"plan": ..., "as": ...}``.
        proposal: Where the database or the warning goes.
    """
    plan: str | None = None
    if isinstance(addon, str):
        plan = addon.strip()
    elif isinstance(addon, dict):
        plan = text_value(addon, "plan")
    if not plan:
        return
    service = plan.split(":", 1)[0]
    for prefix, engine in _DATABASE_ADDONS:
        if service.startswith(prefix):
            proposal.need_database(engine)
            proposal.warn(
                f"The {service} add-on is a {engine} database: create it with 'noust db "
                f"create --engine {engine}' and give the application its URL (Heroku set "
                "it as DATABASE_URL or REDIS_URL)."
            )
            return
    proposal.warn(f"The {service} add-on has no Noust equivalent.")


def _procfile(text: str, proposal: Proposal) -> None:
    """
    Read the Procfile's process types.

    Args:
        text: The file's text.
        proposal: Where the start command and warnings go.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PROCFILE_LINE.match(line)
        if match is None:
            continue
        process, command = match.group(1), match.group(2).strip()
        if process == "web":
            if proposal.start_command is not None and proposal.start_command != command:
                proposal.warn(
                    f"The Procfile declares web more than once; the last one ({command}) "
                    f"is proposed, not {proposal.start_command}."
                )
            proposal.start_command = command
        elif process == "release":
            proposal.warn(
                f"The release command ({command}) has no equivalent; run migrations from "
                "the build script, or by hand after the deploy."
            )
        else:
            proposal.warn(
                f"The {process} process ({command}) has no equivalent; run it as an "
                "application of its own or a 'noust cron' job."
            )
