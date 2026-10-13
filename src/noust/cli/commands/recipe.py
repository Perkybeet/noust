# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust recipe``: the catalogue of applications Noust deploys from a recipe.

``list`` and ``show`` browse it, ``install`` deploys one (the same deployment
as ``noust create --recipe``), ``outdated`` says which installed applications
the catalogue is ahead of, and ``update`` moves one to the catalogue's version
through the one update every application goes through. Everything reads
:mod:`noust.recipes`, as ``GET /api/recipes`` does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.logger import Logger
from noust.recipes import (
    CATEGORIES,
    KINDS,
    Recipe,
    get_recipe,
    search_recipes,
)
from noust.recipes.catalog import catalogue_problems
from noust.recipes.model import ORIGINS

if TYPE_CHECKING:
    from noust.recipes.updates import RecipeUpdateStatus


def _print_variables(logger: Logger, recipe: Recipe) -> None:
    """
    List a recipe's variables the way an operator decides what to give.

    Args:
        logger: Where to print.
        recipe: The recipe.
    """
    inputs = [variable for variable in recipe.variables if variable.is_input]
    if inputs:
        logger.blank()
        logger.info("Asked for at install (--var NAME=VALUE):")
        for variable in inputs:
            note = "required" if variable.is_required else f"default: {variable.default!r}"
            description = f" - {variable.description}" if variable.description else ""
            logger.info(f"  {variable.name} ({note}){description}")
    others = [variable for variable in recipe.variables if not variable.is_input]
    if others:
        logger.blank()
        logger.info("Set by Noust (kept across updates; edit with noust env):")
        for variable in others:
            origin = {
                "generated": f"generated ({variable.generate})",
                "deployment": f"from the deployment ({variable.source})",
                "provisioned": f"from {variable.source}",
                "composed": "composed from other variables",
            }.get(variable.origin(), variable.origin())
            suffix = ", secret" if variable.secret else ""
            logger.info(f"  {variable.name}: {origin}{suffix}")


def print_recipe(logger: Logger, recipe: Recipe) -> None:
    """
    Describe one recipe as an operator reads it.

    Args:
        logger: Where to print.
        recipe: The recipe.
    """
    logger.header(recipe.title)
    if recipe.slogan:
        logger.info(recipe.slogan)
    logger.info(recipe.description)
    logger.key_value("Homepage", recipe.homepage)
    if recipe.docs:
        logger.key_value("Documentation", recipe.docs)
    if recipe.repository:
        logger.key_value("Source code", recipe.repository)
    if not recipe.available:
        logger.key_value("Available", "No")
        logger.info(recipe.unavailable_reason or "")
        return
    details = recipe.to_dict()
    source = details["source"] or {}
    if recipe.schema >= 2:
        logger.key_value("Version", f"{recipe.version} (recipe revision {recipe.revision})")
        logger.key_value("Category", recipe.category)
        if recipe.tags:
            logger.key_value("Tags", ", ".join(recipe.tags))
        if recipe.license:
            logger.key_value("License", recipe.license)
    logger.key_value("Kind", "compose: runs in Docker" if recipe.runs_in_docker else "native")
    if recipe.architectures:
        logger.key_value("Architectures", ", ".join(recipe.architectures))
    if recipe.min_noust:
        logger.key_value("Needs Noust", f"{recipe.min_noust} or newer")
    if recipe.kind == "compose":
        for image in recipe.images:
            pin = image.digest[:19] + "..." if image.digest else "no digest"
            logger.key_value("Image", f"{image.repository}:{image.tag} ({image.service}, {pin})")
        if not recipe.digest_verified:
            logger.warning("Its images are pinned by tag only: no digest was verified.")
        for public in recipe.public_ports:
            logger.warning(
                f"Opens {public.port}/{public.protocol} to the network: {public.purpose}"
            )
    else:
        logger.key_value("Type", recipe.app_type)
        logger.key_value(
            "Source",
            " ".join(
                str(part)
                for part in (
                    source.get("url") or "rendered from the recipe",
                    f"at {source['ref']}" if source.get("ref") else "",
                    "(checksum verified)"
                    if source.get("sha256") or source.get("checksum_url")
                    else "",
                )
                if part
            ),
        )
        logger.key_value("Layout", recipe.layout)
    if recipe.requires:
        logger.key_value("Provisions", ", ".join(recipe.requires))
    if recipe.persistent_paths:
        logger.key_value("Kept across releases", ", ".join(recipe.persistent_paths))
    if recipe.health is not None:
        logger.key_value(
            "Health check", f"{recipe.health.path} ({recipe.health.expect or 'below 500'})"
        )
    for requirement in recipe.needs:
        logger.key_value("Needs", requirement)
    if recipe.schema >= 2:
        _print_variables(logger, recipe)
    elif details["env"]:
        logger.blank()
        logger.info("Environment (set with --env NAME=value to override):")
        for variable in details["env"]:
            suffix = " (generated)" if variable["generated"] else ""
            logger.info(f"  {variable['name']}{suffix}")
    logger.blank()
    logger.info(f"Install it with: noust recipe install {recipe.name} -d <domain>")


def _print_status(logger: Logger, status: RecipeUpdateStatus) -> None:
    """
    Print where one installed recipe stands against the catalogue.

    Args:
        logger: Where to print.
        status: The status.
    """
    if status.update_available:
        message = f"{status.recipe} {status.installed_version} -> {status.latest_version}"
    elif status.reason:
        message = f"{status.recipe} {status.installed_version} ({status.reason})"
    else:
        message = f"{status.recipe} {status.installed_version} (up to date)"
    logger.key_value(status.domain, message)
    for change in status.changes:
        logger.info(f"    {change.version}: {change.summary}")


@click.group("recipe", cls=NoustGroup)
def cli() -> None:
    """The recipe catalogue: browse it, install from it and keep what you installed up to date."""


@cli.command("list", read_only=True)
@click.option("-q", "--query", help="Words to look for in the name, title, slogan and tags.")
@click.option(
    "--category", type=click.Choice(CATEGORIES), help="Only the recipes of this category."
)
@click.option("--tag", help="Only the recipes with this tag.")
@click.option("--kind", type=click.Choice(KINDS), help="Only native or only compose recipes.")
@click.option(
    "--origin",
    type=click.Choice(ORIGINS),
    help="Only the package's recipes (shipped), a catalogue package's (catalog) or yours (operator).",
)
@json_option("Print the recipes as JSON.")
@pass_context
def list_command(
    ctx: Context,
    query: str | None,
    category: str | None,
    tag: str | None,
    kind: str | None,
    origin: str | None,
) -> None:
    """
    List the recipes, the ones this release can deploy first.

    A recipe that is not available says why. Your own recipes (added with
    ``noust recipe add``) are listed with the rest; a file of yours that is
    not a usable recipe is named at the end instead of hiding the catalogue.
    """
    recipes = [
        recipe
        for recipe in search_recipes(query=query, category=category, tag=tag, kind=kind)
        if origin is None or recipe.origin == origin
    ]
    problems = [problem for problem in catalogue_problems() if origin in (None, problem.origin)]
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "items": [recipe.summary() for recipe in recipes],
                    "total": len(recipes),
                    "problems": [problem.to_dict() for problem in problems],
                }
            )
        )
        return
    logger = ctx.logger
    for recipe in recipes:
        if not recipe.available:
            logger.key_value(recipe.name, f"{recipe.title}: not available in this release")
            continue
        badge = " [Docker]" if recipe.runs_in_docker else ""
        mine = " [yours]" if recipe.origin == "operator" else ""
        version = f" {recipe.version}" if recipe.version else ""
        logger.key_value(
            recipe.name,
            f"{recipe.title}{version}{badge}{mine} - {recipe.slogan or recipe.description}",
        )
    if not recipes:
        logger.info("No recipe matches.")
    for problem in problems:
        logger.warning(f"{problem.location}: {problem.error}")
        if problem.details:
            logger.info(f"    {problem.details}")
    logger.blank()
    logger.info("Details: noust recipe show NAME. Install: noust recipe install NAME -d DOMAIN")


@cli.command("show", read_only=True)
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


@cli.command("install")
@click.argument("name")
@click.option("-d", "--domain", required=True, help="The domain to serve it on.")
@click.option(
    "--var",
    "variable_pairs",
    metavar="NAME=VALUE",
    multiple=True,
    help="Give a variable a value: the ones the recipe requires, or any it sets. Repeat for several.",
)
@click.option(
    "--var-file",
    type=click.Path(path_type=Path, dir_okay=False),
    help="A file of NAME=VALUE lines, under --var.",
)
@click.option("--port", type=int, help="The port to listen on; a free one is chosen when omitted.")
@click.option("--no-ssl", is_flag=True, help="Do not request a certificate.")
@click.option("--www", is_flag=True, help="Also answer on www.<domain>, as a redirect.")
@click.option(
    "--force",
    is_flag=True,
    help="Deploy into an application directory that already holds files.",
)
@click.option(
    "--no-backups",
    "no_backups",
    is_flag=True,
    help="Do not schedule the daily backups an installed application starts with.",
)
@click.option(
    "--install-requirements",
    "install_requirements",
    is_flag=True,
    help="Install the language runtime and the database engines the recipe needs and this "
    "server lacks, instead of stopping.",
)
@global_flags
@pass_context
def install_command(
    ctx: Context,
    name: str,
    domain: str,
    variable_pairs: tuple[str, ...],
    var_file: Path | None,
    port: int | None,
    no_ssl: bool,
    www: bool,
    force: bool,
    no_backups: bool,
    install_requirements: bool,
) -> None:
    """
    Install a recipe on a domain.

    The same deployment as ``noust create --recipe NAME -d DOMAIN``: what the
    recipe requires is provisioned, its variables are generated and kept, and
    the application only goes live when it passes its health gate. The
    variables a recipe requires must be given with --var; installation stops
    before anything is created when one is missing.

    The application starts with backups: each database Noust provisioned is
    dumped every day and every dump is test-restored, and its data is archived
    every day. They stay on this server until you pick a destination;
    ``--no-backups`` leaves them out.

    What the recipe needs from the server (a language runtime, a database
    engine) is checked first, before anything is created; ``noust recipe
    requirements NAME`` shows it, and ``--install-requirements`` installs what
    Noust can install.
    """
    # Imported here: the deploy commands load every deployer, which browsing
    # the catalogue has no use for.
    from noust.cli.commands.webapp import _create_from_recipe, _exit, _parse_env_pairs

    _exit(
        _create_from_recipe(
            logger=ctx.logger,
            recipe=name,
            domain=domain,
            source=None,
            app_type="auto",
            branch=None,
            layout=None,
            persist=(),
            port=port,
            webserver="nginx",
            ssl=not no_ssl,
            www=www,
            env_file=var_file,
            env_vars=_parse_env_pairs(variable_pairs),
            package_manager="auto",
            replace_existing=force,
            default_backups=not no_backups,
            install_requirements=install_requirements,
        )
    )


@cli.command("outdated", read_only=True)
@click.option("--all", "show_all", is_flag=True, help="Also list the ones that are up to date.")
@json_option("Print the statuses as JSON.")
@pass_context
def outdated_command(ctx: Context, show_all: bool) -> None:
    """
    Say which installed recipes the catalogue has a newer version of.

    Compares the version each application was installed at with the one this
    release ships, and lists what changed in between.
    """
    from noust.recipes.updates import installed_statuses, outdated

    statuses = installed_statuses() if show_all else outdated()
    if ctx.json_output:
        click.echo(json.dumps({"items": [status.to_dict() for status in statuses]}))
        return
    logger = ctx.logger
    for status in statuses:
        _print_status(logger, status)
    if not statuses:
        logger.success("Every application installed from a recipe is up to date.")
        return
    logger.blank()
    logger.info("Update one with: noust recipe update DOMAIN")


@cli.command("update")
@click.argument("domain")
@click.option(
    "--var",
    "variable_pairs",
    metavar="NAME=VALUE",
    multiple=True,
    help="A value for a variable the new version asks for. Repeat for several.",
)
@click.option(
    "--force", is_flag=True, help="Deploy the catalogue's version even if it is installed."
)
@global_flags
@pass_context
def update_command(ctx: Context, domain: str, variable_pairs: tuple[str, ...], force: bool) -> None:
    """
    Update an installed recipe to the catalogue's version.

    Goes through the one update every application goes through: a backup
    first (a Compose stack's databases included), then the build, then the
    health gate, and the previous version back when the gate fails. Variables
    the new version adds are generated; the ones the application has are kept.
    """
    from noust.cli.commands.webapp import _parse_env_pairs
    from noust.recipes.updates import update_recipe

    logger = ctx.logger
    outcome = update_recipe(
        domain,
        overrides=_parse_env_pairs(variable_pairs),
        force=force,
        on_phase=lambda index, total, message: logger.step(index, total, message),
        on_step=logger.substep,
        logger=logger,
        verbose=logger.verbose,
    )
    if not outcome.updated:
        logger.success(f"{domain} already runs {outcome.recipe} {outcome.to_version}.")
        return
    logger.success(f"{domain} now runs {outcome.recipe} {outcome.to_version}.")
    if outcome.added_variables:
        logger.info(f"Variables added: {', '.join(outcome.added_variables)}")
    if outcome.warnings:
        logger.warning(outcome.warnings)


@cli.group("catalog", cls=NoustGroup)
def catalog_group() -> None:
    """The signed packages of recipes that grow the catalogue between releases."""


@catalog_group.command("show", read_only=True)
@json_option("Print the package as JSON.")
@pass_context
def catalog_show_command(ctx: Context) -> None:
    """Say which package is in place, who signed it, and whose keys are trusted."""
    from noust.recipes.catalog_package import current_package, trusted_keys

    package = current_package()
    keys = sorted(trusted_keys())
    if ctx.json_output:
        click.echo(
            json.dumps({"package": package.to_dict() if package else None, "trusted_keys": keys})
        )
        return
    if package is None:
        ctx.logger.info("No package is imported: the catalogue is what this release ships.")
    else:
        ctx.logger.key_value("Serial", str(package.serial))
        ctx.logger.key_value("Made", package.created)
        ctx.logger.key_value("Signed by", package.signer)
        ctx.logger.key_value("Recipes", ", ".join(sorted(package.recipes)) or "none")
    ctx.logger.key_value("Trusted keys", ", ".join(keys) if keys else "none")


@catalog_group.command("import")
@click.argument("package", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--force", is_flag=True, help="Accept a package whose serial is not higher.")
@global_flags
@pass_context
def catalog_import_command(ctx: Context, package: Path, force: bool) -> None:
    """
    Import a signed package of recipes.

    The signature must be a key you trust (`noust recipe catalog trust`), every
    file must match the digest the manifest signs, every recipe must pass the
    schema and the lint, and the serial must be higher than the one in place.
    Nothing in it runs.
    """
    from noust.recipes.catalog_package import import_package

    _print_import(ctx, import_package(package, force=force))


@catalog_group.command("fetch")
@click.argument("url")
@click.option("--force", is_flag=True, help="Accept a package whose serial is not higher.")
@global_flags
@pass_context
def catalog_fetch_command(ctx: Context, url: str, force: bool) -> None:
    """Download a signed package over https and import it, with every check import has."""
    from noust.recipes.catalog_package import fetch_package

    _print_import(ctx, fetch_package(url, force=force))


def _print_import(ctx: Context, report: Any) -> None:
    """
    Say what an import brought.

    Args:
        ctx: The command's context.
        report: The import report.
    """
    logger = ctx.logger
    logger.success(
        f"Package {report.package.serial} (signed by {report.package.signer}) is in place."
    )
    for label, names in (
        ("New recipes", report.added),
        ("Newer than the release's", report.updated),
        ("Not used, the release is as new", report.ignored),
    ):
        if names:
            logger.key_value(label, ", ".join(names))
    for name in report.clashes:
        logger.warning(
            f"{name} is also one of your own recipes; yours is left out until you rename it."
        )


@catalog_group.command("trust")
@click.argument("name")
@click.argument("public_key")
@global_flags
@pass_context
def catalog_trust_command(ctx: Context, name: str, public_key: str) -> None:
    """Trust a key to sign packages: NAME is yours to choose, PUBLIC_KEY is 64 hex digits."""
    from noust.recipes.catalog_package import trust_key

    trust_key(name, public_key)
    ctx.logger.success(f"Packages signed by {name} are trusted.")


@catalog_group.command("remove")
@global_flags
@pass_context
def catalog_remove_command(ctx: Context) -> None:
    """Go back to the recipes this release ships."""
    from noust.recipes.catalog_package import remove_package

    if remove_package():
        ctx.logger.success("The imported package is removed.")
    else:
        ctx.logger.info("No package was imported.")


@cli.command("requirements", read_only=True)
@click.argument("name")
@json_option("Print the requirements as JSON.")
@pass_context
def requirements_command(ctx: Context, name: str) -> None:
    """
    Show what a recipe needs from this server and whether it has it.

    The tools its deployer runs, the language runtime it pins and the database
    engines it requires, each met or not, with the command that provides what
    is missing. Changes nothing; exits 1 when something is missing.
    """
    from noust.recipes.requirements import check_requirements

    recipe = get_recipe(name)
    found = check_requirements(recipe)
    missing = [item for item in found if not item.met]
    if ctx.json_output:
        click.echo(json.dumps({"recipe": recipe.name, "items": [item.to_dict() for item in found]}))
    else:
        for item in found:
            line = f"{item.name}: {item.detail}"
            if item.met:
                ctx.logger.success(line)
            else:
                ctx.logger.warning(line)
                if item.fix:
                    ctx.logger.info(f"    {item.fix}")
        if not found:
            ctx.logger.info(f"{recipe.title} asks nothing of this server.")
        elif missing:
            ctx.logger.info(
                "Install what Noust can with: "
                f"noust recipe install {recipe.name} -d <domain> --install-requirements"
            )
    if missing:
        raise SystemExit(1)


@cli.command("check", read_only=True)
@click.argument("path", type=click.Path(path_type=Path, exists=True))
@json_option("Print what was checked as JSON.")
@pass_context
def check_command(ctx: Context, path: Path) -> None:
    """
    Check a recipe file, or a folder of them, without adding anything.

    Runs the schema every recipe in the catalogue passes: an unknown key, an
    image on a floating tag or without a digest, a symbolic link or a logo
    the console's policy would refuse are errors that name the field.
    """
    from noust.recipes.private import read_bundles

    bundles = read_bundles(path)
    if ctx.json_output:
        click.echo(json.dumps({"items": [bundle.recipe.summary() for bundle in bundles]}))
        return
    for bundle in bundles:
        recipe = bundle.recipe
        ctx.logger.success(f"{recipe.name} {recipe.version} is a valid {recipe.kind} recipe.")
    ctx.logger.info(f"Add it with: noust recipe add {path}")


@cli.command("add")
@click.argument("path", type=click.Path(path_type=Path, exists=True))
@click.option("--replace", is_flag=True, help="Overwrite a recipe of yours with the same name.")
@global_flags
@pass_context
def add_command(ctx: Context, path: Path, replace: bool) -> None:
    """
    Add your own recipe to the catalogue.

    PATH is a recipe file (``<name>.yaml``, its logo and files in
    ``assets/<name>/`` beside it) or a folder of them. It is checked against
    the same schema as the shipped recipes first, and a name the catalogue
    already holds is refused: a private recipe has a name of its own. Recipes
    are kept in /etc/noust/recipes, shown as yours, and installed and updated
    like any other.
    """
    from noust.recipes.private import add_recipes

    for recipe in add_recipes(path, replace=replace):
        ctx.logger.success(f"Added {recipe.name} {recipe.version}.")
        ctx.logger.info(f"Install it with: noust recipe install {recipe.name} -d <domain>")


@cli.command("remove")
@click.argument("name")
@global_flags
@pass_context
def remove_command(ctx: Context, name: str) -> None:
    """
    Remove one of your own recipes.

    Applications installed from it keep running. The shipped catalogue's
    recipes cannot be removed.
    """
    from noust.recipes.private import remove_recipe

    remove_recipe(name)
    ctx.logger.success(f"Removed {name}.")
