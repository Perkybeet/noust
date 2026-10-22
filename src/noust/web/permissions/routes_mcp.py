# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the MCP endpoint (:mod:`noust.web.api.mcp`).

The endpoint itself needs only ``self``: any API token may speak to it, and what
a tool then does is the token's, because each call is an ordinary request to
the API that is held to the permission of its own route. Reading how MCP is set
up is ``settings.read``; turning it on is the security officer's
(``security.manage``), in sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/mcp"): Permission.SELF,
    ("GET", "/api/mcp"): Permission.SELF,
    ("DELETE", "/api/mcp"): Permission.SELF,
    ("GET", "/api/mcp/status"): Permission.SETTINGS_READ,
    ("PUT", "/api/mcp/settings"): Permission.SECURITY_MANAGE,
}
