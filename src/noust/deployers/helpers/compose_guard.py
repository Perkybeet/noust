# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What in a Docker Compose file is root on the host.

A compose file comes from the repository, and ``docker compose up`` runs it
through the Docker daemon, which is root. A service that is ``privileged`` or
mounts the Docker socket owns the server: a container with the socket starts
another with ``/`` mounted and writes wherever it likes. That is the class of
defect behind Coolify's CVE-2025-64419 and Dokploy's CVE-2026-72901, and until
3.1 nothing here looked.

:func:`inspect_compose` reads a parsed compose file and sorts what it finds:
``privileged: true`` and a mount of ``docker.sock`` are refused for a new
deployment unless an operator recorded an exception for the application
(:func:`noust.deployers.helpers.sandbox.set_compose_exception`, with a reason);
a stack proven to run with them already (the compose file of the commit that
served, the file a redeploy replaces, containers an adoption found running
unchanged from the file) is warned about, never stopped, and anything an
update, a redeploy or an adoption adds is refused like a new stack's.

A stack is also kept to its own files. A bind mount of Noust's own state
(``/etc/noust``, ``/var/lib/noust``, the backups: the console's signing key, the
store, the audit key), of what contains it (``/``, ``/etc``, ``/var``), or of
another application's tree (or of the directory that holds them all) hands the
container the secrets of everything else on the server, which is what the
account an application runs as exists to prevent for the applications that
have one. Those are refused like ``privileged``, with the same recorded
exception as the way out. Paths are resolved before they are judged: ``..``
and a relative source that climbs out of the application are the other
application's directory, whatever they look like.

The host's namespaces (``pid``, ``network_mode``, ``ipc``, ``userns_mode: host``),
added capabilities, devices, disabled confinement and bind mounts of any other
host path outside the application are warned about: some stacks need them, and
the operator should know which do.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from noust.core.compose_yaml import load_compose

#: A mount whose source ends like this is the Docker daemon's API.
DOCKER_SOCKET = "docker.sock"

#: ``security_opt`` values that switch the container's confinement off.
_UNCONFINED = (
    "apparmor:unconfined",
    "apparmor=unconfined",
    "seccomp:unconfined",
    "seccomp=unconfined",
    "label:disable",
    "label=disable",
    "systempaths=unconfined",
)

#: Namespaces a service can share with the host.
_HOST_NAMESPACES = ("pid", "network_mode", "ipc", "userns_mode", "uts", "cgroup")


@dataclass
class ComposeFindings:
    """
    What a compose file asks of the host.

    Attributes:
        refused: Root on the host: refused for a new stack without an
            exception.
        warned: Worth knowing: said in the log and allowed.
    """

    refused: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        """True when anything was found."""
        return bool(self.refused or self.warned)


def _volume_source(volume: Any) -> tuple[str | None, str]:
    """
    Read where a service's volume comes from.

    Args:
        volume: One entry of ``volumes``, short (``src:dst[:mode]``) or long
            (a mapping with ``type`` and ``source``).

    Returns:
        ``(source, kind)``: the host path or volume name (None for an
        anonymous volume) and ``bind`` or ``volume``.
    """
    if isinstance(volume, Mapping):
        source = volume.get("source")
        kind = str(
            volume.get("type")
            or ("bind" if str(source or "").startswith(("/", ".", "~")) else "volume")
        )
        return (str(source) if source else None), kind
    text = str(volume)
    if ":" not in text:
        return None, "volume"
    source = text.split(":", 1)[0]
    kind = "bind" if source.startswith(("/", ".", "~", "$")) else "volume"
    return source, kind


def _resolved(source: str, app_path: Path) -> PurePosixPath | None:
    """
    Turn a bind mount's source into the absolute path Docker will mount.

    ``..`` and ``.`` are collapsed first, the way the daemon sees the path: a
    source of ``/var/www/apps/shop/../other`` is ``other``'s directory, and
    ``../other`` is too when the application is ``shop``. A relative source is
    relative to the application, as ``docker compose`` reads it.

    Args:
        source: The source as written.
        app_path: The application directory.

    Returns:
        The path, or None for a source that starts with ``~`` or ``$``, which
        the shell or the compose file's variables decide and no text shows.
    """
    if source.startswith(("~", "$")):
        return None
    joined = source if source.startswith("/") else posixpath.join(str(app_path), source)
    return PurePosixPath(posixpath.normpath(joined))


def _outside(source: str, app_path: Path) -> bool:
    """
    Tell whether a bind mount's source is a host path outside the application.

    Args:
        source: The source as written.
        app_path: The application directory.

    Returns:
        True for a path (or one from a home directory, or a variable) that is
        not the application directory nor inside it, once ``..`` is resolved.
    """
    path = _resolved(source, app_path)
    if path is None:
        return True
    root = PurePosixPath(posixpath.normpath(str(app_path)))
    return not (path == root or root in path.parents)


def _noust_roots() -> tuple[PurePosixPath, ...]:
    """
    List the directories where Noust keeps what must not leave the host.

    Returns:
        The configuration, the store, the backups and their WASM-era names.
    """
    from noust.core import paths

    return tuple(
        PurePosixPath(str(directory))
        for directory in (
            paths.config_dir(),
            paths.state_dir(),
            paths.backup_dir(),
            paths.LEGACY_CONFIG_DIR,
            paths.LEGACY_STATE_DIR,
            paths.LEGACY_BACKUP_DIR,
        )
    )


def _exposes(source: str, app_path: Path) -> str | None:
    """
    Say whether a bind mount hands over Noust's state or another application.

    Args:
        source: The source as written.
        app_path: The application directory.

    Returns:
        The reason the mount is root-equivalent for everything else on the
        server, or None when it is neither Noust's nor another application's.
    """
    from noust.core.config import Config

    path = _resolved(source, app_path)
    if path is None:
        return None
    for directory in _noust_roots():
        if path == directory or directory in path.parents:
            return "that is Noust's own configuration and secrets"
        if path in directory.parents:
            return f"that contains {directory}, where Noust keeps its configuration and secrets"
    apps = PurePosixPath(posixpath.normpath(str(Config().apps_directory)))
    app = PurePosixPath(posixpath.normpath(str(app_path)))
    if path == app or app in path.parents:
        return None
    if path == apps or path in apps.parents:
        return "that contains every application's files, the other applications' included"
    if apps in path.parents:
        return "that is another application's files, which it must not read"
    return None


def inspect_compose(document: Any, app_path: Path) -> ComposeFindings:
    """
    Sort what a parsed compose file asks of the host.

    Args:
        document: The file, as ``yaml.safe_load`` returned it.
        app_path: The application directory, the one place a bind mount may
            come from without a warning.

    Returns:
        The findings, each a sentence naming the service.
    """
    findings = ComposeFindings()
    services = document.get("services") if isinstance(document, Mapping) else None
    if not isinstance(services, Mapping):
        return findings
    for name, service in services.items():
        if not isinstance(service, Mapping):
            continue
        if service.get("privileged") in (True, "true", "True", "yes"):
            findings.refused.append(
                f"service {name} is privileged: it has every capability and every device "
                "of this server"
            )
        for volume in service.get("volumes") or ():
            source, kind = _volume_source(volume)
            if source is None or kind != "bind":
                continue
            if source.rstrip("/").endswith(DOCKER_SOCKET):
                findings.refused.append(
                    f"service {name} mounts the Docker socket ({source}): it can start a "
                    "container with this server's root filesystem"
                )
            elif (reason := _exposes(source, app_path)) is not None:
                findings.refused.append(
                    f"service {name} mounts {source}: {reason}. The files of other applications "
                    "and Noust's own are not for a container to read"
                )
            elif _outside(source, app_path):
                findings.warned.append(
                    f"service {name} mounts {source} from this server, outside the application"
                )
        for namespace in _HOST_NAMESPACES:
            if str(service.get(namespace) or "") == "host":
                findings.warned.append(f"service {name} shares the host's {namespace}")
        if service.get("cap_add"):
            capabilities = ", ".join(str(cap) for cap in service.get("cap_add") or ())
            findings.warned.append(f"service {name} adds capabilities: {capabilities}")
        if service.get("devices"):
            findings.warned.append(f"service {name} is given host devices")
        for option in service.get("security_opt") or ():
            if str(option).replace(" ", "") in _UNCONFINED:
                findings.warned.append(f"service {name} turns its confinement off ({option})")
    return findings


def stack_findings(app_path: Path) -> ComposeFindings:
    """
    Inspect the compose file a deployed stack runs from.

    Args:
        app_path: The stack's directory.

    Returns:
        The findings; none when no compose file can be read.
    """
    import yaml

    from noust.deployers.docker_compose import COMPOSE_FILE_PRIORITY

    for name in COMPOSE_FILE_PRIORITY:
        candidate = app_path / name
        if candidate.is_file() and not candidate.is_symlink():
            try:
                document = load_compose(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                return ComposeFindings()
            return inspect_compose(document, app_path)
    return ComposeFindings()
