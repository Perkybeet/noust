# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central wants every server to have: the ``fleet_desired`` table and the secrets beside it.

A row is one setting in one scope: ``fleet`` (every server) or ``node:<name>`` (an exception a
person set from the central for that server). Its ``version`` is the row's own never-reused id, so
one number names a row of a scope exactly and what a node applied (``fleet_managed.version``) says
which one it was. The value is the body of the node's own route with the credentials taken out
(:func:`~noust.fleet.settings.kinds.split_secrets`); the credentials are one JSON secret per
version in the central's secret store, sealed when the central is.

Who may set what is decided here and not in the routes (rule 4): a person needs the permission the
node's own route asks for that kind. Which value a server gets is decided here too
(:meth:`FleetSettings.effective_for`), for the reconciler and for the status the console shows.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import PermissionError as NoustPermissionError
from noust.core.exceptions import ValidationError
from noust.core.secrets import SecretStore
from noust.core.server_text import say
from noust.core.store import NoustStore, get_store
from noust.core.timeutil import utc_now_iso
from noust.fleet.settings.kinds import (
    NODE_SCOPE_PREFIX,
    SettingKind,
    check_scope,
    join_secrets,
    kind_of,
    split_secrets,
)
from noust.fleet.settings.signature import new_change_id

__all__ = ["MODES", "DesiredSetting", "FleetSettings"]

#: How a server follows the fleet's settings.
MODES = ("managed", "independent")


@dataclass(frozen=True)
class DesiredSetting:
    """
    One row of ``fleet_desired``.

    Attributes:
        resource: The kind's key (``channel``).
        name: The setting's name.
        scope: ``fleet``, or ``node:<name>`` for an exception.
        version: The row's id: unique, increasing, never reused.
        value: The value without its credentials.
        secret_paths: The paths of the value whose credentials are stored.
        change_id: The change that wrote it.
        changed_by: The person who approved that change.
        changed_at: When, ISO 8601 UTC.
    """

    resource: str
    name: str
    scope: str
    version: int
    value: dict[str, Any]
    secret_paths: tuple[str, ...]
    change_id: str
    changed_by: str
    changed_at: str

    @property
    def node(self) -> str | None:
        """The server an exception is for; None for the fleet's own."""
        return self.scope[len(NODE_SCOPE_PREFIX) :] if self.scope.startswith("node:") else None


def _row(row: sqlite3.Row) -> DesiredSetting:
    """
    Args:
        row: A ``fleet_desired`` row.

    Returns:
        The setting.
    """
    return DesiredSetting(
        resource=str(row["resource"]),
        name=str(row["name"]),
        scope=str(row["scope"]),
        version=int(row["version"]),
        value=json.loads(row["value"]),
        secret_paths=tuple(json.loads(row["secret_paths"])),
        change_id=str(row["change_id"]),
        changed_by=str(row["changed_by"]),
        changed_at=str(row["changed_at"]),
    )


class FleetSettings:
    """
    The settings a central wants, read and changed in one place.

    Args:
        store: The central's store; the process-wide one by default.
        secrets: Where the credentials are kept; the process-wide secret store by default.
    """

    def __init__(self, store: NoustStore | None = None, secrets: SecretStore | None = None) -> None:
        self._store = store
        self._secrets = secrets

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def secrets(self) -> SecretStore:
        """The secret store."""
        return self._secrets or SecretStore()

    # -- Reading ------------------------------------------------------------------

    def list(self, resource: str | None = None) -> list[DesiredSetting]:
        """
        Args:
            resource: Only this kind.

        Returns:
            Every row, by kind, name and scope.
        """
        query = "SELECT * FROM fleet_desired"
        args: tuple[str, ...] = ()
        if resource is not None:
            query += " WHERE resource = ?"
            args = (resource,)
        rows = self.store._get_connection().execute(query + " ORDER BY resource, name, scope", args)
        return [_row(row) for row in rows.fetchall()]

    def get(self, resource: str, name: str, scope: str = "fleet") -> DesiredSetting | None:
        """
        Args:
            resource: The kind.
            name: The setting.
            scope: ``fleet`` or ``node:<name>``.

        Returns:
            The row, or None.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM fleet_desired WHERE resource = ? AND name = ? AND scope = ?",
                (resource, name, scope),
            )
            .fetchone()
        )
        return _row(row) if row is not None else None

    def secrets_of(self, setting: DesiredSetting) -> dict[str, str]:
        """
        Args:
            setting: A row.

        Returns:
            Its credentials by path; empty when it has none.

        Raises:
            SecretsLockedError: The central is sealed and locked.
        """
        if not setting.secret_paths:
            return {}
        stored = self.secrets.read_json(_secret_name(setting.version))
        return {str(path): str(value) for path, value in stored.items()}

    def whole_value(self, setting: DesiredSetting) -> dict[str, Any]:
        """
        Args:
            setting: A row.

        Returns:
            The value with its credentials in it: what is pushed to a node, and nothing that is
            ever shown.
        """
        return join_secrets(kind_of(setting.resource), setting.value, self.secrets_of(setting))

    def shown(self, setting: DesiredSetting) -> dict[str, Any]:
        """
        Args:
            setting: A row.

        Returns:
            The row as the API shows it: its credentials are :data:`REDACTED`, never what they
            are.
        """
        kind = kind_of(setting.resource)
        return {
            "resource": setting.resource,
            "name": setting.name,
            "scope": setting.scope,
            "node": setting.node,
            "version": setting.version,
            "value": kind.shown(setting.value, frozenset(setting.secret_paths)),
            "secrets": list(setting.secret_paths),
            "change": setting.change_id,
            "changed_by": setting.changed_by,
            "changed_at": setting.changed_at,
        }

    def effective_for(
        self, node: str, *, ignore_mode: bool = False
    ) -> builtins.list[DesiredSetting]:
        """
        Say which value a server gets of each setting: its own exception, else the fleet's.

        Args:
            node: The server.
            ignore_mode: Say what it would get if it followed the fleet's settings, whether or
                not it does: how the screen tells an independent server what following again
                would bring.

        Returns:
            One row per setting, by kind and name; nothing for a server that is independent,
            unless the mode is to be ignored.
        """
        if not ignore_mode and self.mode(node) == "independent":
            return []
        chosen: dict[tuple[str, str], DesiredSetting] = {}
        for setting in self.list():
            if setting.scope == "fleet":
                chosen.setdefault((setting.resource, setting.name), setting)
            elif setting.node == node:
                chosen[(setting.resource, setting.name)] = setting
        return [chosen[key] for key in sorted(chosen)]

    # -- Writing ------------------------------------------------------------------

    def save(
        self,
        resource: str,
        name: str,
        value: object,
        *,
        scope: str = "fleet",
        person: str,
        held: Collection[str],
    ) -> DesiredSetting:
        """
        Set a setting, as a new change.

        Args:
            resource: The kind.
            name: The setting.
            value: What it is: the body of the node's own route for it. A credential sent back
                as :data:`REDACTED` keeps the one stored.
            scope: ``fleet``, or ``node:<name>`` for an exception.
            person: Who approves the change (the central's label of them).
            held: The permissions that person holds: they need the one the node's own route asks.

        Returns:
            The row as stored.

        Raises:
            NoustPermissionError: They lack the permission of that kind.
            ValidationError: The kind, name, scope or value is not acceptable, or the server an
                exception is for is not in the fleet.
        """
        kind = kind_of(resource)
        self._require(kind, held)
        name = kind.check_name(name)
        scope = check_scope(scope)
        whole = kind.check_value(value)
        if scope != "fleet":
            self._require_node(scope[len(NODE_SCOPE_PREFIX) :])
        existing = self.get(resource, name, scope)
        # An exception that does not mention a credential starts from the fleet's.
        origin = existing or (self.get(resource, name) if scope != "fleet" else None)
        keep = self.secrets_of(origin) if origin is not None else None
        public, secrets = split_secrets(kind, name, whole, keep=keep)
        with self.store._transaction() as cursor:
            if existing is not None:
                cursor.execute("DELETE FROM fleet_desired WHERE version = ?", (existing.version,))
            cursor.execute(
                "INSERT INTO fleet_desired (resource, name, scope, value, secret_paths, "
                "change_id, changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    resource,
                    name,
                    scope,
                    json.dumps(public, sort_keys=True),
                    json.dumps(sorted(secrets)),
                    new_change_id(),
                    person,
                    utc_now_iso(),
                ),
            )
            version = int(cursor.lastrowid or 0)
            if secrets:
                # Inside the transaction: a secret that cannot be kept leaves no row behind.
                self.secrets.write_json(_secret_name(version), dict(secrets))
        if existing is not None and existing.secret_paths:
            self.secrets.delete(_secret_name(existing.version))
        saved = self.get(resource, name, scope)
        if saved is None:
            raise RuntimeError("a setting that was just saved is not there")
        return saved

    def remove(
        self,
        resource: str,
        name: str,
        scope: str = "fleet",
        *,
        person: str,
        held: Collection[str],
    ) -> bool:
        """
        Take a setting away. What it put on the servers stays there as theirs.

        Args:
            resource: The kind.
            name: The setting.
            scope: ``fleet``, or ``node:<name>`` for an exception.
            person: Who approves the change.
            held: The permissions that person holds.

        Returns:
            Whether there was one.

        Raises:
            NoustPermissionError: They lack the permission of that kind.
            ValidationError: The kind or scope is not acceptable.
        """
        kind = kind_of(resource)
        self._require(kind, held)
        scope = check_scope(scope)
        existing = self.get(resource, name, scope)
        if existing is None:
            return False
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM fleet_desired WHERE version = ?", (existing.version,))
        if existing.secret_paths:
            self.secrets.delete(_secret_name(existing.version))
        return True

    def forget_node(self, node: str) -> None:
        """
        Drop what is kept for a server that left the fleet: its exceptions and its mode.

        Args:
            node: The server.
        """
        for setting in self.list():
            if setting.node == node:
                with self.store._transaction() as cursor:
                    cursor.execute(
                        "DELETE FROM fleet_desired WHERE version = ?", (setting.version,)
                    )
                if setting.secret_paths:
                    self.secrets.delete(_secret_name(setting.version))
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM fleet_node_settings WHERE node = ?", (node,))

    # -- A server's mode ----------------------------------------------------------

    def mode(self, node: str) -> str:
        """
        Args:
            node: The server.

        Returns:
            ``managed`` (it follows the fleet) or ``independent`` (the central pushes it nothing).
        """
        row = (
            self.store._get_connection()
            .execute("SELECT mode FROM fleet_node_settings WHERE node = ?", (node,))
            .fetchone()
        )
        return str(row["mode"]) if row is not None else "managed"

    def modes(self) -> Mapping[str, str]:
        """Returns: The mode of each server that has one set; the rest are ``managed``."""
        rows = self.store._get_connection().execute("SELECT node, mode FROM fleet_node_settings")
        return {str(row["node"]): str(row["mode"]) for row in rows.fetchall()}

    def set_mode(self, node: str, mode: str, *, person: str) -> str:
        """
        Make a server follow the fleet's settings, or leave it out of them.

        Args:
            node: The server.
            mode: ``managed`` or ``independent``.
            person: Who decides.

        Returns:
            The mode.

        Raises:
            ValidationError: The mode is not one of the two, or there is no such server.
        """
        if mode not in MODES:
            raise ValidationError.from_text(say("fleet_settings.bad_mode", mode=str(mode)))
        self._require_node(node)
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO fleet_node_settings (node, mode, changed_by, changed_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(node) DO UPDATE SET mode = excluded.mode, "
                "changed_by = excluded.changed_by, changed_at = excluded.changed_at",
                (node, mode, person, utc_now_iso()),
            )
        return mode

    # -- A server that came back --------------------------------------------------

    def request_resync(self, node: str) -> None:
        """
        Note that a server answered after being silent, so its settings are looked at again.

        Written by whichever process saw it return (the console's or the monitor's); read by
        the console's watcher (:mod:`noust.web.fleet_settings_actions`).

        Args:
            node: The server.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO fleet_node_settings (node, resync_requested_at) VALUES (?, ?) "
                "ON CONFLICT(node) DO UPDATE SET resync_requested_at = COALESCE("
                "fleet_node_settings.resync_requested_at, excluded.resync_requested_at)",
                (node, utc_now_iso()),
            )

    def pending_resync(self) -> builtins.list[str]:
        """Returns: The servers waiting to be looked at, in order of name."""
        rows = self.store._get_connection().execute(
            "SELECT node FROM fleet_node_settings WHERE resync_requested_at IS NOT NULL "
            "ORDER BY node"
        )
        return [str(row["node"]) for row in rows.fetchall()]

    def clear_resync(self, node: str) -> None:
        """
        Args:
            node: The server whose settings were looked at.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE fleet_node_settings SET resync_requested_at = NULL WHERE node = ?",
                (node,),
            )

    # -- Guards -------------------------------------------------------------------

    @staticmethod
    def _require(kind: SettingKind, held: Collection[str]) -> None:
        """
        Args:
            kind: The kind being set.
            held: What the person holds.

        Raises:
            NoustPermissionError: They lack the permission the node's own route asks.
        """
        if kind.permission not in held:
            raise NoustPermissionError.from_text(
                say(
                    "fleet_settings.permission_missing",
                    kind=kind.key,
                    permission=kind.permission,
                )
            )

    def _require_node(self, node: str) -> None:
        """
        Args:
            node: A server's name.

        Raises:
            ValidationError: When this central has no such server.
        """
        if self.store.get_node(node) is None:
            raise ValidationError.from_text(
                say("fleet_settings.unknown_node", node=node, command="noust node list"),
                field="scope",
            )


def _secret_name(version: int) -> str:
    """
    Args:
        version: A row's version.

    Returns:
        The secret store's name for that row's credentials.
    """
    return f"fleet-settings/v{version}"
