# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of shared variables and of the secret managers they can name
(:mod:`noust.web.api.variables`, :mod:`noust.web.api.secret_providers`).

A team's, a project's and an environment's variables are configuration of the
applications that refer to them, so they need what an application's own
environment needs: ``apps.read`` to read (secrets masked), ``apps.manage`` to
change, in the team they belong to. The server's own, and the secret managers
whose tokens reach into other systems, are the server's: ``settings.*``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

_TEAM = "/api/teams/{team}/variables"
_PROJECT = "/api/teams/{team}/projects/{project}/variables"
_ENVIRONMENT = "/api/teams/{team}/projects/{project}/environments/{environment}/variables"

ROUTES: dict[tuple[str, str], str] = {
    ("GET", _TEAM): Permission.APPS_READ,
    ("POST", _TEAM): Permission.APPS_MANAGE,
    ("PUT", _TEAM + "/{name}"): Permission.APPS_MANAGE,
    ("DELETE", _TEAM + "/{name}"): Permission.APPS_MANAGE,
    ("GET", _TEAM + "/{name}/usage"): Permission.APPS_READ,
    ("GET", _PROJECT): Permission.APPS_READ,
    ("POST", _PROJECT): Permission.APPS_MANAGE,
    ("PUT", _PROJECT + "/{name}"): Permission.APPS_MANAGE,
    ("DELETE", _PROJECT + "/{name}"): Permission.APPS_MANAGE,
    ("GET", _PROJECT + "/{name}/usage"): Permission.APPS_READ,
    ("GET", _ENVIRONMENT): Permission.APPS_READ,
    ("POST", _ENVIRONMENT): Permission.APPS_MANAGE,
    ("PUT", _ENVIRONMENT + "/{name}"): Permission.APPS_MANAGE,
    ("DELETE", _ENVIRONMENT + "/{name}"): Permission.APPS_MANAGE,
    ("GET", _ENVIRONMENT + "/{name}/usage"): Permission.APPS_READ,
    ("GET", "/api/variables/server"): Permission.SETTINGS_READ,
    ("POST", "/api/variables/server"): Permission.SETTINGS_MANAGE,
    ("PUT", "/api/variables/server/{name}"): Permission.SETTINGS_MANAGE,
    ("DELETE", "/api/variables/server/{name}"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/variables/server/{name}/usage"): Permission.SETTINGS_READ,
    ("GET", "/api/secret-providers"): Permission.SETTINGS_READ,
    ("GET", "/api/secret-providers/kinds"): Permission.SETTINGS_READ,
    ("PUT", "/api/secret-providers/{name}"): Permission.SETTINGS_MANAGE,
    ("DELETE", "/api/secret-providers/{name}"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/secret-providers/{name}/test"): Permission.SETTINGS_MANAGE,
}
