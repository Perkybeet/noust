# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of a database made reachable from the network (:mod:`noust.web.api.databases.public`).

Seeing what is public, and planning, are database reads. Opening a database to
the network and closing it again are managing databases, and ask for sudo mode;
showing the account's password again is revealing a secret.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/databases/public"): Permission.DATABASES_READ,
    ("POST", "/api/databases/public/plan"): Permission.DATABASES_READ,
    ("POST", "/api/databases/public"): Permission.DATABASES_MANAGE,
    ("GET", "/api/databases/public/{engine}/{database}"): Permission.DATABASES_READ,
    ("POST", "/api/databases/public/{engine}/{database}/reveal"): Permission.SECRETS_REVEAL,
    ("DELETE", "/api/databases/public/{engine}/{database}"): Permission.DATABASES_MANAGE,
}
