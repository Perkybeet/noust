# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/server/software`` (:mod:`noust.web.api.server.software`).

**Reading** what is installed and what a removal would do is ``server.read``.

**Installing** keeps the permission each function has today, so a fleet loses nothing: cloudflared,
the WAF, fail2ban, the clock and the automatic updates are ``server.manage``; Noust DNS's Knot and
the stream module (published ports change nginx and the firewall) are ``server.host_access``; a
database engine is ``databases.manage``; and a package no function of the console installed until
now is ``server.host_access``, which is documented as "change the host itself: SSH, firewall,
packages". The route is mapped to ``server.manage``, the least any of them takes, and the handler
asks for the component's own on top, because only the path says which component it is.

**Removing** is ``server.host_access`` and sudo mode, whatever the component, so a central removes
software on a node only when the node gave it access to the system; an engine also needs
``databases.manage``, which is what removing one has always taken. There is no second person by
default (a compliance framework may ask for one: ``core/accounts/approvals.py``).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/server/software"): Permission.SERVER_READ,
    ("GET", "/api/server/software/{component}/impact"): Permission.SERVER_READ,
    ("POST", "/api/server/software/{component}/install"): Permission.SERVER_MANAGE,
    ("POST", "/api/server/software/{component}/remove"): Permission.SERVER_HOST_ACCESS,
}

#: What the handler asks for installing a component, on top of the route's.
INSTALL_PERMISSIONS: dict[str, str] = {
    "cloudflared": Permission.SERVER_MANAGE,
    "waf": Permission.SERVER_MANAGE,
    "fail2ban": Permission.SERVER_MANAGE,
    "chrony": Permission.SERVER_MANAGE,
    "auto_updates": Permission.SERVER_MANAGE,
    "knot": Permission.SERVER_HOST_ACCESS,
    "nginx_stream_module": Permission.SERVER_HOST_ACCESS,
    "postgresql": Permission.DATABASES_MANAGE,
    "mysql": Permission.DATABASES_MANAGE,
    "redis": Permission.DATABASES_MANAGE,
    "mongodb": Permission.DATABASES_MANAGE,
}

#: What installing any other component takes: it changes the host's packages.
DEFAULT_INSTALL_PERMISSION: str = Permission.SERVER_HOST_ACCESS

#: What the handler asks for removing a component besides the route's ``server.host_access``.
REMOVE_PERMISSIONS: dict[str, str] = {
    "postgresql": Permission.DATABASES_MANAGE,
    "mysql": Permission.DATABASES_MANAGE,
    "redis": Permission.DATABASES_MANAGE,
    "mongodb": Permission.DATABASES_MANAGE,
}
