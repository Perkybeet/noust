"""
WebSocket routes for Noust Web Interface.
"""

from noust.web.websockets.router import router
from noust.web.websockets.terminal import router as terminal_router

# One router for every stream under /ws; the terminal's lives in its own module.
router.include_router(terminal_router)

__all__ = ["router"]
