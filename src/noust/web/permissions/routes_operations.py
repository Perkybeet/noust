# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permission of ``/api/operations/active`` (:mod:`noust.web.api.operations`).

What is running is read like the jobs that run: ``apps.read``, which every role holds. A principal
that does not see every team is shown only the operations on its own applications, by the handler
(the route is ``scoped``, like ``/api/jobs/active``).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/operations/active"): Permission.APPS_READ,
}
