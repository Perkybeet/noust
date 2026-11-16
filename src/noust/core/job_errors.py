# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sentence that ended a job, kept with its code (item 151).

A job keeps the error that ended it as one line of English (``jobs.error``): the message, the word
"Details:" and the fix, glued by ``str(exc)``. That line is what every client always read, and it
cannot be put into another language. The same failure is kept here as the sentence of the catalog it
was (:mod:`noust.core.server_text`) and the failing program's own words apart, so that whoever looks
at the job, later and in their language, reads it in their language and not in the one of whoever
launched it.

A side table (``job_errors``, schema v18) and not a column of ``jobs``: a store that a 4.3 console
migrated is still read by the 3.3 one it can be taken back to, and that one builds a ``JobRecord``
from every column of the row it finds. The row goes with the job (``ON DELETE CASCADE``).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from noust.core.server_text import Param, ServerText, say

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["JobErrors"]

logger = logging.getLogger(__name__)


class JobErrors:
    """The coded failures of the jobs the store keeps."""

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

    def save(self, job_id: str, sentence: ServerText, output: str | None) -> None:
        """
        Keep how a job failed.

        Args:
            job_id: The job, already in the store.
            sentence: The sentence that ended it.
            output: The failing program's own words, already scrubbed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO job_errors (job_id, code, params, output) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET code = excluded.code, "
                "params = excluded.params, output = excluded.output",
                (job_id, sentence.code, json.dumps(dict(sentence.params)), output or None),
            )

    def get(self, job_id: str) -> tuple[ServerText | None, str | None]:
        """
        Args:
            job_id: A job.

        Returns:
            The sentence that ended it and the failing program's own words; ``(None, None)`` for a
            job that failed before the code was kept, one whose error was not a sentence of the
            catalog, or a row this version cannot read (a code that no longer exists).
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT code, params, output FROM job_errors WHERE job_id = ?", (job_id,)
            )
            row = cursor.fetchone()
        if row is None:
            return None, None
        try:
            params: Any = json.loads(row["params"])
            values: dict[str, Param] = {str(k): v for k, v in params.items()}
            return say(row["code"], **values), row["output"]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.debug("The failure of job %s cannot be read back: %s", job_id, exc)
            return None, None
