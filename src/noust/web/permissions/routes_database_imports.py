# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/databases/imports`` (:mod:`noust.web.api.databases.imports`).

Loading a dump runs its SQL, as restoring one does, as the database's owner or, when the
operator asks for it, the engine's superuser, so everything here needs ``databases.manage``: the
upload (it fills a disk with up to ``databases.import_max_bytes`` of someone's data), reading what a
file on the server is, the plan of a dump of all databases (it reads a whole file), and the imports
themselves, which also ask for sudo mode.
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
    # 4.0 (H-18): every database of a pg_dumpall, and its accounts when asked.
    ("POST", "/api/databases/imports/cluster/plan"): Permission.DATABASES_MANAGE,
    ("POST", "/api/databases/imports/cluster"): Permission.DATABASES_MANAGE,
}
