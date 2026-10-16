# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application that runs on several servers: where it runs, and how an update goes out.

A placement is the central's record of "shop.example.com runs on web-1, web-2 and every
server labelled ``role=web``". It holds no copy of the application: each server keeps its
own (its releases, its variables, its database) and the central asks them
(:mod:`noust.fleet.placement_status`). What the record owns is the membership, which is
named servers and a label selector (so a server that gets the label joins), and the
default way an update goes out over it: how many servers at a time, which one goes first,
how many failures stop it, and whether the servers already moved are put back when it
stops.

This is the fleet's side of an application on several servers. The central deploys nothing
itself and routes no traffic: a load balancer in front of the servers is the operator's, and
:mod:`noust.fleet.placement_upstreams` writes it the list of servers to put behind it.

:class:`Placements` is the only reader and writer of ``fleet_placements`` (schema v14).
"""

from __future__ import annotations

import builtins
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import DomainError, NodeError, ValidationError
from noust.core.store import NodeRecord, NoustStore, get_store
from noust.fleet.labels import matches, parse_selector
from noust.fleet.models import validate_node_name
from noust.validators.domain import validate_domain

__all__ = ["MAX_NODES", "UNSET", "Placement", "Placements", "checked_domain"]

#: Servers one placement may name. The label selector is how a larger fleet is grouped.
MAX_NODES = 64

#: Leave a field as it is, in :meth:`Placements.save`: None is a value there.
UNSET: Any = object()


def checked_domain(value: object) -> str:
    """
    Check the application's domain: the bare name, as the servers know it.

    Args:
        value: What the operator typed.

    Returns:
        The domain, lower case.

    Raises:
        ValidationError: When it is not a domain, or is one only after something was
            taken out of it (a scheme, a port, a path).
    """
    text = value if isinstance(value, str) else ""
    try:
        normalised = validate_domain(text)
    except DomainError as exc:
        raise ValidationError(
            f"Invalid domain: {value!r}",
            details="Give the bare domain the application is deployed as, such as "
            "shop.example.com.",
            field="domain",
        ) from exc
    if normalised != text.strip().lower():
        raise ValidationError(
            f"Invalid domain: {value!r}",
            details="Give the bare domain name, without a scheme, a port or a path.",
            field="domain",
        )
    return normalised


@dataclass(frozen=True)
class Placement:
    """
    An application placed on several servers.

    Attributes:
        domain: The application, the same on every server.
        nodes: The servers it is named on.
        selector: A label selector: every server carrying all of its pairs is a member too.
        serial: Servers an update goes to at a time (a number or ``"25%"``); None is one at a
            time.
        max_failures: Failures that stop an update; None stops at the first.
        canary: The server that goes first and alone; None lets the first member do it.
        rollback_on_failure: Put the servers already moved back when an update stops.
        created_at: When it was recorded, ISO 8601 UTC.
        created_by: Who recorded it.
        updated_at: When it last changed.
    """

    domain: str
    nodes: tuple[str, ...]
    selector: dict[str, str]
    serial: int | str | None
    max_failures: int | None
    canary: str | None
    rollback_on_failure: bool
    created_at: str
    created_by: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "domain": self.domain,
            "nodes": list(self.nodes),
            "selector": dict(self.selector),
            "serial": self.serial,
            "max_failures": self.max_failures,
            "canary": self.canary,
            "rollback_on_failure": self.rollback_on_failure,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
        }


def _checked_serial(value: object) -> int | str | None:
    """
    Check how many servers an update goes to at a time.

    Args:
        value: A count, a percentage (``"25%"``) or None.

    Returns:
        The value.

    Raises:
        ValidationError: When it is anything else.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValidationError(
            f"Invalid serial: {value!r}",
            details="Use a number of servers (2) or a percentage (25%).",
            field="serial",
        )
    from noust.web.fleet_jobs import batch_size

    batch_size(value, 1, 1)
    return value


class Placements:
    """
    Read and write where the applications of this central run on several servers.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def list(self) -> builtins.list[Placement]:
        """
        Returns:
            Every placement, by domain.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM fleet_placements ORDER BY domain")
            .fetchall()
        )
        return [self._read(row) for row in rows]

    def get(self, domain: str) -> Placement | None:
        """
        Read one placement.

        Args:
            domain: The application's domain.

        Returns:
            The placement, or None when the application is not placed.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM fleet_placements WHERE domain = ?", (domain.strip().lower(),))
            .fetchone()
        )
        return self._read(row) if row is not None else None

    def save(
        self,
        domain: str,
        *,
        nodes: Sequence[str] | None = None,
        selector: Mapping[str, str] | None = None,
        serial: int | str | None = UNSET,
        max_failures: int | None = UNSET,
        canary: str | None = UNSET,
        rollback_on_failure: bool = UNSET,
        actor: str | None = None,
    ) -> Placement:
        """
        Record where an application runs, or change it; what is not given is kept.

        Args:
            domain: The application.
            nodes: The servers it is named on (replaces the list); kept when None.
            selector: The label selector (replaces it); kept when None.
            serial: Servers an update goes to at a time; None for one at a time.
            max_failures: Failures that stop an update; None stops at the first.
            canary: The server that goes first; None for the first member.
            rollback_on_failure: Put the servers already moved back when an update stops.
            actor: Who records it.

        Returns:
            The placement as it is now.

        Raises:
            ValidationError: When nothing says where it runs, a value is invalid, or the
                canary is a server the placement names but does not include.
            NodeError: When a named server is not registered on this central.
        """
        key = checked_domain(domain)
        current = self.get(key)
        chosen_nodes = (
            self._checked_nodes(nodes) if nodes is not None else (current.nodes if current else ())
        )
        chosen_selector = (
            parse_selector(f"{k}={v}" for k, v in selector.items())
            if selector is not None
            else (current.selector if current else {})
        )
        if not chosen_nodes and not chosen_selector:
            raise ValidationError(
                "Say where the application runs",
                details="Name servers (--nodes web-1,web-2), a label that some servers "
                "carry (--label role=web), or both.",
                field="nodes",
            )
        base = current or Placement(
            domain=key,
            nodes=(),
            selector={},
            serial=None,
            max_failures=None,
            canary=None,
            rollback_on_failure=True,
            created_at=_now(),
            created_by=actor,
            updated_at=_now(),
        )
        changes: dict[str, Any] = {
            "nodes": chosen_nodes,
            "selector": dict(chosen_selector),
            "updated_at": _now(),
        }
        if serial is not UNSET:
            changes["serial"] = _checked_serial(serial)
        if max_failures is not UNSET:
            if max_failures is not None and (
                isinstance(max_failures, bool)
                or not isinstance(max_failures, int)
                or max_failures < 0
            ):
                raise ValidationError(
                    f"Invalid max failures: {max_failures!r}",
                    details="Use 0 to stop at the first failure, or a count.",
                    field="max_failures",
                )
            changes["max_failures"] = max_failures
        if canary is not UNSET:
            changes["canary"] = validate_node_name(canary) if canary is not None else None
        if rollback_on_failure is not UNSET:
            if not isinstance(rollback_on_failure, bool):
                raise ValidationError("rollback_on_failure is true or false", field="rollback")
            changes["rollback_on_failure"] = rollback_on_failure
        placement = replace(base, **changes)
        if (
            placement.canary is not None
            and not chosen_selector
            and placement.canary not in chosen_nodes
        ):
            raise ValidationError(
                f"The canary {placement.canary} is not one of the servers it runs on",
                details=f"It runs on: {', '.join(chosen_nodes)}.",
                field="canary",
            )
        self._write(placement)
        return placement

    def remove(self, domain: str) -> bool:
        """
        Forget where an application runs. Nothing is touched on any server.

        Args:
            domain: The application.

        Returns:
            True when there was a placement.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM fleet_placements WHERE domain = ?", (domain.strip().lower(),)
            )
            return cursor.rowcount > 0

    def forget_node(self, node: str) -> None:
        """
        Take a server that left the fleet out of the placements that named it.

        A placement left with no server and no label is dropped: it would say nothing.

        Args:
            node: The server's name.
        """
        for placement in self.list():
            if node not in placement.nodes and placement.canary != node:
                continue
            kept = tuple(name for name in placement.nodes if name != node)
            if not kept and not placement.selector:
                self.remove(placement.domain)
                continue
            self._write(
                replace(
                    placement,
                    nodes=kept,
                    canary=None if placement.canary == node else placement.canary,
                    updated_at=_now(),
                )
            )

    def members(self, placement: Placement, nodes: Iterable[NodeRecord]) -> builtins.list[str]:
        """
        Name the servers an application runs on right now.

        Args:
            placement: The placement.
            nodes: The servers of this central.

        Returns:
            The named servers and those carrying the selector's labels, in the registry's
            order, each once.
        """
        from noust.fleet.labels import NodeLabels

        records = list(nodes)
        labels = NodeLabels(self.store).all() if placement.selector else {}
        return [
            record.name
            for record in records
            if record.name in placement.nodes
            or (placement.selector and matches(labels.get(record.name, {}), placement.selector))
        ]

    def _checked_nodes(self, names: Sequence[str]) -> tuple[str, ...]:
        """
        Check the servers a placement names.

        Args:
            names: What the operator named.

        Returns:
            The names, each once, in the order given.

        Raises:
            NodeError: When one is not registered.
            ValidationError: When there are more than :data:`MAX_NODES`.
        """
        unique: builtins.list[str] = []
        for name in names:
            checked = validate_node_name(name)
            if checked not in unique:
                unique.append(checked)
        if len(unique) > MAX_NODES:
            raise ValidationError(
                f"An application is named on at most {MAX_NODES} servers",
                details="Label the servers and use the label instead (--label role=web).",
                field="nodes",
            )
        missing = [name for name in unique if self.store.get_node(name) is None]
        if missing:
            raise NodeError(
                f"No node named {', '.join(missing)} is registered on this central",
                details="List them with 'noust node list'.",
                field="nodes",
            )
        return tuple(unique)

    def _write(self, placement: Placement) -> None:
        """
        Store a placement.

        Args:
            placement: The placement, already checked.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO fleet_placements (domain, nodes, selector, serial, max_failures, "
                "canary, rollback_on_failure, created_at, created_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET nodes = excluded.nodes, "
                "selector = excluded.selector, serial = excluded.serial, "
                "max_failures = excluded.max_failures, canary = excluded.canary, "
                "rollback_on_failure = excluded.rollback_on_failure, "
                "updated_at = excluded.updated_at",
                (
                    placement.domain,
                    json.dumps(list(placement.nodes)),
                    json.dumps(placement.selector, sort_keys=True),
                    None if placement.serial is None else str(placement.serial),
                    placement.max_failures,
                    placement.canary,
                    1 if placement.rollback_on_failure else 0,
                    placement.created_at,
                    placement.created_by,
                    placement.updated_at,
                ),
            )

    @staticmethod
    def _read(row: Any) -> Placement:
        """
        Read a stored placement.

        Args:
            row: A ``fleet_placements`` row.

        Returns:
            The placement.
        """
        serial: int | str | None = row["serial"]
        if isinstance(serial, str) and serial.isdigit():
            serial = int(serial)
        return Placement(
            domain=row["domain"],
            nodes=tuple(json.loads(row["nodes"])),
            selector=dict(json.loads(row["selector"])),
            serial=serial,
            max_failures=row["max_failures"],
            canary=row["canary"],
            rollback_on_failure=bool(row["rollback_on_failure"]),
            created_at=row["created_at"],
            created_by=row["created_by"],
            updated_at=row["updated_at"],
        )


def _now() -> str:
    """Returns: The current time, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()
