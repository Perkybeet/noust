# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Coolify, read through its API.

Coolify keeps an application's configuration in its own database, so there is
no file in a repository to read; its API (``/api/v1``, a bearer token with
read access) lists the applications and describes each one. This module turns
what it says into a :class:`~noust.deployers.importers.base.Proposal`, the same
shape every other importer answers with, so the console and ``noust import``
handle a Coolify application like a Vercel project.

What carries over: the repository and branch, the base and publish directories,
the install, build and start commands that are plain commands, the domains, the
port, the health check, the memory and CPU limits, the watch paths, the
environment variables, the scheduled tasks, the persistent storage (as
``--persist`` paths, with the commands that copy the data) and the databases the
application connects to (found by the host name Coolify gives a database: its
uuid), with the commands that dump and load them. What is only said, with what
to do instead: the build pack (Noust detects the stack), Dockerfile and Compose
applications, services Coolify runs beside it, and the variables whose values the
token could not read.

Nothing here writes to Coolify: it only issues GETs.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from noust.core.exceptions import IntegrationError
from noust.deployers.importers.base import (
    INSTANCE_ENGINES,
    Proposal,
    ProposedCron,
    ProposedDatabase,
    ProposedStorage,
    RemoteApplication,
    database_commands,
    database_name_for,
    declared_env,
    directory_export,
    int_value,
    persistent_path_for,
    tarball_restore,
    text_value,
    volume_export,
)
from noust.deployers.importers.remote import Opener, RemoteClient, read_token

PLATFORM = "coolify"
API = "/api/v1"

_REPOSITORY = re.compile(r"^[\w.-]+/[\w./-]+$")


def connect(url: str, token: str, *, opener: Opener | None = None) -> RemoteClient:
    """
    Open a client on a Coolify server.

    Args:
        url: Its address, ``https://coolify.example.com``.
        token: An API token with read access.
        opener: Replacement for urlopen (tests).

    Returns:
        The client.
    """
    return RemoteClient(
        url,
        ("Authorization", f"Bearer {read_token(token, source='the token')}"),
        platform="Coolify",
        opener=opener,
    )


def _list(value: Any) -> list[dict[str, Any]]:
    """
    Args:
        value: An answer that should be a list of objects.

    Returns:
        The objects in it.
    """
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _domains(fqdn: str | None) -> tuple[str, ...]:
    """
    Read Coolify's comma-separated ``fqdn``.

    Args:
        fqdn: ``https://a.example.com,https://b.example.com``.

    Returns:
        The host names, in order, without a scheme or a path.
    """
    hosts: list[str] = []
    for item in (fqdn or "").split(","):
        host = urlsplit(item.strip() if "//" in item else f"//{item.strip()}").hostname
        if host and host not in hosts:
            hosts.append(host.lower())
    return tuple(hosts)


def list_applications(client: RemoteClient) -> list[RemoteApplication]:
    """
    List the applications of a Coolify server.

    Args:
        client: The client.

    Returns:
        One entry per application, in Coolify's order.

    Raises:
        IntegrationError: The server cannot be read.
    """
    found: list[RemoteApplication] = []
    for item in _list(client.get(f"{API}/applications")):
        ref = text_value(item, "uuid")
        if ref is None:
            continue
        found.append(
            RemoteApplication(
                ref=ref,
                name=text_value(item, "name") or ref,
                build=text_value(item, "build_pack") or "",
                source=text_value(item, "git_repository")
                or text_value(item, "docker_registry_image_name")
                or "",
                domains=_domains(text_value(item, "fqdn")),
                project=text_value(item, "environment_name") or "",
            )
        )
    return found


def _source(app: dict[str, Any], proposal: Proposal) -> str | None:
    """
    Name the repository an application is built from.

    Args:
        app: Coolify's description of it.
        proposal: Where a warning goes.

    Returns:
        A URL or ``github:owner/repo``, with no credentials; None when
        Coolify names none.
    """
    full = text_value(app, "git_full_url")
    repository = text_value(app, "git_repository")
    chosen = full or repository
    if chosen is None:
        return None
    if full is None and repository is not None and _REPOSITORY.fullmatch(repository):
        # "owner/repo" is how a source Coolify holds a GitHub App for is named.
        proposal.warn(
            f"Coolify names the repository {repository!r} without a host (a GitHub App source). "
            f"Noust reads it as github:{repository}; give --source if it is elsewhere."
        )
        return f"github:{repository}"
    parts = urlsplit(chosen)
    if parts.username or parts.password:
        host = parts.hostname or ""
        chosen = f"{parts.scheme}://{host}{':' + str(parts.port) if parts.port else ''}{parts.path}"
    return chosen


def _memory_mb(value: str | None) -> int | None:
    """
    Read Coolify's memory limit (``512m``, ``2g``, ``0`` for none).

    Args:
        value: The limit as Coolify stores it.

    Returns:
        Megabytes, or None for no limit.
    """
    match = re.fullmatch(r"(\d+)\s*([kmgKMG]?)[bB]?", (value or "").strip())
    if not match or int(match.group(1)) == 0:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    scale = {"": 1 / (1024 * 1024), "k": 1 / 1024, "m": 1, "g": 1024}[unit]
    return max(1, round(amount * scale))


def _cpu_percent(value: str | None) -> int | None:
    """
    Read Coolify's CPU limit (``1.5`` cores, ``0`` for none).

    Args:
        value: The limit as Coolify stores it.

    Returns:
        Percent of one CPU, or None for no limit.
    """
    try:
        cores = float(value or 0)
    except ValueError:
        return None
    return round(cores * 100) if cores > 0 else None


def read_application(client: RemoteClient, ref: str) -> Proposal:
    """
    Describe one Coolify application as a proposal.

    Args:
        client: The client.
        ref: The application's uuid.

    Returns:
        The proposal. Whatever a second call could not read (variables,
        storage, scheduled tasks) is a warning, not a failure.

    Raises:
        IntegrationError: The application itself cannot be read.
    """
    app = client.get(f"{API}/applications/{ref}")
    if not isinstance(app, dict):
        app = {}
    proposal = Proposal(platform=PLATFORM, files=[f"{client.base_url}/applications/{ref}"])
    proposal.name = text_value(app, "name")
    pack = text_value(app, "build_pack") or "nixpacks"
    proposal.source = _source(app, proposal)
    proposal.branch = text_value(app, "git_branch")
    proposal.base_directory = _directory(text_value(app, "base_directory"))
    proposal.install_command = text_value(app, "install_command")
    proposal.build_command = text_value(app, "build_command")
    proposal.start_command = text_value(app, "start_command")
    proposal.output_directory = _directory(text_value(app, "publish_directory"))
    proposal.domains = list(_domains(text_value(app, "fqdn")))
    ports = [int(p) for p in re.findall(r"\d+", text_value(app, "ports_exposes") or "")]
    proposal.port = ports[0] if ports else None
    if len(ports) > 1:
        proposal.warn(
            f"Coolify exposes ports {', '.join(map(str, ports))}; Noust proposes {ports[0]}, "
            "the one the domain was routed to."
        )
    if app.get("health_check_enabled"):
        proposal.health_path = text_value(app, "health_check_path") or "/"
        proposal.health_timeout = int_value(app, "health_check_timeout")
    proposal.memory_max_mb = _memory_mb(text_value(app, "limits_memory"))
    proposal.cpu_quota_percent = _cpu_percent(text_value(app, "limits_cpus"))
    watch = text_value(app, "watch_paths")
    if watch:
        proposal.watch_paths = [line.strip() for line in watch.splitlines() if line.strip()]
    if app.get("is_auto_deploy_enabled") is False:
        proposal.warn("Auto deploy was off in Coolify: turn deploy on push on only if you want it.")

    _by_pack(pack, app, proposal)
    values = _read_environment(client, ref, proposal)
    _read_storage(client, ref, proposal, pack)
    _read_databases(client, app, values, proposal)
    _read_tasks(client, ref, proposal)
    proposal.note_commands()
    return proposal


#: The names Coolify accepts instead of a cron expression.
_FREQUENCIES = {
    "every_minute": "* * * * *",
    "hourly": "0 * * * *",
    "daily": "0 0 * * *",
    "weekly": "0 0 * * 0",
    "monthly": "0 0 1 * *",
    "yearly": "0 0 1 1 *",
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
}


def cron_of(frequency: str) -> str | None:
    """
    Read a Coolify task frequency as a cron expression.

    Args:
        frequency: ``daily``, ``@hourly`` or five cron fields.

    Returns:
        The five-field expression, or None when it is neither.
    """
    text = frequency.strip().lower()
    if text in _FREQUENCIES:
        return _FREQUENCIES[text]
    fields = text.split()
    if len(fields) == 5 and all(re.fullmatch(r"[0-9*/,\-a-z]+", field) for field in fields):
        return " ".join(fields)
    return None


def _directory(value: str | None) -> str | None:
    """
    Args:
        value: A directory as Coolify stores it (``/``, ``/apps/web``).

    Returns:
        A path relative to the repository, or None for its root.
    """
    stripped = (value or "").strip().strip("/")
    return stripped or None


def _by_pack(pack: str, app: dict[str, Any], proposal: Proposal) -> None:
    """
    Say what Noust does with the way Coolify built the application.

    Args:
        pack: Coolify's build pack.
        app: Its description.
        proposal: The proposal being made.
    """
    if pack == "static":
        proposal.app_type = "static"
        return
    if pack in ("nixpacks", "railpack"):
        proposal.warn(
            f"Coolify built it with {pack}. Noust detects the stack itself and builds it with "
            "the commands of its type; the commands Coolify ran are carried only where they "
            "are plain commands."
        )
        return
    if pack == "dockerfile":
        proposal.app_type = "container"
        location = text_value(app, "dockerfile_location") or "/Dockerfile"
        proposal.equivalent = (
            "noust create -d DOMAIN -s REPOSITORY --type container --builder dockerfile"
            + (f" --dockerfile {location.lstrip('/')}" if location != "/Dockerfile" else "")
        )
        proposal.warn(
            "It is built from a Dockerfile: Noust runs it as a container application. "
            f"Create it with: {proposal.equivalent}"
        )
        return
    if pack == "dockercompose":
        proposal.app_type = "docker-compose"
        location = text_value(app, "docker_compose_location") or "/docker-compose.yaml"
        proposal.equivalent = (
            f"noust create -d DOMAIN -s REPOSITORY --type docker-compose --compose-file "
            f"{location.lstrip('/')}"
        )
        proposal.warn("It is a Docker Compose application. Create it with: " + proposal.equivalent)
        return
    image = text_value(app, "docker_registry_image_name")
    if image:
        tag = text_value(app, "docker_registry_image_tag") or "latest"
        proposal.image = f"{image}:{tag}"
        proposal.app_type = "container"
        proposal.equivalent = f"noust create -d DOMAIN --image {proposal.image}"
        proposal.warn(f"It runs a registry image. Create it with: {proposal.equivalent}")


def _read_environment(client: RemoteClient, ref: str, proposal: Proposal) -> list[str]:
    """
    Add the application's variables to a proposal.

    Args:
        client: The client.
        ref: The application's uuid.
        proposal: The proposal being made.

    Returns:
        Every value the token could read, secrets included, for finding which databases the
        application connects to. They go no further than that search.
    """
    try:
        variables = _list(client.get(f"{API}/applications/{ref}/envs"))
    except IntegrationError as exc:
        proposal.warn(f"The variables could not be read ({exc.message}); copy them by hand.")
        return []
    hidden = 0
    seen: list[str] = []
    for entry in variables:
        name = text_value(entry, "key")
        if name is None or entry.get("is_preview"):
            continue
        value = entry.get("value")
        if value is None or entry.get("is_shown_once"):
            hidden += 1
            proposal.add_env(declared_env(name, None, required=True))
            continue
        seen.append(str(value))
        proposal.add_env(declared_env(name, str(value)))
        if entry.get("is_buildtime") and not entry.get("is_runtime", True):
            proposal.warn(
                f"{name} was only a build-time variable in Coolify; Noust gives the build the "
                "environment of the application."
            )
    if hidden:
        proposal.warn(
            f"{hidden} variable(s) have no value the token could read (sensitive or shown "
            "once): give their values at import with --env-file or --env."
        )
    return seen


def _read_storage(client: RemoteClient, ref: str, proposal: Proposal, pack: str) -> None:
    """
    Add the application's persistent storage to a proposal.

    Coolify answers ``{"persistent_storages": [...], "file_storages": [...]}``: the first are
    Docker volumes (or directories of the server, when they name a ``host_path``) holding data,
    the second are files and directories it writes beside the application and mounts.

    Args:
        client: The client.
        ref: The application's uuid.
        proposal: The proposal being made.
        pack: Coolify's build pack: the buildpack builders run the application in ``/app``, which
            is what makes a mount under it a path of the application's own directory here.
    """
    try:
        answer = client.get(f"{API}/applications/{ref}/storages")
    except IntegrationError as exc:
        proposal.warn(f"The storage could not be read ({exc.message}); copy its data by hand.")
        return
    if isinstance(answer, dict):
        persistent = _list(answer.get("persistent_storages"))
        files = _list(answer.get("file_storages"))
    else:
        persistent, files = _list(answer), []
    work_dir = "/app" if pack in ("nixpacks", "railpack", "static") else ""
    for item in persistent:
        target = text_value(item, "mount_path")
        if target is None:
            continue
        volume = text_value(item, "name")
        host = text_value(item, "host_path")
        persistent_path = persistent_path_for(target, work_dir) if work_dir else None
        storage = ProposedStorage(
            mount_path=target,
            volume=None if host else volume,
            host_path=host,
            persistent_path=persistent_path,
            export=directory_export(host) if host else volume_export(volume) if volume else None,
            restore=tarball_restore(persistent_path),
        )
        proposal.storage.append(storage)
        if persistent_path and persistent_path not in proposal.persistent_paths:
            proposal.persistent_paths.append(persistent_path)
        proposal.warn(storage.describe())
    for item in files:
        target = text_value(item, "mount_path")
        if target is None:
            continue
        kind = "directory" if item.get("is_directory") else "file"
        proposal.warn(
            f"Coolify mounts a {kind} at {target} that it writes itself"
            + (" (from the repository)" if item.get("is_based_on_git") else "")
            + ". Keep a file with 'noust app mount file DOMAIN TARGET --from FILE' (its text is in"
            " Coolify's Storages tab); a file the repository carries needs nothing."
        )


#: How Coolify spells the databases it runs and what Noust calls them.
_DATABASE_TYPES = {
    "standalone-postgresql": "postgresql",
    "standalone-mysql": "mysql",
    "standalone-mariadb": "mariadb",
    "standalone-mongodb": "mongodb",
    "standalone-redis": "redis",
    "standalone-keydb": "keydb",
    "standalone-dragonfly": "dragonfly",
    "standalone-clickhouse": "clickhouse",
}

#: The attributes of a database in Coolify's answer that name its database and account, by engine.
_DATABASE_FIELDS = {
    "postgresql": ("postgres_db", "postgres_user"),
    "mysql": ("mysql_database", "mysql_user"),
    "mariadb": ("mariadb_database", "mariadb_user"),
    "mongodb": ("mongo_initdb_database", "mongo_initdb_root_username"),
}


def _read_databases(
    client: RemoteClient, app: dict[str, Any], values: list[str], proposal: Proposal
) -> None:
    """
    Find the databases the application uses and say how to bring their data.

    A Coolify database is a container named by its uuid, and that uuid is the host name every
    application of the environment connects to, so a variable that holds a database's uuid is
    the link. The databases that only share the application's environment are listed too, as not
    linked: the operator decides.

    Args:
        client: The client.
        app: Coolify's description of the application.
        values: The values of its variables.
        proposal: The proposal being made.
    """
    try:
        databases = _list(client.get(f"{API}/databases"))
    except IntegrationError as exc:
        proposal.warn(f"The databases could not be listed ({exc.message}); check them by hand.")
        return
    environment = app.get("environment_id")
    haystack = "\n".join(values)
    for item in databases:
        uuid = text_value(item, "uuid")
        kind = text_value(item, "database_type") or ""
        engine = _DATABASE_TYPES.get(kind)
        if uuid is None or engine is None:
            continue
        linked = uuid in haystack
        same_environment = environment is not None and item.get("environment_id") == environment
        if not linked and not same_environment:
            continue
        name = text_value(item, "name") or uuid
        image = text_value(item, "image")
        database_key, user_key = _DATABASE_FIELDS.get(engine, ("", ""))
        commands = database_commands(engine, uuid, database_name_for(name), uuid)
        dump, restore = commands if commands is not None else (None, None)
        detail = ProposedDatabase(
            engine=engine,
            name=name,
            reference=uuid,
            image=image,
            version=image.rsplit(":", 1)[1] if image and ":" in image.rsplit("/", 1)[-1] else None,
            database=text_value(item, database_key) if database_key else None,
            user=text_value(item, user_key) if user_key else None,
            linked=linked,
            dump=dump,
            restore=restore,
            note=None
            if linked
            else "It is in the same environment, but no variable of the application names it.",
        )
        proposal.database_details.append(detail)
        if linked and engine not in INSTANCE_ENGINES:
            proposal.need_database(engine)
        if linked:
            proposal.warn(detail.describe())


def _read_tasks(client: RemoteClient, ref: str, proposal: Proposal) -> None:
    """
    Add the application's scheduled tasks to a proposal.

    Args:
        client: The client.
        ref: The application's uuid.
        proposal: The proposal being made.
    """
    try:
        tasks = _list(client.get(f"{API}/applications/{ref}/scheduled-tasks"))
    except IntegrationError:
        return
    for task in tasks:
        if task.get("enabled") is False:
            continue
        name = text_value(task, "name")
        frequency = text_value(task, "frequency")
        command = text_value(task, "command")
        if not (name and frequency and command):
            continue
        schedule = cron_of(frequency)
        if schedule is None:
            proposal.warn(
                f"The scheduled task {name!r} runs {frequency!r}, which is not a cron "
                "expression Noust can read: create it with 'noust cron create'."
            )
            continue
        proposal.cron.append(ProposedCron(name=name, schedule=schedule, command=command))
        if text_value(task, "container"):
            proposal.warn(
                f"The task {name!r} ran inside the container {text_value(task, 'container')!r}. "
                "A Noust cron job runs on the server, as the application's account: check "
                "that its command makes sense there."
            )
