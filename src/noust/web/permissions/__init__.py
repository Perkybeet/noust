# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Permissions: what each route needs, and what each role holds.

Three pieces, each stated once:

- :class:`Permission` names every permission. The names are an interface other
  areas of the product and a central's forwarded requests rely on; add new
  ones, never rename one.
- :mod:`noust.web.permissions.roles` says which permissions each role, and each
  kind of credential that is not an account, holds.
- One ``routes_<area>.py`` per area of the API maps every ``(METHOD, path
  template)`` to the permission it needs. :mod:`noust.web.permissions.registry`
  collects them; ``require_auth`` enforces the result on every request, and the
  OpenAPI schema publishes it as ``x-noust-permission``. A route missing from
  the maps fails ``tests/test_permission_routes.py`` and is refused at runtime.

This module, ``roles`` and the route maps are plain data; the framework only
comes in with :mod:`noust.web.permissions.enforce`, which the credential
checks in :mod:`noust.web.auth` call.
"""

from __future__ import annotations

from noust.core.permission_catalog import ALL_PERMISSIONS, DESCRIPTIONS, PUBLIC, Permission

__all__ = ["ALL_PERMISSIONS", "DESCRIPTIONS", "PUBLIC", "Permission"]
