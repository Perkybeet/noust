# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/databases/imports`` (:mod:`noust.web.api.databases.imports`).

Loading a dump runs its SQL as the engine's superuser, as restoring one does,
so everything here needs ``databases.manage``: the upload (it fills a disk with
up to ``databases.import_max_bytes`` of someone's data), reading what a file on
the server is, and the import itself, which also asks for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/databases/imports/uploads"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/imports/inspect"): Permission.DATABASES_MANAGE,
    ("GET", "/api/databases/imports/uploads/{upload_id}"): Permission.DATABASES_MANAGE,
    ("PUT", "/api/databases/imports/uploads/{upload_id}"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/imports/uploads/{upload_id}/complete"): Permission.DATABASES_MANAGE,
    ("DELETE", "/api/databases/imports/uploads/{upload_id}"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/imports"): Permission.DATABASES_MANAGE,
}
