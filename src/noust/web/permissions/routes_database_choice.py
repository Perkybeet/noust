# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of choosing an engine and a version for a new database.

Which engines and versions a database can be created on is what the install
catalog already shows to anyone who may read databases. Creating the database
with ``install: true`` also asks, in the handler, for ``databases.manage`` and
sudo mode, because it installs packages: the install's own permissions.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/databases/provisioning/choices"): Permission.DATABASES_READ,
}
