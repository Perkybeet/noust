# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turning a recipe into an ordinary deployment, and finishing it.

The one implementation ``noust create --recipe``, ``noust recipe install`` and
``POST /api/apps`` with a ``recipe`` share: :func:`plan_recipe` provisions what
the recipe requires, resolves its variables and the source, and answers the
arguments of a deployer's ``configure``; the caller deploys exactly as it
deploys anything else; then :func:`finish_recipe` links what the deployment
could not know about, records which recipe and version the application came
from, and answers the notes for the operator.

What the recipe requires is provisioned before the deployment, because the
variables carry its credentials and a build may need them (Umami migrates
during its build). A deployment that fails leaves the database in place, and
running the recipe again reuses it and the password Noust stored for it
(:func:`~noust.deployers.helpers.databases.provision_database`).

Nothing is provisioned, written or generated until every ``required``
variable has a value (``${VAR:?required}``): the operator is told all of them
at once, before a database exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from noust.central import require_server_role
from noust.core.config import Config
from noust.core.exceptions import DeploymentError
from noust.core.fs import get_fs
from noust.core.logger import Logger
from noust.core.store import NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.helpers.databases import (
    DatabaseCredentials,
    database_identifiers,
    provision_database,
)
from noust.recipes import Recipe, RecipeError, get_recipe, read_asset
from noust.recipes.model import (
    ENGINE_OF,
    KEYED_REQUIREMENTS,
    SQL_REQUIREMENTS,
    RecipeBuild,
)
from noust.recipes.render import render_value
from noust.recipes.variables import (
    DeploymentContext,
    refuse_compose_dollar,
    require_inputs,
    resolve_variables,
)
from noust.recipes.versions import require_compatible
from noust.validators.domain import validate_domain
from noust.validators.environment import validate_environment

#: Directory, beside the store, that holds the sources rendered from a
#: recipe: the files of a template source and a Compose recipe's document. A
#: deployment copies its tree from there, and so does every later update.
RECIPE_SOURCES_DIR = "recipe-sources"

#: The file a Compose recipe's document is written to. The Compose deployer
#: finds it by this name, among the ones Compose itself looks for.
COMPOSE_FILE = "docker-compose.yml"

#: A health check as ``BaseDeployer.configure`` takes it: path, expect, timeout.
HealthTriple = tuple[str | None, str | None, int | None]


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
        env_vars: The variables, with the operator's overrides.
        layout: ``releases`` or ``inplace``.
        persistent_paths: What every release shares.
        options: Deployer-specific settings (PHP).
        database: The SQL credentials provisioned, when it has a database.
        keyed: What the database service provisioned for a Redis, Valkey or
            MongoDB requirement, to be linked once the application exists.
        secret_names: The variables whose value is a secret.
        build: The recipe's install, build and start commands.
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
    keyed: tuple[Any, ...] = ()
    secret_names: frozenset[str] = frozenset()
    build: RecipeBuild | None = None
    notes: tuple[str, ...] = ()

    def configure_arguments(self, *, health: HealthTriple | None = None) -> dict[str, Any]:
        """
        Say what a deployer's ``configure`` is given, besides the domain.

        The recipe's health check goes in as ``initial_health``, which every
        deployer records on the new row before its first gate: given as a
        loose option it reached only the PHP deployer, and every other type
        judged its first release by ``/`` below 500.

        Args:
            health: The operator's ``(path, expect, timeout)``, when the
                caller has one; each value given wins over the recipe's.

        Returns:
            Keyword arguments: source, branch, env_vars, layout,
            persistent_paths, initial_health, the recipe's build block when it
            has one, and the deployer-specific options.
        """
        arguments: dict[str, Any] = {
            "source": self.source,
            "branch": self.branch,
            "env_vars": dict(self.env_vars),
            "layout": self.layout,
            "persistent_paths": list(self.persistent_paths),
            "initial_health": self.initial_health(health),
            **self.options,
        }
        if self.build is not None:
            arguments["build_block"] = self.build.to_dict()
        return arguments

    def initial_health(self, given: HealthTriple | None = None) -> HealthTriple | None:
        """
        Merge the recipe's health check with the operator's.

        Args:
            given: The operator's ``(path, expect, timeout)``, any of them None.

        Returns:
            The check the new application starts with, or None when neither
            says anything.
        """
        recipe = self.recipe.health
        ours: HealthTriple = (
            (recipe.path, recipe.expect, recipe.timeout)
            if recipe is not None
            else (None, None, None)
        )
        theirs = given or (None, None, None)
        merged: HealthTriple = (
            theirs[0] if theirs[0] is not None else ours[0],
            theirs[1] if theirs[1] is not None else ours[1],
            theirs[2] if theirs[2] is not None else ours[2],
        )
        return merged if any(value is not None for value in merged) else None


def recipe_source_dir(app_name: str, store: NoustStore | None = None) -> Path:
    """
    Say where the source rendered from a recipe is kept.

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


def require_available(recipe: Recipe) -> None:
    """
    Refuse a recipe this release or this server cannot deploy.

    Args:
        recipe: The recipe.

    Raises:
        RecipeError: It is listed as unavailable (the message says why), asks
            for a newer Noust than this, or is not published for this
            server's architecture.
    """
    if not recipe.available:
        raise RecipeError(
            f"{recipe.title} is not available in this release",
            details=recipe.unavailable_reason or "See: noust recipe list",
        )
    require_compatible(recipe)


def plan_recipe(
    name: str,
    domain: str,
    *,
    port: int | None,
    ssl: bool,
    env_overrides: dict[str, str] | None = None,
    logger: Logger,
    store: NoustStore | None = None,
) -> RecipePlan:
    """
    Resolve a recipe for a new application: requirements, variables, source.

    Args:
        name: The recipe.
        domain: The new application's domain.
        port: The port chosen for it, when its type runs a process.
        ssl: Whether it will be served over TLS, for ``from: url``.
        env_overrides: Variables the operator set, over the recipe's: the
            values of its ``required`` and ``default`` variables among them.
        logger: Where progress is reported.
        store: The store. Defaults to the process-wide one.

    Returns:
        The plan.

    Raises:
        RecipeError: The recipe does not exist, is not available, a required
            variable has no value, or it does not render.
        DeploymentError: The domain is already deployed.
        DatabaseError: A requirement could not be provisioned.
        EnvironmentValidationError: An override is not a usable variable.
    """
    # Before a database is provisioned for an application a hub would not run.
    require_server_role("Applications")
    store = store or get_store()
    recipe = get_recipe(name)
    require_available(recipe)
    domain = validate_domain(domain)
    if store.get_app(domain) is not None:
        raise DeploymentError(
            f"{domain} is already deployed",
            details=f"A recipe creates a new application. Update this one with: noust update "
            f"{domain}",
        )
    overrides = validate_environment(env_overrides or {})
    if recipe.schema < 2:
        return _plan_legacy(
            recipe, domain, port=port, ssl=ssl, overrides=overrides, logger=logger, store=store
        )
    if recipe.kind == "compose" and port is None:
        raise RecipeError(
            f"{recipe.title} needs a port to publish its web service on",
            details="Choose one with --port, or leave it out and Noust picks a free one.",
        )

    # Before anything exists: a missing variable must not leave a database behind.
    require_inputs(recipe.title, recipe.variables, overrides)
    if recipe.kind == "compose":
        refuse_compose_dollar(recipe.title, overrides)

    app_name = domain_to_app_name(domain)
    context = DeploymentContext(
        domain=domain,
        ssl=ssl,
        port=port,
        app_name=app_name,
        app_path=str(Config().apps_directory / app_name),
    )
    provisioned, database, keyed = _provision_requirements(
        recipe, domain, app_name, overrides=overrides, logger=logger, store=store
    )
    resolved = resolve_variables(
        recipe.variables, context=context, provisioned=provisioned, overrides=overrides
    )
    env = dict(resolved.values)
    if recipe.kind == "compose":
        # The one value Noust owns in a stack's .env: the Compose document
        # publishes the web service on it.
        env["PORT"] = str(port)

    source, branch = prepare_source(recipe, app_name, store)
    options, persistent = _deployer_options(recipe)
    notes = render_notes(recipe, context, resolved.values, resolved.secrets)
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
        keyed=keyed,
        secret_names=frozenset(resolved.secrets),
        build=recipe.build,
        notes=notes,
    )


def _provision_requirements(
    recipe: Recipe,
    domain: str,
    app_name: str,
    *,
    overrides: dict[str, str],
    logger: Logger,
    store: NoustStore,
) -> tuple[dict[str, dict[str, str]], DatabaseCredentials | None, tuple[Any, ...]]:
    """
    Provision what a recipe requires, through the existing database managers.

    A SQL database goes through
    :func:`~noust.deployers.helpers.databases.provision_database`, as the
    recipes of 2.3 always did. Redis, Valkey and MongoDB go through the
    database service, which also records them so the application's backups
    and its Database tab know them. S3 is provisioned by nobody: it is an
    external service, and its settings are variables the operator gives.

    Args:
        recipe: The recipe.
        domain: The new application's domain.
        app_name: Its name.
        overrides: The operator's variables, which name the variable a keyed
            database's connection string is written to when it is declared.
        logger: Where progress is reported.
        store: The store.

    Returns:
        What each requirement provisioned (requirement to attribute to
        value), the SQL credentials, and the keyed databases to link later.
    """
    provisioned: dict[str, dict[str, str]] = {}
    database: DatabaseCredentials | None = None
    keyed: list[Any] = []
    for requirement in recipe.requires:
        if requirement in SQL_REQUIREMENTS:
            engine = ENGINE_OF[requirement]
            db_name, db_user = database_identifiers(app_name, engine)
            logger.substep(f"Database: {db_name} ({engine})")
            database = provision_database(
                engine, name=db_name, user=db_user, domain=domain, logger=logger, store=store
            )
            provisioned[requirement] = database.context()
        elif requirement in KEYED_REQUIREMENTS:
            from noust.managers.database.service import DatabaseService

            variable = next(
                (v.name for v in recipe.variables if v.source == f"{requirement}.url"), None
            )
            logger.substep(f"Database: {ENGINE_OF[requirement]}")
            prepared = DatabaseService(logger=logger).prepare_for_new_app(
                domain,
                ENGINE_OF[requirement],
                env_var=variable,
                env=overrides,
            )
            keyed.append(prepared)
            provisioned[requirement] = _attributes_of_url(prepared.values[prepared.env_var])
    return provisioned, database, tuple(keyed)


def _attributes_of_url(url: str) -> dict[str, str]:
    """
    Read the attributes of a connection string a database service answered.

    Args:
        url: ``scheme://user:password@host:port/name``.

    Returns:
        ``url``, ``host``, ``port``, ``user``, ``password`` and ``name``,
        empty where the string has none.
    """
    parts = urlsplit(url)
    return {
        "url": url,
        "host": parts.hostname or "",
        "port": str(parts.port or ""),
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "name": parts.path.lstrip("/"),
    }


def prepare_source(recipe: Recipe, app_name: str, store: NoustStore) -> tuple[str, str | None]:
    """
    Settle where a deployment of a recipe takes its tree from.

    A git source and an archive are what they say (the archive carries its
    checksum, which every download verifies). A template source and a Compose
    recipe have no remote: their files are written into the recipe-sources
    directory beside the store, and the deployment copies them like any local
    source. Writing replaces what an earlier version put there, which is how
    an update changes the tree.

    Args:
        recipe: The recipe.
        app_name: The application name.
        store: The store, beside which the files are kept.

    Returns:
        The source, and the git ref when there is one.

    Raises:
        RecipeError: The recipe has no source.
    """
    if recipe.kind == "compose":
        return str(_write_compose_source(recipe, app_name, store)), None
    if recipe.source is None:
        raise RecipeError(f"Recipe {recipe.name} has no source", details="Fix the recipe file.")
    if recipe.source.kind == "template":
        return str(_write_template_source(recipe, app_name, store)), None
    return recipe.source.deploy_source(), recipe.source.ref


def _write_tree(directory: Path, files: dict[str, str]) -> None:
    """
    Replace a recipe source directory with exactly these files.

    Args:
        directory: The directory.
        files: Path inside it to text.
    """
    fs = get_fs()
    if directory.exists():
        fs.remove_tree(directory)
    fs.make_dir(directory, parents=True)
    for path, text in files.items():
        target = directory / path
        fs.make_dir(target.parent, parents=True)
        fs.write_text(target, text)


def _write_compose_source(recipe: Recipe, app_name: str, store: NoustStore) -> Path:
    """
    Write a Compose recipe's document and files.

    Only the document and the files the recipe ships: never ``.env``, whose
    values are secrets and which the deployment writes into the application
    itself.

    Args:
        recipe: The recipe.
        app_name: The application name.
        store: The store, beside which the source is kept.

    Returns:
        The directory.
    """
    directory = recipe_source_dir(app_name, store)
    document = recipe.compose or ""
    files = {COMPOSE_FILE: document if document.endswith("\n") else document + "\n"}
    for path, asset in recipe.files.items():
        files[path] = read_asset(recipe.name, asset)
    _write_tree(directory, files)
    return directory


def _write_template_source(recipe: Recipe, app_name: str, store: NoustStore) -> Path:
    """
    Write the files a template source is made of.

    A version 1 recipe's files are Jinja templates over the deployment; a
    version 2 recipe's are copied as they are.

    Args:
        recipe: The recipe.
        app_name: The application name.
        store: The store, beside which the source is kept.

    Returns:
        The directory.
    """
    files = recipe.source.files if recipe.source is not None else {}
    directory = recipe_source_dir(app_name, store)
    _write_tree(directory, {path: read_asset(recipe.name, asset) for path, asset in files.items()})
    return directory


def _deployer_options(recipe: Recipe) -> tuple[dict[str, Any], list[str]]:
    """
    Gather the deployer-specific settings and the persistent paths of a recipe.

    Args:
        recipe: The recipe.

    Returns:
        The options for ``configure`` and the paths every release shares.
    """
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
    return options, persistent


def render_notes(
    recipe: Recipe,
    context: DeploymentContext,
    values: dict[str, str],
    secrets: set[str],
    *,
    database: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """
    Fill in what to tell the operator after the deployment.

    A note may use ``{{ url }}``, ``{{ domain }}``, ``{{ https }}``,
    ``{{ app_name }}``, ``{{ port }}`` and ``{{ vars.NAME }}``, the value of a
    variable that is not a secret. A secret is never in a note: it would end
    up in the job's log and in whatever the operator pastes it into.

    Args:
        recipe: The recipe.
        context: What the deployment gives.
        values: The variables' values.
        secrets: The names whose value is a secret.
        database: Version 1: the SQL credentials, for ``{{ database.* }}``.

    Returns:
        The notes, rendered.

    Raises:
        ValidationError: A note uses something it may not.
    """
    template_context: dict[str, Any] = {
        "domain": context.domain,
        "url": context.value("url"),
        "https": context.ssl,
        "app_name": context.app_name,
        "app_path": context.app_path,
        "port": context.port,
        "vars": {name: value for name, value in values.items() if name not in secrets},
    }
    if database is not None:
        template_context["database"] = database
    return tuple(
        render_value(note, template_context, what=f"Recipe {recipe.name}: note")
        for note in recipe.notes
    )


def _plan_legacy(
    recipe: Recipe,
    domain: str,
    *,
    port: int | None,
    ssl: bool,
    overrides: dict[str, str],
    logger: Logger,
    store: NoustStore,
) -> RecipePlan:
    """
    Plan a recipe written in the 2.3 format: Jinja values and one SQL database.

    Args:
        recipe: The recipe.
        domain: The validated domain.
        port: The port chosen for it.
        ssl: Whether it will be served over TLS.
        overrides: The operator's variables, validated.
        logger: Where progress is reported.
        store: The store.

    Returns:
        The plan.
    """
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

    source, branch = _legacy_source(recipe, app_name, context, store)
    options, persistent = _deployer_options(recipe)
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


def _legacy_source(
    recipe: Recipe, app_name: str, context: dict[str, Any], store: NoustStore
) -> tuple[str, str | None]:
    """
    Settle the source of a version 1 recipe, rendering its template files.

    Args:
        recipe: The recipe.
        app_name: The application name.
        context: What the files may use.
        store: The store.

    Returns:
        The source and the git ref.

    Raises:
        RecipeError: The recipe has no source.
    """
    if recipe.source is None:
        raise RecipeError(f"Recipe {recipe.name} has no source", details="Fix the recipe file.")
    if recipe.source.kind != "template":
        return recipe.source.deploy_source(), recipe.source.ref
    directory = recipe_source_dir(app_name, store)
    _write_tree(
        directory,
        {
            path: render_value(
                read_asset(recipe.name, asset), context, what=f"{recipe.name}: {path}"
            )
            for path, asset in recipe.source.files.items()
        },
    )
    return str(directory), None


def finish_recipe(
    plan: RecipePlan, *, logger: Logger, store: NoustStore | None = None
) -> list[str]:
    """
    Link what the deployment created to the recipe, and record where it came from.

    The health check is not set here: the deployment recorded it on the new
    row before its first gate, from :meth:`RecipePlan.configure_arguments`.

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
        logger.debug(f"Database {plan.database.name} linked to {plan.domain}")
    if plan.keyed:
        from noust.managers.database.service import DatabaseService

        service = DatabaseService(logger=logger)
        for prepared in plan.keyed:
            service.link_new_app(prepared)
    if plan.secret_names:
        marks = dict(app.env_secret_marks)
        marks.update({name: True for name in plan.secret_names if name in plan.env_vars})
        store.set_env_secret_marks(plan.domain, marks)
    from noust.recipes.installed import record_install

    record_install(plan.domain, plan.recipe, store=store)
    return list(plan.notes)
