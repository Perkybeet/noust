# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of Cloudflare Tunnels (:mod:`noust.web.api.tunnels`).

Seeing the tunnels and what they carry is a read of the server. Creating one installs a
service and keeps a token, routing a name through it exposes a service to the internet and
edits DNS, and deleting one stops what answered: managing the server, and the handlers ask
for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/tunnels"): Permission.SERVER_READ,
    ("POST", "/api/tunnels"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/tunnels/{name}"): Permission.SERVER_MANAGE,
    ("POST", "/api/tunnels/{name}/routes"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/tunnels/{name}/routes/{hostname}"): Permission.SERVER_MANAGE,
}
