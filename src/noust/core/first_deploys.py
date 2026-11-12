# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a first deployment that failed leaves behind for its domain (item 141).

A first deployment that fails undoes every step it took, the application's own row included
(``DeployMixin.deploy``): a half-made application is worse than none, and the same domain can be
tried again. That leaves the domain's page with nothing to say, and the person who just asked for
the deployment with no explanation. This is the record that does: what was asked for, tied to the
job that failed, whose error and end the store already keeps.

The one reader and writer of ``first_deploys`` (schema v18). The guards sit here and not in the
routes (rule 4): a credential in a source is masked before it is written, and every read and every
dismissal is held to the teams that own the project the deployment was meant for, so a caller
cannot reach another team's failure by forgetting a check.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from noust.core.secret_detection import mask_secret_shapes
from noust.core.teams.ownership import new_row_owner
from noust.core.teams.scope import Scope, current_scope, visible_clause
from noust.core.timeutil import utc_now_iso

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["MAX_AGE_DAYS", "ORIGINS", "FirstDeploy", "FirstDeploys"]

logger = logging.getLogger(__name__)

#: How the application was to start: a repository, an archive or a folder (``source``), an image
#: from a registry (``image``), a Compose document typed in (``compose``) or a recipe.
ORIGINS = ("source", "image", "compose", "recipe")

#: Days a failure stays news. Past it the page of the domain is the plain "not found" again and
#: the list of what needs attention is rid of it; a record twelve times older is deleted.
MAX_AGE_DAYS = 14
_FORGET_AFTER_DAYS = MAX_AGE_DAYS * 12

#: The longest value kept in a text column: an address, never a document.
_MAX_TEXT = 2000


@dataclass(frozen=True)
class FirstDeploy:
    """
    One first deployment that failed.

    Attributes:
        job_id: The deployment's job.
        domain: The domain it was for.
        origin: One of :data:`ORIGINS`.
        source: What to read again (an address, a path, an image), credentials masked; None for
            a recipe or a typed document.
        branch: The branch asked for, when one was.
        app_type: The type asked for, ``auto`` or a name.
        recipe: The recipe's name, for a recipe.
        created_at: When it was asked for (ISO 8601, UTC).
        error: The job's error, verbatim, as the store kept it.
        finished_at: When the job ended, as the store kept it.
    """

    job_id: str
    domain: str
    origin: str
    source: str | None
    branch: str | None
    app_type: str | None
    recipe: str | None
    created_at: str
    error: str | None
    finished_at: str | None


def _text(value: str | None) -> str | None:
    """
    Make a value fit a text column without a credential in it.

    Args:
        value: What the caller gave.

    Returns:
        The value with every credential shape masked and its length bounded, or None for an
        empty one.
    """
    if value is None or value.strip() == "":
        return None
    return mask_secret_shapes(value.strip())[:_MAX_TEXT]


class FirstDeploys:
    """The failed first deployments of every domain, in the store's own connection."""

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

    def record(
        self,
        job_id: str,
        domain: str,
        *,
        origin: str,
        source: str | None = None,
        branch: str | None = None,
        app_type: str | None = None,
        recipe: str | None = None,
    ) -> None:
        """
        Remember what a first deployment was asked to do.

        Written when the deployment is queued, before anything can fail, so a failure always
        finds it. The project and the environment are the ones the deployment is queued for
        (:func:`noust.core.teams.ownership.ownership_target`), else the default.

        Args:
            job_id: The deployment's job.
            domain: The domain it is for.
            origin: One of :data:`ORIGINS`.
            source: What to read again. Credentials are masked here, whatever the caller did.
            branch: The branch asked for.
            app_type: The type asked for.
            recipe: The recipe's name.

        Raises:
            ValueError: ``origin`` is not one of :data:`ORIGINS`.
        """
        if origin not in ORIGINS:
            raise ValueError(f"origin must be one of {', '.join(ORIGINS)}, not {origin!r}")
        now = datetime.now(timezone.utc)
        forget = (now - timedelta(days=_FORGET_AFTER_DAYS)).isoformat()
        with self.store._transaction() as cursor:
            project_id, environment_id = new_row_owner(cursor)
            cursor.execute("DELETE FROM first_deploys WHERE created_at < ?", (forget,))
            cursor.execute(
                "INSERT OR REPLACE INTO first_deploys "
                "(job_id, domain, origin, source, branch, app_type, recipe, "
                "project_id, environment_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    domain,
                    origin,
                    _text(source),
                    _text(branch),
                    _text(app_type),
                    _text(recipe),
                    project_id,
                    environment_id,
                    utc_now_iso("microseconds"),
                ),
            )

    def failed(self, *, scope: Scope | None = None, domain: str | None = None) -> list[FirstDeploy]:
        """
        List the first deployments that failed and still are the last word on their domain.

        An attempt counts while its job failed (a cancelled one is an operator's decision), the
        domain has no application, no later attempt for the same domain exists, it was not
        dismissed and it is not older than :data:`MAX_AGE_DAYS`.

        Args:
            scope: Whom the read is for; the ambient request scope by default.
            domain: Only this domain's.

        Returns:
            The failures, newest first, of the projects the scope sees.
        """
        clause, scope_params = visible_clause(scope or current_scope(), "f.project_id")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
        where = ""
        params: list[object] = [cutoff]
        if domain is not None:
            where = " AND f.domain = ?"
            params.append(domain)
        sql = (
            "SELECT f.job_id, f.domain, f.origin, f.source, f.branch, f.app_type, f.recipe, "  # noqa: S608
            "f.created_at, j.error, j.finished_at "
            "FROM first_deploys f JOIN jobs j ON j.id = f.job_id "
            "WHERE j.status = 'failed' AND f.dismissed_at IS NULL AND f.created_at >= ? "
            "AND NOT EXISTS (SELECT 1 FROM apps a WHERE a.domain = f.domain) "
            "AND f.rowid = (SELECT g.rowid FROM first_deploys g WHERE g.domain = f.domain "
            "ORDER BY g.created_at DESC, g.rowid DESC LIMIT 1)"
            f"{where} AND {clause} ORDER BY f.created_at DESC, f.rowid DESC"
        )
        try:
            with self.store._transaction() as cursor:
                cursor.execute(sql, [*params, *scope_params])
                rows = cursor.fetchall()
        except sqlite3.OperationalError as exc:
            # A store a development build opened before v18 had this table: nothing to report.
            logger.warning("Could not read the failed first deployments: %s", exc)
            return []
        return [
            FirstDeploy(
                job_id=row["job_id"],
                domain=row["domain"],
                origin=row["origin"],
                source=row["source"],
                branch=row["branch"],
                app_type=row["app_type"],
                recipe=row["recipe"],
                created_at=row["created_at"],
                error=row["error"],
                finished_at=row["finished_at"],
            )
            for row in rows
        ]

    def dismiss(self, job_id: str, *, scope: Scope | None = None) -> bool:
        """
        Put a failed attempt away: it is no longer listed.

        Args:
            job_id: The attempt's job.
            scope: Whom the change is for; the ambient request scope by default.

        Returns:
            Whether an attempt of a project the scope sees was dismissed.
        """
        clause, scope_params = visible_clause(scope or current_scope(), "project_id")
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE first_deploys SET dismissed_at = ? "  # noqa: S608 - fixed text
                f"WHERE job_id = ? AND dismissed_at IS NULL AND {clause}",
                [utc_now_iso("microseconds"), job_id, *scope_params],
            )
            return cursor.rowcount > 0
