# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permission of the manifest preview (:mod:`noust.web.api.manifest`).

A preview reads, as the caller, what a plan needs of the server; each read is a
request to a route that holds the caller to its own permission and team. The
preview itself therefore needs only to read applications.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/manifest/plan"): Permission.APPS_READ,
}
