# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the manifest preview and apply (:mod:`noust.web.api.manifest`).

A preview reads, as the caller, what a plan needs of the server; each read is a
request to a route that holds the caller to its own permission and team. The
preview itself therefore needs only to read applications.

An apply changes things, and every change it makes is again a request to the
route that owns it, held to its permission, its team and its sudo mode. What the
apply route asks for itself is the floor: to configure applications at all
(``apps.manage``), with sudo mode, since it is several root-equivalent changes
in one call.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/manifest/plan"): Permission.APPS_READ,
    ("POST", "/api/manifest/apply"): Permission.APPS_MANAGE,
}
