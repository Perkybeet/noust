# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the first deployments that failed (:mod:`noust.web.api.first_deploys`).

Reading one is reading the application it was meant to be; putting one away is as much as having
asked for the deployment, which needs ``apps.manage``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/first-deploys"): Permission.APPS_READ,
    ("POST", "/api/first-deploys/{attempt}/dismiss"): Permission.APPS_MANAGE,
}
