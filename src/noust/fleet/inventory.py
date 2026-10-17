# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central knows about a server that the server does not: the inventory that moves with it.

A server is not owned by a central. It trusts one because its own operator ran ``noust fleet
authorize`` there, and it can trust two at once, so moving it is three steps in the right order:
authorize the new central on the server, add the server there, and then carry over what the old
central had recorded about it. That last step is this module. The inventory is its labels, the
applications the fleet runs on it (placements), the machine it is at a provider, whether its monitoring
is paused, and, only when asked, how the old central reached it (its wait and its bastion), because a
path that worked from one central may not exist from the other.

The document is JSON and carries no secret of any kind: not the server's fleet token, not the
central's key for it, not a cloud account's API token (those never leave their central). A document is
checked whole before anything is written, so a damaged one changes nothing.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import NodeError, ValidationError
from noust.fleet.labels import NodeLabels, validate_key, validate_value
from noust.fleet.liveness import Liveness
from noust.fleet.placements import Placements, checked_domain, checked_serial
from noust.fleet.ssh_options import NodeSSHOptions, parse_bastion, validate_connect_timeout

if TYPE_CHECKING:
    from noust.fleet.nodes import NodeManager

__all__ = ["FORMAT", "VERSION", "apply_inventory", "export_inventory", "validate_inventory"]

#: What the document says it is.
FORMAT = "noust-node-inventory"

#: The shape of the document this release writes and reads.
VERSION = 1

#: Most placements a server's inventory carries.
MAX_PLACEMENTS = 500

#: Most labels (a node carries at most :data:`noust.fleet.labels.MAX_LABELS`).
MAX_LABELS = 16

#: Longest reason a pause keeps.
MAX_REASON = 300

#: What a provider's id for a machine looks like: letters, digits and a few separators.
_PROVIDER_ID = re.compile(r"[A-Za-z0-9._:/-]{1,128}")


def export_inventory(manager: NodeManager, name: str) -> dict[str, Any]:
    """
    Write down what this central knows about one server.

    Args:
        manager: The node registry.
        name: The server.

    Returns:
        The document.

    Raises:
        NodeError: When no node has that name.
    """
    from noust.fleet.cloud.servers import DELETED, CloudServers

    record = manager.get(name)
    store = manager.store
    options = NodeSSHOptions(store).get(record.name)
    state = Liveness(store).get(record.name)
    machine = CloudServers(store).find(record.name)
    bastion = options.bastion
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "node": {"name": record.name, "address": record.ssh_host, "port": record.ssh_port},
        "labels": NodeLabels(store).of(record.name),
        "placements": [
            {
                "domain": placement.domain,
                "serial": placement.serial,
                "max_failures": placement.max_failures,
                "canary": placement.canary == record.name,
                "rollback_on_failure": placement.rollback_on_failure,
            }
            for placement in Placements(store).list()
            if record.name in placement.nodes
        ],
        "ssh": {
            "connect_timeout": options.connect_timeout,
            "bastion": (
                {"address": bastion.address, "host_key": bastion.host_key} if bastion else None
            ),
        },
        "monitoring": {"paused": state.paused, "reason": state.paused_reason},
        "cloud": (
            {
                "account": machine.account,
                "provider": machine.provider,
                "provider_id": machine.provider_id,
                "location": machine.location,
                "plan": machine.plan,
                "image": machine.image,
                "address": machine.address,
            }
            if machine is not None and machine.state != DELETED
            else None
        ),
    }


def _mapping(value: Any, what: str) -> dict[str, Any]:
    """
    Require a JSON object.

    Args:
        value: What the document holds.
        what: Its name, for the message.

    Returns:
        The object.

    Raises:
        ValidationError: When it is anything else.
    """
    if not isinstance(value, dict):
        raise ValidationError(f"The inventory's {what} is not an object", field="inventory")
    return value


def validate_inventory(document: Any) -> dict[str, Any]:
    """
    Check an inventory whole, before anything is written.

    Args:
        document: What was read from the file or the request.

    Returns:
        The document, with every part checked and normalised.

    Raises:
        ValidationError: When it is not an inventory this release reads, or any part is
            invalid or too large.
    """
    data = _mapping(document, "document")
    if data.get("format") != FORMAT:
        raise ValidationError(
            "This is not a Noust server inventory",
            details="Export one from the central the server comes from: noust node export NAME.",
            field="inventory",
        )
    if data.get("version") != VERSION:
        raise ValidationError(
            f"This inventory is version {data.get('version')!r}; this Noust reads version {VERSION}",
            details="Export it again with this version of Noust, or update the central.",
            field="inventory",
        )
    node = _mapping(data.get("node"), "node")
    raw_labels = _mapping(data.get("labels", {}), "labels")
    if len(raw_labels) > MAX_LABELS:
        raise ValidationError(
            f"An inventory carries at most {MAX_LABELS} labels", field="inventory"
        )
    labels = {validate_key(k): validate_value(v) for k, v in raw_labels.items()}
    raw_placements = data.get("placements", [])
    if not isinstance(raw_placements, list):
        raise ValidationError("The inventory's placements are not a list", field="inventory")
    if len(raw_placements) > MAX_PLACEMENTS:
        raise ValidationError(
            f"An inventory names at most {MAX_PLACEMENTS} applications", field="inventory"
        )
    placements = []
    for entry in raw_placements:
        item = _mapping(entry, "placement")
        placements.append(
            {
                "domain": checked_domain(item.get("domain")),
                "serial": _serial(item.get("serial")),
                "max_failures": _count(item.get("max_failures")),
                "canary": _bool(item.get("canary", False), "canary"),
                "rollback_on_failure": _bool(item.get("rollback_on_failure", True), "rollback"),
            }
        )
    ssh = _mapping(data.get("ssh", {}), "ssh")
    timeout = ssh.get("connect_timeout")
    bastion = ssh.get("bastion")
    if bastion is not None:
        spec = _mapping(bastion, "bastion")
        parse_bastion(str(spec.get("address") or ""), str(spec.get("host_key") or ""))
    monitoring = _mapping(data.get("monitoring", {}), "monitoring")
    paused = _bool(monitoring.get("paused", False), "monitoring")
    reason = monitoring.get("reason")
    cloud = data.get("cloud")
    if cloud is not None:
        _mapping(cloud, "cloud")
    return {
        "node": node,
        "labels": labels,
        "placements": placements,
        "ssh": {
            "connect_timeout": validate_connect_timeout(timeout) if timeout is not None else None,
            "bastion": bastion,
        },
        "monitoring": {
            "paused": paused,
            "reason": str(reason)[:MAX_REASON] if isinstance(reason, str) and reason else None,
        },
        "cloud": cloud,
    }


def _serial(value: Any) -> int | str | None:
    """
    Read a placement's serial.

    Args:
        value: A count, a percentage or None.

    Returns:
        The value.

    Raises:
        ValidationError: When it is anything else.
    """
    return checked_serial(value)


def _count(value: Any) -> int | None:
    """
    Read a failure count.

    Args:
        value: A whole number from 0, or None.

    Returns:
        The value.

    Raises:
        ValidationError: When it is anything else.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"Invalid max failures: {value!r}", field="inventory")
    return int(value)


def _bool(value: Any, what: str) -> bool:
    """
    Require a true or false.

    Args:
        value: What the document holds.
        what: Its name, for the message.

    Returns:
        The flag.

    Raises:
        ValidationError: When it is anything else.
    """
    if not isinstance(value, bool):
        raise ValidationError(
            f"The inventory's {what} flag is not true or false", field="inventory"
        )
    return value


def apply_inventory(
    manager: NodeManager,
    name: str,
    document: Any,
    *,
    actor: str | None,
    include_ssh: bool = False,
) -> dict[str, Any]:
    """
    Record, for a server just added to this central, what the central it came from knew.

    Idempotent: applying the same document again changes nothing more. What cannot be applied
    here is reported with the reason and what to do, never dropped silently.

    Args:
        manager: The node registry.
        name: The server, which must already be registered here (under any name).
        document: The inventory.
        actor: Who applies it.
        include_ssh: Also apply how the old central reached it (its wait, its bastion).

    Returns:
        ``applied`` and ``skipped``: each a list of ``{"part", ...}``; a skipped part has a
        ``reason``.

    Raises:
        ValidationError: The document is not a valid inventory. Nothing was written.
        NodeError: No node has that name here.
    """
    checked = validate_inventory(document)
    record = manager.get(name)
    store = manager.store
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    if checked["labels"]:
        NodeLabels(store).change(record.name, set_labels=checked["labels"])
        applied.append({"part": "labels", "detail": checked["labels"]})

    if checked["placements"]:
        placements = Placements(store)
        for entry in checked["placements"]:
            existing = placements.get(entry["domain"])
            if existing is None:
                placements.save(
                    entry["domain"],
                    nodes=[record.name],
                    serial=entry["serial"],
                    max_failures=entry["max_failures"],
                    canary=record.name if entry["canary"] else None,
                    rollback_on_failure=entry["rollback_on_failure"],
                    actor=actor,
                )
            elif record.name not in existing.nodes:
                placements.save(entry["domain"], nodes=[*existing.nodes, record.name], actor=actor)
        applied.append(
            {"part": "placements", "detail": [p["domain"] for p in checked["placements"]]}
        )

    monitoring = checked["monitoring"]
    if monitoring["paused"]:
        Liveness(store).pause(record.name, reason=monitoring["reason"], actor=actor)
        applied.append({"part": "monitoring", "detail": "paused"})

    ssh = checked["ssh"]
    if ssh["connect_timeout"] is not None or ssh["bastion"] is not None:
        if include_ssh:
            bastion = None
            if ssh["bastion"] is not None:
                bastion = parse_bastion(ssh["bastion"]["address"], ssh["bastion"]["host_key"])
            NodeSSHOptions(store).configure(
                record.name,
                connect_timeout=ssh["connect_timeout"],
                bastion=bastion,
                actor=actor,
            )
            applied.append({"part": "ssh", "detail": "connection options"})
        else:
            how = "a bastion" if ssh["bastion"] is not None else "a connection wait"
            skipped.append(
                {
                    "part": "ssh",
                    "reason": (
                        f"the central it comes from reached it with {how}; a path that worked "
                        "from there may not exist from here. Set it if this central needs it too: "
                        f"noust node ssh {record.name}"
                        + (" (or apply with --include-ssh)" if ssh["bastion"] else "")
                    ),
                }
            )

    if checked["cloud"] is not None:
        outcome = _link_machine(manager, record.name, checked["cloud"], actor)
        (applied if outcome is None else skipped).append(
            {"part": "cloud", "detail": "machine linked"}
            if outcome is None
            else {"part": "cloud", "reason": outcome}
        )
    return {"applied": applied, "skipped": skipped}


def _link_machine(
    manager: NodeManager, node: str, cloud: dict[str, Any], actor: str | None
) -> str | None:
    """
    Link the provider's machine of an inventory, when this central has the account.

    Args:
        manager: The node registry.
        node: The server here.
        cloud: The inventory's ``cloud`` part.
        actor: Who applies it.

    Returns:
        None when it was linked (or already is); otherwise why it was not.
    """
    from noust.fleet.cloud.accounts import CloudAccounts
    from noust.fleet.cloud.servers import READY, CloudServers

    account = str(cloud.get("account") or "")
    servers = CloudServers(manager.store)
    if servers.find(node) is not None:
        return None
    try:
        CloudAccounts(manager.store).get(account)
    except NodeError:
        return (
            f"this central has no cloud account named {account}. Add it (noust fleet cloud account "
            f"add) and link the machine: noust fleet cloud server link {node} --account "
            f"{account} --id {cloud.get('provider_id')}"
        )
    provider_id = str(cloud.get("provider_id") or "")
    if not _PROVIDER_ID.fullmatch(provider_id):
        return f"the machine's id in the inventory is not one a provider gives: {provider_id!r}"
    known = CloudAccounts(manager.store).get(account)
    servers.record(
        node=node,
        account=account,
        provider=known.provider,
        provider_id=provider_id,
        location=cloud.get("location"),
        plan=cloud.get("plan"),
        image=cloud.get("image"),
        address=cloud.get("address"),
        created_by=actor,
    )
    servers.update(node, state=READY)
    return None
