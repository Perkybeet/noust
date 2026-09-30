# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/cron`` (:mod:`noust.web.api.cron`).

A cron job is a command run as root on a schedule: creating one is
root-equivalent. Running, enabling and disabling one that exists is operating.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/cron"): Permission.APPS_READ,
    ("POST", "/api/cron"): Permission.ROOT_EQUIVALENT,
    ("POST", "/api/cron/preview"): Permission.APPS_READ,
    ("DELETE", "/api/cron/{name}"): Permission.APPS_MANAGE,
    ("POST", "/api/cron/{name}/run"): Permission.APPS_OPERATE,
    ("POST", "/api/cron/{name}/enable"): Permission.APPS_OPERATE,
    ("POST", "/api/cron/{name}/disable"): Permission.APPS_OPERATE,
    ("GET", "/api/cron/{name}/runs"): Permission.APPS_READ,
}
