# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of read replicas (:mod:`noust.web.api.databases.replicas`).

Seeing how the replicas stand, and planning one, are database reads. Making room for a replica opens
an account and a line in the engine's access rules from an address, showing its join code again
hands out the replication password, joining replaces this server's data with a primary's copy, and
promoting or removing one ends a replication: all of them are managing databases, and all of them
ask for sudo mode. Checking a join code is managing too: the code is a secret the caller holds.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/databases/replicas"): Permission.DATABASES_READ,
    ("GET", "/api/databases/replicas/{engine}"): Permission.DATABASES_READ,
    ("POST", "/api/databases/replicas/{engine}/plan"): Permission.DATABASES_READ,
    ("POST", "/api/databases/replicas/{engine}"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/replicas/{engine}/{name}/token"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/replicas/join/plan"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/replicas/join"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/replicas/{engine}/promote"): Permission.DATABASES_MANAGE,
    ("DELETE", "/api/databases/replicas/{engine}/{name}"): Permission.DATABASES_MANAGE,
}
