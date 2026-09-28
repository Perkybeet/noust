# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turning a recipe into an ordinary deployment, and finishing it.

The one implementation ``wasm create --recipe`` and ``POST /api/apps`` with a
``recipe`` share: :func:`plan_recipe` provisions the database, renders the
variables and the source, and answers the arguments of a deployer's
``configure``; the caller deploys exactly as it deploys anything else; then
:func:`finish_recipe` links what the deployment could not know about and
answers the notes for the operator.

The database is provisioned before the deployment, because the variables
carry its credentials and a build may need them (Umami migrates during its
build). A deployment that fails leaves the database in place, and running the
recipe again reuses it and the password WASM stored for it
(:func:`~wasm.deployers.helpers.databases.provision_database`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wasm.core.config import Config
from wasm.core.exceptions import DeploymentError
from wasm.core.fs import get_fs
from wasm.core.logger import Logger
from wasm.core.store import WASMStore, get_store
from wasm.core.utils import domain_to_app_name
from wasm.deployers.helpers.databases import (
    DatabaseCredentials,
    database_identifiers,
    provision_database,
)
from wasm.recipes import Recipe, RecipeError, get_recipe, read_asset
from wasm.recipes.render import render_value
from wasm.validators.domain import validate_domain
from wasm.validators.environment import validate_environment

#: Directory, beside the store, that holds the sources rendered from a
#: recipe's templates: a deployment copies its release from there, and so
#: does every later update.
RECIPE_SOURCES_DIR = "recipe-sources"


@dataclass(frozen=True)
class RecipePlan:
    """
    What a recipe resolved to for one domain.

    Attributes:
        recipe: The recipe.
        domain: The application's domain.
        app_type: The deployer that builds it.
        source: The source a deployment is given.
        branch: The git ref, for a git source.
        port: The port the application listens on, when it runs a process.
        env_vars: The rendered variables, with the operator's overrides.
        layout: ``releases`` or ``inplace``.
        persistent_paths: What every release shares.
        options: Deployer-specific settings (PHP, the health check).
        database: The credentials provisioned, when it has a database.
        notes: What to tell the operator once it is deployed.
    """

    recipe: Recipe
    domain: str
    app_type: str
    source: str
    branch: str | None
    port: int | None
    env_vars: dict[str, str]
    layout: str
    persistent_paths: list[str]
    options: dict[str, Any] = field(default_factory=dict)
    database: DatabaseCredentials | None = None
    notes: tuple[str, ...] = ()

    def configure_arguments(self) -> dict[str, Any]:
        """
        Say what a deployer's ``configure`` is given, besides the domain.

        Returns:
            Keyword arguments: source, branch, env_vars, layout,
            persistent_paths and the deployer-specific options.
        """
        return {
            "source": self.source,
            "branch": self.branch,
            "env_vars": dict(self.env_vars),
            "layout": self.layout,
            "persistent_paths": list(self.persistent_paths),
            **self.options,
        }


def recipe_source_dir(app_name: str, store: WASMStore | None = None) -> Path:
    """
    Say where the source rendered from a recipe's templates is kept.

    Args:
        app_name: The application name.
        store: The store, beside which it lives.

    Returns:
        ``<store directory>/recipe-sources/<app_name>``.
    """
    return (store or get_store()).db_path.parent / RECIPE_SOURCES_DIR / app_name


def refuse_conflicts(*, source: str | None, app_type: str | None) -> None:
    """
    Refuse a source or a type given together with a recipe.

    Args:
        source: The source the caller also gave, if any.
        app_type: The type the caller also gave; ``auto`` or None is none.

    Raises:
        RecipeError: When either was given.
    """
    given = [
        label
        for label, value in (("a source", source), ("a type", app_type))
        if value and value != "auto"
    ]
    if given:
        raise RecipeError(
            f"A recipe brings its own source and type; {' and '.join(given)} cannot be "
            "given with it",
            details="Leave out --source and --type (source and app_type in the API), or "
            "deploy without --recipe.",
        )


def plan_recipe(
    name: str,
    domain: str,
    *,
    port: int | None,
    ssl: bool,
    env_overrides: dict[str, str] | None = None,
    logger: Logger,
    store: WASMStore | None = None,
) -> RecipePlan:
    """
    Resolve a recipe for a new application: database, variables, source.

    Args:
        name: The recipe.
        domain: The new application's domain.
        port: The port chosen for it, when its type runs a process.
        ssl: Whether it will be served over TLS, for ``{{ url }}``.
        env_overrides: Variables the operator set, over the recipe's.
        logger: Where progress is reported.
        store: The store. Defaults to the process-wide one.

    Returns:
        The plan.

    Raises:
        RecipeError: The recipe does not exist, is not available, or does
            not render.
        DeploymentError: The domain is already deployed.
        DatabaseError: The database could not be provisioned.
        EnvironmentValidationError: An override is not a usable variable.
    """
    store = store or get_store()
    recipe = get_recipe(name)
    if not recipe.available:
        raise RecipeError(
            f"{recipe.title} is not available in this release",
            details=recipe.unavailable_reason or "See: wasm recipe list",
        )
    domain = validate_domain(domain)
    if store.get_app(domain) is not None:
        raise DeploymentError(
            f"{domain} is already deployed",
            details=f"A recipe creates a new application. Update this one with: wasm update "
            f"{domain}",
        )
    overrides = validate_environment(env_overrides or {})
    if recipe.source is None:
        raise RecipeError(f"Recipe {recipe.name} has no source", details="Fix the recipe file.")

    app_name = domain_to_app_name(domain)
    context: dict[str, Any] = {
        "domain": domain,
        "url": f"{'https' if ssl else 'http'}://{domain}",
        "https": ssl,
        "app_name": app_name,
        "app_path": str(Config().apps_directory / app_name),
        "port": port,
    }

    database = None
    if recipe.database_engine is not None:
        db_name, db_user = database_identifiers(app_name, recipe.database_engine)
        logger.substep(f"Database: {db_name} ({recipe.database_engine})")
        database = provision_database(
            recipe.database_engine,
            name=db_name,
            user=db_user,
            domain=domain,
            logger=logger,
            store=store,
        )
        context["database"] = database.context()

    env = {
        key: render_value(value, context, what=f"Recipe {recipe.name}: {key}")
        for key, value in recipe.env.items()
    }
    env.update(overrides)

    source, branch = recipe.source.deploy_source(), recipe.source.ref
    if recipe.source.kind == "template":
        source = str(_render_template_source(recipe, app_name, context, store))

    options: dict[str, Any] = {}
    persistent = list(recipe.persistent_paths)
    if recipe.php:
        php = recipe.php
        for key in ("webroot", "deny", "max_upload", "shared_from_release"):
            if key in php:
                options[f"php_{key}"] = php[key]
        files = {
            path: read_asset(recipe.name, asset) for path, asset in (php.get("files") or {}).items()
        }
        if files:
            options["php_files"] = files
        for extra in [*(php.get("shared_from_release") or []), *files]:
            if extra not in persistent:
                persistent.append(extra)
    if recipe.health is not None:
        options["health_path"] = recipe.health.path
        options["health_expect"] = recipe.health.expect

    notes = tuple(
        render_value(note, context, what=f"Recipe {recipe.name}: note") for note in recipe.notes
    )
    return RecipePlan(
        recipe=recipe,
        domain=domain,
        app_type=recipe.app_type,
        source=source,
        branch=branch,
        port=port,
        env_vars=env,
        layout=recipe.layout,
        persistent_paths=persistent,
        options=options,
        database=database,
        notes=notes,
    )


def _render_template_source(
    recipe: Recipe, app_name: str, context: dict[str, Any], store: WASMStore
) -> Path:
    """
    Write the files a template source is made of.

    Args:
        recipe: The recipe.
        app_name: The application name.
        context: What the files may use.
        store: The store, beside which the source is kept.

    Returns:
        The directory, which the deployment copies like any local source.
    """
    files = recipe.source.files if recipe.source is not None else {}
    directory = recipe_source_dir(app_name, store)
    fs = get_fs()
    fs.make_dir(directory, parents=True)
    for path, asset in files.items():
        target = directory / path
        fs.make_dir(target.parent, parents=True)
        fs.write_text(
            target,
            render_value(read_asset(recipe.name, asset), context, what=f"{recipe.name}: {path}"),
        )
    return directory


def finish_recipe(plan: RecipePlan, *, logger: Logger, store: WASMStore | None = None) -> list[str]:
    """
    Link what the deployment created to the recipe's database and health check.

    Args:
        plan: What the recipe resolved to.
        logger: Where progress is reported.
        store: The store. Defaults to the process-wide one.

    Returns:
        The notes for the operator.
    """
    store = store or get_store()
    app = store.get_app(plan.domain)
    if app is None:
        return list(plan.notes)
    if plan.database is not None:
        store.link_database_to_app(plan.database.name, plan.database.engine, plan.domain)
    health = plan.recipe.health
    if health is not None and app.health_path is None and app.health_expect is None:
        # PHP registers it itself, before its first gate; a process type only
        # learns it here, for every gate after this deployment.
        store.set_app_health(plan.domain, path=health.path, expect=health.expect, timeout=None)
        logger.debug(f"Health check: {health.path} ({health.expect or 'below 500'})")
    return list(plan.notes)
