# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Dokku, read through its own command line (checklist N-13).

Dokku has no HTTP API. Its interface is the ``dokku`` command, on its server or over SSH (``ssh
dokku@host apps:list``), and its configuration is in files and plugin data only that command
reads reliably. This module runs the read-only commands (``apps:list``, ``config:export``, the
``*:report`` family with a flag that prints one value, ``cron:list``) through the one
:class:`~noust.core.runner.CommandRunner`, and turns what they print into the
:class:`~noust.deployers.importers.base.Proposal` every importer answers with.

Where it runs is the operator's choice: on the Dokku server itself (Noust installed beside it, or
replacing it), or from another machine with ``--ssh dokku@host``, which needs a key Dokku accepts
and a host key already known (a changed or unknown key is never accepted silently here).

What carries over: the environment (the variables Dokku sets itself are left out), the domains, the
port the container listens on (from the port map), the memory limit, the persistent storage (as
``--persist`` paths where it sits under ``/app``, with the commands that copy the data), the cron
tasks of ``app.json`` and the databases of Dokku's service plugins the application is linked to
(found by the host name the plugin gives them, ``dokku-postgres-<service>``), with the plugin's own
``<engine>:export`` as the way to dump them. What is only said, with what to do instead: the
builder (Noust detects the stack, and runs a Dockerfile as a container application), an application
deployed from an image, the code itself (it lives in a bare repository on the Dokku server and
Noust deploys from a repository it can fetch) and scaling.

Nothing here writes to Dokku.
"""

from __future__ import annotations

import re
import shlex

from noust.core.exceptions import IntegrationError, ValidationError
from noust.core.runner import CommandRunner
from noust.deployers.importers.base import (
    INSTANCE_ENGINES,
    Proposal,
    ProposedCron,
    ProposedDatabase,
    ProposedStorage,
    RemoteApplication,
    database_name_for,
    declared_env,
    persistent_path_for,
    tarball_restore,
)
from noust.deployers.importers.coolify import cron_of

PLATFORM = "dokku"

#: How long one ``dokku`` command gets: they read a few files, and a remote one adds a connection.
COMMAND_TIMEOUT = 60

#: Shapes of the commands this module runs, for ``--dry-run`` (see :mod:`noust.core.runner`):
#: the same read-only commands locally and over SSH as the ``dokku`` user.
_SUBCOMMANDS: tuple[tuple[str, ...], ...] = (
    ("apps:list",),
    ("config:export", "*"),
    ("config:export", "--format", "*", "*"),
    ("domains:report", "*", "--*"),
    ("ports:report", "*", "--*"),
    ("storage:report", "*", "--*"),
    ("builder:report", "*", "--*"),
    ("git:report", "*", "--*"),
    ("resource:report", "*"),
    ("cron:list", "*"),
    ("ps:scale", "*"),
)
_SSH = ("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "*")
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    *(("dokku", "--quiet", *shape) for shape in _SUBCOMMANDS),
    *((*_SSH, *shape) for shape in _SUBCOMMANDS),
)

#: What Dokku accepts as an application's name.
_APP_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")

#: ``user@host``, ``host`` or ``host:port`` is not an SSH target here: the user is ``dokku`` and the
#: port is the default; anything else would let an argument be read as an option or a command.
_HOST = re.compile(r"(?:dokku@)?([A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)")

#: A service plugin's hostname inside Dokku's network: ``dokku-<plugin>-<service>``.
_SERVICE_HOST = re.compile(
    r"dokku-(postgres|mysql|mariadb|mongo|redis|clickhouse|memcached|rabbitmq|nats|elasticsearch)"
    r"-([A-Za-z0-9][A-Za-z0-9_-]*)"
)

#: The plugin and the engine each name.
_PLUGIN_ENGINES = {
    "postgres": "postgresql",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "mongo": "mongodb",
    "redis": "redis",
    "clickhouse": "clickhouse",
}

#: What the plugin's ``export`` writes and what loads it here.
_EXPORT_EXTENSIONS = {"postgresql": "dump", "mysql": "sql", "mariadb": "sql", "mongodb": "archive"}

#: Variables Dokku and its plugins keep in an application's environment for themselves.
_OWN_VARIABLES = re.compile(r"(DOKKU_.*|GIT_REV|NO_VHOST)")


class DokkuClient:
    """
    The ``dokku`` command, here or over SSH.

    Args:
        runner: The one runner every process goes through.
        host: The Dokku server as ``host`` or ``dokku@host``; None for ``dokku`` on this machine.
    """

    def __init__(self, runner: CommandRunner, host: str | None = None) -> None:
        self.runner = runner
        self.host = _target(host) if host is not None else None
        self.base_url = f"dokku@{self.host}" if self.host else "this server's dokku"

    def run(self, *args: str, check: bool = True) -> str:
        """
        Run one read-only command.

        Args:
            *args: The ``dokku`` subcommand and its arguments.
            check: Raise when it fails; otherwise a failure is an empty answer.

        Returns:
            Its standard output.

        Raises:
            IntegrationError: It failed, or Dokku is not there.
        """
        if self.host:
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", f"dokku@{self.host}"]
            argv += list(args)
        else:
            argv = ["dokku", "--quiet", *args]
        result = self.runner.run(argv, timeout=COMMAND_TIMEOUT)
        if result.success:
            return result.stdout
        if not check:
            return ""
        raise IntegrationError(
            f"Dokku refused 'dokku {' '.join(args)}'",
            details=(
                "Check that the host key is known (ssh dokku@HOST once) and that your key is "
                "one Dokku accepts."
                if self.host
                else "Run this as root on the Dokku server, where the dokku command is."
            ),
            output=result.stderr.strip() or result.stdout.strip(),
        )

    def report(self, subcommand: str, app: str, flag: str) -> str:
        """
        Read one value of a ``*:report``.

        Args:
            subcommand: ``domains:report``...
            app: The application.
            flag: The flag that prints the one value (``--domains-app-vhosts``).

        Returns:
            The value, stripped; empty when the flag does not exist in this Dokku.
        """
        return self.run(subcommand, _app(app), flag, check=False).strip()


def _target(host: str) -> str:
    """
    Check the Dokku server to connect to.

    Args:
        host: What the operator gave.

    Returns:
        The host name.

    Raises:
        ValidationError: It is not a host name (a user other than dokku, a port, an option).
    """
    found = _HOST.fullmatch(host.strip())
    if found is None:
        raise ValidationError(
            f"{host!r} is not a Dokku server to connect to",
            details="Give it as dokku@dokku.example.com or dokku.example.com: the user is dokku.",
            field="ssh",
        )
    return found.group(1)


def _app(name: str) -> str:
    """
    Args:
        name: An application's name.

    Returns:
        It, checked.

    Raises:
        ValidationError: It is not a name Dokku accepts (so it cannot be read as an option).
    """
    if not _APP_NAME.fullmatch(name):
        raise ValidationError(f"{name!r} is not a Dokku application name", field="app")
    return name


def connect(runner: CommandRunner, host: str | None = None) -> DokkuClient:
    """
    Open a client on a Dokku server.

    Args:
        runner: The runner.
        host: The server over SSH, or None for this machine.

    Returns:
        The client.
    """
    return DokkuClient(runner, host)


def list_applications(client: DokkuClient) -> list[RemoteApplication]:
    """
    List the applications of a Dokku server.

    Args:
        client: The client.

    Returns:
        One entry per application, with its builder and domains.

    Raises:
        IntegrationError: Dokku cannot be asked.
    """
    found: list[RemoteApplication] = []
    for line in client.run("apps:list").splitlines():
        name = line.strip()
        if not _APP_NAME.fullmatch(name):
            continue
        found.append(
            RemoteApplication(
                ref=name,
                name=name,
                build=client.report("builder:report", name, "--builder-computed-selected")
                or client.report("builder:report", name, "--builder-selected"),
                domains=tuple(_domains(client, name)),
            )
        )
    return found


def _domains(client: DokkuClient, app: str) -> list[str]:
    """
    Args:
        client: The client.
        app: The application.

    Returns:
        The names it answers on, when its domains are enabled.
    """
    if client.report("domains:report", app, "--domains-app-enabled") == "false":
        return []
    return list(client.report("domains:report", app, "--domains-app-vhosts").split())


def read_application(client: DokkuClient, ref: str) -> Proposal:
    """
    Describe one Dokku application as a proposal.

    Args:
        client: The client.
        ref: The application's name.

    Returns:
        The proposal.

    Raises:
        IntegrationError: Dokku cannot be asked.
        ValidationError: The name is not one Dokku accepts.
    """
    app = _app(ref)
    names = {line.strip() for line in client.run("apps:list").splitlines()}
    if app not in names:
        raise IntegrationError(f"Dokku has no application {app!r}")
    proposal = Proposal(platform=PLATFORM, files=[f"{client.base_url}:{app}"])
    proposal.name = app
    proposal.domains = _domains(client, app)
    proposal.branch = client.report("git:report", app, "--git-computed-deploy-branch") or (
        client.report("git:report", app, "--git-deploy-branch") or None
    )
    proposal.port = _container_port(client, app)
    values = _environment(client, app, proposal)
    _by_builder(client, app, proposal)
    _storage(client, app, proposal)
    _limits(client, app, proposal)
    _cron(client, app, proposal)
    _databases(client, values, proposal)
    _scale(client, app, proposal)
    _code(client, app, proposal)
    return proposal


def _container_port(client: DokkuClient, app: str) -> int | None:
    """
    Args:
        client: The client.
        app: The application.

    Returns:
        The port the container listens on, from the port map (``http:80:5000`` -> 5000); the
        detected one when the map is empty.
    """
    for flag in ("--ports-map", "--ports-map-detected"):
        mapping = client.report("ports:report", app, flag)
        for item in mapping.split():
            parts = item.split(":")
            if len(parts) == 3 and parts[2].isdigit():
                return int(parts[2])
    return None


def _environment(client: DokkuClient, app: str, proposal: Proposal) -> list[str]:
    """
    Add the application's variables to a proposal.

    Args:
        client: The client.
        app: The application.
        proposal: The proposal being made.

    Returns:
        Every value, secrets included, for finding the services the application is linked to.
    """
    text = client.run("config:export", app, check=False)
    try:
        tokens = shlex.split(text)
    except ValueError:
        proposal.warn("The variables could not be read (an unmatched quote); copy them by hand.")
        return []
    seen: list[str] = []
    for word in tokens:
        if word == "export" or "=" not in word:
            continue
        name, _, value = word.partition("=")
        if not name or _OWN_VARIABLES.fullmatch(name):
            continue
        seen.append(value)
        proposal.add_env(declared_env(name, value))
    return seen


def _by_builder(client: DokkuClient, app: str, proposal: Proposal) -> None:
    """
    Say what Noust does with the way Dokku built the application.

    Args:
        client: The client.
        app: The application.
        proposal: The proposal being made.
    """
    builder = client.report("builder:report", app, "--builder-computed-selected") or (
        client.report("builder:report", app, "--builder-selected")
    )
    if builder == "dockerfile":
        proposal.app_type = "container"
        proposal.equivalent = (
            "noust create -d DOMAIN -s REPOSITORY --type container --builder dockerfile"
        )
        proposal.warn(
            "It is built from a Dockerfile: Noust runs it as a container application. "
            f"Create it with: {proposal.equivalent}"
        )
    elif builder in ("lambda", "null"):
        proposal.warn(
            f"Its builder is {builder}, which has no equivalent: Noust builds an application from "
            "its source or runs an image."
        )
    else:
        proposal.warn(
            f"Dokku built it with {builder or 'its default builder (buildpacks)'}. Noust detects "
            "the stack itself and builds it with the commands of its type; a Procfile is read."
        )


def _storage(client: DokkuClient, app: str, proposal: Proposal) -> None:
    """
    Add the application's persistent storage to a proposal.

    Args:
        client: The client.
        app: The application.
        proposal: The proposal being made.
    """
    mounts = client.report("storage:report", app, "--storage-run-mounts") or client.report(
        "storage:report", app, "--storage-deploy-mounts"
    )
    try:
        words = shlex.split(mounts)
    except ValueError:
        proposal.warn("The storage could not be read; copy its data by hand.")
        return
    seen: set[str] = set()
    index = 0
    while index < len(words):
        if words[index] != "-v" or index + 1 >= len(words):
            index += 1
            continue
        spec = words[index + 1].split(":")
        index += 2
        if len(spec) < 2 or spec[1] in seen:
            continue
        seen.add(spec[1])
        host, target = spec[0], spec[1]
        persistent = persistent_path_for(target, "/app")
        storage = ProposedStorage(
            mount_path=target,
            host_path=host if host.startswith("/") else None,
            volume=None if host.startswith("/") else host,
            persistent_path=persistent,
            read_only="ro" in spec[2:],
            export=f"tar -C {shlex.quote(host)} -czf {shlex.quote(app)}-storage.tar.gz ."
            if host.startswith("/")
            else f"docker run --rm -v {shlex.quote(host)}:/data:ro alpine tar -C /data -czf - . "
            f"> {shlex.quote(host)}.tar.gz",
            restore=tarball_restore(persistent),
        )
        proposal.storage.append(storage)
        if persistent and persistent not in proposal.persistent_paths:
            proposal.persistent_paths.append(persistent)
        proposal.warn(storage.describe())


def _limits(client: DokkuClient, app: str, proposal: Proposal) -> None:
    """
    Read the memory and processor limits of the web process.

    Args:
        client: The client.
        app: The application.
        proposal: The proposal being made.
    """
    text = client.run("resource:report", app, check=False)
    for line in text.splitlines():
        match = re.fullmatch(
            r"\s*(?:resource )?(?:web )?limit (memory|cpu):\s*(\S+)\s*", line, re.IGNORECASE
        )
        if match is None:
            continue
        kind, value = match.group(1).lower(), match.group(2)
        if kind == "memory":
            proposal.memory_max_mb = _memory_mb(value) or proposal.memory_max_mb
        else:
            try:
                cores = float(value)
            except ValueError:
                continue
            if cores > 0:
                proposal.cpu_quota_percent = round(cores * 100)


def _memory_mb(value: str) -> int | None:
    """
    Args:
        value: Dokku's memory limit: megabytes by default, or with a unit (``512m``, ``1g``).

    Returns:
        Megabytes, or None.
    """
    found = re.fullmatch(r"(\d+)\s*([kmgKMG]?)[bB]?", value.strip())
    if not found or int(found.group(1)) == 0:
        return None
    amount, unit = int(found.group(1)), found.group(2).lower()
    scale = {"": 1, "k": 1 / 1024, "m": 1, "g": 1024}[unit]
    return max(1, round(amount * scale))


def _cron(client: DokkuClient, app: str, proposal: Proposal) -> None:
    """
    Add the cron tasks of the application's ``app.json``.

    Args:
        client: The client.
        app: The application.
        proposal: The proposal being made.
    """
    for line in client.run("cron:list", app, check=False).splitlines():
        match = re.fullmatch(r"(\S+)\s{2,}(.+?)\s{2,}(.+?)\s*", line)
        if match is None or match.group(1) == "ID":
            continue
        schedule = cron_of(match.group(2))
        if schedule is None:
            proposal.warn(
                f"The cron task {match.group(3)!r} runs {match.group(2)!r}, which is not a cron "
                "expression Noust can read: create it with 'noust cron create'."
            )
            continue
        proposal.cron.append(
            ProposedCron(
                name=f"dokku-{match.group(1)[:12]}", schedule=schedule, command=match.group(3)
            )
        )


def _databases(client: DokkuClient, values: list[str], proposal: Proposal) -> None:
    """
    Find the service plugins' databases the application is linked to.

    ``dokku postgres:link`` writes ``DATABASE_URL`` with the service's host name, so a value that
    holds ``dokku-postgres-<service>`` is the link.

    Args:
        client: The client.
        values: The values of the application's variables.
        proposal: The proposal being made.
    """
    found: dict[tuple[str, str], None] = {}
    for value in values:
        for match in _SERVICE_HOST.finditer(value):
            found.setdefault((match.group(1), match.group(2)), None)
    for plugin, service in found:
        engine = _PLUGIN_ENGINES.get(plugin)
        if engine is None:
            proposal.warn(
                f"It is linked to the {plugin} service {service!r}, which Noust has no engine for."
            )
            continue
        extension = _EXPORT_EXTENSIONS.get(engine)
        dump = restore = None
        if extension is not None:
            dump = f"dokku {plugin}:export {service} > {service}.{extension}"
            restore = (
                f"noust db import {database_name_for(service)} {service}.{extension} -e {engine}"
            )
        detail = ProposedDatabase(
            engine=engine,
            name=service,
            reference=f"dokku-{plugin}-{service}",
            linked=True,
            dump=dump,
            restore=restore,
        )
        proposal.database_details.append(detail)
        if engine not in INSTANCE_ENGINES:
            proposal.need_database(engine)
        proposal.warn(detail.describe())


def _scale(client: DokkuClient, app: str, proposal: Proposal) -> None:
    """
    Say what Dokku's scaling means here.

    Args:
        client: The client.
        app: The application.
        proposal: The proposal being made.
    """
    scaled: dict[str, int] = {}
    for line in client.run("ps:scale", app, check=False).splitlines():
        match = re.fullmatch(r"\s*([A-Za-z0-9_-]+):\s*(\d+)\s*", line)
        if match is not None and int(match.group(2)) > 0:
            scaled[match.group(1)] = int(match.group(2))
    extra = {kind: count for kind, count in scaled.items() if kind != "web"}
    if scaled.get("web", 1) > 1:
        proposal.warn(
            f"It ran {scaled['web']} copies of its web process. Noust runs one process per "
            "application on a server (a second server is a second Noust)."
        )
    if extra:
        proposal.warn(
            "It also ran the process types "
            + ", ".join(f"{kind} x{count}" for kind, count in extra.items())
            + ". Noust runs the types of a Procfile as workers beside the web process."
        )


def _code(client: DokkuClient, app: str, proposal: Proposal) -> None:
    """
    Say where the code is, since Dokku keeps it on its own server.

    Args:
        client: The client.
        app: The application.
        proposal: The proposal being made.
    """
    where = f"ssh://dokku@{client.host}/{app}" if client.host else f"/home/dokku/{app}"
    proposal.warn(
        f"Dokku keeps the code in a repository on its server ({where}), not at a host Noust "
        "can fetch from. Push the same repository to one Noust deploys from "
        "(GitHub, GitLab, your own), then create the application with --source."
    )
