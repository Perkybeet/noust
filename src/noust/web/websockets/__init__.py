# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
WebSocket routes for Noust Web Interface.
"""

from noust.web.websockets.docker import router as docker_router
from noust.web.websockets.router import router
from noust.web.websockets.terminal import router as terminal_router

# One router for every stream under /ws; the terminal's and Docker's live in their own modules.
router.include_router(terminal_router)
router.include_router(docker_router)

__all__ = ["router"]
