# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Compose stack made safe to run beside the one it is a preview of.

A preview of a Compose application is the same repository checked out in a
directory of its own, and Compose derives the project from that directory, so
the containers, the default network and the named volumes are already apart
from the original's. What a compose file can still say that two copies cannot
both have is this module's business, and it is written to a file of its own
(``<stem>.noust-preview.yml``, beside the original) rather than edited in
place, because the original is repository content that the next update
replaces:

- **A project name** (``name:``): two copies of one name are one project.
- **Fixed container names**: the second copy fails to start.
- **Published ports**: only the web root service is published, on the loopback
  port the preview was given; every other published port would collide with
  the original's or with another preview's. The services still reach each other
  over the stack's network.
- **Host paths mounted into containers**: ``/srv/shop/uploads:/data`` is the
  original's data. It is redirected under the preview's own directory, so a
  pull request cannot write into production's files. A bind of an existing
  file (a certificate, a configuration) is read-only input, not data, and is
  left alone.
- **Volumes and networks that exist outside the project** (``external: true``
  or a fixed ``name:``): made the project's own, which is an empty volume
  instead of production's.
- **An image tag**, when the service is built: ``image:`` beside ``build:``
  names what the build produces, and two projects building the same name would
  move one another's tag. The preview's image is named by Compose after its
  project.

What it cannot isolate it refuses: a stack on the host's network
(``network_mode: host``) takes the host's ports whatever is published, so two
copies cannot run, and a stack that publishes no web port has nothing to look
at. The host privilege guard still runs on the file this one is made from.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from noust.core.exceptions import DeploymentError
from noust.deployers.helpers.compose_ports import parse_ports, web_root_service

#: The suffix of the file this module writes, before ``.yml``.
DERIVED_SUFFIX = ".noust-preview.yml"

#: The comment that names the file a derived one was made from.
MARKER = "# noust-preview-of:"

#: Where a preview's redirected host paths live, under its own directory.
DATA_DIR = ".noust-preview-data"

#: ``$VAR`` or ``${VAR}`` in a path Compose resolves later.
_VARIABLE = re.compile(r"\$")


@dataclass(frozen=True)
class IsolatedStack:
    """
    A compose document made safe to run beside its original.

    Attributes:
        document: The new document.
        service: The web root service, the one the preview's site proxies to.
        port: The loopback port it is published on.
        container_port: The port inside the service's container.
        changes: What was changed, one line each, for the deployment's log.
        warnings: What could not be isolated and was left as it was.
    """

    document: dict[str, Any]
    service: str
    port: int
    container_port: int
    changes: tuple[str, ...]
    warnings: tuple[str, ...]


def derived_name(original: str) -> str:
    """
    Name the file a preview's compose file is written to.

    Args:
        original: The original's path relative to the application, in POSIX form.

    Returns:
        The same directory and stem with :data:`DERIVED_SUFFIX`.
    """
    path = Path(original)
    return str(path.with_name(path.stem + DERIVED_SUFFIX))


def is_derived(name: str) -> bool:
    """
    Tell whether a compose file name is one this module writes.

    Args:
        name: A path or a file name.

    Returns:
        True for a name ending in :data:`DERIVED_SUFFIX`.
    """
    return name.endswith(DERIVED_SUFFIX)


def original_of(text: str) -> str | None:
    """
    Read which file a derived one was made from.

    Args:
        text: The derived file's content.

    Returns:
        The original's path relative to the application, or None for a file
        that does not carry the marker (not one of ours).
    """
    for line in text.splitlines()[:5]:
        if line.startswith(MARKER):
            return line[len(MARKER) :].strip() or None
    return None


def render(isolated: IsolatedStack, *, domain: str, original: str) -> str:
    """
    Write an isolated document as the file Compose will read.

    Args:
        isolated: The result of :func:`isolate`.
        domain: The preview's domain, for the header.
        original: The original's path relative to the application.

    Returns:
        YAML text starting with a header and the marker that names the original.
    """
    header = (
        f"# Generated by Noust for the preview {domain}. Do not edit: it is written again at "
        "every update.\n"
        f"{MARKER} {original}\n"
    )
    return header + yaml.safe_dump(isolated.document, sort_keys=False, default_flow_style=False)


def isolate(
    document: Any,
    *,
    port: int,
    app_path: Path,
    compose_dir: Path,
    is_file: Callable[[str], bool] = os.path.isfile,
) -> IsolatedStack:
    """
    Make a compose document safe to run beside the stack it is a preview of.

    Args:
        document: The parsed compose file.
        port: The loopback port the web root service is published on.
        app_path: The preview's directory.
        compose_dir: The directory the compose file is in, which relative paths
            in it are relative to.
        is_file: Tells whether a host path is an existing regular file, which a
            bind mount leaves alone. Replaced in tests.

    Returns:
        The new document and what was done to it.

    Raises:
        DeploymentError: The stack uses the host's network or publishes no web
            port, so two copies cannot run or there is nothing to open.
    """
    # Imported here: the deployer imports this module, and this reads its services the way it does.
    from noust.deployers.docker_compose import parse_services

    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise DeploymentError("No services defined in compose file")
    result: dict[str, Any] = copy.deepcopy(document)
    changes: list[str] = []
    warnings: list[str] = []
    services: dict[str, Any] = result["services"]

    for name, data in services.items():
        if isinstance(data, dict) and str(data.get("network_mode", "")) == "host":
            raise DeploymentError(
                f"{name} uses the host's network, so two copies of the stack cannot run",
                details="A service with network_mode: host listens on the host's own ports "
                "whatever is published; the preview would fight the original for them. Give "
                "the service the stack's network, or deploy this stack without previews.",
            )

    root = web_root_service(parse_services(result))
    if root is None:
        raise DeploymentError(
            "This stack publishes no web port, so a preview would have nothing to open",
            details="Previews proxy a service that publishes a TCP port at a number the compose "
            "file names (such as '3000:3000'). Publish one, or deploy this stack without previews.",
        )
    root_data = services[root]
    mapping = next(
        entry
        for entry in parse_ports(root_data.get("ports"))
        if entry.protocol == "tcp" and entry.host_port is not None
    )

    if result.pop("name", None) is not None:
        changes.append("dropped the project name: the preview's directory names its project")

    data_dir = app_path / DATA_DIR
    for name, data in services.items():
        if not isinstance(data, dict):
            continue
        if data.pop("container_name", None) is not None:
            changes.append(f"{name}: dropped its fixed container name")
        if name == root:
            data["ports"] = [f"127.0.0.1:{port}:{mapping.container_port}"]
            changes.append(
                f"{name}: published on 127.0.0.1:{port} (container port {mapping.container_port})"
            )
        elif data.pop("ports", None) is not None:
            changes.append(f"{name}: no longer published on the host")
        if "build" in data and data.pop("image", None) is not None:
            changes.append(f"{name}: the image tag is the project's own")
        volumes = data.get("volumes")
        if isinstance(volumes, list):
            data["volumes"] = [
                _volume(entry, name, app_path, compose_dir, data_dir, is_file, changes, warnings)
                for entry in volumes
            ]

    for section in ("volumes", "networks"):
        declared = result.get(section)
        if not isinstance(declared, dict):
            continue
        for item, spec in declared.items():
            if not isinstance(spec, dict):
                continue
            external = spec.pop("external", None)
            named = spec.pop("name", None)
            if external or named is not None:
                changes.append(
                    f"{section[:-1]} {item}: the project's own, not one that already exists"
                )
    return IsolatedStack(
        document=result,
        service=root,
        port=port,
        container_port=mapping.container_port,
        changes=tuple(changes),
        warnings=tuple(warnings),
    )


def _volume(
    entry: Any,
    service: str,
    app_path: Path,
    compose_dir: Path,
    data_dir: Path,
    is_file: Callable[[str], bool],
    changes: list[str],
    warnings: list[str],
) -> Any:
    """
    Redirect one mount of a service that would reach the original's files.

    Args:
        entry: The mount: ``source:target[:mode]`` or the long form.
        service: Its service, for the log.
        app_path: The preview's directory.
        compose_dir: What relative sources are relative to.
        data_dir: Where redirected sources go.
        is_file: Tells whether a host path is a regular file.
        changes: Receives what was redirected.
        warnings: Receives what could not be judged.

    Returns:
        The mount, redirected when its source is a host path outside the preview.
    """
    if isinstance(entry, dict):
        if entry.get("type", "bind") != "bind" or not isinstance(entry.get("source"), str):
            return entry
        redirected = _redirect(
            entry["source"], service, app_path, compose_dir, data_dir, is_file, changes, warnings
        )
        return {**entry, "source": redirected} if redirected is not None else entry
    if not isinstance(entry, str):
        return entry
    source, sep, rest = entry.partition(":")
    if not sep:
        return entry  # an anonymous volume: the project's own
    redirected = _redirect(
        source, service, app_path, compose_dir, data_dir, is_file, changes, warnings
    )
    return f"{redirected}:{rest}" if redirected is not None else entry


def _redirect(
    source: str,
    service: str,
    app_path: Path,
    compose_dir: Path,
    data_dir: Path,
    is_file: Callable[[str], bool],
    changes: list[str],
    warnings: list[str],
) -> str | None:
    """
    Decide where one mount's source goes.

    Args:
        source: The mount's source as written.
        service: Its service, for the log.
        app_path: The preview's directory.
        compose_dir: What relative sources are relative to.
        data_dir: Where redirected sources go.
        is_file: Tells whether a host path is a regular file.
        changes: Receives what was redirected.
        warnings: Receives what could not be judged.

    Returns:
        The new source, or None to leave it as written (a named volume, a path
        inside the preview, or a file).
    """
    if not source or not (source.startswith(("/", ".", "~", "$"))):
        return None  # a named volume: project-scoped by Compose
    if _VARIABLE.search(source):
        warnings.append(
            f"{service}: the mount source {source} is made of a variable and cannot be isolated"
        )
        return None
    if source.startswith("~"):
        absolute = os.path.normpath(os.path.expanduser(source))
    elif source.startswith("/"):
        absolute = os.path.normpath(source)
    else:
        absolute = os.path.normpath(os.path.join(str(compose_dir), source))
        inside = os.path.normpath(str(app_path))
        if absolute == inside or absolute.startswith(inside + os.sep):
            return None  # the preview's own checkout
    if is_file(absolute):
        return None  # read-only input, not data
    target = os.path.join(str(data_dir), absolute.lstrip(os.sep))
    changes.append(f"{service}: {source} is the preview's own, under {DATA_DIR}")
    return target
