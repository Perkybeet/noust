# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust recipe``: the applications Noust knows how to deploy from a recipe.

Deploying one is ``noust create --recipe NAME -d DOMAIN``; this group only
lists and describes them. Both read :mod:`noust.recipes`, as ``GET
/api/recipes`` does.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.logger import Logger
from noust.recipes import Recipe, get_recipe, list_recipes


def print_recipe(logger: Logger, recipe: Recipe) -> None:
    """
    Describe one recipe as an operator reads it.

    Args:
        logger: Where to print.
        recipe: The recipe.
    """
    logger.header(recipe.title)
    logger.info(recipe.description)
    logger.key_value("Homepage", recipe.homepage)
    if not recipe.available:
        logger.key_value("Available", "No")
        logger.info(recipe.unavailable_reason or "")
        return
    details = recipe.to_dict()
    source = details["source"] or {}
    logger.key_value("Type", recipe.app_type)
    logger.key_value(
        "Source",
        " ".join(
            str(part)
            for part in (
                source.get("url") or "rendered from the recipe",
                f"at {source['ref']}" if source.get("ref") else "",
                "(checksum verified)" if source.get("sha256") or source.get("checksum_url") else "",
            )
            if part
        ),
    )
    logger.key_value("Layout", recipe.layout)
    if recipe.database_engine:
        logger.key_value("Database", recipe.database_engine)
    if recipe.persistent_paths:
        logger.key_value("Kept across releases", ", ".join(recipe.persistent_paths))
    if recipe.health is not None:
        logger.key_value(
            "Health check", f"{recipe.health.path} ({recipe.health.expect or 'below 500'})"
        )
    for requirement in recipe.requires:
        logger.key_value("Requires", requirement)
    if details["env"]:
        logger.blank()
        logger.info("Environment (set with --env NAME=value to override):")
        for variable in details["env"]:
            suffix = " (generated)" if variable["generated"] else ""
            logger.info(f"  {variable['name']}{suffix}")
    logger.blank()
    logger.info(f"Deploy it with: noust create --recipe {recipe.name} -d <domain>")


@click.group("recipe", cls=NoustGroup)
def cli() -> None:
    """Applications Noust deploys from a recipe: WordPress, Uptime Kuma, Umami, n8n."""


@cli.command("list")
@json_option("Print the recipes as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """
    List the recipes, the ones this release can deploy first.

    A recipe that is not available says why.
    """
    recipes = list_recipes()
    if ctx.json_output:
        click.echo(json.dumps({"items": [recipe.summary() for recipe in recipes]}))
        return
    logger = ctx.logger
    for recipe in recipes:
        if recipe.available:
            logger.key_value(recipe.name, f"{recipe.title}: {recipe.description}")
        else:
            logger.key_value(recipe.name, f"{recipe.title}: not available in this release")
    logger.blank()
    logger.info("Details: noust recipe show NAME. Deploy: noust create --recipe NAME -d DOMAIN")


@cli.command("show")
@click.argument("name")
@json_option("Print the recipe as JSON.")
@pass_context
def show_command(ctx: Context, name: str) -> None:
    """Describe one recipe: what it deploys, from where, and what it needs."""
    recipe = get_recipe(name)
    if ctx.json_output:
        click.echo(json.dumps(recipe.to_dict()))
        return
    print_recipe(ctx.logger, recipe)
