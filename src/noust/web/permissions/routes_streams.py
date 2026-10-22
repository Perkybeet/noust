# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of published TCP and UDP ports (:mod:`noust.web.api.streams`).

Seeing what is published, and planning a port, are reads of the server. Publishing one
and closing it change nginx and the firewall, which is changing the host itself, and the
handlers ask for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/streams"): Permission.SERVER_READ,
    ("POST", "/api/streams/plan"): Permission.SERVER_READ,
    ("POST", "/api/streams"): Permission.SERVER_HOST_ACCESS,
    ("DELETE", "/api/streams/{name}"): Permission.SERVER_HOST_ACCESS,
}
