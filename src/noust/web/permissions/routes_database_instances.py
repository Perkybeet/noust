# Copyright (c) 2024-2026 Yago Lopez Prado
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
}
