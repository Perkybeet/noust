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
- :mod:`.host_tls`: the certificate of a name that PostgreSQL, MySQL and MariaDB of the server present.
- :mod:`.databases`: list, create, describe, drop, adopt, fix-owner.
- :mod:`.extensions`: the PostgreSQL extensions of a database.
- :mod:`.users`: accounts, access profiles, password rotation and reveal.
- :mod:`.dumps`: dumps and restores, as jobs, with safety copies.
- :mod:`.backups`: policies, verification, offsite copies, downloads and remote
  restores.
- :mod:`.exposure`: where engines listen, open ports, Connect instructions.
- :mod:`.public`: a database made reachable from the network on purpose, and private again.
- :mod:`.imports`: a dump from another provider: chunked upload, what it is,
  and the import.
- :mod:`.instances`: engines Noust runs itself, in containers, beside the host's:
  make, start, stop, upgrade and remove them.
- :mod:`.migrations`: moving a database to another engine, and an engine to a newer
  major version, with a way back.
- :mod:`.tls`: an instance that serves TLS with a certificate of Noust's own authority.
- :mod:`.tuning`: what an instance may use (memory, CPUs, swap) and how it is probed.
- :mod:`.limits`: what an engine on the host may use (memory, CPUs, swap, tasks), proved.
- :mod:`.query`: the SQL console (history, saved queries, EXPLAIN, export)
  and the connection-string helper.
- :mod:`.browse`: the data explorer, the row editor and the Redis key browser.
- :mod:`.metrics`: engine and database metrics, slow queries.
- :mod:`.pitr`: recover PostgreSQL to an instant: the log archived, base backups, the recovery.
- :mod:`.replicas`: read replicas of PostgreSQL, MySQL and MariaDB: make room, join, watch, promote, remove.
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
    extensions,
    host_tls,
    imports,
    instances,
    limits,
    links,
    metrics,
    migrations,
    pitr,
    public,
    query,
    replicas,
    tls,
    tuning,
    users,
)
from noust.web.api.databases.common import ActionResponse, DatabaseInfoResponse
from noust.web.api.databases.dumps import BackupInfoResponse, BackupListResponse
from noust.web.api.databases.query import QueryRequest, QueryResponse
from noust.web.api.deps import NoustErrorRoute

#: The routers mounted under ``/api/databases``, in order.
AREAS = (
    engines.router,
    host_tls.router,
    databases.router,
    extensions.router,
    users.router,
    dumps.router,
    backups.router,
    exposure.router,
    imports.router,
    # Literal paths before the parametrised ones they share a prefix with.
    migrations.router,
    tls.router,
    tuning.router,
    limits.router,
    instances.router,
    query.router,
    browse.router,
    metrics.router,
    public.router,
    pitr.router,
    replicas.router,
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
