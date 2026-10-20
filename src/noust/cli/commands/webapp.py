# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The nine commands that act on a deployed application.

They are top level (``noust create``, ``noust logs``) rather than nested under a
``webapp`` group because that is how they have always been typed. The Click
group below only exists as a container: :mod:`noust.cli.app` picks the command
whose name the user typed out of it.

Each command is a thin shell around a private function that takes explicit
arguments. That seam is what lets the argparse-shaped entry point
(:func:`handle_webapp`, still called by :mod:`noust.cli.interactive` for its
create, list and update flows) and the Click commands share one implementation
instead of drifting into two.

Everything this module needs is imported here rather than inside the handlers.
An import that only exists inside one function is a NameError waiting for the
next caller, which is exactly how ``site delete`` lost its certificate cleanup.
"""

from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.cli.commands.app_container import changes_from_options, container_options
from noust.cli.commands.team_common import placed_in_a_project
from noust.cli.panel_links import open_in_panel
from noust.core.app_state import RUNNING, RUNNING_UNMANAGED, STATIC, resolve_states
from noust.core.app_tags import AppTags, validate_tag
from noust.core.config import Config
from noust.core.container_store import forget_secrets
from noust.core.dependencies import check_deployment_ready
from noust.core.exceptions import (
    DeploymentError,
    NoustError,
    ServiceError,
    SourceError,
    ValidationError,
)
from noust.core.logger import Logger, state, styled
from noust.core.runner import (
    CommandResult,
    get_runner,
)
from noust.core.store import (
    DeploymentTrigger,
    get_store,
    is_adopted,
    is_compose_backed,
    validate_tag_pattern,
)
from noust.core.tags import newest_tag
from noust.core.teams import Scope, ScopedStore, TeamStore
from noust.core.utils import domain_to_app_name
from noust.deployers import get_deployer
from noust.deployers.docker_compose import DockerComposeDeployer, stack_deployer
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.layout import CONFIGURED, LAYOUTS, choose_layout
from noust.deployers.helpers.package_manager import SUPPORTED_PACKAGE_MANAGERS
from noust.deployers.helpers.php_fpm import is_php_fpm
from noust.deployers.lifecycle import (
    NOTHING_NEW_HINT,
    AppUpdate,
    check_adopted_directory,
    check_upstream,
    delete_app,
    deletion_databases,
    is_in_apps_directory,
    set_follow_tags,
    update_app,
)
from noust.deployers.monorepo import MonorepoDeployer
from noust.deployers.native_adopt import adoption_of
from noust.deployers.php_fpm import control_pool
from noust.deployers.registry import available_types
from noust.managers.apache_manager import ApacheManager
from noust.managers.app_processes import ProcessManager
from noust.managers.app_replicas import ReplicaManager
from noust.managers.database.service import OWN_DATABASE_TYPES, DatabaseService
from noust.managers.nginx_manager import NginxManager
from noust.managers.port_claims import PortClaims
from noust.managers.service_manager import ResourceLimits, ServiceManager
from noust.managers.source_manager import SourceManager
from noust.recipes import RecipeError, get_recipe
from noust.recipes.deploy import (
    finish_recipe,
    keep_after_failed_deploy,
    plan_recipe,
    refuse_conflicts,
)
from noust.validators.domain import should_include_www, validate_domain
from noust.validators.environment import is_valid_env_name

#: Larger than any environment file a person writes; a bigger one is an
#: archive or a build artefact passed by mistake.
MAX_ENV_FILE_SIZE = 1024 * 1024

#: Following logs is interactive and ends with Ctrl+C, but the runner insists
#: on a deadline. A day is long enough to be indistinguishable from forever.
_FOLLOW_TIMEOUT = 86400

#: Application types ``create`` accepts, in the order they are offered. Read
#: from the registry rather than typed out here: a hand-written copy is a copy
#: that goes stale the first time a deployer is added, and it did.
APP_TYPES = [entry["type"] for entry in available_types()]

#: Node package managers ``create`` and ``update`` accept. Derived from
#: PackageManagerHelper's own list plus "auto" (detect from the lock file),
#: so a manager it can drive - yarn among them - is never rejected here
#: before it gets the chance to.
PACKAGE_MANAGERS = [*SUPPORTED_PACKAGE_MANAGERS, "auto"]

#: Web servers a site can be fronted by.
WEBSERVERS = ["nginx", "apache"]

#: Present participle and past participle of each service operation, so the
#: three commands that only differ in a verb are one implementation.
_SERVICE_VERBS: dict[str, tuple[str, str]] = {
    "start": ("Starting", "started"),
    "stop": ("Stopping", "stopped"),
    "restart": ("Restarting", "restarted"),
}


def _follow(argv: list[str], cwd: Path | None = None) -> CommandResult | None:
    """
    Stream a long-running command until the user interrupts it.

    Args:
        argv: Program and arguments.
        cwd: Working directory.

    Returns:
        The command outcome, or None when the user pressed Ctrl+C.
    """
    try:
        return get_runner().stream(argv, on_line=print, cwd=cwd, timeout=_FOLLOW_TIMEOUT)
    except KeyboardInterrupt:
        return None


def _read_env_file(env_file: Path, logger: Logger) -> dict[str, str]:
    """
    Read the variables of the file given to ``--env-file``.

    Parsed by :meth:`~noust.deployers.helpers.env_manager.EnvManager.read_env_file`,
    the grammar ``noust env`` and the panel read the application's own env file
    with, so ``export FOO=bar``, quotes and comments mean the same thing on the
    way in as they do once deployed. A name that is not an environment
    variable is reported and skipped rather than aborting the deployment,
    because one stray line in a long file should not cost the user the run.

    Args:
        env_file: File to read.
        logger: Logger of the current command.

    Returns:
        The variables found, in file order.

    Raises:
        DeploymentError: When the file is missing, unreadable, too large to be
            an environment file, or not UTF-8.
    """
    if not env_file.exists():
        raise DeploymentError(
            f"Environment file not found: {env_file}",
            details="Check the path given to --env-file, or omit the option.",
        )

    file_size = env_file.stat().st_size
    if file_size > MAX_ENV_FILE_SIZE:
        raise DeploymentError(
            f"Environment file too large: {file_size} bytes (max: {MAX_ENV_FILE_SIZE})",
            details="An environment file holds KEY=value lines. Check you did "
            "not pass a build artefact or an archive by mistake.",
        )

    # read_env_file treats an unreadable file as an empty one, which is right
    # for an application that has no .env yet and wrong for a file the
    # operator named on the command line.
    if not os.access(env_file, os.R_OK):
        raise DeploymentError(
            f"Cannot read environment file {env_file}",
            details="Check its permissions, or run the command as root.",
        )

    try:
        values = EnvManager().read_env_file(env_file)
    except UnicodeDecodeError as e:
        raise DeploymentError(
            f"Failed to read environment file {env_file}: {e}",
            details="Check the file is UTF-8 text.",
        ) from e

    env_vars: dict[str, str] = {}
    for key, value in values.items():
        if not is_valid_env_name(key):
            logger.warning(f"Invalid variable name '{key}' in {env_file}, skipping")
            continue
        env_vars[key] = value
    return env_vars


def _create_app(
    *,
    logger: Logger,
    domain: str,
    source: str,
    app_type: str = "auto",
    port: int | None = None,
    webserver: str = "nginx",
    branch: str | None = None,
    ssl: bool = True,
    www: bool = False,
    env_file: Path | None = None,
    package_manager: str = "auto",
    subdomains: tuple[str, ...] = (),
    workspaces: tuple[str, ...] = (),
    skip_database: bool = False,
    compose_file: str | None = None,
    compose_profiles: tuple[str, ...] = (),
    layout: str | None = None,
    persist: tuple[str, ...] = (),
    replace_existing: bool = False,
    env_vars: dict[str, str] | None = None,
    env_secret_marks: dict[str, bool] | None = None,
    limits: ResourceLimits | None = None,
    initial_health: tuple[str | None, str | None, int | None] | None = None,
    database: tuple[str, str | None] | None = None,
    database_version: str | None = None,
    install_database: bool = False,
    container: dict[str, Any] | None = None,
    build_document: str | None = None,
    database_instance: str | None = None,
    no_cache: bool = False,
    registry_logins: tuple[str, ...] = (),
    seed_files: dict[str, str] | None = None,
    seed_modes: dict[str, int] | None = None,
) -> int:
    """
    Deploy an application.

    Args:
        logger: Logger of the current command.
        domain: Domain the application is served on.
        source: Git URL or local directory.
        app_type: Application type, or ``auto`` to detect it.
        port: Port to listen on. A free one is chosen when None.
        webserver: ``nginx`` or ``apache``.
        branch: Git branch to deploy.
        ssl: Request a certificate.
        www: Also answer on ``www.<domain>``, recorded as a redirect to the
            domain and covered by its certificate.
        env_file: File of KEY=value pairs for the application environment.
        package_manager: Node package manager, or ``auto``.
        subdomains: ``app:subdomain`` mappings, for a monorepo.
        workspaces: Monorepo workspaces to deploy; empty means all.
        skip_database: Skip database provisioning, for a monorepo.
        compose_file: Compose file to use, for a Docker Compose project.
        compose_profiles: Compose profiles to activate.
        layout: ``inplace`` or ``releases``. None gives a new application
            the server's configured layout (``deploy.layout``).
        persist: Paths kept in ``shared/`` and linked into every release.
        replace_existing: Deploy into an application directory that already
            holds files (``--force``); refused otherwise.
        env_vars: Variables for the application environment, over those of
            ``env_file``: what ``noust app import`` and ``noust import`` give.
        env_secret_marks: The secret marks the new application's row starts
            with, so its first build's log is already scrubbed of them.
        limits: The unit's memory, CPU and task limits from the start; None
            creates it without any.
        initial_health: ``(path, expect, timeout)`` a new application's
            health check starts with, so its first deployment's gate already
            asks it (an import's); None leaves the defaults.
        database: ``(engine, name)`` of a database created before the first
            build, its connection string written into the environment marked
            secret (``--database``); the name None derives it from the
            application. Kept, with how to drop it, when the deploy fails.
        database_version: The version of the database's engine
            (``--database-version``); None for the installed one.
        install_database: Install the engine, in that version, when it is
            missing (``--install-database``).
        database_instance: The instance Noust runs to make the database on
            (``--database-instance``); None for the host's engine.
        container: What a container application is made from and how it runs,
            as :func:`~noust.cli.commands.app_container.changes_from_options`
            gives it (``--image``, ``--builder``, ``--build-arg``...); None for
            a repository with a Dockerfile as it is.
        build_document: The operator's build block, as YAML (``--build-file``):
            what the first deployment builds with, kept for every later one.
        no_cache: Build the first deployment's images without their layer
            cache (``--no-cache``).
        registry_logins: Registry accounts (``noust registry add``) to log in
            for the first deployment of a Docker Compose or container
            application, and to attach to it once it exists (``--registry``).
        seed_files: Text of files that must be in place before the first start, by path
            relative to the application (an import's mounted files); written once.
        seed_modes: Their permissions.

    Returns:
        Exit code.

    Raises:
        NoustError: When validation or any deployment step fails.
    """
    domain = validate_domain(domain)
    if build_document is not None and app_type in ("monorepo", "docker-compose"):
        raise click.UsageError(
            f"--build-file cannot be used with a {app_type} application: it is not built "
            "by a build block."
        )
    if database is not None and app_type in OWN_DATABASE_TYPES:
        raise click.UsageError(
            f"--database cannot be used with a {app_type} application: it provisions its own."
        )

    if app_type in ("monorepo", "docker-compose", "container"):
        # Refuses an explicit --layout releases for the types that have no
        # release pipeline yet, with the same words the deployers use.
        choose_layout(None, layout, app_type=app_type, supports_releases=False)

    if port:
        port = PortClaims().validated(port)
    elif app_type == "docker-compose":
        # A stack publishes its own ports, and the site goes to the one it publishes for `/`
        # (docs/compose.md); a free port Noust picked would be one nothing listens on.
        port = None
    else:
        port = PortClaims().choose(preferred=3000)
        if not port:
            raise DeploymentError(
                "No available port found",
                details="Free a port in the range Noust allocates from, or pass --port.",
            )

    # The readiness check needs a concrete type before anything is fetched, so
    # it runs against nodejs; the deployer itself keeps "auto" and detects the
    # real type from the fetched source. Rewriting app_type here, as this used
    # to, deployed every untyped app as a plain Node service.
    readiness_type = "nodejs" if app_type == "auto" else app_type

    can_deploy, missing, warnings = check_deployment_ready(
        app_type=readiness_type,
        package_manager=package_manager,
        verbose=logger.verbose,
    )

    for warning in warnings:
        logger.warning(warning)

    if not can_deploy:
        logger.error("System is not ready for deployment")
        logger.blank()
        logger.info("Missing requirements:")
        for item in missing:
            logger.error(f"  - {item}")
        logger.blank()
        logger.info("To fix these issues, run:")
        logger.info("  sudo noust setup init")
        logger.blank()
        logger.info("Or for detailed diagnostics:")
        logger.info("  noust setup doctor")
        return 1

    env_vars = {**(_read_env_file(env_file, logger) if env_file else {}), **(env_vars or {})}

    logger.header("Noust Deployment")
    logger.key_value("Domain", domain)
    logger.key_value("Source", source)
    logger.key_value("Type", "detected from the source" if app_type == "auto" else app_type)
    logger.key_value("Port", str(port) if port else "the one the stack publishes")
    logger.key_value("Package Manager", package_manager)
    logger.key_value("SSL", "Yes" if ssl else "No")
    if ssl and www and should_include_www(domain):
        logger.key_value("WWW", f"www.{domain} redirects to {domain}")
    logger.blank()

    if app_type == "monorepo":
        return _create_monorepo(
            logger=logger,
            domain=domain,
            source=source,
            webserver=webserver,
            ssl=ssl,
            branch=branch,
            env_vars=env_vars,
            subdomains=subdomains,
            workspaces=workspaces,
            skip_database=skip_database,
            replace_existing=replace_existing,
        )

    if app_type == "docker-compose":
        return _create_docker_compose(
            logger=logger,
            domain=domain,
            source=source,
            webserver=webserver,
            ssl=ssl,
            branch=branch,
            env_vars=env_vars,
            compose_file=compose_file,
            compose_profiles=compose_profiles,
            port=port,
            replace_existing=replace_existing,
        )

    options = _import_options(env_secret_marks, limits, initial_health)
    if build_document is not None:
        options["build_document"] = build_document
    if no_cache:
        options["no_cache"] = True
    if registry_logins:
        options["registry_logins"] = list(registry_logins)
    if seed_files:
        options["seed_files"] = dict(seed_files)
        options["seed_modes"] = dict(seed_modes or {})
    databases = DatabaseService(logger=logger) if database is not None else None
    new_database = None
    if databases is not None and database is not None:
        engine, name = database
        # Only what was asked for: no version and no --install is the 3.3 call.
        asked: dict[str, Any] = {}
        if database_version:
            asked["version"] = database_version
        if install_database:
            asked["install"] = True
        if database_instance:
            asked["instance"] = database_instance
        new_database = databases.prepare_for_new_app(
            domain, engine, name=name, env=env_vars, **asked
        )
        options.update(new_database.configure_options(options.get("env_secret_marks")))

    container_settings = _container_settings(domain, app_type, container, source)
    # Passed only when given, so a plain create configures the deployer exactly
    # as it always has.
    container_options: dict[str, Any] = (
        {} if container_settings is None else {"container": container_settings}
    )
    deployer = get_deployer(app_type, verbose=logger.verbose)
    try:
        deployer.configure(
            domain=domain,
            source=source,
            port=port,
            webserver=webserver,
            ssl=ssl,
            branch=branch,
            env_vars=env_vars,
            package_manager=package_manager,
            include_www=www,
            # CONFIGURED rather than the configured value itself: an application
            # that already exists keeps its layout unless one was asked for.
            layout=layout or CONFIGURED,
            persistent_paths=list(persist) if persist else None,
            replace_existing=replace_existing,
            **container_options,
            **options,
        )
        deployer.deploy()
    except NoustError as exc:
        if databases is not None and new_database is not None:
            databases.keep_after_failed_deploy(new_database, exc)
        if container is not None and get_store().get_app(domain) is None:
            # The registry password and the build secrets it was given were
            # kept for an application that did not come to exist.
            forget_secrets(domain_to_app_name(domain))
        raise
    if databases is not None and new_database is not None:
        outcome = databases.link_new_app(new_database)
        if outcome.env_vars:
            logger.success(
                f"Database {outcome.database} ({outcome.engine}) linked as "
                f"{', '.join(outcome.env_vars)}"
            )

    return 0


def _container_settings(
    domain: str, app_type: str, container: dict[str, Any] | None, source: str
) -> dict[str, Any] | None:
    """
    Turn the container options of ``noust create`` into the settings a deployer takes.

    Their secrets (the registry password, the build secrets) are written to the
    secret store here, once nothing is refused.

    Args:
        domain: The new application's domain.
        app_type: The application type asked for.
        container: What :func:`changes_from_options` gave, or None.
        source: The source: the repository, or the image for ``--image``.

    Returns:
        The settings as a mapping, or None when there is nothing to say
        (the deployer's defaults are what is wanted).

    Raises:
        click.UsageError: Container options were given for another type.
        ValidationError: The settings are not valid.
    """
    if container is None:
        return None
    from dataclasses import asdict

    from noust.deployers.helpers.container_settings import settings_for_new_app

    if app_type != "container":
        raise click.UsageError("Container options only apply to --type container.")
    changes = {**container, "method": container.get("method") or "dockerfile"}
    if changes["method"] == "image":
        changes["image"] = changes.get("image") or source
    return asdict(settings_for_new_app(domain, changes))


def _newest_followed_tag(source: str, pattern: str) -> str:
    """
    Pick the tag a new application that follows tags is first deployed at.

    Asked of the remote before anything is fetched or built, so a pattern that
    matches nothing fails at once instead of after a deploy of the wrong thing.

    Args:
        source: The git source.
        pattern: The glob to follow.

    Returns:
        The newest tag matching it, by version.

    Raises:
        ValidationError: The pattern is not a tag glob.
        SourceError: The source is not git, cannot be read, or has no tag
            matching the pattern.
    """
    validate_tag_pattern(pattern)
    newest = newest_tag(SourceManager().remote_tags(source), pattern)
    if newest is None:
        raise SourceError(
            f"The repository has no tag matching {pattern}",
            details="Tag a release that does, deploy a branch with --branch, or change the "
            "pattern. Only tags that are version numbers (v1.2.3) are ordered.",
        )
    return newest


def _follow_tags_after_create(domain: str, pattern: str, tag: str) -> None:
    """
    Record that a new application follows tags, once it has been deployed at one.

    The deployer recorded the tag as the ref it built; it is not a branch to
    return to, so it is forgotten when it is exactly that.

    Args:
        domain: The application's domain.
        pattern: The glob it follows.
        tag: The tag it was deployed at.

    Raises:
        NoustError: The application could not be made to follow the pattern.
    """
    set_follow_tags(domain, pattern)
    store = get_store()
    app = store.get_app(domain)
    if app is not None and app.branch == tag:
        app.branch = None
        store.update_app(app)


def _import_options(
    env_secret_marks: dict[str, bool] | None,
    limits: ResourceLimits | None,
    initial_health: tuple[str | None, str | None, int | None] | None = None,
) -> dict[str, Any]:
    """
    The deployer options only an import gives.

    Passed only when given, so a plain ``noust create`` configures the
    deployer exactly as it always has.

    Args:
        env_secret_marks: The secret marks the new row starts with, or None.
        limits: The unit's limits, or None.
        initial_health: The health check the new row starts with, or None.

    Returns:
        Keyword arguments for ``configure``.
    """
    options: dict[str, Any] = {}
    if initial_health is not None:
        options["initial_health"] = initial_health
    if env_secret_marks:
        options["env_secret_marks"] = env_secret_marks
    if limits is not None:
        options.update(
            memory_max_mb=limits.memory_max_mb,
            cpu_quota_percent=limits.cpu_quota_percent,
            tasks_max=limits.tasks_max,
            resource_limits_given=True,
        )
    return options


def _parse_database(spec: str) -> tuple[str, str | None]:
    """
    Read ``--database ENGINE[:NAME]``.

    Args:
        spec: What was given.

    Returns:
        The engine and the name, None when it was left out.

    Raises:
        click.UsageError: When there is no engine.
    """
    engine, _, name = spec.partition(":")
    if not engine.strip():
        raise click.UsageError(
            f"--database {spec!r} names no engine: give ENGINE or ENGINE:NAME, such as "
            "postgresql or postgresql:shop."
        )
    return engine.strip(), name.strip() or None


def _parse_env_pairs(pairs: tuple[str, ...]) -> dict[str, str]:
    """
    Read ``--env NAME=VALUE`` options.

    Args:
        pairs: What was given, in order.

    Returns:
        Name to value; a later pair wins.

    Raises:
        click.BadParameter: When one is not ``NAME=VALUE`` with a valid name.
    """
    values: dict[str, str] = {}
    for pair in pairs:
        name, equals, value = pair.partition("=")
        if not equals or not is_valid_env_name(name):
            raise click.BadParameter(
                f"{pair!r} is not NAME=VALUE with a valid variable name", param_hint="--env"
            )
        values[name] = value
    return values


def _create_from_recipe(
    *,
    logger: Logger,
    recipe: str,
    domain: str,
    source: str | None,
    app_type: str,
    branch: str | None,
    layout: str | None,
    persist: tuple[str, ...],
    port: int | None,
    webserver: str,
    ssl: bool,
    www: bool,
    env_file: Path | None,
    env_vars: dict[str, str],
    package_manager: str,
    replace_existing: bool,
    default_backups: bool = True,
    install_requirements: bool = False,
    database: str | None = None,
) -> int:
    """
    Deploy an application from a recipe.

    The recipe's plan (:func:`noust.recipes.deploy.plan_recipe`, which the
    API's job uses too) provisions the database and renders the variables;
    the deployment is then the ordinary one, and the recipe's notes are
    printed at the end.

    Args:
        logger: Logger of the current command.
        recipe: The recipe's name.
        domain: Domain the application is served on.
        source: A source given as well, which is refused.
        app_type: A type given as well (other than ``auto``), which is refused.
        branch: A branch given as well, which is refused: the recipe pins it.
        layout: A layout given as well, which is refused.
        persist: Persistent paths given as well, which are refused.
        port: Port to listen on. A free one near the recipe's is chosen when None.
        webserver: ``nginx`` or ``apache``.
        ssl: Request a certificate.
        www: Also answer on ``www.<domain>``.
        env_file: File of variables over the recipe's.
        env_vars: ``--env`` variables, over the file's.
        package_manager: Node package manager, or ``auto``.
        replace_existing: Deploy into a directory that already holds files.
        default_backups: Schedule the daily backups an installed application
            starts with.
        install_requirements: Install the runtime and database engines the
            recipe needs that this server lacks.
        database: The SQL engine to run it on, among those the recipe offers.

    Returns:
        Exit code.

    Raises:
        NoustError: When the recipe, the database or any deployment step fails.
    """
    refuse_conflicts(source=source, app_type=app_type)
    if branch or layout or persist:
        raise RecipeError(
            "A recipe pins its ref, its layout and its persistent paths",
            details="Leave out --branch, --layout and --persist with --recipe.",
        )
    domain = validate_domain(domain)
    chosen = get_recipe(recipe)
    if not chosen.available:
        raise RecipeError(
            f"{chosen.title} is not available in this release",
            details=chosen.unavailable_reason or "See: noust recipe list",
        )

    if port:
        port = PortClaims().validated(port)
    else:
        port = PortClaims().choose(preferred=chosen.port or 3000)
        if not port:
            raise DeploymentError(
                "No available port found",
                details="Free a port in the range Noust allocates from, or pass --port.",
            )

    can_deploy, missing, warnings = check_deployment_ready(
        app_type=chosen.app_type, package_manager=package_manager, verbose=logger.verbose
    )
    for warning in warnings:
        logger.warning(warning)
    if not can_deploy:
        logger.error("System is not ready for deployment")
        for item in missing:
            logger.error(f"  - {item}")
        for requirement in chosen.needs:
            logger.info(f"  {chosen.title} requires: {requirement}")
        return 1

    overrides = {**(_read_env_file(env_file, logger) if env_file else {}), **env_vars}

    logger.header(f"Noust Deployment: {chosen.title}")
    logger.key_value("Domain", domain)
    logger.key_value("Recipe", chosen.name)
    logger.key_value("Type", chosen.app_type)
    logger.key_value("SSL", "Yes" if ssl else "No")
    logger.blank()

    plan = plan_recipe(
        chosen.name,
        domain,
        port=port,
        ssl=ssl,
        env_overrides=overrides,
        logger=logger,
        install_requirements=install_requirements,
        database=database,
    )
    logger.key_value("Source", plan.source)
    deployer = get_deployer(plan.app_type, verbose=logger.verbose)
    deployer.configure(
        domain=domain,
        port=port,
        webserver=webserver,
        ssl=ssl,
        package_manager=package_manager,
        include_www=www,
        replace_existing=replace_existing,
        **plan.configure_arguments(),
    )
    try:
        deployer.deploy()
    except NoustError as exc:
        keep_after_failed_deploy(plan, exc, logger=logger)
        raise

    notes = finish_recipe(plan, logger=logger, default_backups=default_backups)
    if notes:
        logger.blank()
        logger.info("Next steps:")
        for note in notes:
            logger.info(f"  {note}")
    return 0


def _create_monorepo(
    *,
    logger: Logger,
    domain: str,
    source: str,
    webserver: str,
    ssl: bool,
    branch: str | None,
    env_vars: dict[str, str],
    subdomains: tuple[str, ...],
    workspaces: tuple[str, ...],
    skip_database: bool,
    replace_existing: bool = False,
) -> int:
    """
    Deploy every deployable workspace of a monorepo.

    Args:
        logger: Logger of the current command.
        domain: Validated domain.
        source: Git URL or local directory.
        webserver: ``nginx`` or ``apache``.
        ssl: Request certificates.
        branch: Git branch to deploy.
        env_vars: Environment variables for the deployment.
        subdomains: ``app:subdomain`` mappings.
        workspaces: Workspaces to deploy; empty means all.
        skip_database: Skip database provisioning.
        replace_existing: Deploy into a directory that already holds files.

    Returns:
        Exit code.

    Raises:
        NoustError: When a deployment step fails.
    """
    subdomain_overrides: dict[str, str] = {}
    for mapping in subdomains:
        if ":" in mapping:
            app_name, subdomain = mapping.split(":", 1)
            subdomain_overrides[app_name] = subdomain
        else:
            logger.warning(f"Invalid subdomain mapping: {mapping} (expected app:subdomain)")

    deployer = MonorepoDeployer(verbose=logger.verbose)
    deployer.configure(
        domain=domain,
        source=source,
        webserver=webserver,
        ssl=ssl,
        branch=branch,
        env_vars=env_vars,
        subdomain_overrides=subdomain_overrides,
        workspace_filter=list(workspaces) or None,
        skip_database=skip_database,
        replace_existing=replace_existing,
    )
    deployer.deploy()

    return 0


def _create_docker_compose(
    *,
    logger: Logger,
    domain: str,
    source: str,
    webserver: str,
    ssl: bool,
    branch: str | None,
    env_vars: dict[str, str],
    compose_file: str | None,
    compose_profiles: tuple[str, ...],
    port: int | None,
    replace_existing: bool = False,
) -> int:
    """
    Deploy a Docker Compose project.

    Args:
        logger: Logger of the current command.
        domain: Validated domain.
        source: Git URL or local directory.
        webserver: ``nginx`` or ``apache``.
        ssl: Request a certificate.
        branch: Git branch to deploy.
        env_vars: Environment variables for the deployment.
        compose_file: Compose file to use, relative to the project.
        compose_profiles: Compose profiles to activate.
        port: Port the proxy forwards to.
        replace_existing: Deploy into a directory that already holds files.

    Returns:
        Exit code.

    Raises:
        NoustError: When a deployment step fails.
    """
    deployer = DockerComposeDeployer(verbose=logger.verbose)
    deployer.configure(
        domain=domain,
        source=source,
        webserver=webserver,
        ssl=ssl,
        branch=branch,
        env_vars=env_vars,
        compose_file=compose_file,
        compose_profiles=list(compose_profiles) or None,
        port=port,
        replace_existing=replace_existing,
    )
    deployer.deploy()

    return 0


def _app_summary(app: Any, current: Any) -> dict[str, Any]:
    """
    Build the JSON entry for one application, from the same data the table shows.

    Args:
        app: Store row for the application.
        current: Live state, as :func:`~noust.core.app_state.resolve_states`
            reports it.

    Returns:
        A JSON-safe mapping: domain, type, status, whether it needs attention,
        why when it does, port and whether SSL is enabled.
    """
    return {
        "domain": app.domain,
        "type": app.app_type,
        "status": current.label,
        "healthy": current.healthy,
        "detail": current.detail or None,
        "port": app.port,
        "ssl_enabled": bool(app.ssl_enabled),
    }


def _list_apps(
    logger: Logger,
    *,
    json_output: bool = False,
    team: str | None = None,
    project: str | None = None,
    environment: str | None = None,
    tag: str | None = None,
) -> int:
    """
    Show every deployed application.

    Args:
        logger: Logger of the current command.
        json_output: Print ``{"items": [...]}`` instead of a table.
        team: Only this team's applications.
        project: Only the projects with this slug.
        environment: Only the environments with this slug.
        tag: Only the applications that carry this tag.

    Returns:
        Exit code.
    """
    store = get_store()
    # The CLI is the instance's own: it lists everything unless asked for a team's.
    if team or project or environment:
        apps = ScopedStore(Scope.everything(), store).list_apps(
            project=project, environment=environment, team=team
        )
    else:
        apps = store.list_apps()
    if tag is not None:
        wanted = validate_tag(tag)
        tagged = AppTags(store).by_application()
        apps = [app for app in apps if wanted in tagged.get(app.domain, [])]
    # Where each lives is shown once there is more than one place to live: a
    # server nobody organised in teams reads exactly as it did.
    owners = (
        TeamStore(store).owner_labels() if _organised(store, team, project, environment) else None
    )

    if not apps:
        if json_output:
            click.echo(json.dumps({"items": []}))
            return 0
        logger.header("Deployed Applications")
        logger.info("No applications deployed")
        logger.blank()
        logger.info("Deploy an application with:")
        logger.info("  noust deploy -d example.com -s https://github.com/user/repo")
        return 0

    # Asked of systemd and of the port, not read from the status column. That
    # column is written at deploy time and never again, so it called everything
    # Running while health, which did ask, reported half of them stopped.
    states = resolve_states(apps, ServiceManager(verbose=logger.verbose))

    if json_output:
        items = [_app_summary(app, states[app.domain]) for app in apps]
        if owners is not None:
            for item, app in zip(items, apps, strict=True):
                item["team"], item["project"], item["environment"] = owners.of(
                    app.project_id, app.environment_id
                )
        click.echo(json.dumps({"items": items}))
        return 0

    logger.header("Deployed Applications")

    headers = ["Domain", "Type", "Status", "Port", "SSL"]
    justify = ["left", "left", "left", "right", "left"]
    if owners is not None:
        headers.append("Project")
        justify.append("left")
    rows = []

    for app in apps:
        current = states[app.domain]
        row = [
            styled(app.domain, "bold"),
            app.app_type,
            state(current.label),
            styled(app.port, "") if app.port else state("static"),
            state("yes") if app.ssl_enabled else state("no"),
        ]
        if owners is not None:
            where = owners.of(app.project_id, app.environment_id)
            row.append("/".join(part for part in where if part) or "-")
        rows.append(row)

    logger.table(headers, rows, justify=justify)

    running = sum(1 for s in states.values() if s.label == RUNNING)
    static = sum(1 for s in states.values() if s.label == STATIC)
    outside = sum(1 for s in states.values() if s.label == RUNNING_UNMANAGED)
    unhealthy = [(domain, s) for domain, s in states.items() if not s.healthy]

    logger.blank()
    counts = f"{running} running, {static} static"
    if outside:
        counts += f", {outside} running outside Noust"
    logger.info(f"Total: {len(apps)} apps ({counts})")

    # The reason belongs next to the list. Making the operator run a second
    # command to find out why something says Stopped is how the contradiction
    # between these two commands went unnoticed for as long as it did.
    if unhealthy:
        logger.blank()
        logger.warning(f"{len(unhealthy)} need attention:")
        for domain, current in unhealthy:
            logger.list_item(f"{domain} - {current.detail or current.label.lower()}")

    return 0


def _organised(store: Any, team: str | None, project: str | None, environment: str | None) -> bool:
    """
    Whether this server is arranged in more than the default team, or asked to be.

    Args:
        store: The store.
        team: The ``--team`` filter.
        project: The ``--project`` filter.
        environment: The ``--env`` filter.

    Returns:
        True when the list should say where each application lives.
    """
    return bool(team or project or environment) or len(TeamStore(store).list_teams()) > 1


def _show_status(domain: str, logger: Logger, *, json_output: bool = False) -> int:
    """
    Show what is known about one application.

    Args:
        domain: Application domain.
        logger: Logger of the current command.
        json_output: Print ``{"app": {...}}`` instead of a key/value report.

    Returns:
        Exit code.

    Raises:
        NoustError: When the domain is not a valid domain.
    """
    service_manager = ServiceManager(verbose=logger.verbose)
    store = get_store()

    domain = validate_domain(domain)
    app_name = domain_to_app_name(domain)

    app_data = store.get_app_with_relations(domain)

    if not app_data or not app_data["app"]:
        # An app deployed before the store existed is still a real app; report
        # what systemd knows rather than claiming it is missing.
        status = service_manager.get_status(app_name)
        if not status["exists"]:
            logger.warning(f"Application not found: {domain}")
            return 1

        if json_output:
            click.echo(
                json.dumps(
                    {
                        "app": {
                            "domain": domain,
                            "legacy": True,
                            "service": status["name"],
                            "active": bool(status["active"]),
                            "enabled": bool(status["enabled"]),
                        }
                    }
                )
            )
            return 0

        logger.header(f"Status: {domain}")
        logger.warning("Legacy app (not in store)")
        logger.key_value("Service", status["name"])
        logger.key_value("Active", "Yes" if status["active"] else "No")
        logger.key_value("Enabled", "Yes" if status["enabled"] else "No")
        return 0

    app = app_data["app"]
    site = app_data["site"]
    service = app_data["service"]
    databases = app_data["databases"]

    if json_output:
        systemd_status = service_manager.get_status(app_name) if service else {}
        click.echo(
            json.dumps(
                {
                    "app": {
                        "domain": domain,
                        "type": app.app_type,
                        "status": app.status,
                        "path": app.app_path,
                        "is_static": bool(app.is_static),
                        "port": app.port,
                        "source": app.source,
                        "branch": app.branch,
                        "deployed_at": app.deployed_at,
                        "site": {
                            "webserver": site.webserver,
                            "ssl_enabled": bool(site.ssl_enabled),
                            "config_path": site.config_path,
                        }
                        if site
                        else None,
                        "service": {
                            "name": service.name,
                            "active": bool(systemd_status.get("active")),
                            "enabled": bool(systemd_status.get("enabled")),
                            "pid": systemd_status.get("pid"),
                            "uptime": systemd_status.get("uptime"),
                        }
                        if service
                        else None,
                        "databases": [{"engine": db.engine, "name": db.name} for db in databases],
                    }
                }
            )
        )
        return 0

    logger.header(f"Status: {domain}")

    logger.key_value("Type", app.app_type)
    logger.key_value("Status", app.status)
    logger.key_value("Path", app.app_path)
    logger.key_value("Static", "Yes" if app.is_static else "No")

    if app.port:
        logger.key_value("Port", str(app.port))

    if app.source:
        logger.key_value("Source", app.source)
        if app.branch:
            logger.key_value("Branch", app.branch)

    if app.deployed_at:
        logger.key_value("Deployed", app.deployed_at)

    if site:
        logger.blank()
        logger.info("Site Configuration:")
        logger.key_value("  Web Server", site.webserver)
        logger.key_value("  SSL", "Yes" if site.ssl_enabled else "No")
        logger.key_value("  Config", site.config_path)

    if service:
        logger.blank()
        logger.info("Service:")
        logger.key_value("  Name", service.name)

        systemd_status = service_manager.get_status(app_name)
        logger.key_value("  Active", "Yes" if systemd_status.get("active") else "No")
        logger.key_value("  Enabled", "Yes" if systemd_status.get("enabled") else "No")

        if systemd_status.get("pid"):
            logger.key_value("  PID", systemd_status["pid"])
        if systemd_status.get("uptime"):
            logger.key_value("  Started", systemd_status["uptime"])

    if databases:
        logger.blank()
        logger.info(f"Databases ({len(databases)}):")
        for db in databases:
            logger.key_value(f"  {db.engine}", db.name)

    return 0


def _control_service(domain: str, action: str, logger: Logger) -> int:
    """
    Start, stop or restart the service behind an application.

    Args:
        domain: Application domain.
        action: ``start``, ``stop`` or ``restart``.
        logger: Logger of the current command.

    Returns:
        Exit code.

    Raises:
        NoustError: When the domain is invalid or systemd refuses the operation.
    """
    present, past = _SERVICE_VERBS[action]

    store = get_store()
    domain = validate_domain(domain)
    app_name = domain_to_app_name(domain)

    app = store.get_app(domain)
    if app and is_php_fpm(app):
        # Stored as static, but its pool runs it: the pool is what starts,
        # stops and restarts.
        logger.info(f"{present} {domain}...")
        logger.info(control_pool(app, action, logger=logger))
        logger.success(f"Application {past}: {domain}")
        return 0
    if app and app.is_static:
        logger.info(f"Static application - no service to {action}: {domain}")
        return 0

    service_manager = ServiceManager(verbose=logger.verbose)

    if not service_manager.unit_exists(app_name):
        logger.warning(f"Service not found for: {domain}")
        logger.info("This may be a static application or the service was not created")
        return 1

    operations = {
        "start": service_manager.start,
        "stop": service_manager.stop,
        "restart": service_manager.restart,
    }

    logger.info(f"{present} {domain}...")
    operations[action](app_name)
    # The processes that belong to it (a queue worker, a scheduler) go with it.
    for unit in [
        *ProcessManager(services=service_manager).control(domain, action),
        *ReplicaManager(services=service_manager).control(domain, action),
    ]:
        logger.substep(f"{past.capitalize()} {unit}")
    logger.success(f"Application {past}: {domain}")

    return 0


def _can_ask() -> bool:
    """
    Report whether someone is at a terminal to answer a question.

    Returns:
        True when standard input is a terminal.
    """
    return sys.stdin.isatty()


def _update_app(
    domain: str,
    *,
    logger: Logger,
    source: str | None = None,
    branch: str | None = None,
    commit: str | None = None,
    tag: str | None = None,
    package_manager: str = "auto",
    force: bool = False,
    schema_changed_ok: bool = False,
    no_cache: bool = False,
    frozen_ok: bool = False,
    build_only: bool = False,
) -> int:
    """
    Rebuild a deployed application from its source, then restart it.

    The sequence itself is :func:`noust.deployers.lifecycle.update_app`, shared
    with the panel and the git webhook; this only presents it.

    A plain update first asks the remote whether the branch has anything the
    live build lacks (:func:`~noust.deployers.lifecycle.check_upstream`). When
    it does not, a terminal is asked whether to rebuild anyway; a script, with
    no one to ask, rebuilds and says so. ``force`` skips the question.

    Args:
        domain: Application domain.
        logger: Logger of the current command.
        source: Fetch from this source instead of the recorded one.
        branch: Git branch to update from.
        commit: Deploy this commit instead of the head of the branch.
        tag: Deploy the commit this tag points at.
        package_manager: Node package manager, or ``auto``.
        force: Rebuild without asking when there is nothing new.
        schema_changed_ok: Rebuild an earlier commit even past deployments
            that changed the database schema.
        no_cache: Build from scratch: no package manager cache, no
            dependencies from the release that serves, no image layer cache.
            It asks to rebuild what is live, so it is never "nothing new".
        frozen_ok: Deploy although deploys are frozen for the application.
        build_only: Build the release and leave it waiting, not serving
            (:func:`noust.deployers.staged.stage_build`).

    Returns:
        Exit code.

    Raises:
        NoustError: When the application is unknown or a step fails.
    """
    if build_only:
        from noust.deployers.staged import stage_build

        logger.header(f"Building without activating: {domain}")
        logger.blank()
        stage_build(
            domain,
            commit=commit,
            branch=branch,
            package_manager=package_manager,
            trigger=DeploymentTrigger.CLI.value,
            on_step=logger.substep,
            logger=logger,
            verbose=logger.verbose,
            no_cache=no_cache,
        )
        return 0
    # A commit, a tag or a new source is explicit about what to build; only a
    # plain update can be "the same thing again".
    if commit is None and source is None and tag is None and not no_cache:
        upstream = check_upstream(domain, branch=branch, logger=logger)
        if upstream is not None and not upstream.has_new_commits:
            logger.info(upstream.summary)
            if force:
                logger.info("Rebuilding it anyway (--force)")
            elif _can_ask():
                logger.info(NOTHING_NEW_HINT)
                if not click.confirm("Rebuild it anyway?", default=False):
                    logger.info("Nothing to do")
                    return 0
            else:
                logger.info("Not a terminal, so nobody to ask: rebuilding it anyway")

    logger.header(
        f"Updating: {domain}" + (f" at {commit}" if commit else "") + (f" to {tag}" if tag else "")
    )
    logger.blank()

    outcome = update_app(
        domain,
        source=source,
        branch=branch,
        commit=commit,
        tag=tag,
        package_manager=package_manager,
        trigger=DeploymentTrigger.CLI.value,
        on_phase=logger.step,
        on_step=logger.substep,
        logger=logger,
        verbose=logger.verbose,
        schema_changed_ok=schema_changed_ok,
        no_cache=no_cache,
        frozen_ok=frozen_ok,
    )
    _report_hooks(logger, outcome)

    if outcome.is_static:
        logger.success(f"Application updated successfully: {outcome.domain}")
        logger.blank()
        logger.key_value("Type", outcome.app_type)
        logger.key_value("Package Manager", outcome.package_manager)
        return 0

    if not outcome.restarted:
        logger.warning("No service found to restart - the application may need to be redeployed")
        logger.info(f"Try: noust create -d {outcome.domain}")
        return 0

    if not outcome.active:
        logger.warning("Application restarted but may not be running correctly")
        logger.info(f"Check logs with: noust logs {outcome.domain}")
        return 0

    logger.success(f"Application updated successfully: {outcome.domain}")
    logger.blank()
    logger.key_value("Status", "Running")
    for name in outcome.restarted:
        logger.key_value("Restarted", name)
    logger.key_value("Package Manager", outcome.package_manager)
    if outcome.prisma_updated:
        logger.key_value("Prisma", "Updated")
    return 0


def _report_hooks(logger: Logger, outcome: AppUpdate) -> None:
    """
    Say what the deploy hooks did: the warnings first, then each hook.

    Args:
        logger: Logger of the current command.
        outcome: What the update did.
    """
    for entry in outcome.hooks:
        label = "Prisma migration" if entry.get("automatic") else f"Hook ({entry.get('phase')})"
        state = "ok" if entry.get("ok") else f"failed, exit {entry.get('exit_code')}"
        logger.key_value(label, f"{entry.get('run')}: {state}")
    if outcome.schema_changed:
        logger.key_value("Database schema", "changed by this update")
    if outcome.warnings:
        logger.warning(f"Deployed with warnings: {outcome.warnings}")


def _delete_app(
    domain: str,
    *,
    logger: Logger,
    force: bool = False,
    keep_files: bool = False,
    dry_run: bool = False,
    remove_adopted_directory: str | None = None,
    keep_certificate: bool = False,
    remove_volumes: bool = False,
    remove_databases: bool = False,
) -> int:
    """
    Remove an application, its service, its site and its certificate.

    Args:
        domain: Application domain.
        logger: Logger of the current command.
        force: Do not ask for confirmation. Without it, the application's name is typed to confirm.
        keep_files: Leave the application directory on disk.
        dry_run: Only report what would be removed.
        keep_certificate: Leave the domain's certificate in place.
        remove_volumes: Also remove the Docker volumes of a stack or a container, which hold its
            data.
        remove_databases: Also drop the databases that belong to the application and that nothing
            else uses, each after a last dump.
        remove_adopted_directory: The application's directory, named, when it
            is outside Noust's apps directory (an adopted stack): only then is
            it removed with the files.

    Returns:
        Exit code.

    Raises:
        NoustError: When the domain is invalid.
        ValidationError: The directory named is not the application's.
    """
    config = Config()
    store = get_store()

    domain = validate_domain(domain)
    app_name = domain_to_app_name(domain)

    app = store.get_app(domain)
    # The store holds the real path: an adopted stack runs where it was found.
    app_path = (
        Path(app.app_path) if app is not None and app.app_path else config.apps_directory / app_name
    )
    app_exists_on_disk = app_path.exists()

    if not app and not app_exists_on_disk:
        logger.warning(f"Application not found: {domain}")
        return 1

    if app is not None and is_adopted(app.app_type):
        return _forget_adopted(
            domain,
            logger=logger,
            force=force,
            dry_run=dry_run,
            remove_adopted_directory=remove_adopted_directory,
        )

    # Asked before the question and before a dry run, so either says what
    # would really happen; the deletion asks again.
    adopted_named = check_adopted_directory(
        domain, app_path, remove_files=not keep_files, named=remove_adopted_directory
    )
    keep_adopted = not keep_files and not adopted_named and not is_in_apps_directory(app_path)

    # The store rows are deleted directly rather than through the runner, so a
    # dry run has to stop here instead of relying on the execution seam.
    if dry_run:
        return _preview_delete(
            domain=domain,
            app_name=app_name,
            app_path=app_path,
            logger=logger,
            keep_files=keep_files or keep_adopted,
            registered=app is not None,
            app_exists_on_disk=app_exists_on_disk,
            keep_certificate=keep_certificate,
            remove_volumes=remove_volumes,
            remove_databases=remove_databases,
        )

    if not force:
        consequences = [f"the {app_name} service", "its site configuration"]
        consequences.append("(its certificate stays)" if keep_certificate else "its certificate")
        if not keep_files and not keep_adopted:
            consequences.append(str(app_path))
        if app:
            consequences.append("its database records")
        logger.warning(f"This removes {', '.join(consequences)}.")
        if remove_volumes:
            logger.warning(
                "--remove-volumes: the Docker volumes of the stack or the container go too, "
                "and with them the data in them."
            )
        _say_databases(logger, domain, remove_databases)
        if keep_adopted:
            logger.info(
                f"{app_path} is outside Noust's apps directory and is kept; to remove it too: "
                f"--remove-adopted-directory {app_path}"
            )
        # The name, typed: a stray 'y' does not delete an application.
        typed = click.prompt(
            f"Delete the application {domain}? Type its name to confirm",
            default="",
            show_default=False,
        )
        if typed.strip() != domain:
            logger.info("Aborted: the name did not match")
            return 0

    logger.header(f"Deleting: {domain}")

    # The one deletion, shared with the console's delete job: it takes a
    # Compose stack down (volumes kept), removes every unit of the app, its
    # site, certificate, files and rows, and holds the app's lock.
    outcome = delete_app(
        domain,
        remove_files=not keep_files,
        remove_certificate=not keep_certificate,
        remove_volumes=remove_volumes,
        remove_databases=remove_databases,
        remove_adopted_directory=remove_adopted_directory,
        on_phase=lambda index, total, message: logger.step(index, total, message),
        logger=logger,
    )
    logger.substep(
        f"Certificate deleted: {domain}"
        if outcome.certificate_removed
        else "Certificate kept"
        if keep_certificate
        else "No certificate found"
    )
    if outcome.volumes_removed:
        logger.substep("Docker volumes removed")
    for removed, dump in zip(outcome.databases_removed, outcome.database_dumps, strict=False):
        logger.substep(f"Database dropped: {removed} (last dump: {dump})")
    for note in outcome.kept:
        logger.info(note)
    if outcome.kept_directory is not None:
        logger.key_value("Kept", outcome.kept_directory)
    if outcome.warnings:
        logger.warning("Deleted, except for what is listed above")
        return 1
    logger.success(f"Application deleted: {domain}")

    return 0


def _say_databases(logger: Logger, domain: str, remove_databases: bool) -> None:
    """
    Say what deleting an application does to the databases it uses.

    Args:
        logger: Logger of the current command.
        domain: The application.
        remove_databases: Whether the operator asked for them to go.
    """
    for database in deletion_databases(domain):
        label = f"{database.name} on {database.engine}"
        if database.shared_with:
            logger.info(
                f"Database {label} is also used by {', '.join(database.shared_with)}: kept."
            )
        elif not database.owned:
            logger.info(f"Database {label} is used by {domain} but is not its own: kept.")
        elif remove_databases:
            logger.warning(f"Database {label} belongs to {domain}: dropped after a last dump.")
        else:
            logger.info(
                f"Database {label} belongs to {domain}: kept (--remove-databases drops it)."
            )


def _forget_adopted(
    domain: str,
    *,
    logger: Logger,
    force: bool,
    dry_run: bool,
    remove_adopted_directory: str | None,
) -> int:
    """
    Stop managing a site Noust adopted, saying that nothing of it is removed.

    Args:
        domain: The adopted application.
        logger: Logger of the current command.
        force: Do not ask for confirmation.
        dry_run: Only report what would be forgotten.
        remove_adopted_directory: What the operator named for removal; refused.

    Returns:
        Exit code.

    Raises:
        ValidationError: The operator asked for the directory to be removed.
    """
    if remove_adopted_directory is not None:
        raise ValidationError(
            f"{domain} is an adopted site: Noust never removes its files",
            details="Deleting it only forgets it. Remove the directory yourself if it should go.",
        )
    record = adoption_of(domain)
    keeps = "its unit, its site, its certificate and its files stay where they are"
    if dry_run:
        logger.info(
            f"Would forget {domain}: its record, its backup schedules and the names Noust "
            f"holds for it; {keeps}."
        )
        return 0
    if not force:
        logger.warning(
            f"This forgets {domain}: its record, its backup schedules and the names Noust holds "
            f"for it. Nothing is removed: {keeps}."
        )
        if not click.confirm(f"Stop managing {domain}?", default=False):
            logger.info("Aborted")
            return 0
    logger.header(f"Forgetting: {domain}")
    outcome = delete_app(
        domain,
        remove_files=False,
        remove_certificate=False,
        on_phase=lambda index, total, message: logger.step(index, total, message),
        logger=logger,
    )
    if outcome.warnings:
        logger.warning("Forgotten, except for what is listed above")
        return 1
    logger.success(
        f"{domain} is no longer managed"
        + (f"; {record.unit}.service keeps running" if record is not None and record.unit else "")
    )
    return 0


def _preview_delete(
    *,
    domain: str,
    app_name: str,
    app_path: Path,
    logger: Logger,
    keep_files: bool,
    registered: bool,
    app_exists_on_disk: bool,
    keep_certificate: bool = False,
    remove_volumes: bool = False,
    remove_databases: bool = False,
) -> int:
    """
    Report what deleting an application would remove.

    Args:
        domain: Validated domain.
        app_name: Directory and service name of the application.
        app_path: Directory holding the application.
        logger: Logger of the current command.
        keep_files: Whether the files would be kept.
        registered: Whether the application has store records.
        app_exists_on_disk: Whether the application directory exists.
        keep_certificate: Whether the certificate would be kept.
        remove_volumes: Whether the Docker volumes would go.
        remove_databases: Whether its own databases would be dropped.

    Returns:
        Exit code.
    """
    logger.header(f"Dry-run: Would delete {domain}")
    logger.blank()
    logger.info("The following actions would be performed:")
    logger.blank()

    service_manager = ServiceManager(verbose=logger.verbose)
    try:
        status = service_manager.get_status(app_name)
        if status.get("exists"):
            logger.key_value("Stop and remove service", app_name)
    except ServiceError as e:
        logger.debug(f"Could not query service {app_name}: {e}")

    nginx = NginxManager(verbose=logger.verbose)
    if nginx.site_exists(domain):
        logger.key_value("Remove nginx config", f"/etc/nginx/sites-available/{domain}")

    apache = ApacheManager(verbose=logger.verbose)
    if apache.site_exists(domain):
        logger.key_value("Remove apache config", f"/etc/apache2/sites-available/{domain}.conf")

    if app_exists_on_disk:
        if keep_files:
            logger.key_value("Keep app files", str(app_path))
        else:
            logger.key_value("Remove app files", str(app_path))

    logger.key_value("Certificate", "keep" if keep_certificate else "remove")
    if remove_volumes:
        logger.key_value("Remove Docker volumes", "the stack's or the container's named volumes")
    if registered:
        _say_databases(logger, domain, remove_databases)
        logger.key_value("Remove from database", f"App, Site, and Service records for {domain}")

    logger.blank()
    logger.info("Run without --dry-run to execute these actions.")
    return 0


def _print_or_emit_logs(text: str, *, json_output: bool) -> None:
    """
    Show a block of log text the way the caller asked for it.

    Args:
        text: The log output, verbatim.
        json_output: Print ``{"lines": [...]}`` instead of the raw text.
    """
    if json_output:
        click.echo(json.dumps({"lines": text.splitlines()}))
    else:
        print(text)


def _show_logs(
    domain: str,
    *,
    logger: Logger,
    follow: bool = False,
    lines: int = 50,
    json_output: bool = False,
) -> int:
    """
    Print the recent log of an application.

    Args:
        domain: Application domain.
        logger: Logger of the current command.
        follow: Keep streaming until interrupted.
        lines: How many recent lines to show.
        json_output: Print ``{"lines": [...]}`` instead of raw text. Refused
            together with ``follow``: a stream has no single payload to print.

    Returns:
        Exit code.

    Raises:
        NoustError: When the domain is invalid.
    """
    service_manager = ServiceManager(verbose=logger.verbose)

    domain = validate_domain(domain)
    app_name = domain_to_app_name(domain)

    store = get_store()
    app = store.get_app(domain)

    if app and is_compose_backed(app.app_type):
        # Built by the deployer, like every other compose command: the file
        # the unit names and the project the store pins (an adopted stack's
        # need not be its directory's name, and a guess shows no logs).
        deployer = stack_deployer(app)
        app_path = deployer.app_path
        try:
            deployer._discover_compose_file()
        except DeploymentError as exc:
            logger.debug(f"No compose file found, asking Compose in {app_path}: {exc}")
        if follow:
            cmd = deployer._compose("logs", "--tail", str(lines))
            cmd.append("-f")
            _follow(cmd, cwd=app_path)
        else:
            _print_or_emit_logs(deployer.logs(lines=lines), json_output=json_output)
        return 0

    # Resolves both the legacy wasm-* unit names and the current ones.
    service_name = service_manager._resolve_service_name(app_name)

    if follow:
        followed = _follow(
            [
                "journalctl",
                "-u",
                f"{service_name}.service",
                "-f",
                "-n",
                str(lines),
            ]
        )
        if followed is not None and not followed.success and not followed.timed_out:
            logger.error(f"Failed to follow the journal: {followed.stderr}")
            return 1
    else:
        logs = service_manager.logs(app_name, lines=lines)
        _print_or_emit_logs(logs, json_output=json_output)

    return 0


# ---------------------------------------------------------------------------
# argparse-shaped entry point, still used by noust.cli.interactive
# ---------------------------------------------------------------------------


def handle_webapp(args: Namespace) -> int:
    """
    Handle webapp commands.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    action = args.action

    handlers = {
        "create": _handle_create,
        "new": _handle_create,
        "deploy": _handle_create,
        "list": _handle_list,
        "ls": _handle_list,
        "status": _handle_status,
        "info": _handle_status,
        "restart": _handle_restart,
        "stop": _handle_stop,
        "start": _handle_start,
        "update": _handle_update,
        "upgrade": _handle_update,
        "delete": _handle_delete,
        "remove": _handle_delete,
        "rm": _handle_delete,
        "logs": _handle_logs,
    }

    handler = handlers.get(action)
    if not handler:
        print(f"Unknown action: {action}", file=sys.stderr)
        return 1

    try:
        return handler(args)
    except NoustError as e:
        logger = Logger(verbose=args.verbose)
        logger.error(e.message)
        if e.details:
            # Print details preserving formatting (for SSH guidance, command output, etc.)
            logger.blank()
            # Limit output to avoid flooding the terminal
            detail_lines = e.details.split("\n")
            max_lines = 50 if args.verbose else 20
            for line in detail_lines[:max_lines]:
                print(f"  {line}")
            if len(detail_lines) > max_lines:
                print(
                    f"  ... ({len(detail_lines) - max_lines} more lines, use --verbose for full output)"
                )
            logger.blank()
        return 1
    except Exception as e:
        logger = Logger(verbose=args.verbose)
        logger.error(f"Unexpected error: {e}")
        logger.debug(f"Unhandled {type(e).__name__} in webapp {action}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def _handle_create(args: Namespace) -> int:
    """
    Handle webapp create command.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    env_file = getattr(args, "env_file", None)
    return _create_app(
        logger=Logger(verbose=args.verbose),
        domain=args.domain,
        source=args.source,
        app_type=args.type,
        port=args.port,
        webserver=args.webserver,
        branch=args.branch,
        ssl=not args.no_ssl,
        www=getattr(args, "www", False),
        env_file=Path(env_file) if env_file else None,
        package_manager=getattr(args, "package_manager", "auto") or "auto",
        subdomains=tuple(getattr(args, "subdomains", None) or ()),
        workspaces=tuple(getattr(args, "workspaces", None) or ()),
        skip_database=getattr(args, "no_database", False),
        compose_file=getattr(args, "compose_file", None),
        compose_profiles=tuple(getattr(args, "compose_profiles", None) or ()),
        layout=getattr(args, "layout", None),
        persist=tuple(getattr(args, "persist", None) or ()),
        replace_existing=getattr(args, "force", False),
    )


def _handle_list(args: Namespace) -> int:
    """
    Handle webapp list command.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _list_apps(Logger(verbose=args.verbose), json_output=getattr(args, "json", False))


def _handle_status(args: Namespace) -> int:
    """
    Handle webapp status command.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _show_status(
        args.domain, Logger(verbose=args.verbose), json_output=getattr(args, "json", False)
    )


def _handle_restart(args: Namespace) -> int:
    """
    Handle webapp restart command.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _control_service(args.domain, "restart", Logger(verbose=args.verbose))


def _handle_stop(args: Namespace) -> int:
    """
    Handle webapp stop command.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _control_service(args.domain, "stop", Logger(verbose=args.verbose))


def _handle_start(args: Namespace) -> int:
    """
    Handle webapp start command.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _control_service(args.domain, "start", Logger(verbose=args.verbose))


def _handle_update(args: Namespace) -> int:
    """
    Handle webapp update command.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _update_app(
        args.domain,
        logger=Logger(verbose=args.verbose),
        source=getattr(args, "source", None),
        branch=getattr(args, "branch", None),
        commit=getattr(args, "commit", None),
        tag=getattr(args, "tag", None),
        package_manager=getattr(args, "package_manager", "auto") or "auto",
        force=bool(getattr(args, "force", False)),
        no_cache=bool(getattr(args, "no_cache", False)),
    )


def _handle_delete(args: Namespace) -> int:
    """
    Handle webapp delete command.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _delete_app(
        args.domain,
        logger=Logger(verbose=args.verbose),
        force=args.force,
        keep_files=args.keep_files,
        dry_run=getattr(args, "dry_run", False),
        remove_adopted_directory=getattr(args, "remove_adopted_directory", None),
    )


def _handle_logs(args: Namespace) -> int:
    """
    Handle webapp logs command.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _show_logs(
        args.domain,
        logger=Logger(verbose=args.verbose),
        follow=args.follow,
        lines=args.lines,
        json_output=getattr(args, "json", False),
    )


# ---------------------------------------------------------------------------
# Click command tree
# ---------------------------------------------------------------------------


def _exit(code: int) -> None:
    """
    End the command with a handler's exit code.

    Click ignores what a command callback returns, so a non-zero code has to go
    through the context or every failure would report success to the shell.

    Args:
        code: Exit code. Zero returns normally.
    """
    if code:
        click.get_current_context().exit(code)


def _read_build_file(path: Path) -> str:
    """
    Read and check the build block a new application is created with.

    Args:
        path: The YAML file given to ``--build-file``.

    Returns:
        Its text, once it parses as an operator's build block: a refusal names
        the field, before anything is fetched.

    Raises:
        ValidationError: The file cannot be read, is too large, or is not a
            valid build block.
    """
    from noust.deployers.helpers.project_file import parse_build_document

    limit = 64 * 1024
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"Cannot read {path}: {exc}", details="Give a readable UTF-8 YAML file.", field="file"
        ) from exc
    if len(text) > limit:
        raise ValidationError(
            f"{path} is larger than {limit // 1024} KiB",
            details="A build block is a few lines.",
            field="file",
        )
    parse_build_document(text, source="operator")
    return text


@click.group(cls=NoustGroup)
def cli() -> None:
    """Commands that act on a deployed application."""


@cli.command()
@click.option("-d", "--domain", required=True, help="Domain the application is served on.")
@click.option(
    "-s",
    "--source",
    help="Git URL, archive URL or directory to deploy from. Not with --recipe.",
)
@click.option(
    "--recipe",
    metavar="NAME",
    help="Deploy a known application from its recipe (see 'noust recipe list'): its "
    "source, type, database and settings come from the recipe.",
)
@click.option(
    "--env",
    "env_pairs",
    metavar="NAME=VALUE",
    multiple=True,
    help="Set a variable of the application environment, over --env-file and a "
    "recipe's own. Repeat for several.",
)
@click.option(
    "-t",
    "--type",
    "app_type",
    type=click.Choice(APP_TYPES),
    default="auto",
    show_default=True,
    help="Application type. Detected from the source when left on auto.",
)
@click.option(
    "-p",
    "--port",
    type=click.INT,
    help="Port the application listens on. A free one is chosen when omitted.",
)
@click.option(
    "-w",
    "--webserver",
    type=click.Choice(WEBSERVERS),
    default="nginx",
    show_default=True,
    help="Web server that fronts the application.",
)
@click.option("-b", "--branch", help="Git branch to deploy.")
@click.option(
    "--follow-tags",
    "follow_tags",
    metavar="PATTERN",
    help="Deploy the newest git tag that matches this glob (such as 'v*') and keep deploying "
    "new ones, from a release or a tag push, instead of following a branch.",
)
@click.option("--no-ssl", is_flag=True, help="Serve over plain HTTP, without a certificate.")
@click.option(
    "--www",
    is_flag=True,
    help="Also answer on www.<domain>, redirecting it to the domain (see 'noust domain').",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="File of KEY=value lines to install as the application environment.",
)
@click.option(
    "--package-manager",
    "--pm",
    "package_manager",
    type=click.Choice(PACKAGE_MANAGERS),
    default="auto",
    show_default=True,
    help="Node package manager. Detected from the lockfile when left on auto.",
)
@click.option(
    "--subdomains",
    metavar="APP:SUBDOMAIN",
    multiple=True,
    help="Serve a monorepo app on this subdomain. Repeat once per app.",
)
@click.option(
    "--workspaces",
    metavar="NAME",
    multiple=True,
    help="Deploy only this monorepo workspace. Repeat to select several.",
)
@click.option("--no-database", is_flag=True, help="Skip database provisioning for a monorepo.")
@click.option(
    "--database",
    "database_spec",
    metavar="ENGINE[:NAME]",
    help="Create a database for the application before its first build, owned by an "
    "account of its own, with its connection string in the environment (DATABASE_URL, "
    "REDIS_URL for Redis). NAME defaults to one derived from the domain. Not with --recipe.",
)
@click.option(
    "--database-version",
    metavar="VERSION",
    help="The version of the --database engine, such as 17 or 11.4. The installed one when "
    "omitted; one the server does not run is refused with what it runs.",
)
@click.option(
    "--install-database",
    is_flag=True,
    help="Install the --database engine, in --database-version, when it is not installed. "
    "Installs packages: the same as 'noust db install'.",
)
@click.option(
    "--database-instance",
    metavar="NAME",
    help="Make the --database on this instance Noust runs ('noust db instance list') instead "
    "of the host's engine. One is also chosen by itself when --database-version is what an "
    "instance runs and the host does not.",
)
@click.option(
    "--compose-file",
    help="Compose file to use, relative to the project. Detected when omitted.",
)
@click.option(
    "--compose-profiles",
    metavar="PROFILE",
    multiple=True,
    help="Activate this Docker Compose profile. Repeat to activate several.",
)
@click.option(
    "--layout",
    type=click.Choice(LAYOUTS),
    help="Build every deploy as a release behind a health gate, or in place. "
    "Defaults to the server's deploy.layout.",
)
@click.option(
    "--persist",
    metavar="PATH",
    multiple=True,
    help="Keep this path, relative to the app, in shared/ across releases. Repeat for several.",
)
@click.option(
    "--build-file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="A YAML file holding 'build:' (base_directory, install, build, start, publish_directory, "
    "runtime): the operator's block for this application, which replaces the repository's "
    "noust.yaml and is kept for every update. How several applications are deployed from one "
    "repository: see docs/build.md.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Deploy into an app directory that already holds files: in place a git checkout "
    "is brought up to date, keeping what git does not track (.env, data), and anything "
    "else is refused, never emptied; on releases a release is added beside them.",
)
@click.option(
    "--no-cache",
    is_flag=True,
    help="Build the first deployment without any cache: images without their layer cache.",
)
@click.option(
    "--registry",
    "registry_logins",
    multiple=True,
    metavar="NAME",
    help="A registry account (noust registry add) to pull private images with, for a Docker "
    "Compose or container application. Repeat it for several.",
)
@container_options
@placed_in_a_project
@global_flags
@pass_context
def create(
    ctx: Context,
    domain: str,
    source: str | None,
    recipe: str | None,
    env_pairs: tuple[str, ...],
    app_type: str,
    port: int | None,
    webserver: str,
    branch: str | None,
    follow_tags: str | None,
    no_ssl: bool,
    www: bool,
    env_file: Path | None,
    package_manager: str,
    subdomains: tuple[str, ...],
    workspaces: tuple[str, ...],
    no_database: bool,
    database_spec: str | None,
    database_version: str | None,
    install_database: bool,
    database_instance: str | None,
    compose_file: str | None,
    compose_profiles: tuple[str, ...],
    layout: str | None,
    persist: tuple[str, ...],
    build_file: Path | None,
    force: bool,
    no_cache: bool,
    registry_logins: tuple[str, ...],
    **container_given: Any,
) -> None:
    """
    Deploy a web application and put it online.

    Fetches the source, builds it, runs it under systemd, publishes it on the
    domain and obtains a certificate for it. With --recipe, the source, the
    type, the database and the settings come from the recipe. With --image, a
    container from a registry is deployed instead, and needs no source.
    """
    env_vars = _parse_env_pairs(env_pairs)
    container = changes_from_options(**container_given)
    if container:
        if recipe is not None:
            raise click.UsageError("Container options cannot be used with --recipe.")
        if app_type not in ("auto", "container"):
            raise click.UsageError(
                f"Container options do not apply to --type {app_type}: use --type container."
            )
        app_type = "container"
        if "image" in container:
            if source:
                raise click.UsageError("--image is the source of a container: leave -s out.")
            source = container["image"]
    database = _parse_database(database_spec) if database_spec is not None else None
    if recipe is not None and database is not None:
        raise click.UsageError("--database cannot be used with --recipe: the recipe has its own.")
    if (database_version or install_database or database_instance) and database is None:
        raise click.UsageError(
            "--database-version, --database-instance and --install-database need --database."
        )
    if follow_tags is not None:
        if branch is not None or recipe is not None:
            raise click.UsageError(
                "--follow-tags says what to deploy: not with --branch or --recipe."
            )
        if not source:
            raise click.UsageError("Missing option '-s' / '--source'.")
        # The tag becomes the ref the deployer builds; a branch is what it
        # would have followed otherwise.
        branch = _newest_followed_tag(source, follow_tags)
    build_document = _read_build_file(build_file) if build_file is not None else None
    if build_document is not None and recipe is not None:
        raise click.UsageError(
            "--build-file cannot be used with --recipe: the recipe pins its own."
        )
    if recipe is not None:
        _exit(
            _create_from_recipe(
                logger=ctx.logger,
                recipe=recipe,
                domain=domain,
                source=source,
                app_type=app_type,
                branch=branch,
                layout=layout,
                persist=persist,
                port=port,
                webserver=webserver,
                ssl=not no_ssl,
                www=www,
                env_file=env_file,
                env_vars=env_vars,
                package_manager=package_manager,
                replace_existing=force,
            )
        )
        return
    if not source:
        raise click.UsageError(
            "Missing option '-s' / '--source' (or deploy a --recipe, or an --image)."
        )
    _exit(
        _create_app(
            logger=ctx.logger,
            domain=domain,
            source=source,
            env_vars=env_vars or None,
            app_type=app_type,
            port=port,
            webserver=webserver,
            branch=branch,
            ssl=not no_ssl,
            www=www,
            env_file=env_file,
            package_manager=package_manager,
            subdomains=subdomains,
            workspaces=workspaces,
            skip_database=no_database,
            compose_file=compose_file,
            compose_profiles=compose_profiles,
            layout=layout,
            persist=persist,
            replace_existing=force,
            database=database,
            database_version=database_version,
            install_database=install_database,
            database_instance=database_instance,
            container=container or None,
            build_document=build_document,
            no_cache=no_cache,
            registry_logins=registry_logins,
        ),
    )
    # Reached only when the deploy succeeded: _exit leaves on a failure code.
    if follow_tags is not None and branch is not None:
        _follow_tags_after_create(domain, follow_tags, branch)


@cli.command(name="list")
@click.option(
    "--open",
    "open_panel",
    is_flag=True,
    help="Print the panel URL for the app list, opening it if a display is available.",
)
@click.option("--team", default=None, help="Only this team's applications.")
@click.option(
    "--project", default=None, help="Only the applications of the projects with this name."
)
@click.option("--env", "environment", default=None, help="Only those in this environment.")
@click.option("--tag", default=None, help="Only the applications that carry this tag.")
@json_option("Print machine-readable JSON instead of a table.")
@global_flags
@pass_context
def list_apps(
    ctx: Context,
    open_panel: bool,
    team: str | None,
    project: str | None,
    environment: str | None,
    tag: str | None,
) -> None:
    """List the applications deployed on this server."""
    if ctx.json_output and open_panel:
        raise click.UsageError("--json and --open cannot be combined.")
    code = _list_apps(
        ctx.logger,
        json_output=ctx.json_output,
        team=team,
        project=project,
        environment=environment,
        tag=tag,
    )
    if open_panel and code == 0:
        open_in_panel("/apps", logger=ctx.logger)
    _exit(code)


@cli.command()
@click.argument("domain")
@click.option(
    "--open",
    "open_panel",
    is_flag=True,
    help="Print the panel URL for this application, opening it if a display is available.",
)
@json_option("Print machine-readable JSON instead of a table.")
@global_flags
@pass_context
def status(ctx: Context, domain: str, open_panel: bool) -> None:
    """Show how an application is configured and whether it is running."""
    if ctx.json_output and open_panel:
        raise click.UsageError("--json and --open cannot be combined.")
    code = _show_status(domain, ctx.logger, json_output=ctx.json_output)
    if open_panel and code == 0:
        open_in_panel(f"/apps/{domain}", logger=ctx.logger)
    _exit(code)


@cli.command()
@click.argument("domain")
@global_flags
@pass_context
def start(ctx: Context, domain: str) -> None:
    """Start an application that is stopped."""
    _exit(_control_service(domain, "start", ctx.logger))


@cli.command()
@click.argument("domain")
@global_flags
@pass_context
def stop(ctx: Context, domain: str) -> None:
    """Stop an application and leave it stopped."""
    _exit(_control_service(domain, "stop", ctx.logger))


@cli.command()
@click.argument("domain")
@global_flags
@pass_context
def restart(ctx: Context, domain: str) -> None:
    """Restart an application, picking up its current build and environment."""
    _exit(_control_service(domain, "restart", ctx.logger))


@cli.command()
@click.argument("domain")
@click.option("-s", "--source", help="Fetch from this source instead of the recorded one.")
@click.option("-b", "--branch", help="Git branch to update from.")
@click.option(
    "--commit",
    metavar="SHA",
    help="Deploy this commit (full or abbreviated) instead of the head of the branch.",
)
@click.option(
    "--tag",
    metavar="TAG",
    help="Deploy the commit this tag points at. Never an older tag than one already "
    "deployed: use 'noust rollback' for that.",
)
@click.option(
    "--package-manager",
    "--pm",
    "package_manager",
    type=click.Choice(PACKAGE_MANAGERS),
    default="auto",
    show_default=True,
    help="Node package manager. Detected from the lockfile when left on auto.",
)
@click.option(
    "-y",
    "--force",
    is_flag=True,
    help="Rebuild without asking when the branch has nothing new since the live commit.",
)
@click.option(
    "--schema-changed-ok",
    is_flag=True,
    default=False,
    help="With --commit, go back even past deployments that changed the database schema.",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Build from scratch: empty the build cache first, install the dependencies again "
    "instead of taking them from the release that serves, and build images without their "
    "layer cache. Rebuilds what is live even when there is nothing new.",
)
@click.option(
    "--during-freeze",
    is_flag=True,
    default=False,
    help="Deploy although deploys are frozen for the application (noust deploy freeze). "
    "A person's decision, recorded in the audit trail.",
)
@click.option(
    "--build-only",
    is_flag=True,
    default=False,
    help="Build the release and leave it waiting, not serving: nothing is activated, no hook "
    "runs and nothing restarts. Make it serve later, without building it again, with "
    "'noust releases promote'. Only for an application on the releases layout.",
)
@global_flags
@pass_context
def update(
    ctx: Context,
    domain: str,
    source: str | None,
    branch: str | None,
    commit: str | None,
    tag: str | None,
    package_manager: str,
    force: bool,
    schema_changed_ok: bool,
    no_cache: bool,
    during_freeze: bool,
    build_only: bool,
) -> None:
    """
    Pull the latest code, rebuild and restart an application.

    On the in-place layout, a backup is taken first and the service is only
    restarted once the new build succeeded. On the releases layout, there is
    no backup step: the release that was serving stays on disk, the new one
    is built and activated behind a health gate, and a release that does not
    answer is rolled back automatically - the previous release is the way
    back.

    When the branch has no commit since the live one, it asks before
    rebuilding the same commit (a script is not asked and rebuilds); -y
    skips the question. --commit deploys that exact commit: on releases an
    existing release of it is activated, otherwise it is built; in place the
    checkout is put on it, and the next update follows the branch again.
    --tag does the same for the commit a tag points at. An application that
    follows tags (noust app follow-tags) and is updated with none deploys the
    newest tag that matches.
    """
    if tag is not None and (commit is not None or source is not None or branch is not None):
        raise click.UsageError("--tag names exactly what to deploy: not with --commit, -s or -b.")
    if build_only and (tag is not None or source is not None):
        raise click.UsageError(
            "--build-only builds the branch or a --commit: not with --tag or -s."
        )
    _exit(
        _update_app(
            domain,
            logger=ctx.logger,
            source=source,
            branch=branch,
            commit=commit,
            tag=tag,
            package_manager=package_manager,
            force=force,
            schema_changed_ok=schema_changed_ok,
            no_cache=no_cache,
            frozen_ok=during_freeze,
            build_only=build_only,
        ),
    )


@cli.command()
@click.argument("domain")
@click.option("-f", "-y", "--force", is_flag=True, help="Delete without asking for confirmation.")
@click.option("--keep-files", is_flag=True, help="Leave the application directory on disk.")
@click.option(
    "--keep-certificate",
    is_flag=True,
    help="Leave the domain's certificate in place (it is removed with the application otherwise).",
)
@click.option(
    "--remove-volumes",
    is_flag=True,
    help="Also remove the Docker volumes of a Compose stack or a container application: they "
    "hold its data, so they are never removed unless asked for.",
)
@click.option(
    "--remove-databases",
    is_flag=True,
    help="Also drop the databases that belong to the application and that no other application "
    "uses, each after a last dump. Never unless asked for; one it merely uses is kept.",
)
@click.option(
    "--remove-adopted-directory",
    metavar="PATH",
    help="Also remove the application's directory when it is outside Noust's apps directory "
    "(an adopted stack). Name it exactly; without this it is kept.",
)
@global_flags
@pass_context
def delete(
    ctx: Context,
    domain: str,
    force: bool,
    keep_files: bool,
    keep_certificate: bool,
    remove_volumes: bool,
    remove_databases: bool,
    remove_adopted_directory: str | None,
) -> None:
    """
    Delete an application and everything deployed with it.

    Removes the service, the site configuration, the certificate, the
    application directory and the database records; asks for the application's
    name to be typed (-y skips it). The Docker volumes of a stack and the
    databases it owns stay unless --remove-volumes and --remove-databases
    say they go (a database is dumped first, and one another application
    uses is never dropped). A directory outside
    Noust's apps directory, such as an adopted stack's, is kept unless
    --remove-adopted-directory names it. A site adopted with 'noust app
    adopt-site' is only forgotten: its unit, site, certificate and files stay.
    """
    _exit(
        _delete_app(
            domain,
            logger=ctx.logger,
            force=force,
            keep_files=keep_files,
            dry_run=ctx.dry_run,
            remove_adopted_directory=remove_adopted_directory,
            keep_certificate=keep_certificate,
            remove_volumes=remove_volumes,
            remove_databases=remove_databases,
        ),
    )


@cli.command()
@click.argument("domain")
@click.option("-f", "--follow", is_flag=True, help="Keep streaming until you press Ctrl+C.")
@click.option(
    "-n",
    "--lines",
    type=click.INT,
    default=50,
    show_default=True,
    help="How many recent lines to show.",
)
@click.option(
    "--open",
    "open_panel",
    is_flag=True,
    help="Print the panel URL for this application, opening it if a display is available.",
)
@json_option("Print machine-readable JSON instead of a table.")
@global_flags
@pass_context
def logs(ctx: Context, domain: str, follow: bool, lines: int, open_panel: bool) -> None:
    """Show what an application has been writing to its log."""
    if ctx.json_output and open_panel:
        raise click.UsageError("--json and --open cannot be combined.")
    if ctx.json_output and follow:
        raise click.UsageError(
            "--json cannot be combined with --follow: a stream has no single JSON payload."
        )
    code = _show_logs(
        domain, logger=ctx.logger, follow=follow, lines=lines, json_output=ctx.json_output
    )
    if open_panel and code == 0:
        open_in_panel(f"/apps/{domain}/logs", logger=ctx.logger)
    _exit(code)
