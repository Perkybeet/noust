# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_bound_tokens.py`` that other tests build on."""

from __future__ import annotations

from noust.web.permissions.access import AccessManager
from noust.web.server import get_token_manager


def issue(
    username: str,
    *,
    team: str | None = None,
    project: str | None = None,
    environment: str | None = None,
    scope: str = "admin",
    permissions: list[str] | None = None,
) -> dict[str, str]:
    """
    Issue a token for an account, bound to a scope, and return its header.

    Args:
        username: The owner.
        team: The team to bind it to.
        project: The project.
        environment: The environment.
        scope: The token's scope.
        permissions: What to narrow it to.

    Returns:
        The Authorization header.
    """
    manager = get_token_manager()
    account = manager.accounts.find(username)
    assert account is not None
    bound = AccessManager().bound_scope(team=team, project=project, environment=environment)
    issued = manager.create_api_token(
        f"{username}-{team}-{project}-{environment}-{scope}",
        scope,
        None,
        owner=account,
        permissions=permissions,
        allow_elevated=True,
        bound=bound,
    )
    return {"Authorization": f"Bearer {issued['token']}"}
