# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/probes`` (:mod:`noust.web.api.probes`).

Probes are what the monitor asks, so they follow the monitor's own permissions:
``server.read`` to see them, ``server.manage`` to change them. Putting the status
page on a name over TLS changes how the host is reached, like publishing the
console, so it is ``server.host_access``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/probes"): Permission.SERVER_READ,
    ("GET", "/api/probes/status-page"): Permission.SERVER_READ,
    ("PUT", "/api/probes/status-page"): Permission.SERVER_MANAGE,
    ("POST", "/api/probes/status-page/write"): Permission.SERVER_MANAGE,
    ("POST", "/api/probes/status-page/publish"): Permission.SERVER_HOST_ACCESS,
    ("DELETE", "/api/probes/status-page/site"): Permission.SERVER_HOST_ACCESS,
    ("GET", "/api/probes/{name}/history"): Permission.SERVER_READ,
    ("PUT", "/api/probes/{name}"): Permission.SERVER_MANAGE,
    ("POST", "/api/probes/{name}/check"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/probes/{name}"): Permission.SERVER_MANAGE,
}
