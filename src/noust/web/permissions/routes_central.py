# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/central`` (:mod:`noust.web.api.central`).

Unlocking a sealed central is custody of its passphrase, a security duty. The check of whether it
can deploy on its machine's Docker asks the engine and starts a small container to prove a folder,
which is what managing Docker is (``docker.manage``, the administrator's). What the central schedules
itself, where there are no timers, is read like the rest of the server (``server.read``).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/central/unlock"): Permission.SECURITY_MANAGE,
    ("POST", "/api/central/container-host/check"): Permission.DOCKER_MANAGE,
    ("GET", "/api/central/schedule"): Permission.SERVER_READ,
}
