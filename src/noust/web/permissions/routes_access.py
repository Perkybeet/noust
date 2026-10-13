# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of custom roles and role bindings (:mod:`noust.web.api.access`).

Reading the catalogue of roles is ``teams.read``, which every role holds: a
team's owner needs it to offer a role. Defining a role and giving a role on the
whole server are the security role's decision (``accounts.manage``), and reading
every binding of the server is ``accounts.read``. What happens inside one team
- who holds what on its projects and environments - is the team's own
(``teams.read`` and ``teams.manage``, held in that team by its owner).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/access/roles"): Permission.TEAMS_READ,
    ("POST", "/api/access/roles"): Permission.ACCOUNTS_MANAGE,
    ("GET", "/api/access/roles/{role}"): Permission.TEAMS_READ,
    ("PATCH", "/api/access/roles/{role}"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/access/roles/{role}"): Permission.ACCOUNTS_MANAGE,
    ("GET", "/api/access/bindings"): Permission.ACCOUNTS_READ,
    ("POST", "/api/access/bindings"): Permission.ACCOUNTS_MANAGE,
    ("DELETE", "/api/access/bindings/{binding_id}"): Permission.ACCOUNTS_MANAGE,
    ("GET", "/api/teams/{team}/access"): Permission.TEAMS_READ,
    ("POST", "/api/teams/{team}/access"): Permission.TEAMS_MANAGE,
    ("DELETE", "/api/teams/{team}/access/{binding_id}"): Permission.TEAMS_MANAGE,
}
