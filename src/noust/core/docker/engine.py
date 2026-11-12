# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which Docker there is, and what does not fit in it: the engine's doctor.

A panel that talks to an engine it does not control fails with the daemon's words (Docker 29 refused
every client under API 1.44 until 29.3, and a client newer than the engine is refused by it) and the
person who sees "client version 1.53 is too new" has nobody to ask. Noust asks the engine instead:
the version of the client and of the server, the API each speaks and the one they agree on, Compose,
where the images are kept, whether it runs without root and whose the socket is. :func:`read_engine`
reads that; :func:`diagnose` says it in sentences with a code (the console translates them) and says,
apart, what does not fit.

Three decisions, each from a way this goes wrong.

- **The API is asked, never fixed.** The one in use is the lower of the two sides, the way the client
  negotiates it, so a NAS whose Docker is 24.0.2 (API 1.43) is judged by what it speaks and not by
  what a newer client could.
- **The formats are the oldest client's.** ``--format '{{json .}}'`` exists in Docker 20.10 (Debian
  12) and ``--format json`` does not.
- **A fact Docker did not give is empty, not guessed.** An engine that does not answer has a client
  version and no server, and its own words in :attr:`EngineInfo.error`.
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import stat as stat_module
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from noust.core.runner import CommandRunner
from noust.core.server_text import ServerText, say

__all__ = [
    "ADOPTION_COMPOSE",
    "MIN_API",
    "MIN_COMPOSE",
    "EngineInfo",
    "EngineReport",
    "SocketInfo",
    "compose_answers",
    "daemon_answers",
    "diagnose",
    "read_engine",
    "version_tuple",
]

#: The oldest API Noust works with: Docker 20.10, the first engine Compose v2 supports. Debian 12's
#: own Docker is that one.
MIN_API = (1, 41)

#: The oldest Compose Noust works with: the plugin, version 2.
MIN_COMPOSE = (2, 0)

#: Rehearsing the adoption of a stack that already runs (``up --dry-run --no-build``) needs this one.
ADOPTION_COMPOSE = (2, 20)

#: What a question to the engine may take.
PROBE_TIMEOUT = 30

#: Where Docker's socket is unless it is told otherwise.
DEFAULT_SOCKET = "/var/run/docker.sock"

#: Where Docker says to install Compose.
COMPOSE_DOCS = "https://docs.docker.com/compose/install/"

_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")
_COMPOSE = re.compile(r"version\s+v?(\d+(?:\.\d+)*)", re.IGNORECASE)


def version_tuple(text: str) -> tuple[int, ...]:
    """
    Read a version as numbers, which is the only way to compare one.

    Args:
        text: ``1.52``, ``29.1.3``, ``v2.40.3`` or ``2.20.0-desktop.1``.

    Returns:
        The numbers before any suffix; empty when there are none.
    """
    match = _VERSION.match(text.strip())
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _shown(numbers: tuple[int, ...]) -> str:
    """
    Args:
        numbers: A version as numbers.

    Returns:
        ``1.41`` for ``(1, 41)``.
    """
    return ".".join(str(number) for number in numbers)


@dataclass(frozen=True)
class SocketInfo:
    """
    Whose Docker's socket is.

    Attributes:
        owner: The account that owns it.
        group: The group that owns it: whoever is in it may use Docker, which is root's power.
        mode: Its permission bits, ``0660``.
    """

    owner: str
    group: str
    mode: str


@dataclass(frozen=True)
class EngineInfo:
    """
    What Docker says of itself.

    Attributes:
        installed: There is a ``docker`` program.
        reachable: Its daemon answered.
        client_version: The program's version.
        server_version: The daemon's version; empty when it did not answer.
        client_api: The newest API the program speaks.
        server_api: The newest API the daemon speaks.
        min_api: The oldest API the daemon accepts.
        api: The API in use: the lower of the two sides, empty without a daemon.
        compose_version: The Compose plugin's version; empty without the plugin.
        compose_v1: There is a ``docker-compose`` program, which is Compose 1 and not the plugin.
        image_store: ``containerd`` or ``classic``; empty without a daemon.
        storage_driver: The driver that keeps the images (``overlay2``, ``overlayfs``...).
        rootless: The daemon runs without root.
        host: Where the program reaches the daemon (``unix:///var/run/docker.sock``).
        socket: The path of the socket when it is local.
        socket_owner: Whose it is.
        socket_group: Which group's.
        socket_mode: Its permission bits.
        root_dir: Where Docker keeps its data.
        os: The operating system Docker runs on, as it says.
        error: Docker's own words when the daemon did not answer, the first line.
    """

    installed: bool = False
    reachable: bool = False
    client_version: str = ""
    server_version: str = ""
    client_api: str = ""
    server_api: str = ""
    min_api: str = ""
    api: str = ""
    compose_version: str = ""
    compose_v1: bool = False
    image_store: str = ""
    storage_driver: str = ""
    rootless: bool = False
    host: str = ""
    socket: str = ""
    socket_owner: str = ""
    socket_group: str = ""
    socket_mode: str = ""
    root_dir: str = ""
    os: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass(frozen=True)
class EngineReport:
    """
    The engine in sentences.

    Attributes:
        info: What Docker said.
        facts: What is, as sentences with a code.
        limits: What does not fit, as sentences with a code and the fix of each.
        ok: Whether Noust can use this Docker: no limit is of the kind that stops it. A Compose that
            cannot rehearse an adoption is a limit and leaves it true.
    """

    info: EngineInfo
    facts: tuple[ServerText, ...]
    limits: tuple[ServerText, ...]
    ok: bool

    @property
    def problem(self) -> ServerText | None:
        """
        The first limit of the kind that stops Noust using this Docker, the one a person is told when
        a function needs it and it cannot be had (no engine, no answer, too old, no Compose v2).

        Returns:
            The sentence, or None when Noust can use this Docker.
        """
        return next((limit for limit in self.limits if limit.code in _BLOCKING), None)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            ``ok``, the facts and the limits as ``{code, params}`` with their English beside, and
            the information they were made from.
        """

        def one(text: ServerText) -> dict[str, Any]:
            return {**text.to_dict(), "text": text.text, "hint": text.hint}

        return {
            "ok": self.ok,
            "facts": [one(text) for text in self.facts],
            "limits": [one(text) for text in self.limits],
            "info": self.info.to_dict(),
        }


def _stat_socket(path: str) -> SocketInfo | None:
    """
    Read whose a socket is.

    Args:
        path: The socket's path.

    Returns:
        Its owner, group and mode, or None when it is not there to be read.
    """
    try:
        status = os.stat(path)
    except OSError:
        return None
    try:
        owner = pwd.getpwuid(status.st_uid).pw_name
    except KeyError:
        owner = str(status.st_uid)
    try:
        group = grp.getgrgid(status.st_gid).gr_name
    except KeyError:
        group = str(status.st_gid)
    return SocketInfo(owner, group, f"{stat_module.S_IMODE(status.st_mode):04o}")


def _json(text: str) -> Any:
    """
    Args:
        text: What a command printed.

    Returns:
        The JSON in it, or None when there is none.
    """
    try:
        return json.loads(text)
    except ValueError:
        return None


def _mapping(value: Any) -> dict[str, Any]:
    """
    Args:
        value: Anything.

    Returns:
        ``value`` when it is an object, else an empty one: Docker's JSON is read, never trusted.
    """
    return value if isinstance(value, dict) else {}


def _first_line(text: str) -> str:
    """
    Args:
        text: A tool's output.

    Returns:
        Its first line that says something.
    """
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _server_of(version: dict[str, Any]) -> tuple[str, str, str]:
    """
    Read the daemon out of ``docker version``.

    Args:
        version: Its JSON.

    Returns:
        The daemon's version, its API and the oldest API it accepts: from the top of ``Server`` or,
        in the shape older engines print, from the ``Engine`` component.
    """
    server = _mapping(version.get("Server"))
    number, api, minimum = (
        str(server.get("Version") or ""),
        str(server.get("ApiVersion") or ""),
        str(server.get("MinAPIVersion") or ""),
    )
    if number and api:
        return number, api, minimum
    for component in server.get("Components") or []:
        if isinstance(component, dict) and component.get("Name") == "Engine":
            details = _mapping(component.get("Details"))
            return (
                str(component.get("Version") or number),
                str(details.get("ApiVersion") or api),
                str(details.get("MinAPIVersion") or minimum),
            )
    return number, api, minimum


def _lower_api(client: str, server: str) -> str:
    """
    Args:
        client: The newest API the program speaks.
        server: The newest API the daemon speaks.

    Returns:
        The one in use: the lower, the way the client negotiates it; empty when the daemon did not
        say.
    """
    if not server:
        return ""
    if not client:
        return server
    return client if version_tuple(client) < version_tuple(server) else server


def read_engine(
    runner: CommandRunner,
    *,
    stat_socket: Callable[[str], SocketInfo | None] = _stat_socket,
) -> EngineInfo:
    """
    Ask Docker what it is.

    Args:
        runner: Runs the questions, each a command that only looks and has a deadline.
        stat_socket: Reads whose the socket is; replaced in a test.

    Returns:
        The facts. Nothing is asked when there is no ``docker`` program.
    """
    if not runner.exists("docker"):
        return EngineInfo()
    answer = runner.run(["docker", "version", "--format", "{{json .}}"], timeout=PROBE_TIMEOUT)
    version = _mapping(_json(answer.stdout))
    client = _mapping(version.get("Client"))
    server_version, server_api, min_api = _server_of(version)
    reachable = answer.success and bool(server_version)
    info_raw: dict[str, Any] = {}
    if reachable:
        asked = runner.run(["docker", "info", "--format", "{{json .}}"], timeout=PROBE_TIMEOUT)
        info_raw = _mapping(_json(asked.stdout)) if asked.success else {}
    compose = runner.run(["docker", "compose", "version"], timeout=PROBE_TIMEOUT)
    compose_match = _COMPOSE.search(compose.stdout) if compose.success else None
    options = [str(option) for option in info_raw.get("SecurityOptions") or []]
    status = [row for row in info_raw.get("DriverStatus") or [] if isinstance(row, list)]
    containerd = any("containerd.snapshotter" in " ".join(map(str, row)) for row in status)
    host, path = _host_of(runner)
    owned = stat_socket(path) if path else None
    api = _lower_api(str(client.get("ApiVersion") or ""), server_api)
    return EngineInfo(
        installed=True,
        reachable=reachable,
        client_version=str(client.get("Version") or ""),
        server_version=server_version if reachable else "",
        client_api=str(client.get("ApiVersion") or ""),
        server_api=server_api if reachable else "",
        min_api=min_api if reachable else "",
        api=api if reachable else "",
        compose_version=compose_match.group(1) if compose_match else "",
        compose_v1=compose_match is None and runner.exists("docker-compose"),
        image_store=("containerd" if containerd else "classic") if info_raw else "",
        storage_driver=str(info_raw.get("Driver") or ""),
        rootless=any("name=rootless" in option for option in options),
        host=host,
        socket=path,
        socket_owner=owned.owner if owned else "",
        socket_group=owned.group if owned else "",
        socket_mode=owned.mode if owned else "",
        root_dir=str(info_raw.get("DockerRootDir") or ""),
        os=str(info_raw.get("OperatingSystem") or ""),
        error="" if reachable else _first_line(answer.stderr or answer.stdout),
    )


def compose_answers(runner: CommandRunner, *, timeout: int = PROBE_TIMEOUT) -> bool:
    """
    Ask whether ``docker compose`` (the plugin, Compose v2) answers.

    Args:
        runner: Runs the question.
        timeout: Seconds it has.

    Returns:
        True when it does.
    """
    return runner.run(["docker", "compose", "version"], timeout=timeout).success


def daemon_answers(runner: CommandRunner, *, timeout: int = PROBE_TIMEOUT) -> bool:
    """
    Ask whether the Docker daemon answers.

    Args:
        runner: Runs the question.
        timeout: Seconds it has.

    Returns:
        True when ``docker info`` does.
    """
    return runner.run(["docker", "info"], timeout=timeout).success


def _host_of(runner: CommandRunner) -> tuple[str, str]:
    """
    Ask where the program reaches the daemon.

    Args:
        runner: Runs ``docker context inspect``.

    Returns:
        The endpoint (``unix:///var/run/docker.sock``, ``ssh://deploy@nas``...) and, when it is a
        socket of this machine, its path.
    """
    result = runner.run(["docker", "context", "inspect"], timeout=PROBE_TIMEOUT)
    contexts = _json(result.stdout) if result.success else None
    first = _mapping(contexts[0]) if isinstance(contexts, list) and contexts else {}
    host = str(_mapping(_mapping(first.get("Endpoints")).get("docker")).get("Host") or "")
    if host.startswith("unix://"):
        return host, host.removeprefix("unix://")
    return host, ""


#: The limits that stop Noust using this Docker. The others leave something undone and nothing else.
_BLOCKING = frozenset(
    {
        "docker.engine.missing",
        "docker.engine.unreachable",
        "docker.engine.too_old",
        "docker.compose.missing",
        "docker.compose.v1",
    }
)


def diagnose(info: EngineInfo) -> EngineReport:
    """
    Say in sentences which Docker there is and what does not fit in it.

    Args:
        info: What :func:`read_engine` found.

    Returns:
        The facts, the limits and whether Noust can use this Docker.
    """
    facts: list[ServerText] = []
    limits: list[ServerText] = []
    if not info.installed:
        limits.append(say("docker.engine.missing", command="noust setup init"))
        return EngineReport(info, (), tuple(limits), ok=False)
    if not info.reachable:
        limits.append(
            say(
                "docker.engine.unreachable",
                reason=info.error or "it did not say why",
                command="systemctl start docker",
                socket=info.socket or DEFAULT_SOCKET,
            )
        )
    else:
        facts.append(say("docker.engine.version", version=info.server_version, api=info.api))
        if version_tuple(info.api) < MIN_API:
            limits.append(
                say(
                    "docker.engine.too_old",
                    version=info.server_version,
                    api=info.api,
                    minimum=_shown(MIN_API),
                )
            )
    if info.compose_version:
        facts.append(say("docker.compose.version", version=info.compose_version))
        if version_tuple(info.compose_version) < MIN_COMPOSE:
            limits.append(say("docker.compose.v1", url=COMPOSE_DOCS))
        elif version_tuple(info.compose_version) < ADOPTION_COMPOSE:
            limits.append(
                say(
                    "docker.compose.too_old",
                    version=info.compose_version,
                    needed=_shown(ADOPTION_COMPOSE),
                )
            )
    elif info.compose_v1:
        limits.append(say("docker.compose.v1", url=COMPOSE_DOCS))
    else:
        limits.append(say("docker.compose.missing", command="noust setup init"))
    if info.reachable and info.image_store:
        facts.append(
            say(
                "docker.engine.image_store",
                store=info.image_store,
                driver=info.storage_driver or "unknown",
            )
        )
    if info.socket:
        facts.append(
            say(
                "docker.engine.socket",
                socket=info.socket,
                owner=info.socket_owner or "?",
                group=info.socket_group or "?",
                mode=info.socket_mode or "?",
            )
        )
    elif info.host:
        facts.append(say("docker.engine.remote", host=info.host))
    if info.rootless:
        facts.append(say("docker.engine.rootless"))
    return EngineReport(
        info,
        tuple(facts),
        tuple(limits),
        ok=not any(limit.code in _BLOCKING for limit in limits),
    )
