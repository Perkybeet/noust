# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust import --from PLATFORM``: read another platform's configuration.

The reading is :mod:`noust.deployers.importers`, which the new-app wizard's
inspection uses too; with ``--deploy`` the proposal becomes an export
document (:func:`noust.deployers.app_export.proposal_document`) and goes
through ``noust app import``'s plan and run, so there is one create path.
This module only parses and presents.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import click

from noust.cli.app import Context, NoustCommand, global_flags, json_option, pass_context
from noust.cli.commands.app import gather_env, run_import
from noust.core.logger import Logger
from noust.deployers.app_export import plan_import, proposal_document
from noust.deployers.importers import PLATFORMS, UNSUPPORTED_PLATFORMS, Proposal, read_platform


def print_proposal(logger: Logger, proposal: Proposal) -> None:
    """
    Render a proposal for a human.

    Args:
        logger: Logger the command writes through.
        proposal: The proposal.
    """
    logger.key_value("Platform", f"{proposal.platform} ({', '.join(proposal.files)})")
    logger.key_value("Type", proposal.app_type or "detected from the source")
    for label, value in (
        ("Install", proposal.install_command),
        ("Build", proposal.build_command),
        ("Start", proposal.start_command),
        ("Output", proposal.output_directory),
    ):
        if value:
            logger.key_value(label, value)
    if proposal.port is not None:
        logger.key_value("Port", str(proposal.port))
    if proposal.health_path or proposal.health_timeout:
        timeout = f", {proposal.health_timeout} s" if proposal.health_timeout else ""
        logger.key_value("Health check", f"{proposal.health_path or '/'}{timeout}")
    for variable in proposal.env:
        if variable.generated:
            shown = "generated secret"
        elif variable.value is not None:
            shown = variable.value
        elif variable.required:
            shown = "needs a value" + (f" ({variable.note})" if variable.note else "")
        else:
            shown = "optional, no default"
        logger.key_value(f"  {variable.name}", shown)
    if proposal.databases:
        logger.key_value("Databases", ", ".join(proposal.databases))
    if proposal.domains:
        logger.key_value("Domains", ", ".join(proposal.domains))
    if proposal.persistent_paths:
        logger.key_value("Persistent", ", ".join(proposal.persistent_paths))
    for warning in proposal.warnings:
        logger.warning(warning)


@click.command("import", cls=NoustCommand)
@click.option(
    "--from",
    "platform",
    required=True,
    type=click.Choice([*PLATFORMS, *UNSUPPORTED_PLATFORMS]),
    help="The platform whose configuration the repository carries.",
)
@click.argument(
    "path",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--deploy",
    "deploy_domain",
    metavar="DOMAIN",
    help="Deploy it on this domain with the proposal, through the normal create path.",
)
@click.option(
    "--source",
    help="With --deploy: the repository to deploy. Default: PATH itself.",
)
@click.option("--branch", help="With --deploy: the branch to deploy.")
@click.option(
    "--env-file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="With --deploy: NAME=value lines, for the variables the platform kept.",
)
@click.option(
    "--env",
    "env_pairs",
    multiple=True,
    metavar="NAME=VALUE",
    help="With --deploy: a variable's value. Repeat for several.",
)
@global_flags
@json_option("Print the proposal (or, with --deploy, what was done) as JSON.")
@pass_context
def cli(
    ctx: Context,
    platform: str,
    path: Path,
    deploy_domain: str | None,
    source: str | None,
    branch: str | None,
    env_file: Path | None,
    env_pairs: tuple[str, ...],
) -> None:
    """
    Read Vercel, Railway, Render or Heroku configuration from a repository.

    PATH is the checked-out repository (default: here). Prints what Noust would
    deploy - type, commands, port, health check, variables, databases,
    domains - and a warning for everything without an equivalent. With
    --deploy DOMAIN it deploys that, from --source or PATH, like 'noust app
    import'. Coolify keeps its configuration in its own database, so there is
    nothing in the repository to read.
    """
    proposal = read_platform(platform, path)
    if deploy_domain is None:
        if source or branch or env_file or env_pairs:
            raise click.UsageError("--source, --branch, --env-file and --env go with --deploy.")
        if ctx.json_output:
            click.echo(json.dumps(dataclasses.asdict(proposal)))
            return
        print_proposal(ctx.logger, proposal)
        ctx.logger.info(
            f"Deploy it with: noust import --from {platform} {path} --deploy DOMAIN [--source URL]"
        )
        return

    env = gather_env(env_file, env_pairs, ctx.logger)
    document = proposal_document(
        proposal,
        domain=deploy_domain,
        source=source or str(path.resolve()),
        branch=branch,
        env=env,
    )
    if not ctx.json_output:
        for warning in proposal.warnings:
            ctx.logger.warning(warning)
    run_import(ctx, plan_import(document, env=env))
