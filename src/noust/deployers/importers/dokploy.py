# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Dokploy, read through its API.

Dokploy keeps an application's configuration in its own database; its API
(``/api/<router>.<procedure>``, an ``x-api-key`` made under Settings > API Keys)
lists projects with their applications and describes each. This module turns
that into the :class:`~noust.deployers.importers.base.Proposal` every importer
answers with. It only issues GETs.

What carries over: the repository and branch (GitHub, GitLab, Bitbucket or a
plain git URL), the build path, the publish directory of a static site, the
domains and the port, the variables, the watch paths, submodules and the
memory and CPU limits, the volumes and bind mounts (as ``--persist`` paths where they
sit under ``/app``, with the commands that copy the data) and the databases of the same
environment that a variable of the application names (a Dokploy database is a service
whose name is the host the application connects to), with the commands that dump and
load them. What is only said, with what to do instead: buildpacks and Swarm settings
(replicas, placement, update configs), Docker image and Dockerfile applications, Gitea
sources (the host lives in a Dokploy provider), upload ("drop") applications, which have
no repository, and a file Dokploy writes and mounts.
"""

from __future__ import annotations

import re
from typing import Any

from noust.core.exceptions import IntegrationError
from noust.deployers.importers.base import (
    INSTANCE_ENGINES,
    Proposal,
    ProposedDatabase,
    ProposedStorage,
    RemoteApplication,
    database_commands,
    database_name_for,
    declared_env,
    directory_export,
    persistent_path_for,
    tarball_restore,
    text_value,
    volume_export,
)
from noust.deployers.importers.remote import Opener, RemoteClient, read_token

PLATFORM = "dokploy"
API = "/api"


def connect(url: str, token: str, *, opener: Opener | None = None) -> RemoteClient:
    """
    Open a client on a Dokploy server.

    Args:
        url: Its address, ``https://dokploy.example.com``.
        token: An API key (``x-api-key``).
        opener: Replacement for urlopen (tests).

    Returns:
        The client.
    """
    return RemoteClient(
        url,
        ("x-api-key", read_token(token, source="the API key")),
        platform="Dokploy",
        opener=opener,
    )


def _objects(value: Any) -> list[dict[str, Any]]:
    """
    Args:
        value: An answer that should be a list of objects.

    Returns:
        The objects in it.
    """
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _applications_of(project: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """
    Find the applications of a project, whichever way this Dokploy nests them.

    Older versions list ``applications`` on the project; newer ones put them in
    its environments.

    Args:
        project: One entry of ``project.all``.

    Returns:
        ``(environment name, application)`` pairs.
    """
    found = [("", item) for item in _objects(project.get("applications"))]
    for environment in _objects(project.get("environments")):
        name = text_value(environment, "name") or ""
        found.extend((name, item) for item in _objects(environment.get("applications")))
    return found


def list_applications(client: RemoteClient) -> list[RemoteApplication]:
    """
    List the applications of a Dokploy server.

    Args:
        client: The client.

    Returns:
        One entry per application.

    Raises:
        IntegrationError: The server cannot be read.
    """
    found: list[RemoteApplication] = []
    for project in _objects(client.get(f"{API}/project.all")):
        project_name = text_value(project, "name") or ""
        for environment, app in _applications_of(project):
            ref = text_value(app, "applicationId")
            if ref is None:
                continue
            where = f"{project_name} / {environment}" if environment else project_name
            found.append(
                RemoteApplication(
                    ref=ref,
                    name=text_value(app, "name") or text_value(app, "appName") or ref,
                    build=text_value(app, "buildType") or "",
                    source=_repository(app) or text_value(app, "dockerImage") or "",
                    domains=tuple(
                        host
                        for host in (text_value(d, "host") for d in _objects(app.get("domains")))
                        if host
                    ),
                    project=where,
                )
            )
    return found


def _repository(app: dict[str, Any]) -> str | None:
    """
    Name the repository a Dokploy application is pulled from.

    Args:
        app: Dokploy's description of it.

    Returns:
        A clone URL, or None when it has none (a Docker image, an upload, a
        Gitea provider whose host Dokploy does not repeat).
    """
    kind = text_value(app, "sourceType")
    if kind == "github":
        owner, repo = text_value(app, "owner"), text_value(app, "repository")
        return f"https://github.com/{owner}/{repo}.git" if owner and repo else None
    if kind == "gitlab":
        path = text_value(app, "gitlabPathNamespace")
        if not path:
            owner, repo = text_value(app, "gitlabOwner"), text_value(app, "gitlabRepository")
            path = f"{owner}/{repo}" if owner and repo else None
        return f"https://gitlab.com/{path}.git" if path else None
    if kind == "bitbucket":
        owner = text_value(app, "bitbucketOwner")
        slug = text_value(app, "bitbucketRepositorySlug") or text_value(app, "bitbucketRepository")
        return f"https://bitbucket.org/{owner}/{slug}.git" if owner and slug else None
    if kind == "git":
        return text_value(app, "customGitUrl")
    return None


def _branch(app: dict[str, Any]) -> str | None:
    """
    Args:
        app: Dokploy's description of the application.

    Returns:
        The branch its source follows.
    """
    kind = text_value(app, "sourceType")
    key = {
        "github": "branch",
        "gitlab": "gitlabBranch",
        "bitbucket": "bitbucketBranch",
        "gitea": "giteaBranch",
        "git": "customGitBranch",
    }.get(kind or "")
    return text_value(app, key) if key else None


def _build_path(app: dict[str, Any]) -> str | None:
    """
    Args:
        app: Dokploy's description of the application.

    Returns:
        The build path of its source, relative to the repository, or None
        for the root.
    """
    kind = text_value(app, "sourceType")
    key = {
        "github": "buildPath",
        "gitlab": "gitlabBuildPath",
        "bitbucket": "bitbucketBuildPath",
        "gitea": "giteaBuildPath",
        "git": "customGitBuildPath",
    }.get(kind or "")
    stripped = (text_value(app, key) or "").strip().strip("/") if key else ""
    return stripped or None


def _memory_mb(value: str | None) -> int | None:
    """
    Read Dokploy's memory limit (bytes as text, or ``512m``).

    Args:
        value: What the limit field holds.

    Returns:
        Megabytes, or None for none.
    """
    match = re.fullmatch(r"(\d+)\s*([kmgKMG]?)[bB]?", (value or "").strip())
    if not match or int(match.group(1)) == 0:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    scale = {"": 1 / (1024 * 1024), "k": 1 / 1024, "m": 1, "g": 1024}[unit]
    return max(1, round(amount * scale))


def _cpu_percent(value: str | None) -> int | None:
    """
    Read Dokploy's CPU limit (nano CPUs as text, or cores).

    Args:
        value: What the limit field holds.

    Returns:
        Percent of one CPU, or None for none.
    """
    try:
        number = float(value or 0)
    except ValueError:
        return None
    if number <= 0:
        return None
    cores = number / 1_000_000_000 if number >= 1000 else number
    return max(1, round(cores * 100))


def read_application(client: RemoteClient, ref: str) -> Proposal:
    """
    Describe one Dokploy application as a proposal.

    Args:
        client: The client.
        ref: The application's id.

    Returns:
        The proposal.

    Raises:
        IntegrationError: The application cannot be read.
    """
    app = client.get(f"{API}/application.one", {"applicationId": ref})
    if not isinstance(app, dict):
        raise IntegrationError(f"Dokploy has no application {ref}")
    proposal = Proposal(platform=PLATFORM, files=[f"{client.base_url}/application.one?{ref}"])
    proposal.name = text_value(app, "name") or text_value(app, "appName")
    proposal.source = _repository(app)
    proposal.branch = _branch(app)
    proposal.base_directory = _build_path(app)
    proposal.memory_max_mb = _memory_mb(text_value(app, "memoryLimit"))
    proposal.cpu_quota_percent = _cpu_percent(text_value(app, "cpuLimit"))
    if app.get("enableSubmodules") is not None:
        proposal.submodules = bool(app.get("enableSubmodules"))
    paths = app.get("watchPaths")
    if isinstance(paths, list):
        proposal.watch_paths = [str(item) for item in paths if str(item).strip()]

    domains = _objects(app.get("domains"))
    if not domains:
        try:
            domains = _objects(client.get(f"{API}/domain.byApplicationId", {"applicationId": ref}))
        except IntegrationError:
            proposal.warn("The domains could not be read: add them with 'noust domain add'.")
    for domain in domains:
        host = text_value(domain, "host")
        if (
            host
            and host not in proposal.domains
            and domain.get("domainType", "application") != "preview"
        ):
            proposal.domains.append(host)
    for domain in domains:
        port = domain.get("port")
        if isinstance(port, int) and not isinstance(port, bool) and proposal.port is None:
            proposal.port = port

    _by_source(app, proposal)
    _environment(app, proposal)
    _swarm(app, proposal)
    _storage(app, proposal)
    _databases(client, ref, text_value(app, "env") or "", proposal)
    proposal.note_commands()
    return proposal


def _storage(app: dict[str, Any], proposal: Proposal) -> None:
    """
    Add the application's mounts to a proposal.

    Args:
        app: Dokploy's description of the application.
        proposal: The proposal being made.
    """
    build = text_value(app, "buildType") or "nixpacks"
    # The buildpack builders run the application in /app; a Dockerfile runs it wherever it says.
    work_dir = (
        "/app"
        if build in ("nixpacks", "railpack", "static", "heroku_buildpacks", "paketo_buildpacks")
        else ""
    )
    for mount in _objects(app.get("mounts")):
        target = text_value(mount, "mountPath")
        kind = mount.get("type")
        if target is None:
            continue
        if kind == "file":
            proposal.warn(
                f"Dokploy writes a file and mounts it at {target}. Keep one with 'noust app "
                "mount file DOMAIN TARGET --from FILE' (its text is in Dokploy's Advanced > "
                "Volumes tab)."
            )
            continue
        if kind not in ("volume", "bind"):
            continue
        volume = text_value(mount, "volumeName") if kind == "volume" else None
        host = text_value(mount, "hostPath") if kind == "bind" else None
        persistent = persistent_path_for(target, work_dir) if work_dir else None
        storage = ProposedStorage(
            mount_path=target,
            volume=volume,
            host_path=host,
            persistent_path=persistent,
            export=directory_export(host) if host else volume_export(volume) if volume else None,
            restore=tarball_restore(persistent),
        )
        proposal.storage.append(storage)
        if persistent and persistent not in proposal.persistent_paths:
            proposal.persistent_paths.append(persistent)
        proposal.warn(storage.describe())


#: The lists ``project.all`` keeps each kind of database in, and what Noust calls its engine.
_DATABASE_LISTS = {
    "postgres": "postgresql",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "mongo": "mongodb",
    "redis": "redis",
}


def _databases(client: RemoteClient, ref: str, env_text: str, proposal: Proposal) -> None:
    """
    Find the databases of the application's environment that its variables name.

    A Dokploy database is a Swarm service named by its ``appName``, and that name is the host
    every application of the environment connects to, so a variable that holds it is the link.

    Args:
        client: The client.
        ref: The application's id.
        env_text: The application's variables, ``NAME=value`` per line.
        proposal: The proposal being made.
    """
    try:
        projects = _objects(client.get(f"{API}/project.all"))
    except IntegrationError as exc:
        proposal.warn(f"The databases could not be listed ({exc.message}); check them by hand.")
        return
    for project in projects:
        environments = [project, *_objects(project.get("environments"))]
        for environment in environments:
            if ref not in {
                text_value(a, "applicationId") for a in _objects(environment.get("applications"))
            }:
                continue
            for key, engine in _DATABASE_LISTS.items():
                for item in _objects(environment.get(key)):
                    _one_database(client, key, engine, item, env_text, proposal)
            return


def _one_database(
    client: RemoteClient,
    key: str,
    engine: str,
    item: dict[str, Any],
    env_text: str,
    proposal: Proposal,
) -> None:
    """
    Add one database of the environment, when a variable names it.

    Args:
        client: The client.
        key: The list it came from (``postgres``).
        engine: As ``noust db`` names it.
        item: Its entry of ``project.all``.
        env_text: The application's variables.
        proposal: The proposal being made.
    """
    service = text_value(item, "appName")
    if service is None or service not in env_text:
        return
    detail: dict[str, Any] = {}
    identifier = text_value(item, f"{key}Id")
    if identifier is not None:
        try:
            answer = client.get(f"{API}/{key}.one", {f"{key}Id": identifier})
        except IntegrationError:
            answer = None
        detail = answer if isinstance(answer, dict) else {}
    name = text_value(item, "name") or service
    image = text_value(detail, "dockerImage")
    container = f"$(docker ps -qf name=^{service}\\.)"
    commands = database_commands(engine, container, database_name_for(name), service)
    dump, restore = commands if commands is not None else (None, None)
    entry = ProposedDatabase(
        engine=engine,
        name=name,
        reference=service,
        image=image,
        version=image.rsplit(":", 1)[1] if image and ":" in image.rsplit("/", 1)[-1] else None,
        database=text_value(detail, "databaseName"),
        user=text_value(detail, "databaseUser"),
        linked=True,
        dump=dump,
        restore=restore,
    )
    proposal.database_details.append(entry)
    if engine not in INSTANCE_ENGINES:
        proposal.need_database(engine)
    proposal.warn(entry.describe())


def _by_source(app: dict[str, Any], proposal: Proposal) -> None:
    """
    Say what Noust does with the way Dokploy gets and builds the code.

    Args:
        app: Dokploy's description of the application.
        proposal: The proposal being made.
    """
    kind = text_value(app, "sourceType")
    build = text_value(app, "buildType") or "nixpacks"
    if kind == "docker":
        image = text_value(app, "dockerImage")
        proposal.image = image
        proposal.app_type = "container"
        proposal.equivalent = f"noust create -d DOMAIN --image {image or 'IMAGE'}"
        proposal.warn(f"It runs a registry image. Create it with: {proposal.equivalent}")
        if text_value(app, "username"):
            proposal.warn(
                "The image needs a registry login: add it with 'noust registry add' first."
            )
        return
    if kind == "drop":
        proposal.warn(
            "It was uploaded as an archive, not pulled from a repository: Noust deploys a "
            "directory or an archive URL, or the repository the archive came from."
        )
        return
    if kind == "gitea":
        proposal.warn(
            "Its source is a Gitea provider; Dokploy keeps the server's address in the provider, "
            "not in the application. Give the repository with --source."
        )
    if build == "static":
        proposal.app_type = "static"
        publish = (text_value(app, "publishDirectory") or "").strip("/")
        proposal.output_directory = publish or None
        if app.get("isStaticSpa") is False:
            proposal.warn(
                "It was not a single-page app: Noust serves a static site with a fallback to "
                "index.html."
            )
    elif build == "dockerfile":
        proposal.app_type = "container"
        dockerfile = text_value(app, "dockerfile") or "Dockerfile"
        context = text_value(app, "dockerContextPath") or "."
        proposal.equivalent = (
            "noust create -d DOMAIN -s REPOSITORY --type container --builder dockerfile"
            + (f" --dockerfile {dockerfile}" if dockerfile != "Dockerfile" else "")
            + (f" --context {context}" if context not in (".", "") else "")
        )
        proposal.warn(f"It is built from a Dockerfile. Create it with: {proposal.equivalent}")
    else:
        proposal.warn(
            f"Dokploy built it with {build.replace('_', ' ')}. Noust detects the stack itself "
            "and builds it with the commands of its type."
        )


def _environment(app: dict[str, Any], proposal: Proposal) -> None:
    """
    Add the application's variables to a proposal.

    Dokploy keeps them as one text of ``NAME=value`` lines.

    Args:
        app: Dokploy's description of the application.
        proposal: The proposal being made.
    """
    for line in (text_value(app, "env") or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, _, value = text.partition("=")
        name = name.strip()
        if name:
            proposal.add_env(declared_env(name, value.strip().strip("\"'")))
    if text_value(app, "buildArgs"):
        proposal.warn(
            "It had build arguments. Noust's build gets the application's environment; "
            "put anything else a build needs in the environment."
        )


def _swarm(app: dict[str, Any], proposal: Proposal) -> None:
    """
    Say which Docker Swarm settings have no equivalent.

    Args:
        app: Dokploy's description of the application.
        proposal: The proposal being made.
    """
    replicas = app.get("replicas")
    if isinstance(replicas, int) and replicas > 1:
        proposal.warn(
            f"It ran {replicas} replicas. Noust runs one process per application on a server "
            "(a second server is a second Noust)."
        )
    for key, what in (
        ("healthCheckSwarm", "a Swarm health check"),
        ("placementSwarm", "a Swarm placement"),
        ("updateConfigSwarm", "a Swarm update configuration"),
        ("restartPolicySwarm", "a Swarm restart policy"),
    ):
        if app.get(key):
            proposal.warn(
                f"It had {what}, which has no equivalent: Noust's health gate asks the "
                "application's own address after each deployment."
            )
