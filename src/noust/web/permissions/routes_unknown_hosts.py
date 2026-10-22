# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of what answers an unknown name (:mod:`noust.web.api.unknown_hosts`).

Looking at it is a read of the server. Changing it replaces what the whole server answers
on ports 80 and 443, which is the web server's own configuration: ``server.manage``, and the
handlers ask for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/unknown-hosts"): Permission.SERVER_READ,
    ("PUT", "/api/unknown-hosts"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/unknown-hosts"): Permission.SERVER_MANAGE,
}
