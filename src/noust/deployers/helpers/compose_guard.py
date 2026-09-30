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
a stack that already runs them is warned about, never stopped. The host's
namespaces (``pid``, ``network_mode``, ``ipc``, ``userns_mode: host``), added
capabilities, devices, disabled confinement and bind mounts of host paths
outside the application are warned about: some stacks need them, and the
operator should know which do.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

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


def _outside(source: str, app_path: Path) -> bool:
    """
    Tell whether a bind mount's source is a host path outside the application.

    Args:
        source: The source as written.
        app_path: The application directory.

    Returns:
        True for an absolute path (or one from a home directory) that is not
        the application directory nor inside it.
    """
    if source.startswith(("~", "$")):
        return True
    if not source.startswith("/"):
        return False
    path = PurePosixPath(source)
    root = PurePosixPath(str(app_path))
    return not (path == root or root in path.parents)


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
                document = yaml.safe_load(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                return ComposeFindings()
            return inspect_compose(document, app_path)
    return ComposeFindings()
