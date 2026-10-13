# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one reader and writer of shared variables and of what the store knows about
an application's own variables.

Two tables of ``schema_v14.ENVIRONMENTS_SQL``:

- ``shared_variables``: a variable of a scope, used by reference.
- ``app_env_variables``: per variable of an application, the expression its
  value is made from, which part of a deployment sees it, and (for a build-only
  variable, which is in no ``.env``) its value.

They are tables of their own, not columns of ``apps``, so the full-row write of
a redeploy cannot put back what an operator changed while it ran.
"""

from __future__ import annotations

import builtins
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store
from noust.core.variables.model import (
    BOTH,
    MAX_VARIABLES,
    PREVIEW,
    SCOPES,
    SERVER,
    SharedVariable,
    validate_audience,
    validate_scope,
    validate_shared_value,
)
from noust.validators.environment import validate_env_name

_COLUMNS = (
    "scope, owner, name, value, secret, audience, created_at, created_by, updated_at, updated_by"
)

#: The owner column each scope fills; the others stay NULL (the table checks it).
_OWNER_COLUMN = {
    "team": "team_id",
    "project": "project_id",
    "environment": "environment_id",
    "preview": "app_id",
}


def _now() -> str:
    """
    Returns:
        The current time as UTC ISO 8601, seconds.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class AppVariable:
    """
    What the store knows of one variable of an application.

    Attributes:
        name: The variable's name.
        audience: ``both``, ``build`` or ``runtime``.
        expression: The text the value is made from when it holds references;
            None for a literal.
        build_value: The value of a build-only variable.
    """

    name: str
    audience: str = BOTH
    expression: str | None = None
    build_value: str | None = None


def _variable(row: tuple[object, ...]) -> SharedVariable:
    """
    Args:
        row: The columns :data:`_COLUMNS` selects.

    Returns:
        The variable.
    """
    secret = row[4]
    return SharedVariable(
        scope=str(row[0]),
        owner=int(str(row[1])),
        name=str(row[2]),
        value=str(row[3]),
        secret=None if secret is None else bool(secret),
        audience=str(row[5]),
        created_at=str(row[6]),
        created_by=None if row[7] is None else str(row[7]),
        updated_at=str(row[8]),
        updated_by=None if row[9] is None else str(row[9]),
    )


class VariableStore:
    """
    Shared variables and the per-variable data of applications.

    Args:
        store: The store; the process-wide one when None.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store this reads and writes."""
        return self._store or get_store()

    # Shared variables -------------------------------------------------------

    def list(self, scope: str, owner: int = 0) -> builtins.list[SharedVariable]:
        """
        List the variables of one scope.

        Args:
            scope: One of :data:`~noust.core.variables.model.SCOPES`.
            owner: The id of the team, project, environment or application; 0
                for the server.

        Returns:
            Its variables, by name.
        """
        validate_scope(scope)
        with self.store._transaction() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM shared_variables WHERE scope = ? AND owner = ? "  # noqa: S608
                "ORDER BY name",
                (scope, owner),
            )
            return [_variable(tuple(row)) for row in cursor.fetchall()]

    def get(self, scope: str, owner: int, name: str) -> SharedVariable | None:
        """
        Read one variable.

        Args:
            scope: The scope.
            owner: Its owner's id (0 for the server).
            name: The variable's name.

        Returns:
            The variable, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM shared_variables "  # noqa: S608
                "WHERE scope = ? AND owner = ? AND name = ?",
                (scope, owner, name),
            )
            row = cursor.fetchone()
        return _variable(tuple(row)) if row is not None else None

    def set(
        self,
        scope: str,
        owner: int,
        name: str,
        value: object,
        *,
        secret: bool | None = None,
        audience: str = BOTH,
        actor: str | None = None,
    ) -> SharedVariable:
        """
        Create or change one variable.

        Args:
            scope: The scope.
            owner: Its owner's id (0 for the server).
            name: The variable's name.
            value: Its value; references to other scopes are allowed.
            secret: The operator's mark, or None for the classifier's verdict.
            audience: ``both``, ``build`` or ``runtime``.
            actor: Who did it, for the record.

        Returns:
            The variable as stored.

        Raises:
            ValidationError: A name or value that cannot be an environment
                variable, an unknown scope or audience, an owner that does not
                exist, or a scope already holding :data:`MAX_VARIABLES`.
        """
        self._check_owner_shape(scope, owner)
        validate_env_name(name)
        text = validate_shared_value(value, name=name)
        validate_audience(audience)
        stamp = _now()
        column = _OWNER_COLUMN.get(scope)
        with self.store._transaction() as cursor:
            if self._count(cursor, scope, owner) >= MAX_VARIABLES and not self._exists(
                cursor, scope, owner, name
            ):
                raise ValidationError(
                    f"A scope holds at most {MAX_VARIABLES} variables",
                    details="Remove the ones nothing uses, or keep them in the applications.",
                    field="name",
                )
            if column is not None and not self._owner_exists(cursor, scope, owner):
                raise ValidationError(
                    f"No {scope} has the id {owner}",
                    details="The scope this variable belongs to is gone.",
                    field="scope",
                )
            columns = ["scope", "owner", "name", "value", "secret", "audience"]
            values: list[object] = [
                scope,
                owner,
                name,
                text,
                None if secret is None else int(bool(secret)),
                audience,
            ]
            if column is not None:
                columns.append(column)
                values.append(owner)
            columns += ["created_at", "created_by", "updated_at", "updated_by"]
            values += [stamp, actor, stamp, actor]
            cursor.execute(
                f"INSERT INTO shared_variables ({', '.join(columns)}) "  # noqa: S608
                f"VALUES ({', '.join('?' for _ in columns)}) "
                "ON CONFLICT(scope, owner, name) DO UPDATE SET value = excluded.value, "
                "secret = excluded.secret, audience = excluded.audience, "
                "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                values,
            )
        stored = self.get(scope, owner, name)
        if stored is None:  # a rehearsal rolled the write back; answer what it would have been
            return SharedVariable(
                scope, owner, name, text, secret, audience, stamp, actor, stamp, actor
            )
        return stored

    def delete(self, scope: str, owner: int, name: str) -> bool:
        """
        Remove one variable.

        Args:
            scope: The scope.
            owner: Its owner's id (0 for the server).
            name: The variable's name.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM shared_variables WHERE scope = ? AND owner = ? AND name = ?",
                (scope, owner, name),
            )
            return cursor.rowcount > 0

    def replace(
        self,
        scope: str,
        owner: int,
        variables: Mapping[str, Mapping[str, object]],
        *,
        actor: str | None = None,
    ) -> builtins.list[SharedVariable]:
        """
        Make a scope hold exactly these variables.

        Everything is checked before anything is written: one bad name leaves
        the scope as it was.

        Args:
            scope: The scope.
            owner: Its owner's id (0 for the server).
            variables: Name to ``{"value": ..., "secret": ..., "audience": ...}``.
            actor: Who did it.

        Returns:
            The scope's variables afterwards.

        Raises:
            ValidationError: As :meth:`set`, for any of them.
        """
        self._check_owner_shape(scope, owner)
        if len(variables) > MAX_VARIABLES:
            raise ValidationError(
                f"A scope holds at most {MAX_VARIABLES} variables", field="variables"
            )
        checked: dict[str, tuple[str, bool | None, str]] = {}
        for name, entry in variables.items():
            validate_env_name(name)
            secret = entry.get("secret")
            checked[name] = (
                validate_shared_value(entry.get("value", ""), name=name),
                None if secret is None else bool(secret),
                validate_audience(str(entry.get("audience") or BOTH)),
            )
        for name in {variable.name for variable in self.list(scope, owner)} - checked.keys():
            self.delete(scope, owner, name)
        for name, (text, secret, audience) in checked.items():
            self.set(scope, owner, name, text, secret=secret, audience=audience, actor=actor)
        return self.list(scope, owner)

    def usage_count(self, scope: str, owner: int) -> int:
        """
        Args:
            scope: The scope.
            owner: Its owner's id (0 for the server).

        Returns:
            How many variables it holds.
        """
        with self.store._transaction() as cursor:
            return self._count(cursor, scope, owner)

    def referencing_apps(self, scope: str, owner: int, name: str) -> builtins.list[str]:
        """
        Find the applications whose variables name a shared variable.

        Args:
            scope: ``server``, ``team``, ``project`` or ``environment``.
            owner: Its owner's id (0 for the server).
            name: The variable's name.

        Returns:
            The domains of the applications, in the owner's scope, with an
            expression that mentions ``{{scope.name}}``; sorted.
        """
        needle = f"%{{{{{scope}.{name}}}}}%"
        sql = (
            "SELECT DISTINCT a.domain FROM app_env_variables v JOIN apps a ON a.id = v.app_id "
            "WHERE v.expression LIKE ? "
        )
        params: list[object] = [needle]
        if scope == "environment":
            sql += "AND a.environment_id = ? "
            params.append(owner)
        elif scope == "project":
            sql += "AND a.project_id = ? "
            params.append(owner)
        elif scope == "team":
            sql += "AND a.project_id IN (SELECT id FROM projects WHERE team_id = ?) "
            params.append(owner)
        sql += "ORDER BY a.domain"
        with self.store._transaction() as cursor:
            cursor.execute(sql, params)
            return [str(row[0]) for row in cursor.fetchall()]

    # What an application's variables carry -----------------------------------

    def app_variables(self, app_id: int) -> dict[str, AppVariable]:
        """
        Args:
            app_id: The application's id.

        Returns:
            What the store knows of its variables, by name. A variable that
            is a plain literal for both audiences has no row.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT name, audience, expression, build_value FROM app_env_variables "
                "WHERE app_id = ? ORDER BY name",
                (app_id,),
            )
            return {
                str(row[0]): AppVariable(
                    str(row[0]),
                    str(row[1]),
                    None if row[2] is None else str(row[2]),
                    None if row[3] is None else str(row[3]),
                )
                for row in cursor.fetchall()
            }

    def set_app_variables(self, app_id: int, variables: Iterable[AppVariable]) -> None:
        """
        Record what the store knows of an application's variables, completely.

        The rows given are the application's rows: any other it had is removed,
        so a variable that became a plain literal, or left the environment,
        leaves nothing behind. A variable that is a literal for both audiences
        needs no row, and is not given one.

        Args:
            app_id: The application's id.
            variables: The variables to record.
        """
        rows = list(variables)
        for variable in rows:
            validate_audience(variable.audience)
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM app_env_variables WHERE app_id = ?", (app_id,))
            for variable in rows:
                if (
                    variable.audience == BOTH
                    and variable.expression is None
                    and variable.build_value is None
                ):
                    continue
                cursor.execute(
                    "INSERT INTO app_env_variables (app_id, name, audience, expression, "
                    "build_value) VALUES (?, ?, ?, ?, ?)",
                    (
                        app_id,
                        variable.name,
                        variable.audience,
                        variable.expression,
                        variable.build_value,
                    ),
                )

    def copy_app_variables(self, source_id: int, target_id: int) -> int:
        """
        Give an application the variable data of another (a clone).

        Args:
            source_id: The application copied from.
            target_id: The application copied to; its own rows are replaced.

        Returns:
            How many rows were copied.
        """
        rows = list(self.app_variables(source_id).values())
        self.set_app_variables(target_id, rows)
        return len(rows)

    # Internals ---------------------------------------------------------------

    @staticmethod
    def _check_owner_shape(scope: str, owner: int) -> None:
        """
        Args:
            scope: The scope.
            owner: The owner's id.

        Raises:
            ValidationError: An unknown scope, or an owner id that does not fit
                it (the server has 0 and nothing else has).
        """
        validate_scope(scope)
        if (scope == SERVER) != (owner == 0):
            raise ValidationError(
                f"A {scope} variable has {'no owner id' if scope == SERVER else 'an owner'}",
                details="Variables of the server have owner 0; the others name the team, "
                "project, environment or application they belong to.",
                field="scope",
            )

    @staticmethod
    def _count(cursor: sqlite3.Cursor, scope: str, owner: int) -> int:
        cursor.execute(
            "SELECT COUNT(*) FROM shared_variables WHERE scope = ? AND owner = ?", (scope, owner)
        )
        return int(cursor.fetchone()[0])

    @staticmethod
    def _exists(cursor: sqlite3.Cursor, scope: str, owner: int, name: str) -> bool:
        cursor.execute(
            "SELECT 1 FROM shared_variables WHERE scope = ? AND owner = ? AND name = ?",
            (scope, owner, name),
        )
        return cursor.fetchone() is not None

    @staticmethod
    def _owner_exists(cursor: sqlite3.Cursor, scope: str, owner: int) -> bool:
        table = {
            "team": "teams",
            "project": "projects",
            "environment": "environments",
            PREVIEW: "apps",
        }[scope]
        cursor.execute(f"SELECT 1 FROM {table} WHERE id = ?", (owner,))  # noqa: S608
        return cursor.fetchone() is not None


__all__ = ["SCOPES", "AppVariable", "VariableStore"]
