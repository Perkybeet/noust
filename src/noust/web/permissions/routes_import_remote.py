# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of reading another platform's server (:mod:`noust.web.api.import_remote`).

Both calls make this server contact an address an operator typed, with a
credential, to prepare creating applications: ``apps.manage``, like the import
they lead to, and the handlers also ask for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/import/applications"): Permission.APPS_MANAGE,
    ("POST", "/api/import/proposal"): Permission.APPS_MANAGE,
}
