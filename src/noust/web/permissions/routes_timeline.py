# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/timeline`` (:mod:`noust.web.api.timeline`).

Seeing the server is enough to ask; each source inside keeps the permission its
own page needs, and a source the caller lacks is answered as withheld
(:data:`noust.web.api.timeline.SOURCE_PERMISSIONS`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/timeline"): Permission.SERVER_READ,
}
