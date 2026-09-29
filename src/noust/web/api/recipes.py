# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Recipes: the applications Noust deploys from a declarative file (2.3).

A client of :mod:`noust.recipes`, like ``noust recipe``: this only lists and
describes them. Deploying one is ``POST /api/apps`` with ``recipe``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from noust.recipes import RecipeNotFoundError, get_recipe, list_recipes
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)


class RecipeSummaryOut(BaseModel):
    """
    One recipe in the catalogue.

    Attributes:
        name: What ``recipe`` takes in ``POST /api/apps``.
        title: What it is called.
        description: One sentence.
        homepage: The project's site.
        available: Whether this release can deploy it.
        unavailable_reason: Why not, when it cannot.
        app_type: The deployer that builds it; None when unavailable.
        database: The database engine it is given, if any.
        requires: What the server needs, in words.
    """

    name: str
    title: str
    description: str
    homepage: str
    available: bool
    unavailable_reason: str | None = None
    app_type: str | None = None
    database: str | None = None
    requires: list[str] = []


class RecipeListOut(BaseModel):
    """The catalogue, the recipes this release can deploy first."""

    items: list[RecipeSummaryOut]


class RecipeSourceOut(BaseModel):
    """
    Where a recipe's code comes from.

    Attributes:
        kind: ``git``, ``archive`` or ``template``.
        url: The repository or archive.
        ref: The git tag deployed.
        sha256: The archive's pinned checksum.
        checksum_url: Where the archive's checksum is published.
        files: For a template, the files rendered from the recipe.
    """

    kind: str
    url: str | None = None
    ref: str | None = None
    sha256: str | None = None
    checksum_url: str | None = None
    files: list[str] = []


class RecipeEnvOut(BaseModel):
    """
    One variable a recipe sets.

    Attributes:
        name: The variable.
        generated: Whether Noust generates its value (a secret, a database
            credential, the domain); either way ``env_vars`` overrides it.
    """

    name: str
    generated: bool


class RecipeHealthOut(BaseModel):
    """What the health gate asks of the application."""

    path: str
    expect: str | None = None


class RecipeOut(RecipeSummaryOut):
    """
    One recipe in full.

    Attributes:
        source: Where its code comes from; None when unavailable.
        layout: ``releases`` or ``inplace``.
        port: Its default port, when it runs a process.
        env: The variables it sets.
        persistent_paths: What survives every release in ``shared/``.
        health: Its health check.
        notes: What the operator is told after the deploy, with ``{{ url }}``
            and the like still to be filled in.
    """

    source: RecipeSourceOut | None = None
    layout: str = "releases"
    port: int | None = None
    env: list[RecipeEnvOut] = []
    persistent_paths: list[str] = []
    health: RecipeHealthOut | None = None
    notes: list[str] = []


@router.get("", response_model=RecipeListOut)
def get_recipes(session: Annotated[dict, Depends(get_current_session)]) -> RecipeListOut:
    """
    List the recipes, the ones this release can deploy first.

    Args:
        session: The authenticated session.

    Returns:
        Every recipe, with whether it is available and why not.
    """
    return RecipeListOut(items=[RecipeSummaryOut(**recipe.summary()) for recipe in list_recipes()])


@router.get("/{name}", response_model=RecipeOut)
def get_recipe_detail(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> RecipeOut:
    """
    Describe one recipe.

    Args:
        name: The recipe.
        session: The authenticated session.

    Returns:
        The recipe in full.

    Raises:
        HTTPException: 404 when no recipe has that name.
    """
    try:
        recipe = get_recipe(name)
    except RecipeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return RecipeOut(**recipe.to_dict())
