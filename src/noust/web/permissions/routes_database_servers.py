# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the database servers (:mod:`noust.web.api.databases.servers`).

Seeing where databases run is a database read, like the engines' list it joins with the
instances'. Choosing which server new databases of an engine are made on decides where every
team's next database goes: managing databases.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/databases/servers"): Permission.DATABASES_READ,
    ("GET", "/api/databases/servers/{key}"): Permission.DATABASES_READ,
    ("PUT", "/api/databases/servers/{key}/default"): Permission.DATABASES_MANAGE,
    ("DELETE", "/api/databases/servers/{key}/default"): Permission.DATABASES_MANAGE,
    # 4.1: a database an engine holds and Noust does not manage, added to Noust.
    ("POST", "/api/databases/databases/{engine}/{name}/adopt"): Permission.DATABASES_MANAGE,
}
