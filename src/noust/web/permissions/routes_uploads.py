# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of uploading the code of an application (:mod:`noust.web.api.source_uploads`).

An upload only stores a size-capped, owner-only, swept archive: what makes it code that runs is
using it as a source, and that is creating or changing an application, which has its own
permission and sudo mode. So sending the archive is ``apps.manage`` without sudo mode, as the
database dumps are.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/uploads"): Permission.APPS_MANAGE,
    ("POST", "/api/uploads/dockerfile"): Permission.APPS_MANAGE,
    ("GET", "/api/uploads/{upload_id}"): Permission.APPS_MANAGE,
    ("PUT", "/api/uploads/{upload_id}"): Permission.APPS_MANAGE,
    ("POST", "/api/uploads/{upload_id}/complete"): Permission.APPS_MANAGE,
    ("DELETE", "/api/uploads/{upload_id}"): Permission.APPS_MANAGE,
}
