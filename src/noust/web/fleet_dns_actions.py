# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust DNS on several servers of the fleet: the fleet action behind ``POST /api/fleet/dns/cluster`` (item 104).

A name server in one place is a point of failure for every domain it serves, so Noust DNS is a
primary and secondaries on servers in other networks. Setting that up is a bulk action like the
others (:mod:`noust.web.fleet_jobs`): a plan first, one server after the other, a record of what
was done, the node's own words when something fails. The primary goes first and alone (the action's
canary), and every step is a call to a server's own API (rule 3), so a server that runs an older
Noust is shown as unable to take part, and one whose ceiling does not allow changing its host is
shown as that:

- **The primary** is installed and set up, with the name it has as a name server and the addresses
  it answers on.
- **Each secondary** reads the primary's address and its transfer key (the one call that hands the
  key out), is installed and set up with them, and then tells the primary where it answers so the
  primary lets it transfer and notifies it. The key crosses the central in the body of one request
  and is kept nowhere here: not in the job's record, not in its log, not in a word a server
  answers with.

From then on the servers keep each other in step with DNS itself: a zone made on the primary
reaches every secondary through the catalog zone, with no call from the central. The central only
sets the cluster up and looks at it (``GET /api/fleet/dns``).
"""

from __future__ import annotations

from typing import Any

from noust.core.authdns_store import PRIMARY, SECONDARY, check_address
from noust.core.exceptions import ValidationError
from noust.fleet.models import validate_node_name
from noust.validators.dns_zone import check_hostname
from noust.web.fleet_jobs import ACTIONS, ActionFailed, ActionSpec, FleetRequest, NodeRun

__all__ = ["ACTION", "DNS_OPERATIONS", "cluster_request"]

#: The action's name.
ACTION = "dns_cluster"

STATUS = "/api/dns/noust/server"
KEY = "/api/dns/noust/server/key"
SECONDARIES = "/api/dns/noust/server/secondaries"

#: The most servers one cluster is set up on at once.
MAX_SERVERS = 16

#: What a server must offer to take part, as its own OpenAPI schema spells them.
DNS_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("GET", STATUS),
    ("POST", STATUS),
    ("GET", KEY),
    ("POST", SECONDARIES),
)


def _servers(value: Any) -> dict[str, dict[str, Any]]:
    """
    Check the servers of the cluster.

    Args:
        value: Server name to ``{"hostname", "listen"}``.

    Returns:
        The same, judged.

    Raises:
        ValidationError: One is not usable.
    """
    if not isinstance(value, dict) or not value or len(value) > MAX_SERVERS:
        raise ValidationError(
            f"Say which servers run Noust DNS (from 1 to {MAX_SERVERS})",
            details="Give a primary and, for a name server that survives an outage, a secondary "
            "in another network.",
            field="options",
        )
    checked: dict[str, dict[str, Any]] = {}
    for node, spec in value.items():
        if not isinstance(spec, dict) or set(spec) - {"hostname", "listen"}:
            raise ValidationError(
                f"{node}: give its hostname and, optionally, the addresses it answers on",
                field="options",
            )
        hostname = check_hostname(spec.get("hostname"), field="hostname")
        if hostname == ".":
            raise ValidationError(f"{node}: a name server has a name", field="hostname")
        listen = spec.get("listen")
        if listen is not None and (not isinstance(listen, list) or not 1 <= len(listen) <= 16):
            raise ValidationError(f"{node}: listen is a list of addresses", field="listen")
        checked[validate_node_name(str(node))] = {
            "hostname": hostname,
            "listen": [check_address(a, field_name="listen") for a in listen] if listen else None,
        }
    return checked


def _cluster_options(options: Any) -> dict[str, Any]:
    """
    Check and complete the options of the action.

    Args:
        options: What was given: ``primary``, ``servers`` and ``epel``.

    Returns:
        The options, judged.

    Raises:
        ValidationError: An option that is not one, a primary that is not among the servers, or
            two servers with one name.
    """
    unknown = sorted(set(options) - {"primary", "servers", "epel"})
    if unknown:
        raise ValidationError(
            f"Unknown option(s): {', '.join(unknown)}",
            details="This action takes: epel, primary, servers.",
            field="options",
        )
    servers = _servers(options.get("servers"))
    primary = options.get("primary")
    if not isinstance(primary, str) or validate_node_name(primary) not in servers:
        raise ValidationError(
            "The primary is one of the servers",
            details="Name it in primary and give its hostname in servers.",
            field="primary",
        )
    names = [spec["hostname"] for spec in servers.values()]
    if len(set(names)) != len(names):
        raise ValidationError("Two servers cannot have one name as name servers", field="servers")
    epel = options.get("epel", False)
    if not isinstance(epel, bool):
        raise ValidationError("epel is true or false", field="options")
    return {"primary": validate_node_name(primary), "servers": servers, "epel": epel}


def _dns_cluster(run: NodeRun) -> None:
    """
    Set up Noust DNS on one server of the cluster.

    Args:
        run: The server, with the job's options and the primary as its peer.

    Raises:
        ActionFailed: The primary is not ready for a secondary, or a step failed on the server.
    """
    options = run.options
    me = run.name
    wanted = options["servers"][me]
    is_primary = me == options["primary"]
    role = PRIMARY if is_primary else SECONDARY
    run.step("Looking at the server")
    body: dict[str, Any] = {"role": role, "hostname": wanted["hostname"], "epel": options["epel"]}
    if wanted.get("listen"):
        body["listen"] = wanted["listen"]
    if not is_primary:
        if run.peer is None:
            raise ActionFailed("The primary is not known to this job")
        run.step(f"Asking {run.peer.name} for its address and its key")
        primary = run.call("GET", STATUS, on=run.peer)
        listen = primary.get("listen") or []
        if not primary.get("configured") or primary.get("role") != PRIMARY or not listen:
            raise ActionFailed(
                f"{run.peer.name} is not a primary of Noust DNS yet",
                details="The primary is set up first: run this action again once it is.",
            )
        key = run.call("GET", KEY, on=run.peer)
        body["primary_address"] = listen[0]
        body["transfer_key"] = {"secret": key["secret"]}
    run.step("Installing Knot DNS and setting it up")
    answer = run.call("POST", STATUS, body=body)
    run.run_job(answer, f"setting up Noust DNS as a {role}")
    if is_primary:
        run.step("Done")
        return
    after = run.call("GET", STATUS)
    run.step(f"Telling {run.peer.name if run.peer else 'the primary'} where it answers")
    for address in after.get("listen") or []:
        run.call(
            "POST",
            SECONDARIES,
            body={"address": address, "hostname": wanted["hostname"]},
            on=run.peer,
        )
    run.step("Done")


def cluster_request(
    primary: str, servers: dict[str, dict[str, Any]], *, epel: bool = False
) -> FleetRequest:
    """
    Build the request that sets Noust DNS up on several servers.

    The one place what the operator asked for becomes a request, for the console's API and the
    terminal alike: the primary alone first (the canary), then one secondary at a time.

    Args:
        primary: The server that holds the zones.
        servers: Every server of the cluster, the primary included, with ``hostname`` and,
            optionally, ``listen``.
        epel: Agree to enable EPEL where Knot comes from it.

    Returns:
        The request.
    """
    return FleetRequest(
        action=ACTION,
        nodes=list(servers),
        canary=primary,
        serial=1,
        options={"primary": primary, "servers": servers, "epel": epel},
    )


ACTIONS.update(
    {
        ACTION: ActionSpec(
            ACTION,
            "Set up Noust DNS on several servers",
            DNS_OPERATIONS,
            serial=1,
            max_failures=0,
            run=_dns_cluster,
            check_options=_cluster_options,
            peer_option="primary",
        )
    }
)
