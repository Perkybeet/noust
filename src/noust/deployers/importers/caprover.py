# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
CapRover, read through its API (checklist N-13).

CapRover keeps an application's configuration in its own data store (``/captain/data/config-captain.json``
on its server), so there is no file in a repository to read. Its API (``/api/v2``, Apache-2.0,
https://github.com/caprover/caprover) lists every application definition with one call after a login. This
module turns what it says into the :class:`~noust.deployers.importers.base.Proposal` every importer
answers with.

CapRover has no read-only credential: the login is the dashboard's password, which is the whole of
its authority. It is exchanged for a session token for the length of the command and written
nowhere. A server that asks for a one-time code (two-factor authentication) cannot be read this
way: the code expires before a person could type it into a second program, so the importer says so.

What carries over: the repository and branch of a webhook-deployed application, the domains
(the custom ones and ``<name>.<root domain>``), the port the container listens on, the environment
variables, the volumes (as ``--persist`` paths where they sit under ``/app``, with the commands that
copy the data), the databases the application connects to (one-click databases are applications
that run a database image, found by the host name CapRover gives each: ``srv-captain--<name>``) and
the instance count as a warning. What is only said, with what to do instead: a pre-deploy function
(JavaScript), a custom nginx configuration, HTTP basic authentication, extra networks, published
ports and an application CapRover built from a ``captain-definition`` (the definition is not part of
the application's record, so the proposal is a container application the operator creates from the
same repository).

Nothing here writes to CapRover: after the login it only issues GETs.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

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
    int_value,
    persistent_path_for,
    tarball_restore,
    text_value,
    volume_export,
)
from noust.deployers.importers.remote import Opener, RemoteClient, read_token
from noust.managers.database.instances import image_engine

PLATFORM = "caprover"
API = "/api/v2"

#: The header CapRover reads the namespace of the request from; ``captain`` is the only one.
NAMESPACE = ("x-namespace", "captain")

#: What CapRover answers when everything went well; any other ``status`` is an error that comes with HTTP 200.
STATUS_OK = 100

#: The answers to a login that have a sentence of their own.
_LOGIN_HINTS = {
    1105: "The password was not accepted. It is the dashboard's password.",
    1113: "CapRover is refusing logins for thirty seconds after wrong passwords: wait and retry.",
    1114: "This CapRover asks for a one-time code (two-factor authentication), which the "
    "importer cannot take. Turn it off in CapRover's settings for the import, or copy the "
    "application by hand.",
}


def connect(url: str, token: str, *, opener: Opener | None = None) -> RemoteClient:
    """
    Log in to a CapRover server and open a client on it.

    Args:
        url: Its address, ``https://captain.example.com``.
        token: The dashboard's password (CapRover has no read-only credential). It is exchanged
            for a session token and not kept.
        opener: Replacement for urlopen (tests).

    Returns:
        The client, which carries the session token.

    Raises:
        ValidationError: The password is empty or has a character a request cannot carry.
        IntegrationError: The server cannot be reached, is not CapRover, or refuses the login.
    """
    password = read_token(token, source="the password")
    client = RemoteClient(
        url,
        NAMESPACE,
        platform="CapRover",
        opener=opener,
        hints={
            401: "CapRover refused the request. Check the address and the password.",
            403: "CapRover refused the request. Check the address and the password.",
        },
    )
    answer = client.post_json(f"{API}/login", {"password": password})
    data = _data(answer, "the login")
    session = text_value(data, "token") if isinstance(data, dict) else None
    if session is None:
        raise IntegrationError("CapRover's login answered no session token")
    client.add_header("x-captain-auth", session)
    return client


def _data(answer: Any, what: str) -> Any:
    """
    Unwrap a CapRover answer.

    CapRover reports its own errors with HTTP 200 and a ``status`` of 1000 or more, so a call
    that raised nothing may still have failed.

    Args:
        answer: The decoded JSON.
        what: What was asked, for the message.

    Returns:
        The ``data`` of a successful answer.

    Raises:
        IntegrationError: The answer is not CapRover's, or reports an error.
    """
    if not isinstance(answer, dict) or "status" not in answer:
        raise IntegrationError(
            f"CapRover's answer to {what} is not CapRover's",
            details="Is this really the address of a CapRover server (its captain subdomain)?",
        )
    status = answer.get("status")
    if status != STATUS_OK:
        hint = _LOGIN_HINTS.get(status if isinstance(status, int) else 0, "")
        raise IntegrationError(
            f"CapRover refused {what}: {text_value(answer, 'description') or status}",
            details=hint or "Check the address and the password.",
        )
    return answer.get("data")


def _definitions(client: RemoteClient) -> tuple[list[dict[str, Any]], str]:
    """
    Read every application definition.

    Args:
        client: The client.

    Returns:
        The definitions, and the server's root domain (``example.com``).

    Raises:
        IntegrationError: They cannot be read.
    """
    data = _data(client.get(f"{API}/user/apps/appDefinitions"), "the application list")
    if not isinstance(data, dict):
        return [], ""
    found = data.get("appDefinitions")
    items = [item for item in found if isinstance(item, dict)] if isinstance(found, list) else []
    return items, text_value(data, "rootDomain") or ""


def _image(app: dict[str, Any]) -> str:
    """
    Args:
        app: A definition.

    Returns:
        The image the deployed version runs, or an empty string when nothing was deployed.
    """
    versions = app.get("versions")
    if not isinstance(versions, list):
        return ""
    deployed = int_value(app, "deployedVersion")
    for entry in versions:
        if isinstance(entry, dict) and entry.get("version") == deployed:
            return text_value(entry, "deployedImageName") or ""
    return ""


def _built_here(image: str) -> bool:
    """
    Args:
        image: The deployed image's name.

    Returns:
        Whether CapRover built it from a ``captain-definition`` (its images are ``img-captain-*``).
    """
    return image.startswith("img-captain-")


def _hosts(app: dict[str, Any], root: str) -> tuple[str, ...]:
    """
    Args:
        app: A definition.
        root: The server's root domain.

    Returns:
        The names the application answers on, custom first.
    """
    names: list[str] = []
    domains = app.get("customDomain")
    for entry in domains if isinstance(domains, list) else []:
        host = text_value(entry, "publicDomain") if isinstance(entry, dict) else None
        if host and host.lower() not in names:
            names.append(host.lower())
    name = text_value(app, "appName")
    if name and root and not app.get("notExposeAsWebApp"):
        names.append(f"{name}.{root}".lower())
    return tuple(names)


def _repository(app: dict[str, Any]) -> tuple[str | None, str | None]:
    """
    Read the repository a webhook-deployed application is pulled from.

    The definition carries the credentials of that repository (``password``, ``sshKey``); they are
    not read, and the address is returned without a user.

    Args:
        app: A definition.

    Returns:
        The clone URL and the branch; None for each CapRover does not say.
    """
    hook = app.get("appPushWebhook")
    info = hook.get("repoInfo") if isinstance(hook, dict) else None
    if not isinstance(info, dict):
        return None, None
    repo = text_value(info, "repo")
    branch = text_value(info, "branch")
    if repo is None:
        return None, branch
    if "://" not in repo and not repo.startswith("git@"):
        repo = f"https://{repo}"
    parts = urlsplit(repo)
    if parts.username or parts.password:
        port = f":{parts.port}" if parts.port else ""
        repo = f"{parts.scheme}://{parts.hostname}{port}{parts.path}"
    return repo, branch


def list_applications(client: RemoteClient) -> list[RemoteApplication]:
    """
    List the applications of a CapRover server.

    Args:
        client: The client.

    Returns:
        One entry per application, in CapRover's order; the databases among them (one-click
        applications that run a database image) are named by their engine in ``build``.

    Raises:
        IntegrationError: The server cannot be read.
    """
    definitions, root = _definitions(client)
    found: list[RemoteApplication] = []
    for app in definitions:
        name = text_value(app, "appName")
        if name is None:
            continue
        image = _image(app)
        engine = image_engine(image)
        repo, _branch = _repository(app)
        found.append(
            RemoteApplication(
                ref=name,
                name=name,
                build=f"database ({engine.engine})"
                if engine is not None
                else "captain-definition"
                if _built_here(image)
                else "image"
                if image
                else "",
                source=repo or ("" if _built_here(image) else image),
                domains=_hosts(app, root),
                project=text_value(app, "projectId") or "",
            )
        )
    return found


def read_application(client: RemoteClient, ref: str) -> Proposal:
    """
    Describe one CapRover application as a proposal.

    Args:
        client: The client.
        ref: The application's name.

    Returns:
        The proposal.

    Raises:
        IntegrationError: The server cannot be read, or has no such application.
    """
    definitions, root = _definitions(client)
    app = next((item for item in definitions if text_value(item, "appName") == ref), None)
    if app is None:
        raise IntegrationError(f"CapRover has no application {ref!r}")
    proposal = Proposal(platform=PLATFORM, files=[f"{client.base_url}/apps/{ref}"])
    proposal.name = ref
    proposal.source, proposal.branch = _repository(app)
    proposal.domains = list(_hosts(app, root))
    proposal.port = int_value(app, "containerHttpPort") or 80
    image = _image(app)

    _by_image(app, image, proposal)
    values = _environment(app, proposal)
    _storage(app, proposal)
    _databases(app, definitions, values, proposal)
    _unsupported(app, proposal)
    return proposal


def _by_image(app: dict[str, Any], image: str, proposal: Proposal) -> None:
    """
    Say what Noust does with the way CapRover ran the application.

    Args:
        app: The definition.
        image: The deployed image.
        proposal: The proposal being made.
    """
    engine = image_engine(image)
    if engine is not None and not proposal.source:
        proposal.app_type = "container"
        proposal.image = image
        proposal.warn(
            f"This application runs the database image {image}: it is a database, not an "
            f"application. Create it with 'noust db install {engine.engine}' and bring its data "
            "as the applications that use it say."
        )
        return
    if _built_here(image) or (not image and proposal.source):
        proposal.app_type = "container"
        source = proposal.source or "REPOSITORY"
        proposal.equivalent = (
            f"noust create -d DOMAIN -s {source} --type container --builder dockerfile"
        )
        proposal.warn(
            "CapRover built it from a captain-definition, which the application's record does not "
            "keep. If the repository has a Dockerfile, Noust runs it as a container application: "
            f"create it with {proposal.equivalent}"
        )
        return
    if image:
        proposal.app_type = "container"
        proposal.image = image
        proposal.equivalent = f"noust create -d DOMAIN --image {image}"
        proposal.warn(f"It runs a registry image. Create it with: {proposal.equivalent}")
        return
    proposal.warn(
        "Nothing was deployed to it yet: CapRover has an application record and no image."
    )


def _environment(app: dict[str, Any], proposal: Proposal) -> list[str]:
    """
    Add the application's variables to a proposal.

    Args:
        app: The definition.
        proposal: The proposal being made.

    Returns:
        Every value, secrets included, for finding which databases the application connects to.
    """
    seen: list[str] = []
    entries = app.get("envVars")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        name = text_value(entry, "key")
        if name is None:
            continue
        value = entry.get("value")
        text = "" if value is None else str(value)
        seen.append(text)
        proposal.add_env(declared_env(name, text))
    return seen


def _storage(app: dict[str, Any], proposal: Proposal) -> None:
    """
    Add the application's volumes to a proposal.

    Args:
        app: The definition.
        proposal: The proposal being made.
    """
    legacy = bool(app.get("isLegacyAppName"))
    volumes = app.get("volumes")
    built_from_code = _built_here(_image(app)) or bool(proposal.source)
    for entry in volumes if isinstance(volumes, list) else []:
        if not isinstance(entry, dict):
            continue
        target = text_value(entry, "containerPath")
        if target is None:
            continue
        host = text_value(entry, "hostPath")
        named = text_value(entry, "volumeName")
        volume = f"captain--{named}" if named and legacy else named
        # A CapRover application built from a Dockerfile runs wherever the Dockerfile says;
        # only the working directory the buildpacks share is known.
        persistent = persistent_path_for(target, "/app") if built_from_code else None
        storage = ProposedStorage(
            mount_path=target,
            volume=None if host else volume,
            host_path=host,
            persistent_path=persistent,
            read_only=(text_value(entry, "mode") or "").lower() == "ro",
            export=directory_export(host) if host else volume_export(volume) if volume else None,
            restore=tarball_restore(persistent),
        )
        proposal.storage.append(storage)
        if persistent and persistent not in proposal.persistent_paths:
            proposal.persistent_paths.append(persistent)
        proposal.warn(storage.describe())


def _databases(
    app: dict[str, Any], definitions: list[dict[str, Any]], values: list[str], proposal: Proposal
) -> None:
    """
    Find the databases the application connects to.

    A one-click database is an application like the others, and every application reaches it by
    the service name CapRover gives it, ``srv-captain--<name>``, so a variable that holds that name
    is the link.

    Args:
        app: The definition of the application.
        definitions: Every definition of the server.
        values: The values of the application's variables.
        proposal: The proposal being made.
    """
    me = text_value(app, "appName")
    haystack = "\n".join(values)
    for other in definitions:
        name = text_value(other, "appName")
        match = image_engine(_image(other))
        if name is None or match is None or name == me:
            continue
        service = f"srv-captain--{name}"
        if service not in haystack:
            continue
        engine = match.engine
        label = database_name_for(name)
        container = f"$(docker ps -qf name=^{service}\\.)"
        commands = database_commands(engine, container, label, label)
        dump, restore = commands if commands is not None else (None, None)
        image = _image(other)
        detail = ProposedDatabase(
            engine=engine,
            name=name,
            reference=service,
            image=image,
            version=image.rsplit(":", 1)[1] if ":" in image.rsplit("/", 1)[-1] else None,
            linked=True,
            dump=dump,
            restore=restore,
        )
        proposal.database_details.append(detail)
        if engine not in INSTANCE_ENGINES:
            proposal.need_database(engine)
        proposal.warn(detail.describe())


def _unsupported(app: dict[str, Any], proposal: Proposal) -> None:
    """
    Say what CapRover does for the application that has no equivalent.

    Args:
        app: The definition.
        proposal: The proposal being made.
    """
    count = int_value(app, "instanceCount")
    if count is not None and count > 1:
        proposal.warn(
            f"It ran {count} instances behind CapRover's load balancer. Noust runs one process "
            "per application on a server (a second server is a second Noust)."
        )
    if app.get("preDeployFunction"):
        proposal.warn(
            "It had a pre-deploy function (JavaScript run by CapRover before each deploy). Put "
            "what it did in a 'hooks.pre_deploy' command of noust.yaml."
        )
    if app.get("customNginxConfig"):
        proposal.warn(
            "It had a custom nginx configuration. Noust writes the site itself; edit the site "
            "afterwards in the console (Domains > the site) if it needs more."
        )
    auth = app.get("httpAuth")
    if isinstance(auth, dict) and text_value(auth, "user"):
        proposal.warn(
            f"It was behind HTTP basic authentication (user {text_value(auth, 'user')}); the "
            "password is not readable. Ask for one here with 'noust app policy set DOMAIN "
            "--password'."
        )
    networks = app.get("networks")
    if isinstance(networks, list) and networks:
        proposal.warn(
            "It joined extra Docker networks ("
            + ", ".join(str(item) for item in networks)
            + "). Noust applications reach each other on the server's loopback address."
        )
    ports = app.get("ports")
    published = (
        [
            f"{item.get('hostPort')}->{item.get('containerPort')}"
            for item in ports
            if isinstance(item, dict)
        ]
        if isinstance(ports, list)
        else []
    )
    if published:
        proposal.warn(
            "It published ports on the host (" + ", ".join(published) + "). Noust publishes a TCP"
            " port through nginx with 'noust stream'."
        )
    if app.get("forceSsl") is False:
        proposal.warn(
            "It was served over HTTP as well. Noust redirects to HTTPS once there is a "
            "certificate; 'noust app policy set DOMAIN --no-force-https' answers on both."
        )
    name = text_value(app, "appName") or "the application"
    if app.get("notExposeAsWebApp") and not proposal.database_details:
        proposal.warn(
            f"{name} was not exposed as a web application (CapRover kept it internal): if it is "
            "a worker, Noust runs it as a container application without a domain."
        )
