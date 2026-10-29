# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server started for an event, which the central follows to the end: the ``source_follows`` table.

A server that took a push or a pull request queues an update or builds a preview. The commit's
status on the code host and the pull request's comment are the central's to write (the App is its
and the server has no use for it), so the central keeps a row for each such thing and looks at it
on the server until it ends: a deployment (a job of the server, that ends in a release or in a
failure) or a preview (a record of the server that goes from queued to ready or failed). What the
row remembers is only what is needed to write the next status without repeating the last one:
the id GitHub gave the deployment, the id of the comment, and what was last said.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from noust.core.store import NoustStore, get_store

__all__ = ["KINDS", "Follow", "Follows"]

#: What is followed.
KINDS = ("deployment", "preview")

#: Rows kept after they ended, in seconds.
KEEP_FOR = 14 * 86400


@dataclass(frozen=True)
class Follow:
    """
    One deployment or preview the central follows.

    Attributes:
        id: Its number.
        forward_id: The delivery that started it.
        node: The server it is on.
        domain: The application deployed, or the one previewed.
        kind: ``deployment`` or ``preview``.
        repository: ``owner/repo`` it is of.
        installation_id: The installation that writes to the repository; None when no
            installation of the App reaches it.
        ref: The commit (or the branch or the tag) deployed.
        environment: ``production`` or ``preview``.
        environment_url: Where the application answers.
        job_id: The server's job (a deployment).
        number: The pull request (a preview).
        action: What happened to the pull request: ``opened``, ``updated`` or ``closed``.
        provider_ref: The id the code host gave the deployment.
        comment_ref: The id of the pull request's comment.
        last_state: What was last said to the code host.
        state: ``active``, ``done`` or ``lost``.
        detail: Why it was lost, or how it ended.
        created_at: When it began.
        updated_at: When it was last looked at.
        finished_at: When it ended.
    """

    id: int
    forward_id: str
    node: str
    domain: str
    kind: str
    repository: str
    installation_id: int | None
    ref: str | None
    environment: str
    environment_url: str | None
    job_id: str | None
    number: int | None
    action: str | None
    provider_ref: int | None
    comment_ref: str | None
    last_state: str | None
    state: str
    detail: str | None
    created_at: float
    updated_at: float
    finished_at: float | None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The follow as the API shows it.
        """
        return {
            "id": self.id,
            "forward": self.forward_id,
            "node": self.node,
            "domain": self.domain,
            "kind": self.kind,
            "repository": self.repository,
            "ref": self.ref,
            "environment": self.environment,
            "job_id": self.job_id,
            "number": self.number,
            "action": self.action,
            "last_state": self.last_state,
            "state": self.state,
            "detail": self.detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }


def _follow(row: Any) -> Follow:
    """
    Args:
        row: A ``source_follows`` row.

    Returns:
        The follow.
    """
    return Follow(
        id=int(row["id"]),
        forward_id=str(row["forward_id"]),
        node=str(row["node"]),
        domain=str(row["domain"]),
        kind=str(row["kind"]),
        repository=str(row["repo"]),
        installation_id=row["installation_id"],
        ref=row["ref"],
        environment=str(row["environment"]),
        environment_url=row["environment_url"],
        job_id=row["job_id"],
        number=row["number"],
        action=row["action"],
        provider_ref=row["provider_ref"],
        comment_ref=row["comment_ref"],
        last_state=row["last_state"],
        state=str(row["state"]),
        detail=row["detail"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        finished_at=row["finished_at"],
    )


class Follows:
    """
    The deployments and previews followed, read and written in one place.

    Args:
        store: The central's store; the process-wide one by default.
        clock: Epoch seconds (tests move it).
    """

    def __init__(
        self, store: NoustStore | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self._store = store
        self._clock = clock

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def get(self, follow_id: int) -> Follow | None:
        """
        Args:
            follow_id: A follow's number.

        Returns:
            The follow, or None.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM source_follows WHERE id = ?", (follow_id,))
            .fetchone()
        )
        return _follow(row) if row else None

    def deployment(
        self,
        forward_id: str,
        node: str,
        domain: str,
        job_id: str,
        *,
        repository: str,
        installation_id: int | None,
        ref: str | None,
        environment_url: str | None,
    ) -> Follow:
        """
        Begin following an update a server queued.

        Args:
            forward_id: The delivery that started it.
            node: The server.
            domain: The application.
            job_id: The server's job.
            repository: ``owner/repo``.
            installation_id: The installation that writes to it.
            ref: The commit, branch or tag.
            environment_url: Where the application answers.

        Returns:
            The follow, active.
        """
        return self._insert(
            forward_id,
            node,
            domain,
            "deployment",
            repository,
            installation_id,
            ref,
            "production",
            environment_url,
            job_id,
            None,
            None,
        )

    def preview(
        self,
        forward_id: str,
        node: str,
        parent: str,
        number: int,
        *,
        repository: str,
        installation_id: int | None,
        ref: str | None,
        action: str,
    ) -> Follow:
        """
        Begin following the preview of a pull request, or take up again the one that was.

        A pull request has one comment, edited as its preview changes: a push to it follows
        the same row, keeping the comment's id.

        Args:
            forward_id: The delivery that started it.
            node: The server.
            parent: The application previewed.
            number: The pull request.
            repository: ``owner/repo``.
            installation_id: The installation that writes to it.
            ref: The commit.
            action: ``opened``, ``updated`` or ``closed``.

        Returns:
            The follow, active.
        """
        now = self._clock()
        row = (
            self.store._get_connection()
            .execute(
                "SELECT id FROM source_follows WHERE kind = 'preview' AND node = ? AND domain = ? "
                "AND number = ? AND repo = ? ORDER BY id DESC LIMIT 1",
                (node, parent, number, repository),
            )
            .fetchone()
        )
        if row is None:
            return self._insert(
                forward_id,
                node,
                parent,
                "preview",
                repository,
                installation_id,
                ref,
                "preview",
                None,
                None,
                number,
                action,
            )
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE source_follows SET forward_id = ?, installation_id = ?, ref = ?, "
                "action = ?, state = 'active', detail = NULL, last_state = NULL, updated_at = ?, "
                "finished_at = NULL WHERE id = ?",
                (forward_id, installation_id, ref, action, now, row["id"]),
            )
        found = self.get(int(row["id"]))
        if found is None:
            raise RuntimeError("a follow that was just updated is not there")
        return found

    def _insert(
        self,
        forward_id: str,
        node: str,
        domain: str,
        kind: str,
        repository: str,
        installation_id: int | None,
        ref: str | None,
        environment: str,
        environment_url: str | None,
        job_id: str | None,
        number: int | None,
        action: str | None,
    ) -> Follow:
        """
        Args:
            forward_id: The delivery.
            node: The server.
            domain: The application.
            kind: What is followed.
            repository: ``owner/repo``.
            installation_id: The installation.
            ref: The commit, branch or tag.
            environment: ``production`` or ``preview``.
            environment_url: Where it answers.
            job_id: The server's job.
            number: The pull request.
            action: What happened to the pull request.

        Returns:
            The new follow, active.
        """
        now = self._clock()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO source_follows (forward_id, node, domain, kind, repo, installation_id, "
                "ref, environment, environment_url, job_id, number, action, state, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    forward_id,
                    node,
                    domain,
                    kind,
                    repository,
                    installation_id,
                    ref,
                    environment,
                    environment_url,
                    job_id,
                    number,
                    action,
                    now,
                    now,
                ),
            )
            number_id = int(cursor.lastrowid or 0)
        found = self.get(number_id)
        if found is None:
            raise RuntimeError("a follow that was just written is not there")
        return found

    def of(self, forward_id: str) -> list[Follow]:
        """
        Args:
            forward_id: A delivery.

        Returns:
            What servers started for it, in the order they began.
        """
        rows = self.store._get_connection().execute(
            "SELECT * FROM source_follows WHERE forward_id = ? ORDER BY id", (forward_id,)
        )
        return [_follow(row) for row in rows.fetchall()]

    def active(self) -> list[Follow]:
        """
        Returns:
            The follows still to be looked at, oldest first.
        """
        rows = self.store._get_connection().execute(
            "SELECT * FROM source_follows WHERE state = 'active' ORDER BY id"
        )
        return [_follow(row) for row in rows.fetchall()]

    def list(self, *, node: str | None = None, limit: int = 100) -> list[Follow]:
        """
        Args:
            node: Only this server's.
            limit: Most rows.

        Returns:
            The follows, newest first.
        """
        rows = self.store._get_connection().execute(
            "SELECT * FROM source_follows WHERE (? IS NULL OR node = ?) ORDER BY id DESC LIMIT ?",
            (node, node, max(1, min(limit, 1000))),
        )
        return [_follow(row) for row in rows.fetchall()]

    def remember(self, follow_id: int, **values: Any) -> None:
        """
        Remember what was said to the code host, so the next look does not say it again.

        Args:
            follow_id: The follow.
            **values: ``provider_ref``, ``comment_ref`` and ``last_state``.
        """
        allowed = {"provider_ref", "comment_ref", "last_state"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"a follow does not remember {', '.join(sorted(unknown))}")
        if not values:
            return
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE source_follows SET provider_ref = COALESCE(?, provider_ref), "
                "comment_ref = COALESCE(?, comment_ref), last_state = COALESCE(?, last_state), "
                "updated_at = ? WHERE id = ?",
                (
                    values.get("provider_ref"),
                    values.get("comment_ref"),
                    values.get("last_state"),
                    self._clock(),
                    follow_id,
                ),
            )

    def finish(self, follow_id: int, state: str, detail: str | None = None) -> None:
        """
        End a follow.

        Args:
            follow_id: The follow.
            state: ``done``, or ``lost`` when the central could not see it through.
            detail: How it ended.
        """
        now = self._clock()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE source_follows SET state = ?, detail = ?, updated_at = ?, finished_at = ? "
                "WHERE id = ?",
                (state, detail, now, now, follow_id),
            )

    def forget(self, node: str) -> int:
        """
        Forget everything followed on a server, which left the fleet.

        Args:
            node: The server.

        Returns:
            How many were forgotten.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM source_follows WHERE node = ?", (node,))
            return int(cursor.rowcount)

    def purge(self) -> int:
        """
        Forget the follows that ended more than :data:`KEEP_FOR` seconds ago.

        Returns:
            How many were forgotten.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM source_follows WHERE state != 'active' AND updated_at < ?",
                (self._clock() - KEEP_FOR,),
            )
            return int(cursor.rowcount)
