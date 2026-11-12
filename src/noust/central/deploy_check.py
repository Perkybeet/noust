# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Can this central deploy on the machine's own Docker, and what to change when it cannot.

The container of a NAS deploys containers on the NAS's Docker only when it is asked twice, on
purpose and outside the console: Docker's socket mounted into it, and ``central.deploy_here``
(:mod:`noust.central`). Between the two lies everything that goes wrong on a NAS, and each of it goes
wrong later and far from its cause: a socket the central's account may not open fails at the first
deploy, and a folder that is not at the same path inside the container and on the machine does not
fail at all, it makes a stack keep its data somewhere nobody looks. This is the check that finds it
first, each failure with the change that fixes it:

- **setting**: the central was told to deploy here (a server always deploys here);
- **socket**: Docker's socket is there to be opened;
- **engine**: the engine's doctor (:mod:`noust.core.docker.engine`) says Noust can use this Docker:
  its API, and Compose v2, which an old engine or a minimal image lacks;
- **group**: the account Noust runs as may open the socket, which is the group of the socket and not
  a thing the container can guess;
- **apps_folder**: the folder of the applications is *the same folder* inside the container and on
  the machine. A stack's ``./data`` is resolved by the Docker daemon, on the machine, so a folder the
  container sees under another path is a folder a stack mounts empty. It is proved, not assumed: a
  witness file is written in it and a throw-away container started by the daemon, with the folder
  mounted from the machine's side, has to read it;
- **data_folder**: the same for Noust's own data, which a volume copy and a database engine in a
  container are handed paths of. It is a warning, not a failure: deploying works without it, those two
  do not.

Nothing here is guessed. A check that cannot be made because an earlier one failed is said to be
unchecked, with the reason, never passed.
"""

from __future__ import annotations

import grp
import os
import pwd
import secrets
import shlex
import socket
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust import central
from noust.central import machine
from noust.core import paths
from noust.core.config import Config
from noust.core.docker import witness as witness_module
from noust.core.docker.engine import DEFAULT_SOCKET, EngineReport
from noust.core.fs import get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.core.server_text import ServerText, say
from noust.managers.docker_volumes import HELPER_IMAGE

__all__ = [
    "CHECKS",
    "STATUS_FAIL",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "STATUS_WARN",
    "Check",
    "Report",
    "Seams",
    "SocketAccess",
    "check_deploy_here",
    "default_seams",
    "read_socket_access",
]

#: The checks, in the order they are made and shown.
CHECKS = ("setting", "socket", "engine", "group", "apps_folder", "data_folder")

#: The check passed.
STATUS_OK = "ok"
#: The check failed, and the central cannot deploy until it is fixed.
STATUS_FAIL = "fail"
#: The check failed and deploying still works; something else does not.
STATUS_WARN = "warn"
#: The check could not be made because what it needs was not there: not a pass.
STATUS_SKIPPED = "skipped"

#: What the environment line that turns it on says.
SETTING_LINE = "NOUST_DEPLOY_HERE=containers"


@dataclass(frozen=True)
class SocketAccess:
    """
    What the container sees of Docker's socket.

    Attributes:
        path: Where it is.
        is_socket: What is there is a socket.
        gid: The number of the group that owns it.
        group: That group's name here, or its number when this system has no name for it.
        accessible: Noust, as the account it runs as, with the groups it was given, may read and
            write it.
    """

    path: str
    is_socket: bool
    gid: int
    group: str
    accessible: bool


def read_socket_access(path: str) -> SocketAccess | None:
    """
    Look at a socket the way the process that would open it does.

    Args:
        path: The socket's path.

    Returns:
        What is there, or None when nothing is.
    """
    try:
        status = os.stat(path)
    except OSError:
        return None
    try:
        group = grp.getgrgid(status.st_gid).gr_name
    except KeyError:
        group = str(status.st_gid)
    return SocketAccess(
        path=path,
        is_socket=stat.S_ISSOCK(status.st_mode),
        gid=status.st_gid,
        group=group,
        accessible=os.access(path, os.R_OK | os.W_OK),
    )


@dataclass(frozen=True)
class Seams:
    """
    The few things the check asks of the machine that a test replaces.

    Attributes:
        socket_access: Looks at Docker's socket.
        token: Makes the witness file's name and content.
        hostname: This container's name, which Docker knows it by.
    """

    socket_access: Callable[[str], SocketAccess | None] = read_socket_access
    token: Callable[[], str] = lambda: secrets.token_hex(16)
    hostname: Callable[[], str] = socket.gethostname


def default_seams() -> Seams:
    """
    Returns:
        What the check asks of the machine itself. A function, so that a test of the command or the
        route, which cannot hand the check its own, replaces it in one place.
    """
    return Seams()


@dataclass(frozen=True)
class Check:
    """
    One thing that has to be so, and whether it is.

    Attributes:
        key: Which of :data:`CHECKS` it is.
        status: One of ``ok``, ``fail``, ``warn`` and ``skipped``.
        said: The sentence, with a code the console translates: what is so, or what is not and the
            change that fixes it in its fix.
    """

    key: str
    status: str
    said: ServerText

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The check as the API says it: ``{key, status, code, params, text, hint}``.
        """
        return {
            "key": self.key,
            "status": self.status,
            **self.said.to_dict(),
            "text": self.said.text,
            "hint": self.said.hint,
        }


@dataclass(frozen=True)
class Report:
    """
    Whether this central can deploy on the machine's Docker.

    Attributes:
        ready: No check failed: Noust can deploy here. A warning does not stop it.
        role: ``server`` or ``hub``.
        deploy_here: What the central was told to deploy here: ``containers`` or empty.
        checks: Each check, in the order of :data:`CHECKS` (the data folder only when Noust keeps
            its data in a folder of its own).
    """

    ready: bool
    role: str
    deploy_here: str
    checks: tuple[Check, ...]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The report as the API says it.
        """
        return {
            "ready": self.ready,
            "role": self.role,
            "deploy_here": self.deploy_here,
            "checks": [item.to_dict() for item in self.checks],
        }


def _setting(role: str, mode: str) -> Check:
    """
    Args:
        role: This Noust's role.
        mode: What it was told to deploy here.

    Returns:
        Whether it was asked to deploy on this machine.
    """
    if role == central.ROLE_SERVER:
        return Check("setting", STATUS_OK, say("deploy_here.setting.server"))
    if mode == central.DEPLOY_HERE_CONTAINERS:
        return Check("setting", STATUS_OK, say("deploy_here.setting.on"))
    return Check("setting", STATUS_FAIL, say("deploy_here.setting.off", variable=SETTING_LINE))


def _socket(path: str, found: SocketAccess | None) -> Check:
    """
    Args:
        path: Where Docker's socket should be.
        found: What is there.

    Returns:
        Whether there is a socket to open.
    """
    if found is None or not found.is_socket:
        return Check("socket", STATUS_FAIL, say("deploy_here.socket.missing", socket=path))
    return Check("socket", STATUS_OK, say("deploy_here.socket.found", socket=path))


def _user() -> str:
    """
    Returns:
        The account Noust runs as, by name when this system has one for it.
    """
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def _group(found: SocketAccess | None) -> Check:
    """
    Args:
        found: What is there at the socket's path, None when nothing is or it is not a socket.

    Returns:
        Whether the account Noust runs as may open the socket.
    """
    if found is None or not found.is_socket:
        return Check("group", STATUS_SKIPPED, say("deploy_here.unchecked.socket"))
    if found.accessible:
        return Check("group", STATUS_OK, say("deploy_here.group.ok", group=found.group))
    return Check(
        "group",
        STATUS_FAIL,
        say(
            "deploy_here.group.denied",
            user=_user(),
            group=found.group,
            gid=found.gid,
            command=f"stat -c %g {shlex.quote(found.path)}",
        ),
    )


def _engine(report: EngineReport, *, usable_socket: bool) -> Check:
    """
    Args:
        report: What the engine's doctor said just now.
        usable_socket: The socket is there and Noust may open it.

    Returns:
        Whether Noust can use this Docker, in the doctor's own words when it cannot.
    """
    if not usable_socket:
        return Check("engine", STATUS_SKIPPED, say("deploy_here.unchecked.socket"))
    if not report.info.installed:
        return Check("engine", STATUS_FAIL, say("deploy_here.engine.no_client"))
    if report.problem is not None:
        return Check("engine", STATUS_FAIL, report.problem)
    info = report.info
    return Check(
        "engine",
        STATUS_OK,
        say(
            "deploy_here.engine.ok",
            version=info.server_version,
            api=info.api,
            compose=info.compose_version,
        ),
    )


def _first_line(text: str) -> str:
    """
    Args:
        text: What a program said.

    Returns:
        Its first line that says something.
    """
    return next((line.strip() for line in text.splitlines() if line.strip()), "it said nothing")


def _folder(
    runner: CommandRunner,
    folder: Path,
    seams: Seams,
    *,
    key: str,
    bad: str,
    image: Callable[[], str],
) -> Check:
    """
    Prove that a folder is the same one inside the container and on the machine.

    A file with a random name and content is written in it, and a container the daemon starts (so
    from the machine's side) mounts the folder at the path the machine knows it by and reads the
    file back. What it prints is the proof; the file is removed whichever way it went.

    Args:
        runner: Runs Docker.
        folder: The folder, as the container sees it.
        seams: What a test replaces.
        key: The check's key.
        bad: The status a failure has: the applications' folder fails, the data folder warns.
        image: Picks the image to start the witness from, when it comes to that.

    Returns:
        The check.
    """
    shown = str(folder)
    if not folder.is_dir():
        return Check(key, bad, say("deploy_here.folder.missing_inside", folder=shown))
    token = seams.token()
    witness = folder / f".noust-witness-{token}"
    fs = get_fs()
    try:
        try:
            fs.write_text(witness, token, mode=0o644)
        except OSError:
            owner = f"{os.getuid()}:{os.getgid()}"
            command = f"chown -R {owner} {shlex.quote(shown)}"
            return Check(
                key, bad, say("deploy_here.folder.unwritable", folder=shown, command=command)
            )
        answer = witness_module.read_back(runner, folder, witness.name, image())
    finally:
        fs.remove(witness)
    if answer.success and answer.stdout.strip() == token:
        return Check(key, STATUS_OK, say("deploy_here.folder.ok", folder=shown))
    said = answer.stderr or answer.stdout
    if witness_module.NO_SOURCE in said:
        return Check(key, bad, say("deploy_here.folder.missing_outside", folder=shown))
    if "No such file" in said or answer.success:
        return Check(key, bad, say("deploy_here.folder.other", folder=shown))
    return Check(
        key, bad, say("deploy_here.folder.unverified", folder=shown, reason=_first_line(said))
    )


def check_deploy_here(
    runner: CommandRunner | None = None,
    *,
    config: Config | None = None,
    seams: Seams | None = None,
) -> Report:
    """
    Make the checks that say whether this central can deploy on its machine's Docker.

    It asks the engine afresh and writes, and removes, a witness file in the folders it proves; it
    changes nothing else.

    Args:
        runner: Runs Docker; the process-wide one by default.
        config: The configuration; the process-wide one by default.
        seams: What a test replaces.

    Returns:
        The report: every check in order, and whether none of them failed.

    Raises:
        ConfigError: When ``central.role`` or ``central.deploy_here`` holds what is not one of their
            values.
    """
    active = runner or get_runner()
    chosen = seams or default_seams()
    role, mode = central.role(config), central.deploy_here(config)
    # Asked once, up front: where the socket is comes from the same reading that says what is behind it.
    asked_report = machine.engine_report(active, fresh=True)
    path = asked_report.info.socket or DEFAULT_SOCKET
    found = chosen.socket_access(path)
    setting = _setting(role, mode)
    socket_check = _socket(path, found)
    group = _group(found)
    usable = socket_check.status == STATUS_OK and group.status == STATUS_OK
    engine = _engine(asked_report, usable_socket=usable)
    asked = engine.status == STATUS_OK
    cached: list[str] = []

    def image() -> str:
        if not cached:
            cached.append(witness_module.own_image(active, chosen.hostname(), HELPER_IMAGE))
        return cached[0]

    def folder(where: Path, key: str, bad: str) -> Check:
        if not asked:
            return Check(key, STATUS_SKIPPED, say("deploy_here.unchecked.docker"))
        return _folder(active, where, chosen, key=key, bad=bad, image=image)

    checks = [
        setting,
        socket_check,
        engine,
        group,
        folder(Path((config or Config()).get("apps_directory")), "apps_folder", STATUS_FAIL),
    ]
    if paths.DATA_DIR is not None:
        checks.append(folder(Path(paths.DATA_DIR), "data_folder", STATUS_WARN))
    return Report(
        ready=all(item.status != STATUS_FAIL for item in checks),
        role=role,
        deploy_here=mode,
        checks=tuple(checks),
    )
