# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the engines Noust runs in containers (:mod:`noust.web.api.databases.instances`).

Seeing them, their catalog and what a creation or a removal would do are
database reads. Making one, moving it to a newer image and removing it are
managing databases, and ask for sudo mode: each pulls an image and starts, or
deletes, a container as root, the install of an engine's own kind. Starting and
stopping one is what starting and stopping an engine is: operating.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/databases/instances"): Permission.DATABASES_READ,
    ("GET", "/api/databases/instances/catalog"): Permission.DATABASES_READ,
    ("POST", "/api/databases/instances/plan"): Permission.DATABASES_READ,
    ("POST", "/api/databases/instances"): Permission.DATABASES_MANAGE,
    ("GET", "/api/databases/instances/{name}"): Permission.DATABASES_READ,
    ("POST", "/api/databases/instances/{name}/start"): Permission.APPS_OPERATE,
    ("POST", "/api/databases/instances/{name}/stop"): Permission.APPS_OPERATE,
    ("POST", "/api/databases/instances/{name}/upgrade"): Permission.DATABASES_MANAGE,
    ("GET", "/api/databases/instances/{name}/remove-plan"): Permission.DATABASES_READ,
    ("DELETE", "/api/databases/instances/{name}"): Permission.DATABASES_MANAGE,
    # 4.0 (C-13, C-21): a database moved to another engine, an engine to a newer version.
    ("POST", "/api/databases/moves/plan"): Permission.DATABASES_READ,
    ("POST", "/api/databases/moves"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/instances/migrate/plan"): Permission.DATABASES_READ,
    ("POST", "/api/databases/instances/migrate"): Permission.DATABASES_MANAGE,
    ("GET", "/api/databases/instances/migrations"): Permission.DATABASES_READ,
    ("POST", "/api/databases/instances/migrations/{migration_id}/rollback"): (
        Permission.DATABASES_MANAGE
    ),
    # 4.0 (C-25, C-26): an instance that serves TLS, and a link that verifies it.
    ("GET", "/api/databases/instances/tls-authority"): Permission.DATABASES_READ,
    ("GET", "/api/databases/instances/{name}/tls"): Permission.DATABASES_READ,
    ("POST", "/api/databases/instances/{name}/tls/plan"): Permission.DATABASES_READ,
    ("PUT", "/api/databases/instances/{name}/tls"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/instances/{name}/tls/renew"): Permission.DATABASES_MANAGE,
    # 4.0 (C-30, C-31): what an instance may use, and how it is probed.
    ("GET", "/api/databases/instances/{name}/tuning"): Permission.DATABASES_READ,
    ("POST", "/api/databases/instances/{name}/tuning/plan"): Permission.DATABASES_READ,
    ("PUT", "/api/databases/instances/{name}/tuning"): Permission.DATABASES_MANAGE,
    # 4.0: what the container is started with, beyond what Noust works out (a command or
    # arguments of the operator's own).
    ("GET", "/api/databases/instances/{name}/launch"): Permission.DATABASES_READ,
    ("POST", "/api/databases/instances/{name}/launch/plan"): Permission.DATABASES_READ,
    ("PUT", "/api/databases/instances/{name}/launch"): Permission.DATABASES_MANAGE,
}
