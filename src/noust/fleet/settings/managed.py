# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a node applied of the fleet's settings: the ``fleet_managed`` table and its two states.

A node keeps, for each setting a central pushed, which central, which version, who approved the
change and a **fingerprint** of what the node held right after the push - never the value. The
setting itself lives where the node's own managers keep it, and its operator can change it there
by the console, by the command line or by hand-editing a file. The node does not wait to be told:
whenever anybody asks what it manages (:meth:`ManagedSettings.verified`) it looks at what it holds
and compares. A setting that no longer matches is ``overridden`` - the node rules on its own
machine, and the central shows it as an exception - until the central applies it again.

The fingerprint is an HMAC under a key that only this node has (``fleet-managed/fingerprint-key``
in its secret store): credentials take part in it, so that a changed password is found, and a
fingerprint is of no use to anyone who gets hold of the table (a copy, an export).
"""

from __future__ import annotations

import builtins
import hashlib
import hmac
import json
import secrets as token
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from noust.core import audit
from noust.core.secrets import SecretStore
from noust.core.server_text import say
from noust.core.store import NoustStore, get_store
from noust.core.teams.model import ResourceNotFoundError
from noust.core.timeutil import utc_now_iso
from noust.fleet.settings import snapshots
from noust.fleet.settings.signature import SyncSignature

__all__ = ["STATES", "ManagedRow", "ManagedSettings"]

#: What a node says of a setting a central pushed.
STATES = ("managed", "overridden")

#: Where this node's fingerprint key is kept.
_FINGERPRINT_NAME = "fleet-managed/fingerprint-key"

#: Reads what the node holds of a setting; :func:`noust.fleet.settings.snapshots.take`.
Reader = Callable[..., dict[str, Any] | None]


@dataclass(frozen=True)
class ManagedRow:
    """
    One row of ``fleet_managed``.

    Attributes:
        resource: The kind.
        name: The setting.
        central: The central that pushed it, as this node knows it (its fleet token's name).
        version: The version of the central's row it applied.
        fingerprint: The fingerprint of what it held right after.
        state: ``managed``, or ``overridden`` when its own operator changed it.
        change_id: The change the central pushed it for.
        applied_by: The person who approved that change.
        applied_at: When, ISO 8601 UTC.
        overridden_at: When the node found it changed; None while managed.
        restore_requested_at: When this node's operator asked the central to put it back.
    """

    resource: str
    name: str
    central: str
    version: int
    fingerprint: str
    state: str
    change_id: str
    applied_by: str
    applied_at: str
    overridden_at: str | None
    restore_requested_at: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The row as the API shows it: the fingerprint cut to a handle, since the whole of it
            tells nobody anything.
        """
        return {
            "resource": self.resource,
            "name": self.name,
            "central": self.central,
            "version": self.version,
            "fingerprint": self.fingerprint[:16],
            "state": self.state,
            "change": self.change_id,
            "applied_by": self.applied_by,
            "applied_at": self.applied_at,
            "overridden_at": self.overridden_at,
            "restore_requested_at": self.restore_requested_at,
        }


def _row(row: sqlite3.Row) -> ManagedRow:
    """
    Args:
        row: A ``fleet_managed`` row.

    Returns:
        The row.
    """
    return ManagedRow(
        resource=str(row["resource"]),
        name=str(row["name"]),
        central=str(row["central"]),
        version=int(row["version"]),
        fingerprint=str(row["fingerprint"]),
        state=str(row["state"]),
        change_id=str(row["change_id"]),
        applied_by=str(row["applied_by"]),
        applied_at=str(row["applied_at"]),
        overridden_at=row["overridden_at"],
        restore_requested_at=row["restore_requested_at"],
    )


class ManagedSettings:
    """
    The settings a central manages on this node.

    Args:
        store: This node's store; the process-wide one by default.
        secrets: Its secret store, where the fingerprint key is.
        reader: How it reads what it holds of a setting; its own managers by default.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        reader: Reader | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._reader = reader

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def secrets(self) -> SecretStore:
        """The secret store."""
        return self._secrets or SecretStore()

    # -- The fingerprint -----------------------------------------------------------

    def _key(self) -> bytes:
        """
        Returns:
            This node's fingerprint key, made the first time it is needed.
        """
        found = self.secrets.read(_FINGERPRINT_NAME)
        if not found:
            found = token.token_hex(32)
            self.secrets.write(_FINGERPRINT_NAME, found)
        return found.strip().encode("ascii")

    def fingerprint(self, held: dict[str, Any]) -> str:
        """
        Args:
            held: A setting as this node stores it (:mod:`noust.fleet.settings.snapshots`).

        Returns:
            Its HMAC-SHA256 under this node's key: 64 hex digits.
        """
        canonical = json.dumps(held, sort_keys=True, separators=(",", ":"), default=str)
        return hmac.new(self._key(), canonical.encode(), hashlib.sha256).hexdigest()

    def _held(self, resource: str, name: str) -> dict[str, Any] | None:
        """
        Args:
            resource: The kind.
            name: The setting.

        Returns:
            What this node holds of it.

        Raises:
            KeyError: For a kind this node cannot read.
        """
        if self._reader is not None:
            return self._reader(resource, name, store=self._store, secrets=self._secrets)
        return snapshots.take(resource, name, store=self._store, secrets=self._secrets)

    # -- Reading -------------------------------------------------------------------

    def list(self) -> list[ManagedRow]:
        """Returns: Every row as it is stored, by kind and name; nothing is compared."""
        rows = self.store._get_connection().execute(
            "SELECT * FROM fleet_managed ORDER BY resource, name"
        )
        return [_row(row) for row in rows.fetchall()]

    def get(self, resource: str, name: str) -> ManagedRow | None:
        """
        Args:
            resource: The kind.
            name: The setting.

        Returns:
            Its row, or None when no central manages it here.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM fleet_managed WHERE resource = ? AND name = ?", (resource, name)
            )
            .fetchone()
        )
        return _row(row) if row is not None else None

    def verified(self) -> builtins.list[ManagedRow]:
        """
        Look at what this node holds of each setting it manages, and say which still match.

        A setting that does not match is marked ``overridden`` (once: it stays so until the
        central applies it again, also if the operator changes it back by hand) and the audit
        trail says so. A kind this node has no reader for is left alone.

        Returns:
            Every row, as it stands after looking.
        """
        for row in self.list():
            if row.state != "managed":
                continue
            try:
                held = self._held(row.resource, row.name)
            except KeyError:
                continue
            if held is not None and self.fingerprint(held) == row.fingerprint:
                continue
            self._overridden(row, "removed" if held is None else "changed")
        return self.list()

    def _overridden(self, row: ManagedRow, how: str) -> None:
        """
        Mark a setting its own operator changed, and say so in the audit trail.

        Args:
            row: The row.
            how: ``changed`` or ``removed``.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE fleet_managed SET state = 'overridden', overridden_at = ? "
                "WHERE resource = ? AND name = ? AND state = 'managed'",
                (utc_now_iso(), row.resource, row.name),
            )
            changed = cursor.rowcount
        if changed:
            audit.record(
                "fleet.setting.diverge",
                target=f"{row.resource}:{row.name}",
                details={"central": row.central, "version": row.version, "how": how},
            )

    # -- Writing -------------------------------------------------------------------

    def record_applied(
        self,
        resource: str,
        name: str,
        *,
        central: str,
        version: int,
        signature: SyncSignature,
        actor: audit.Actor | None = None,
    ) -> ManagedRow:
        """
        Note that a central pushed a setting, and fingerprint what this node holds of it now.

        Args:
            resource: The kind.
            name: The setting.
            central: The central, as this node knows it.
            version: The version of the central's row that was pushed.
            signature: The person and the change the push was signed with.
            actor: Who the audit trail names (the central acting for the person).

        Returns:
            The row; the setting is ``managed`` again, also if it was ``overridden``.

        Raises:
            ResourceNotFoundError: This node holds no such setting: the push did not make it.
            KeyError: For a kind this node cannot read.
        """
        held = self._held(resource, name)
        if held is None:
            raise ResourceNotFoundError.from_text(
                say("fleet_settings.managed_missing", kind=resource, name=name)
            )
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO fleet_managed (resource, name, central, version, fingerprint, state, "
                "change_id, applied_by, applied_at) VALUES (?, ?, ?, ?, ?, 'managed', ?, ?, ?) "
                "ON CONFLICT(resource, name) DO UPDATE SET central = excluded.central, "
                "version = excluded.version, fingerprint = excluded.fingerprint, "
                "state = 'managed', change_id = excluded.change_id, "
                "applied_by = excluded.applied_by, applied_at = excluded.applied_at, "
                "overridden_at = NULL, restore_requested_at = NULL",
                (
                    resource,
                    name,
                    central,
                    version,
                    self.fingerprint(held),
                    signature.change,
                    signature.person,
                    utc_now_iso(),
                ),
            )
        audit.record(
            "fleet.setting.apply",
            actor=actor,
            target=f"{resource}:{name}",
            details={"central": central, "version": version, "change": signature.change},
        )
        row = self.get(resource, name)
        if row is None:
            raise RuntimeError("a setting that was just recorded is not there")
        return row

    def release(self, resource: str, name: str, *, actor: audit.Actor | None = None) -> bool:
        """
        Stop treating a setting as the fleet's. What the node holds of it stays as its own.

        Args:
            resource: The kind.
            name: The setting.
            actor: Who the audit trail names.

        Returns:
            Whether the node managed it.
        """
        row = self.get(resource, name)
        if row is None:
            return False
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM fleet_managed WHERE resource = ? AND name = ?", (resource, name)
            )
        audit.record(
            "fleet.setting.release",
            actor=actor,
            target=f"{resource}:{name}",
            details={"central": row.central, "version": row.version, "state": row.state},
        )
        return True

    def release_all(self, central: str, *, actor: audit.Actor | None = None) -> int:
        """
        Let go of everything one central manages here: it left, or this node left it.

        Args:
            central: The central, as this node knows it.
            actor: Who the audit trail names.

        Returns:
            How many settings were released.
        """
        released = 0
        for row in self.list():
            if row.central == central and self.release(row.resource, row.name, actor=actor):
                released += 1
        return released

    def request_restore(self, resource: str, name: str) -> ManagedRow:
        """
        Note that this node's operator wants the central to put a setting back as the fleet's.

        A node cannot call its central: the request waits here until the central next applies
        the fleet's settings to this node, which it reads from :meth:`verified`.

        Args:
            resource: The kind.
            name: The setting.

        Returns:
            The row.

        Raises:
            ResourceNotFoundError: No central manages that setting here.
        """
        if self.get(resource, name) is None:
            raise ResourceNotFoundError.from_text(
                say("fleet_settings.not_managed", kind=resource, name=name)
            )
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE fleet_managed SET restore_requested_at = COALESCE(restore_requested_at, ?) "
                "WHERE resource = ? AND name = ?",
                (utc_now_iso(), resource, name),
            )
        row = self.get(resource, name)
        if row is None:
            raise RuntimeError("a setting that was just asked back is not there")
        return row
