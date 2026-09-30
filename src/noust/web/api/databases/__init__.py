# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database API endpoints: one module per area, one service behind all of them.

Every router here is a client of
:class:`~noust.managers.database.service.DatabaseService`, the same service
``noust db`` calls: an endpoint translates HTTP into a service call and back,
and quoting, privilege whitelists, ownership guards, the store and the runner
all live below it.

- :mod:`.engines`: install, start, stop, status, capabilities, support.
- :mod:`.databases`: list, create, describe, drop, adopt, fix-owner.
- :mod:`.users`: accounts, access profiles, password rotation and reveal.
- :mod:`.dumps`: dumps and restores, as jobs, with safety copies.
- :mod:`.backups`: policies, verification, offsite copies, downloads and remote
  restores.
- :mod:`.exposure`: where engines listen, open ports, Connect instructions.
- :mod:`.query`: the SQL console (history, saved queries, EXPLAIN, export)
  and the connection-string helper.
- :mod:`.browse`: the data explorer, the row editor and the Redis key browser.
- :mod:`.metrics`: engine and database metrics, slow queries.
- :mod:`.links`: ``/api/apps/{domain}/databases``, mounted under ``/apps``
  as :data:`apps_router`.

Literal paths must come before a parametrised
one they share a prefix with (``/users/grant`` before ``/users/{engine}``);
each module keeps its own in that order, and the areas below never share one.
"""

from __future__ import annotations

from fastapi import APIRouter

from noust.web.api.databases import (
    backups,
    browse,
    databases,
    dumps,
    engines,
    exposure,
    links,
    metrics,
    query,
    users,
)
from noust.web.api.databases.common import ActionResponse, DatabaseInfoResponse
from noust.web.api.databases.dumps import BackupInfoResponse, BackupListResponse
from noust.web.api.databases.query import QueryRequest, QueryResponse
from noust.web.api.deps import NoustErrorRoute

#: The routers mounted under ``/api/databases``, in order.
AREAS = (
    engines.router,
    databases.router,
    users.router,
    dumps.router,
    backups.router,
    exposure.router,
    query.router,
    browse.router,
    metrics.router,
)

router = APIRouter(route_class=NoustErrorRoute)
for _area in AREAS:
    router.include_router(_area)

#: ``/{domain}/databases``, mounted under ``/api/apps``.
apps_router = links.router

__all__ = [
    "AREAS",
    "ActionResponse",
    "BackupInfoResponse",
    "BackupListResponse",
    "DatabaseInfoResponse",
    "QueryRequest",
    "QueryResponse",
    "apps_router",
    "router",
]
