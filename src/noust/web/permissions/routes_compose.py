# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/compose`` (:mod:`noust.web.api.compose`).

Looking at a document runs nothing and writes nothing, but it is the first step
of creating an application, so it asks for what creating one asks for.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/compose/analyze"): Permission.APPS_MANAGE,
    ("POST", "/api/compose/import"): Permission.APPS_MANAGE,
}
