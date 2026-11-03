# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The panels of the console's Home, one set per account (item 113).

The one reader and writer of ``account_dashboards`` (schema v17). An account keeps up to 20 panels,
each a name, whether it follows the server in the selector or the whole fleet, a place in the list
and a layout: the closed document :mod:`noust.validators.dashboards` judges before anything is
written, so what is stored is exactly what was judged and nothing the person typed that was not.

The guards sit here and not in the route (rule 4): the limit of 20 is the condition of the
statement that inserts a panel, so two requests at once cannot both take the last place, and every
read and write names the account, so no caller can reach another account's panels by forgetting a
check. Which account is asking is the caller's to say; the API answers 400 to a credential that is
no account, as it does for the active team.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import ValidationError
from noust.validators import dashboards

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["AccountDashboards", "Dashboard"]

logger = logging.getLogger(__name__)

#: Whole statements, never built from pieces: the columns a panel is read with, and which rows.
_SELECT_ALL = (
    "SELECT id, name, scope, position, layout, created_at, updated_at "
    "FROM account_dashboards WHERE account_id = ? ORDER BY position, created_at, id"
)
_SELECT_ONE = (
    "SELECT id, name, scope, position, layout, created_at, updated_at "
    "FROM account_dashboards WHERE account_id = ? AND id = ?"
)


@dataclass(frozen=True)
class Dashboard:
    """
    One panel of an account.

    Attributes:
        id: The id the person gave it, unique among the account's.
        name: What it is called.
        scope: ``server`` (it follows the server chosen in the selector) or ``fleet``.
        position: Its place among the account's panels, from 0.
        layout: The document, version 1, as :func:`noust.validators.dashboards.check_layout`
            returns it.
        created_at: When it was first saved, UNIX seconds.
        updated_at: When it was last saved, UNIX seconds.
    """

    id: str
    name: str
    scope: str
    position: int
    layout: dict[str, Any]
    created_at: float
    updated_at: float


def _read(row: sqlite3.Row) -> Dashboard:
    try:
        layout = json.loads(row["layout"])
    except ValueError:
        # Only this module writes the column, so this is a store edited by hand. One panel that
        # cannot be read must not take the list of the others with it.
        logger.warning("The layout of panel %r is not JSON; showing it empty", row["id"])
        layout = {"version": dashboards.LAYOUT_VERSION, "widgets": []}
    return Dashboard(
        id=row["id"],
        name=row["name"],
        scope=row["scope"],
        position=int(row["position"]),
        layout=layout,
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


class AccountDashboards:
    """The panels of every account, in the store's own connection."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
        """
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    def for_account(self, account_id: int) -> list[Dashboard]:
        """
        List an account's panels.

        Args:
            account_id: The account.

        Returns:
            Its panels in the order it keeps them; empty when it never saved one.
        """
        with self.store._transaction() as cursor:
            cursor.execute(_SELECT_ALL, (account_id,))
            return [_read(row) for row in cursor.fetchall()]

    def get(self, account_id: int, dashboard_id: str) -> Dashboard | None:
        """
        Find one of an account's panels.

        Args:
            account_id: The account.
            dashboard_id: The panel's id.

        Returns:
            The panel, or None when the account has none by that id.
        """
        with self.store._transaction() as cursor:
            cursor.execute(_SELECT_ONE, (account_id, dashboard_id))
            row = cursor.fetchone()
        return _read(row) if row is not None else None

    def save(
        self, account_id: int, dashboard_id: str, *, name: object, scope: object, layout: object
    ) -> Dashboard:
        """
        Create a panel, or replace what an account has under an id.

        A panel that is new goes last; one that exists keeps its place and its birth.

        Args:
            account_id: The account.
            dashboard_id: The panel's id, the person's own.
            name: What it is called.
            scope: ``server`` or ``fleet``.
            layout: The document, as the client sent it.

        Returns:
            The panel as it is now.

        Raises:
            ValidationError: Something is not valid (``field`` names it: ``dashboard``, ``name``,
                ``scope`` or ``layout.widgets[2].w``), or the account already keeps 20 panels and
                this one is new. Nothing is written.
        """
        key = dashboards.check_dashboard_id(dashboard_id)
        label = dashboards.check_dashboard_name(name)
        follows = dashboards.check_dashboard_scope(scope)
        text = dashboards.layout_text(dashboards.check_layout(layout))
        now = time.time()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE account_dashboards SET name = ?, scope = ?, layout = ?, updated_at = ? "
                "WHERE account_id = ? AND id = ?",
                (label, follows, text, now, account_id, key),
            )
            if cursor.rowcount == 0:
                # The limit is the statement's own condition: counted and inserted together.
                cursor.execute(
                    "INSERT INTO account_dashboards "
                    "(account_id, id, name, scope, position, layout, created_at, updated_at) "
                    "SELECT ?, ?, ?, ?, COALESCE(MAX(position) + 1, 0), ?, ?, ? "
                    "FROM account_dashboards WHERE account_id = ? HAVING COUNT(*) < ?",
                    (
                        account_id,
                        key,
                        label,
                        follows,
                        text,
                        now,
                        now,
                        account_id,
                        dashboards.MAX_DASHBOARDS,
                    ),
                )
                if cursor.rowcount == 0:
                    raise ValidationError(
                        f"An account keeps at most {dashboards.MAX_DASHBOARDS} panels",
                        details="Delete a panel to make room for this one.",
                        field="dashboard",
                    )
            cursor.execute(_SELECT_ONE, (account_id, key))
            return _read(cursor.fetchone())

    def delete(self, account_id: int, dashboard_id: str) -> bool:
        """
        Delete one of an account's panels.

        The others keep their order.

        Args:
            account_id: The account.
            dashboard_id: The panel's id.

        Returns:
            Whether the account had one by that id.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM account_dashboards WHERE account_id = ? AND id = ?",
                (account_id, dashboard_id),
            )
            return cursor.rowcount > 0

    def reorder(self, account_id: int, order: Sequence[str]) -> list[Dashboard]:
        """
        Put an account's panels in the order given.

        Args:
            account_id: The account.
            order: The id of every panel the account has, each once, in the order wanted.

        Returns:
            The panels in their new order.

        Raises:
            ValidationError: ``field`` is ``order``: an id is not valid, repeats, is not a panel
                of the account, or a panel of the account is left out. Nothing is changed.
        """
        ids: list[str] = []
        for item in order:
            try:
                ids.append(dashboards.check_dashboard_id(item))
            except ValidationError as error:
                raise ValidationError(error.message, error.details, field="order") from error
        with self.store._transaction() as cursor:
            cursor.execute("SELECT id FROM account_dashboards WHERE account_id = ?", (account_id,))
            have = {row["id"] for row in cursor.fetchall()}
            if len(set(ids)) != len(ids) or set(ids) != have:
                unknown = sorted(set(ids) - have)
                left_out = sorted(have - set(ids))
                raise ValidationError(
                    "The order must name every panel of the account once",
                    details=(
                        "; ".join(
                            part
                            for part in (
                                f"Not a panel: {', '.join(unknown)}" if unknown else "",
                                f"Left out: {', '.join(left_out)}" if left_out else "",
                                "Some are repeated" if len(set(ids)) != len(ids) else "",
                            )
                            if part
                        )
                        or "List them all."
                    ),
                    field="order",
                )
            for position, dashboard_id in enumerate(ids):
                cursor.execute(
                    "UPDATE account_dashboards SET position = ? WHERE account_id = ? AND id = ?",
                    (position, account_id, dashboard_id),
                )
        return self.for_account(account_id)
