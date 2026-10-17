# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of deploying with git push (:mod:`noust.web.api.push`).

Reading says which applications deploy this way, which keys may push (public halves) and what
stops it. Preparing the server creates an account that SSH logs in to and changes sshd's rules:
that is the host itself, so it is host access (and sudo mode). Turning push on for an application,
adding a key and removing one are managing the application: whoever holds a key can deploy code
to it, so the handlers also ask for sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/push"): Permission.SERVER_READ,
    ("POST", "/api/push/setup"): Permission.SERVER_HOST_ACCESS,
    ("GET", "/api/apps/{domain}/push"): Permission.APPS_READ,
    ("PUT", "/api/apps/{domain}/push"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/push"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/{domain}/push/refresh"): Permission.APPS_MANAGE,
    ("GET", "/api/apps/{domain}/push/keys"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/push/keys"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/push/keys/{label}"): Permission.APPS_MANAGE,
}
