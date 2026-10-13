# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of an application's declaration (:mod:`noust.web.api.app_apply`).

Writing it out names the application's variables, hooks and schedules, as the export does, so it
needs the same permission. Applying one can set a build block, hooks, tasks and Compose
arguments, which run commands as the application or on the machine: root-equivalent, and the
handler also asks for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/declaration"): Permission.SECRETS_REVEAL,
    ("POST", "/api/apps/{domain}/apply"): Permission.ROOT_EQUIVALENT,
}
