# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server``: managing the machine Noust runs on.

The console's Server page is a client of this router and the ``noust server``
command is another; both call the managers of :mod:`noust.managers.server`, which
are the only implementation of what happens to the machine. An endpoint here
translates HTTP into a manager call, records that somebody asked, and answers.

``/api/services`` is not part of this and stays as it is: the Services page moves
under Server in the console, its API does not move.

The areas are assembled here, each in its own module. The security checks
(``security.py``) belong to another workstream and are included when the module
exists, so adding it needs no edit to this file.
"""

from __future__ import annotations

import importlib
import importlib.util

from fastapi import APIRouter

from noust.web.api.server.common import ServerRoute
from noust.web.api.server.logs import router as logs_router
from noust.web.api.server.overview import router as overview_router
from noust.web.api.server.power import router as power_router
from noust.web.api.server.storage import router as storage_router
from noust.web.api.server.storage import swap_router
from noust.web.api.server.system import router as system_router
from noust.web.api.server.updates import router as updates_router

router = APIRouter(route_class=ServerRoute)

router.include_router(overview_router)
router.include_router(updates_router, prefix="/updates")
router.include_router(power_router, prefix="/power")
router.include_router(storage_router, prefix="/storage")
router.include_router(swap_router, prefix="/swap")
router.include_router(system_router)
router.include_router(logs_router, prefix="/logs")

# Found by name, and imported plainly: an ImportError inside the security
# module is a bug to see, not something to swallow as "not installed".
if importlib.util.find_spec("noust.web.api.server.security") is not None:
    router.include_router(importlib.import_module("noust.web.api.server.security").router)

__all__ = ["router"]
