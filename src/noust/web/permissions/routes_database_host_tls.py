# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of a host engine's certificate (:mod:`noust.web.api.databases.host_tls`).

Seeing what an engine is given and planning it are database reads. Giving an engine a certificate,
taking it off and refreshing the copy after a renewal reload or restart the engine and change what it
presents to every client: all are managing databases, and all ask for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/databases/engines/tls"): Permission.DATABASES_READ,
    ("GET", "/api/databases/engines/{engine}/tls"): Permission.DATABASES_READ,
    ("POST", "/api/databases/engines/{engine}/tls/plan"): Permission.DATABASES_READ,
    ("PUT", "/api/databases/engines/{engine}/tls"): Permission.DATABASES_MANAGE,
    ("DELETE", "/api/databases/engines/{engine}/tls"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/engines/tls/refresh"): Permission.DATABASES_MANAGE,
}
