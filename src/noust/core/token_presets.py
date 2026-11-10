# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The named sets of permissions an API token is narrowed to: "only deploy" for a pipeline (item 163).

A token's scope (read, deploy, admin) is coarse. A deploy token also reads the databases, the
server and the settings, which a CI job that only has to start a deployment has no business
seeing; narrowing it meant naming permissions, and nobody knows them by name. The ones jobs
ask for are named here, once: ``noust token create --preset``, ``POST /api/auth/tokens``
(``preset``) and the console's dialog all take the name, and the console reads the list from
``GET /api/auth/roles`` (``token_presets``) instead of keeping its own copy (rule 3).

A preset is for a job, never for the machine or for who may do what: none holds a permission of
the server, of secrets, of accounts, of security or of the audit trail (a test holds the list to
that). A token still never holds more than whoever issues it
(:meth:`noust.web.auth.TokenManager.create_api_token`); a preset the issuer does not hold all of
is refused whole, not trimmed.
"""

from __future__ import annotations

from dataclasses import dataclass

from noust.core.exceptions import SecurityError
from noust.core.permission_catalog import Permission

P = Permission


@dataclass(frozen=True)
class TokenPreset:
    """
    A set of permissions a token is narrowed to, by name.

    Attributes:
        id: Its stable name, in lower case with underscores: what the command line, the API and
            the console's translations key on. Never renamed once released.
        permissions: What a token made with it holds, ``self`` included, which every credential
            keeps for its own session and tokens.
    """

    id: str
    permissions: frozenset[str]


#: What the jobs people write ask for, narrowest first. ``self`` is in every one of them.
TOKEN_PRESETS: tuple[TokenPreset, ...] = (
    TokenPreset("read_apps", frozenset({P.SELF, P.APPS_READ})),
    TokenPreset("deploy_only", frozenset({P.SELF, P.APPS_READ, P.APPS_DEPLOY})),
    TokenPreset(
        "deploy_and_restart", frozenset({P.SELF, P.APPS_READ, P.APPS_OPERATE, P.APPS_DEPLOY})
    ),
    TokenPreset("backups", frozenset({P.SELF, P.BACKUPS_READ, P.BACKUPS_RUN})),
)

#: The names, in the order above, for a command's choices and a message.
PRESET_IDS: tuple[str, ...] = tuple(preset.id for preset in TOKEN_PRESETS)


def preset_permissions(preset_id: str) -> frozenset[str]:
    """
    The permissions a preset names.

    Args:
        preset_id: One of :data:`PRESET_IDS`.

    Returns:
        What a token made with it holds.

    Raises:
        SecurityError: When no preset has that name, with the ones there are.
    """
    for preset in TOKEN_PRESETS:
        if preset.id == preset_id:
            return preset.permissions
    raise SecurityError(
        f"Unknown token preset: {preset_id!r}",
        details=f"Use one of: {', '.join(PRESET_IDS)}.",
    )
