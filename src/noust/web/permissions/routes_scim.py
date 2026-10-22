# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/scim/v2`` and ``/api/auth/scim``: SCIM provisioning
(:mod:`noust.web.api.scim`, :mod:`noust.web.api.scim_admin`).

``/api/scim/v2`` is public to this layer because its caller is not a session or an API token
but a *source*, authenticated by the bearer token issued for it; the handlers check it once
(``scim_source``) and a wrong one is counted against the address like any guessed credential.
Which sources there are, and their tokens, is the security officer's (``accounts.read`` and
``accounts.manage``, every change in sudo mode).
"""

from __future__ import annotations

from noust.web.permissions import PUBLIC, Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/scim/v2/ServiceProviderConfig"): PUBLIC,
    ("POST", "/api/scim/v2/.search"): PUBLIC,
    ("GET", "/api/scim/v2/ResourceTypes"): PUBLIC,
    ("GET", "/api/scim/v2/ResourceTypes/{name}"): PUBLIC,
    ("GET", "/api/scim/v2/Schemas"): PUBLIC,
    ("GET", "/api/scim/v2/Schemas/{schema_id}"): PUBLIC,
    ("GET", "/api/scim/v2/Users"): PUBLIC,
    ("POST", "/api/scim/v2/Users"): PUBLIC,
    ("POST", "/api/scim/v2/Users/.search"): PUBLIC,
    ("GET", "/api/scim/v2/Users/{user_id}"): PUBLIC,
    ("PUT", "/api/scim/v2/Users/{user_id}"): PUBLIC,
    ("PATCH", "/api/scim/v2/Users/{user_id}"): PUBLIC,
    ("DELETE", "/api/scim/v2/Users/{user_id}"): PUBLIC,
    ("GET", "/api/scim/v2/Groups"): PUBLIC,
    ("POST", "/api/scim/v2/Groups"): PUBLIC,
    ("POST", "/api/scim/v2/Groups/.search"): PUBLIC,
    ("GET", "/api/scim/v2/Groups/{group_id}"): PUBLIC,
    ("PUT", "/api/scim/v2/Groups/{group_id}"): PUBLIC,
    ("PATCH", "/api/scim/v2/Groups/{group_id}"): PUBLIC,
    ("DELETE", "/api/scim/v2/Groups/{group_id}"): PUBLIC,
    ("GET", "/api/auth/scim/sources"): Permission.ACCOUNTS_READ,
    ("PUT", "/api/auth/scim/sources/{name}"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/auth/scim/sources/{name}"): Permission.ACCOUNTS_MANAGE,
    ("GET", "/api/auth/scim/sources/{name}/users"): Permission.ACCOUNTS_READ,
}
