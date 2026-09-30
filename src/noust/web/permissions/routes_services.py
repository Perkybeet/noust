# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/services`` (:mod:`noust.web.api.services`).

A unit file is root: writing one, or creating one, runs any command as root at
the next start. Reading one can reveal the secrets a simple-mode service
inlines as ``Environment=``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/services"): Permission.SERVER_READ,
    ("POST", "/api/services/verify"): Permission.ROOT_EQUIVALENT,
    ("GET", "/api/services/{name}"): Permission.SERVER_READ,
    ("POST", "/api/services/{name}/start"): Permission.APPS_OPERATE,
    ("POST", "/api/services/{name}/stop"): Permission.APPS_OPERATE,
    ("POST", "/api/services/{name}/restart"): Permission.APPS_OPERATE,
    ("POST", "/api/services/{name}/enable"): Permission.APPS_OPERATE,
    ("POST", "/api/services/{name}/disable"): Permission.APPS_OPERATE,
    ("GET", "/api/services/{name}/logs"): Permission.SERVER_READ,
    ("GET", "/api/services/{name}/config"): Permission.SECRETS_REVEAL,
    ("PUT", "/api/services/{name}/config"): Permission.ROOT_EQUIVALENT,
    ("POST", "/api/services"): Permission.ROOT_EQUIVALENT,
    ("DELETE", "/api/services/{name}"): Permission.SERVER_MANAGE,
}
