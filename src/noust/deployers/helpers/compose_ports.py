# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one reader of a Compose service's ``ports:``, and what is decided from it.

Compose accepts a port in many shapes: ``"3000"``, ``"8080:80"``,
``"127.0.0.1:3000:3000"``, ``"[::1]:3000:3000"``, ranges such as
``"3000-3002:3000-3002"``, a ``/udp`` or ``/tcp`` suffix, and the long form
(``{target, published, host_ip, protocol}``). Every part of Noust that needs
a stack's ports reads them here: the port the site proxies to, whether a
stack is a web at all, and which service ``/`` goes to. Before this each had
its own split on ``:``, and ``127.0.0.1:3000:3000`` - the way a careful stack
publishes only to loopback - fell back to 3000 whatever it said.

The file is read before Compose resolves it, so ``${VAR:-default}`` is taken
at its default; a variable without one cannot be known here, and an entry
using it yields no mapping. :func:`is_headless_stack` still counts such an
entry as published, because calling a stack headless removes its site.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from noust.deployers.docker_compose import DockerComposeService

__all__ = [
    "PortMapping",
    "ServicePorts",
    "is_headless_stack",
    "parse_ports",
    "published_port",
    "web_root_service",
]

#: ``${NAME:-default}`` or ``${NAME-default}``: what Compose substitutes when unset.
_DEFAULTED_VARIABLE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}")

#: A port or a range of them.
_PORT_RANGE = re.compile(r"^(\d+)(?:-(\d+))?$")

#: The highest TCP or UDP port.
_MAX_PORT = 65535


@dataclass(frozen=True)
class PortMapping:
    """
    One container port and where, if anywhere known, it is published on the host.

    Attributes:
        host_ip: The host address it is bound to, without brackets for IPv6;
            None for every address.
        host_port: The host port, or None when Docker picks one at random
            (``"3000"``, ``"127.0.0.1::3000"``, a long form without
            ``published``) - published, but not at a port anyone can name.
        container_port: The port inside the container.
        protocol: ``tcp`` or ``udp``.
    """

    host_ip: str | None
    host_port: int | None
    container_port: int
    protocol: str


class ServicePorts(Protocol):
    """What these functions read of a service: its name, ports and dependencies."""

    name: str
    ports: list[Any]
    depends_on: list[str]


def parse_ports(value: object) -> list[PortMapping]:
    """
    Read a service's ``ports:`` - the whole list, or one entry of it.

    Args:
        value: The list under ``ports:``, or a single entry (a string, an
            integer or a long-form mapping).

    Returns:
        One mapping per published container port, ranges expanded, in the
        order the file gives them. Entries that cannot be read are left out.
    """
    if value is None:
        return []
    entries: Iterable[object] = value if isinstance(value, list) else [value]
    mappings: list[PortMapping] = []
    for entry in entries:
        mappings.extend(_parse_entry(entry) or [])
    return mappings


def published_port(service: ServicePorts) -> int | None:
    """
    Name the host port a web server can proxy to for a service.

    Args:
        service: The service.

    Returns:
        The first TCP host port it publishes, or None when it publishes none
        at a known port.
    """
    for mapping in parse_ports(list(service.ports)):
        if mapping.protocol == "tcp" and mapping.host_port is not None:
            return mapping.host_port
    return None


def web_root_service(services: Sequence[DockerComposeService]) -> str | None:
    """
    Choose the service ``/`` goes to when nothing else says.

    The one in front: a web service that depends on another web service (a
    Next.js front that calls its API) and that no other web service depends
    on. Without such a service, the first web service in the file. A web
    service is one publishing a TCP port at a known host port.

    Args:
        services: The stack's services, in file order.

    Returns:
        The service's name, or None when no service publishes a usable port.
    """
    web = [service for service in services if published_port(service) is not None]
    if not web:
        return None
    names = {service.name for service in web}
    fronting = [s for s in web if any(dep in names for dep in s.depends_on)]
    fronted = {dep for service in web for dep in service.depends_on}
    front = [s for s in fronting if s.name not in fronted]
    return (front or fronting or web)[0].name


def is_headless_stack(services: Sequence[DockerComposeService]) -> bool:
    """
    Tell whether a stack is not a web: no service publishes a TCP port on the host.

    The one definition, used by the deploy (no site, no certificate), the
    store (no port), ``noust diagnose``, the monitor and the summaries, which
    judge such a stack by its containers instead of by an HTTP probe. A port
    published at a random host port still counts, and so does an entry that
    cannot be read here (``${PORT}:3000``): either publishes something, and a
    stack wrongly called headless loses its site.

    Args:
        services: The stack's services.

    Returns:
        True when nothing is published over TCP.
    """
    for service in services:
        for entry in service.ports:
            mappings = _parse_entry(entry)
            if mappings is None or any(m.protocol == "tcp" for m in mappings):
                return False
    return True


def _parse_entry(entry: object) -> list[PortMapping] | None:
    """
    Read one entry of ``ports:``.

    Args:
        entry: A short-form string or integer, or a long-form mapping.

    Returns:
        Its mappings, or None when it cannot be read.
    """
    if isinstance(entry, dict):
        return _parse_long(entry)
    if isinstance(entry, bool) or not isinstance(entry, int | str):
        return None
    return _parse_short(str(entry))


def _parse_long(entry: dict[object, object]) -> list[PortMapping] | None:
    """
    Read a long-form entry: ``{target, published, host_ip, protocol}``.

    Args:
        entry: The mapping.

    Returns:
        Its mappings, or None when ``target`` is missing or not a port.
    """
    targets = _port_range(_resolve(entry.get("target")))
    if targets is None:
        return None
    protocol = str(entry.get("protocol") or "tcp").lower()
    host_ip = _resolve(entry.get("host_ip")) or None
    published = _resolve(entry.get("published"))
    if not published:
        return [PortMapping(host_ip, None, port, protocol) for port in targets]
    hosts = _port_range(published)
    if hosts is None or len(hosts) != len(targets):
        return None
    return [
        PortMapping(host_ip, host, port, protocol)
        for host, port in zip(hosts, targets, strict=True)
    ]


def _parse_short(text: str) -> list[PortMapping] | None:
    """
    Read a short-form entry: ``[[ip:]host:]container[/protocol]``.

    Args:
        text: The entry.

    Returns:
        Its mappings, or None when it cannot be read.
    """
    text = _DEFAULTED_VARIABLE.sub(lambda match: match.group(1), text.strip())
    if not text or "$" in text:
        return None
    protocol = "tcp"
    if "/" in text:
        text, protocol = text.rsplit("/", 1)
        protocol = protocol.lower()
    host_ip: str | None = None
    if text.startswith("["):
        # An IPv6 address is bracketed so its colons are not taken for ours.
        closing = text.find("]")
        if closing < 0 or text[closing + 1 : closing + 2] != ":":
            return None
        host_ip, text = text[1:closing], text[closing + 2 :]
    parts = text.split(":")
    if host_ip is None and len(parts) == 3:
        host_ip = parts.pop(0) or None
    if len(parts) == 1:
        container, host = parts[0], ""
    elif len(parts) == 2:
        host, container = parts
    else:
        return None
    containers = _port_range(container)
    if containers is None:
        return None
    if not host:
        return [PortMapping(host_ip, None, port, protocol) for port in containers]
    hosts = _port_range(host)
    if hosts is None:
        return None
    if len(hosts) != len(containers):
        # "8000-8010:80" publishes one container port on any host port of
        # the range; which one Docker takes cannot be known here.
        if len(containers) != 1:
            return None
        return [PortMapping(host_ip, None, containers[0], protocol)]
    return [
        PortMapping(host_ip, host_port, port, protocol)
        for host_port, port in zip(hosts, containers, strict=True)
    ]


def _resolve(value: object) -> str:
    """
    Turn a long-form field into text, taking ``${VAR:-default}`` at its default.

    Args:
        value: The field.

    Returns:
        The text, empty when absent or still holding a variable.
    """
    if value is None or isinstance(value, bool):
        return ""
    text = _DEFAULTED_VARIABLE.sub(lambda match: match.group(1), str(value).strip())
    return "" if "$" in text else text


def _port_range(text: str) -> list[int] | None:
    """
    Read a port or a ``start-end`` range.

    Args:
        text: The text.

    Returns:
        The ports, or None when it is not a port or a valid range.
    """
    match = _PORT_RANGE.match(text.strip())
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if not 1 <= start <= end <= _MAX_PORT:
        return None
    return list(range(start, end + 1))
