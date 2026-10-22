# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The configuration an application was last put to work with, as the store keeps it.

One row per application in ``app_applied_config`` (``schema_v14.RUNTIME_SQL``):
a JSON document with one section per area of configuration, taken when a
deployment succeeds and refreshed for the areas a restart applies. What differs
between it and the configuration now is the application's pending changes
(:mod:`noust.deployers.pending_changes`, which knows what the sections are).

Values are never kept, only their hashes (the caller makes them): the row says
that something changed, never to what. A table of its own so the full-row write
of a redeploy cannot overwrite it, and removed with the application. This is the
only module that reads or writes the table (rule 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class AppliedConfig:
    """
    What an application's configuration was when it was last put to work.

    Attributes:
        sections: Area name to what that area held.
        deployment_id: The deployment that took it, when one did.
        taken_at: When the document was last written, as an ISO timestamp.
    """

    sections: dict[str, Any]
    deployment_id: int | None
    taken_at: str


def get(domain: str, *, store: NoustStore | None = None) -> AppliedConfig | None:
    """
    Read what an application's configuration was when it was last put to work.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The document, or None when none was ever taken.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT c.document, c.deployment_id, c.taken_at FROM app_applied_config c "
            "JOIN apps a ON a.id = c.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    try:
        sections = json.loads(row[0])
    except ValueError:
        return None
    if not isinstance(sections, dict):
        return None
    return AppliedConfig(sections=sections, deployment_id=row[1], taken_at=row[2])


def put(
    domain: str,
    sections: dict[str, Any],
    *,
    deployment_id: int | None,
    keep_deployment: bool = False,
    store: NoustStore | None = None,
) -> bool:
    """
    Store the configuration an application was just put to work with.

    Args:
        domain: The application's domain.
        sections: Area name to what that area holds; replaces the document.
        deployment_id: The deployment that took it, when one did.
        keep_deployment: Leave the recorded deployment as it is (a restart
            refreshes some areas; the deployment is still the last one).
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.
    """
    target = store or get_store()
    document = json.dumps(sections, sort_keys=True)
    now = datetime.now().isoformat()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        found = cursor.fetchone()
        if found is None:
            return False
        if keep_deployment:
            cursor.execute(
                "INSERT INTO app_applied_config (app_id, deployment_id, document, taken_at) "
                "VALUES (?, NULL, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
                "document = excluded.document, taken_at = excluded.taken_at",
                (found[0], document, now),
            )
        else:
            cursor.execute(
                "INSERT INTO app_applied_config (app_id, deployment_id, document, taken_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
                "deployment_id = excluded.deployment_id, document = excluded.document, "
                "taken_at = excluded.taken_at",
                (found[0], deployment_id, document, now),
            )
    return True
