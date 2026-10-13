# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How an application runs and is operated (3.4): one router for the paths under
``/api/apps/{domain}`` that the runtime area adds.

Each part is its own module with its own router and permission map; this only
puts them under the one prefix the console's router mounts, so the area touches
the shared router in one place.
"""

from __future__ import annotations

from fastapi import APIRouter

from noust.web.api.app_files import router as files_router
from noust.web.api.deps import NoustErrorRoute
from noust.web.api.hardening import router as hardening_router
from noust.web.api.mounts import router as mounts_router
from noust.web.api.pending import router as pending_router
from noust.web.api.processes import router as processes_router
from noust.web.api.replicas import router as replicas_router

router = APIRouter(route_class=NoustErrorRoute)
router.include_router(files_router)
router.include_router(hardening_router)
router.include_router(mounts_router)
router.include_router(pending_router)
router.include_router(processes_router)
router.include_router(replicas_router)
