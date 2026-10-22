# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The servers an application runs on, written for the load balancer in front of them.

Noust routes no traffic between servers and does not manage a balancer: the one in front of an
application that runs on several servers is the operator's (a cloud load balancer, HAProxy, an
nginx of their own). What the central knows and the balancer needs is which servers to put behind
it and which of them serve right now, so that is what this writes: nginx's ``upstream`` block, a
plain list of ``address:port``, or JSON.

The addresses are the ones the servers were added with (the host the central's SSH reaches), which
is the operator's to make right for their balancer (a private address on the balancer's network,
say); a server added as one that dials in has none and is left out, saying so.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from noust.core.exceptions import ValidationError
from noust.fleet.placement_status import HEALTHY_STATES

__all__ = ["FORMATS", "render_upstreams", "upstream_servers"]

#: What the servers can be written as.
FORMATS = ("nginx", "list", "json")


def _hostport(address: str, port: int) -> str:
    """
    Spell an address and a port as a balancer reads them.

    Args:
        address: A host name, an IPv4 address or an IPv6 address.
        port: The port.

    Returns:
        ``host:port``, with an IPv6 address in brackets.
    """
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def upstream_servers(
    status: Mapping[str, Any], addresses: Mapping[str, str], *, port: int
) -> list[dict[str, Any]]:
    """
    Decide which of an application's servers take traffic.

    Args:
        status: :func:`noust.fleet.placement_status.placement_status`'s answer.
        addresses: Server name to the address a balancer reaches it on.
        port: The port the servers' web server answers on.

    Returns:
        One entry per member: ``node``, ``address`` (``host:port``; None without one),
        ``serving`` (it answers, has the application and runs it) and ``note`` (why not, or
        that it is on another commit).
    """
    reference = status.get("reference_commit")
    found: list[dict[str, Any]] = []
    for server in status["servers"]:
        name = server["node"]
        host = addresses.get(name)
        serving = (
            bool(server.get("reachable"))
            and bool(server.get("present"))
            and server.get("status") in HEALTHY_STATES
        )
        note = None
        if host is None:
            serving, note = False, f"{name} has no address a balancer can reach (it dials in)"
        elif not server.get("reachable"):
            note = f"{name} does not answer"
        elif not server.get("present"):
            note = f"{status['domain']} is not installed on {name}"
        elif not serving:
            note = f"{status['domain']} is {server.get('status') or 'not running'} on {name}"
        elif server.get("sync") == "differs":
            note = f"on {server.get('commit')}, not {reference}"
        found.append(
            {
                "node": name,
                "address": _hostport(host, port) if host else None,
                "serving": serving,
                "sync": server.get("sync"),
                "commit": server.get("commit"),
                "note": note,
            }
        )
    return found


def render_upstreams(domain: str, servers: list[dict[str, Any]], fmt: str) -> str:
    """
    Write the servers for a balancer.

    Args:
        domain: The application.
        servers: :func:`upstream_servers`' answer.
        fmt: One of :data:`FORMATS`.

    Returns:
        The text.

    Raises:
        ValidationError: For a format that is not one.
    """
    if fmt == "json":
        return json.dumps({"domain": domain, "servers": servers}, indent=2) + "\n"
    if fmt == "list":
        return "".join(f"{s['address']}\n" for s in servers if s["serving"] and s["address"])
    if fmt != "nginx":
        raise ValidationError(
            f"Unknown format: {fmt!r}",
            details=f"Use one of: {', '.join(FORMATS)}.",
            field="format",
        )
    name = re.sub(r"[^a-z0-9_]", "_", domain.lower())
    lines = [f"# {domain} on {len(servers)} server{'s' if len(servers) != 1 else ''}"]
    lines.append("upstream " + name + " {")
    for server in servers:
        if server["address"] is None:
            lines.append(f"    # {server['note']}")
            continue
        down = "" if server["serving"] else " down"
        comment = f"  # {server['node']}" + (f": {server['note']}" if server["note"] else "")
        lines.append(f"    server {server['address']}{down};{comment}")
    lines.append("}")
    if not any(server["serving"] for server in servers):
        lines.append("# No server serves it right now: every one is marked down.")
    lines += [
        "",
        f"# In the server block that answers {domain}:",
        "#   location / {",
        f"#       proxy_pass https://{name};",
        f"#       proxy_set_header Host {domain};",
        "#       proxy_ssl_server_name on;",
        f"#       proxy_ssl_name {domain};",
        "#   }",
    ]
    return "\n".join(lines) + "\n"
