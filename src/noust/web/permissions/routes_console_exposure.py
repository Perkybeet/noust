# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/console/exposure`` (:mod:`noust.web.api.console_exposure`).

Reading whether the console is published is ``server.read``. Everything that
changes how it is reached, a preview of that included (it resolves names and
reads the accounts), is ``server.host_access``: a central holds it on a node
only where the node allowed host access, so a central cannot open a node's
console to the internet on its own.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/console/exposure"): Permission.SERVER_READ,
    ("POST", "/api/console/exposure/preview"): Permission.SERVER_HOST_ACCESS,
    ("POST", "/api/console/exposure"): Permission.SERVER_HOST_ACCESS,
    ("DELETE", "/api/console/exposure"): Permission.SERVER_HOST_ACCESS,
}
