# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of what the console serves outside ``/api``
(:mod:`noust.web.server`, :mod:`noust.web.events`).

The static shell and ``/health`` carry no data; the event stream carries the
same application states ``apps.read`` shows.
"""

from __future__ import annotations

from noust.web.permissions import PUBLIC, Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/health"): PUBLIC,
    ("GET", "/{path}"): PUBLIC,
    ("GET", "/events"): Permission.APPS_READ,
}
