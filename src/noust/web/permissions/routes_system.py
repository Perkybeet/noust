# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/system`` (:mod:`noust.web.api.system`).

Process command lines, which can carry secrets, are left out of the listing
for anyone without ``secrets.reveal`` by the handler itself.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/system"): Permission.SERVER_READ,
    ("GET", "/api/system/machine"): Permission.SERVER_READ,
    ("GET", "/api/system/cpu"): Permission.SERVER_READ,
    ("GET", "/api/system/memory"): Permission.SERVER_READ,
    ("GET", "/api/system/disks"): Permission.SERVER_READ,
    ("GET", "/api/system/processes"): Permission.SERVER_READ,
    ("GET", "/api/system/network"): Permission.SERVER_READ,
    ("GET", "/api/system/version"): Permission.SERVER_READ,
    ("GET", "/api/system/health"): Permission.SERVER_READ,
    # Noust updating itself: installing a package as root, like applying the
    # operating system's updates (sudo mode is asked on top).
    ("GET", "/api/system/update"): Permission.SERVER_READ,
    ("POST", "/api/system/update"): Permission.SERVER_MANAGE,
}
