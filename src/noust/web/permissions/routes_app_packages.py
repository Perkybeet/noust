# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's system packages (:mod:`noust.web.api.app_packages`).

Reading what is declared and what the host has is ``apps.read``. Writing the list, and installing
it, makes root run the package manager: that is as root-equivalent as writing the deploy hooks,
so it is ``root.equivalent`` (a second person where the server asks for one) and sudo mode, and
no team's role ever holds it.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/packages"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/packages"): Permission.ROOT_EQUIVALENT,
    ("POST", "/api/apps/{domain}/packages/install"): Permission.ROOT_EQUIVALENT,
}
