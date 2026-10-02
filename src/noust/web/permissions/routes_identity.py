# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's own system account (:mod:`noust.web.api.identity`).

Moving an application to its own account changes who its processes run as
and who owns its files: root-equivalent, and the handler also asks for sudo
mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/identity"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/identity/migrate"): Permission.ROOT_EQUIVALENT,
}
