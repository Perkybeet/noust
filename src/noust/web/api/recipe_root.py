# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Installing a recipe that is root on the server, through the API.

A recipe that says ``root_equivalent`` (Portainer controls the Docker daemon) is installed only after
somebody confirms it, and through the API that somebody is an account with the server's permission
in sudo mode, as for a package installed as root. The confirmation is the record the compose guard
reads, made in the account's name before the job is queued, so the job that plans the recipe finds
it and the deployment that follows is not refused (``noust.recipes.deploy``). This module is the one
place the API asks for all of that, for ``POST /api/apps`` and ``POST /api/recipes/{name}/install``
alike.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from noust.core.store import get_store
from noust.recipes import Recipe
from noust.recipes.deploy import record_root_equivalent, require_root_equivalent_confirmed
from noust.web.api.deps import actor_label, ensure_elevated
from noust.web.api.scope import require_instance_scope
from noust.web.permissions import Permission
from noust.web.permissions.enforce import check_permission


def confirm_root_equivalent(
    recipe: Recipe, domain: str, accepted: bool, request: Request, session: dict[str, Any]
) -> None:
    """
    Check and record the confirmation of a recipe that is root on the server.

    Args:
        recipe: The recipe being installed; nothing happens for one that is not root-equivalent.
        domain: Where it will be installed.
        accepted: ``accept_root_equivalent`` of the request.
        request: The incoming request, for the sudo-mode check.
        session: The authenticated session.

    Raises:
        RecipeError: The recipe is root on the server and the request did not accept it (400, with
            the sentence for the console and the command that confirms).
        HTTPException: The credential lacks the server's permission (403), or has not confirmed
            recently (403 ``elevation_required``).
    """
    if recipe.root_equivalent is None:
        return
    store = get_store()
    require_root_equivalent_confirmed(
        recipe, domain, actor_label(session) if accepted else None, store
    )
    # Root on the server, whoever's application it is for: the instance's permission and sudo mode.
    require_instance_scope(Permission.SERVER_MANAGE)
    check_permission(session, Permission.SERVER_MANAGE)
    ensure_elevated(request, dict(session))
    if accepted:
        record_root_equivalent(recipe, domain, actor_label(session), store)
