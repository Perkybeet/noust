# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's pending changes (:mod:`noust.web.api.pending`).

Reading them shows which settings changed, never their values, so it is as
public as reading the application.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/pending"): Permission.APPS_READ,
}
