# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of point-in-time recovery (:mod:`noust.web.api.databases.pitr`).

Reading how it stands, and planning, are database reads. Turning it on or off restarts the
engine, a base backup copies the cluster, and a recovery replaces it: all of them are managing
databases, and every one but the base backup and the sending asks for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/databases/pitr"): Permission.DATABASES_READ,
    ("POST", "/api/databases/pitr/enable/plan"): Permission.DATABASES_READ,
    ("POST", "/api/databases/pitr/enable"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/pitr/disable"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/pitr/base"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/pitr/sync"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/pitr/fetch"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/pitr/restore/plan"): Permission.DATABASES_READ,
    ("POST", "/api/databases/pitr/restore"): Permission.DATABASES_MANAGE,
}
