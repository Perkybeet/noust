# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The copies a central holds of its servers, and how each server is doing (item 195).

The only reader and writer of ``collected_backups`` and ``collect_nodes`` (schema v18). A row of
``collected_backups`` is a copy whose every file is on the central's disk, whole and checked: it is
written after the last file is in place, never before, so a crash leaves files with no row (which
nothing offers, and the next pass takes again over) and never a row with files missing. A copy the
central let go of (its retention, or its operator) stays as a ``retired`` row until the server stops
listing it, so a server that still has it is not asked for it again.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from noust.core.store import NoustStore, get_store
from noust.core.timeutil import utc_now_iso

__all__ = [
    "HELD",
    "RETIRED",
    "Collected",
    "CollectedFile",
    "CollectedStore",
    "NodeState",
    "NodeStates",
]

#: A copy the central holds, and one it let go of.
HELD = "held"
RETIRED = "retired"


@dataclass(frozen=True)
class CollectedFile:
    """
    One file of a copy the central holds.

    Attributes:
        name: Its name, in the copy's folder.
        size: Bytes, as received.
        sha256: The digest of what was received.
        role: ``archive`` or ``sidecar``.
    """

    name: str
    size: int
    sha256: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The file, JSON-ready.
        """
        return {"name": self.name, "size": self.size, "sha256": self.sha256, "role": self.role}


@dataclass(frozen=True)
class Collected:
    """
    A copy the central holds of a server.

    Attributes:
        node: The server.
        kind: ``app``, ``database``, ``volume`` or ``noust``.
        item_id: Its identifier on the server.
        owner: What it is a copy of.
        folder: One plain name, the folder of the central's it is in.
        origin: ``scheduled``, ``manual`` or ``safety``.
        created_at: When the server took it, UTC.
        collected_at: When the central finished taking it, UTC.
        size: What its files weigh.
        files: Its files.
        verified: How it was checked: ``digest`` (every file matched the digest the server
            recorded for it) or ``size`` (the server recorded none, and what arrived is as long as
            it said).
        state: :data:`HELD` or :data:`RETIRED`.
        meta: Small facts a restore needs (the application, the engine and the database, the
            target); what a retention groups volume copies by.
    """

    node: str
    kind: str
    item_id: str
    owner: str
    folder: str
    origin: str
    created_at: str
    collected_at: str
    size: int
    files: tuple[CollectedFile, ...]
    verified: str
    state: str = HELD
    meta: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The copy as the central's API shows it, with no path in it.
        """
        return {
            "node": self.node,
            "kind": self.kind,
            "id": self.item_id,
            "owner": self.owner,
            "origin": self.origin,
            "created_at": self.created_at,
            "collected_at": self.collected_at,
            "size": self.size,
            "files": [item.to_dict() for item in self.files],
            "verified": self.verified,
            "state": self.state,
            "meta": dict(self.meta),
        }


class CollectedStore:
    """
    Read and write the copies the central holds.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @staticmethod
    def _read(row: Any) -> Collected:
        """
        Args:
            row: A ``collected_backups`` row.

        Returns:
            The copy.
        """
        try:
            listed = json.loads(row["files"])
        except ValueError:
            listed = []
        files = tuple(
            CollectedFile(str(f["name"]), int(f["size"]), str(f["sha256"]), str(f["role"]))
            for f in listed
            if isinstance(f, dict) and {"name", "size", "sha256", "role"} <= set(f)
        )
        try:
            meta = json.loads(row["meta"] or "{}")
        except ValueError:
            meta = {}
        return Collected(
            node=str(row["node"]),
            kind=str(row["kind"]),
            item_id=str(row["item_id"]),
            owner=str(row["owner"]),
            folder=str(row["folder"]),
            origin=str(row["origin"]),
            created_at=str(row["created_at"]),
            collected_at=str(row["collected_at"]),
            size=int(row["size"]),
            files=files,
            verified=str(row["verified"]),
            state=str(row["state"]),
            meta={str(k): str(v) for k, v in meta.items()} if isinstance(meta, dict) else {},
        )

    def add(self, copy: Collected) -> None:
        """
        Record a copy whose files are all in place.

        Args:
            copy: The copy. One of the same server, kind and identifier is replaced.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO collected_backups (node, kind, item_id, owner, folder, origin, "
                "created_at, collected_at, size, files, verified, state, meta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(node, kind, item_id) DO UPDATE SET owner = excluded.owner, "
                "folder = excluded.folder, origin = excluded.origin, "
                "created_at = excluded.created_at, collected_at = excluded.collected_at, "
                "size = excluded.size, files = excluded.files, verified = excluded.verified, "
                "state = excluded.state, meta = excluded.meta",
                (
                    copy.node,
                    copy.kind,
                    copy.item_id,
                    copy.owner,
                    copy.folder,
                    copy.origin,
                    copy.created_at,
                    copy.collected_at,
                    copy.size,
                    json.dumps([f.to_dict() for f in copy.files]),
                    copy.verified,
                    copy.state,
                    json.dumps(copy.meta),
                ),
            )

    def get(self, node: str, kind: str, item_id: str) -> Collected | None:
        """
        Args:
            node: The server.
            kind: The kind of copy.
            item_id: Its identifier on the server.

        Returns:
            The copy, held or retired; None when the central never took it.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM collected_backups WHERE node = ? AND kind = ? AND item_id = ?",
                (node, kind, item_id),
            )
            .fetchone()
        )
        return self._read(row) if row is not None else None

    def list(
        self,
        *,
        node: str | None = None,
        nodes: Iterable[str] | None = None,
        kind: str | None = None,
        state: str | None = HELD,
    ) -> list[Collected]:
        """
        List copies, newest first.

        Args:
            node: Only this server's.
            nodes: Only these servers' (what a person's teams see).
            kind: Only this kind.
            state: Only copies in this state; None for every state.

        Returns:
            The copies.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if node is not None:
            clauses.append("node = ?")
            params.append(node)
        if nodes is not None:
            names = list(nodes)
            clauses.append(f"node IN ({','.join('?' * len(names))})" if names else "0")
            params.extend(names)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            self.store._get_connection()
            .execute(f"SELECT * FROM collected_backups{where} ORDER BY created_at DESC", params)  # noqa: S608 - the clauses are literals; values are bound
            .fetchall()
        )
        return [self._read(row) for row in rows]

    def retire(self, node: str, kind: str, item_id: str) -> bool:
        """
        Let a copy go: it stays on record as retired, with nothing to offer.

        Args:
            node: The server.
            kind: The kind of copy.
            item_id: Its identifier on the server.

        Returns:
            True when there was a held copy to retire.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE collected_backups SET state = ?, files = '[]', size = 0 "
                "WHERE node = ? AND kind = ? AND item_id = ? AND state = ?",
                (RETIRED, node, kind, item_id, HELD),
            )
            return cursor.rowcount > 0

    def forget_retired_except(self, node: str, listed: set[tuple[str, str]]) -> int:
        """
        Drop the retired copies of a server that it no longer lists.

        Args:
            node: The server.
            listed: ``(kind, identifier)`` of every copy it lists now.

        Returns:
            How many were dropped.
        """
        gone = [
            (copy.kind, copy.item_id)
            for copy in self.list(node=node, state=RETIRED)
            if (copy.kind, copy.item_id) not in listed
        ]
        with self.store._transaction() as cursor:
            for kind, item_id in gone:
                cursor.execute(
                    "DELETE FROM collected_backups WHERE node = ? AND kind = ? AND item_id = ? "
                    "AND state = ?",
                    (node, kind, item_id, RETIRED),
                )
        return len(gone)

    def forget_node(self, node: str) -> None:
        """
        Forget everything recorded of a server that is no longer managed.

        Args:
            node: The server.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM collected_backups WHERE node = ?", (node,))
            cursor.execute("DELETE FROM collect_nodes WHERE node = ?", (node,))

    def usage(self, node: str | None = None) -> dict[str, int]:
        """
        Args:
            node: Only this server's.

        Returns:
            Bytes held by kind, as recorded.
        """
        query = "SELECT kind, SUM(size) AS bytes FROM collected_backups WHERE state = ?"
        params: list[Any] = [HELD]
        if node is not None:
            query += " AND node = ?"
            params.append(node)
        rows = self.store._get_connection().execute(query + " GROUP BY kind", params).fetchall()
        return {str(row["kind"]): int(row["bytes"] or 0) for row in rows}


@dataclass(frozen=True)
class NodeState:
    """
    How collecting is going with one server.

    Attributes:
        node: The server.
        follow: Its own answer (True or False), or None to follow the fleet's.
        last_pass_at: When a pass over it last ran.
        last_ok_at: When one last ended with nothing left to take and nothing that failed.
        last_error: What the last failure said, verbatim.
        last_error_code: A stable word for it (``unreachable``, ``refused``, ``disk_full``,
            ``mismatch``, ``unsupported``...).
        failures: Passes in a row that ended in a failure.
        fingerprint: The server's fingerprint of its list when the last pass finished it.
        support: What the server can be collected for: ``mode`` (``full``, ``databases_only`` or
            ``none``), ``kinds`` and ``version`` as it said.
    """

    node: str
    follow: bool | None = None
    last_pass_at: str | None = None
    last_ok_at: str | None = None
    last_error: str | None = None
    last_error_code: str | None = None
    failures: int = 0
    fingerprint: str | None = None
    support: dict[str, Any] = field(default_factory=dict)


class NodeStates:
    """
    Read and write how collecting is going with each server.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @staticmethod
    def _read(row: Any) -> NodeState:
        """
        Args:
            row: A ``collect_nodes`` row.

        Returns:
            The state.
        """
        try:
            support = json.loads(row["support"] or "{}")
        except ValueError:
            support = {}
        follow = row["follow"]
        return NodeState(
            node=str(row["node"]),
            follow=None if follow is None else bool(follow),
            last_pass_at=row["last_pass_at"],
            last_ok_at=row["last_ok_at"],
            last_error=row["last_error"],
            last_error_code=row["last_error_code"],
            failures=int(row["failures"] or 0),
            fingerprint=row["fingerprint"],
            support=support if isinstance(support, dict) else {},
        )

    def get(self, node: str) -> NodeState:
        """
        Args:
            node: The server.

        Returns:
            Its state; an empty one when nothing was ever recorded.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM collect_nodes WHERE node = ?", (node,))
            .fetchone()
        )
        return self._read(row) if row is not None else NodeState(node)

    def all(self) -> dict[str, NodeState]:
        """
        Returns:
            The state of every server that has one, by name.
        """
        rows = self.store._get_connection().execute("SELECT * FROM collect_nodes").fetchall()
        return {str(row["node"]): self._read(row) for row in rows}

    def record_look(self, node: str, *, support: dict[str, Any] | None = None) -> None:
        """
        Write down that a server was asked and said nothing had changed.

        It does not count as a whole pass: ``last_ok_at`` stays, so the server's whole list is
        still read again when it is due.

        Args:
            node: The server.
            support: What the server was found able to give.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO collect_nodes (node, last_pass_at, support) VALUES (?, ?, ?) "
                "ON CONFLICT(node) DO UPDATE SET last_pass_at = excluded.last_pass_at, "
                "last_error = NULL, last_error_code = NULL, failures = 0, "
                "support = COALESCE(excluded.support, collect_nodes.support)",
                (node, utc_now_iso(), json.dumps(support) if support is not None else None),
            )

    def record_pass(
        self,
        node: str,
        *,
        ok: bool,
        error: str | None = None,
        code: str | None = None,
        fingerprint: str | None = None,
        support: dict[str, Any] | None = None,
    ) -> NodeState:
        """
        Write down how a pass over a server ended.

        Args:
            node: The server.
            ok: Whether it ended with nothing left to take and nothing that failed.
            error: What the failure said, verbatim.
            code: A stable word for it.
            fingerprint: The server's fingerprint of its list, when the pass took all of it.
            support: What the server was found able to give.

        Returns:
            The state as it is now.
        """
        now = utc_now_iso()
        before = self.get(node)
        failures = 0 if ok else before.failures + 1
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO collect_nodes (node, last_pass_at, last_ok_at, last_error, "
                "last_error_code, failures, fingerprint, support) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(node) DO UPDATE SET last_pass_at = excluded.last_pass_at, "
                "last_ok_at = COALESCE(excluded.last_ok_at, collect_nodes.last_ok_at), "
                "last_error = excluded.last_error, last_error_code = excluded.last_error_code, "
                "failures = excluded.failures, fingerprint = excluded.fingerprint, "
                "support = COALESCE(excluded.support, collect_nodes.support)",
                (
                    node,
                    now,
                    now if ok else None,
                    None if ok else (error or "")[:4000],
                    None if ok else code,
                    failures,
                    fingerprint if ok else None,
                    json.dumps(support) if support is not None else None,
                ),
            )
        return self.get(node)
