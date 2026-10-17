# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an application lives, for the channels whose route asks.

A route that says "only the ``shop`` project" or "only production"
(:mod:`noust.core.notifications.routing`) needs the application's project,
environment and tags. The routing module is pure and takes them as a
:class:`~noust.core.notifications.routing.Placement`; this is the one place
that reads them from the store, so the answer to "which project is this
application in" is the teams' (:class:`~noust.core.teams.TeamStore`) and not a
second opinion.
"""

from __future__ import annotations

import logging
import sqlite3

from noust.core.exceptions import NoustError
from noust.core.notifications.routing import Placement
from noust.core.store import get_store
from noust.core.teams.repository import TeamStore

logger = logging.getLogger(__name__)


def store_placement(domain: str) -> Placement | None:
    """
    Read an application's project, environment and tags.

    Args:
        domain: The application's domain.

    Returns:
        Where it lives, or None when the application is not known or the store
        cannot be read (a route that asks about projects then does not match:
        what cannot be proven is not delivered, and the log says why).
    """
    try:
        store = get_store()
        app = store.get_app(domain)
        if app is None:
            return None
        team, project, environment = (
            TeamStore(store).owner_labels().of(app.project_id, app.environment_id)
        )
    except (NoustError, sqlite3.Error, OSError) as exc:
        logger.warning("Could not tell where %s lives to route a notification: %s", domain, exc)
        return None
    # Tags belong to the application record from the release that adds them;
    # until then an application has none, and a route naming tags matches nothing.
    tags = frozenset(str(tag) for tag in (getattr(app, "tags", None) or ()))
    return Placement(team=team, project=project, environment=environment, tags=tags)
