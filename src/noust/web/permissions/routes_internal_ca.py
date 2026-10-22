# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the server's own certificate authority (:mod:`noust.web.api.internal_ca`).

A root of trust is a decision about every device that installs it, so making it, deleting it and
trusting it on this server are server settings (and ask for sudo mode). What it signs is kept as
the uploaded certificates are, and is managed like them.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/certs/ca"): Permission.APPS_READ,
    ("POST", "/api/certs/ca"): Permission.SETTINGS_MANAGE,
    ("DELETE", "/api/certs/ca"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/certs/ca/root"): Permission.APPS_READ,
    ("GET", "/api/certs/ca/trust"): Permission.APPS_READ,
    ("POST", "/api/certs/ca/trust-here"): Permission.SETTINGS_MANAGE,
    ("DELETE", "/api/certs/ca/trust-here"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/certs/ca/issued"): Permission.APPS_MANAGE,
    ("POST", "/api/certs/ca/issued/{name}/renew"): Permission.APPS_MANAGE,
}
