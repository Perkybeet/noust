# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust import --from PLATFORM``: read another platform's configuration.

The reading is :mod:`noust.deployers.importers`, which the new-app wizard's
inspection uses too; with ``--deploy`` the proposal becomes an export
document (:func:`noust.deployers.app_export.proposal_document`) and goes
through ``noust app import``'s plan and run, so there is one create path.

Coolify, Dokploy and CapRover keep an application's configuration in their own
database, so they are read through their API: ``--url`` is the server and a
read-only token comes from ``--token-file`` or the ``NOUST_IMPORT_TOKEN``
environment variable (never an argument, where every local user could read it;
CapRover has no read-only credential, so its "token" is the dashboard's
password). Dokku has no API: it is read by running its own read-only commands on
its server, or over SSH with ``--ssh dokku@host``. Without ``--app`` the server's
applications are listed; with it, one is read and proposed. This module only
parses and presents.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustCommand, global_flags, json_option, pass_context
from noust.cli.commands.app import gather_env, run_import
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.core.runner import get_runner
from noust.deployers.app_export import plan_import, proposal_document
from noust.deployers.importers import (
    COMMAND_PLATFORMS,
    PLATFORMS,
    REMOTE_PLATFORMS,
    UNSUPPORTED_PLATFORMS,
    Proposal,
    read_platform,
)
from noust.deployers.importers.base import RemoteApplication

#: Where a remote platform's token may come from besides a file.
TOKEN_ENV = "NOUST_IMPORT_TOKEN"  # noqa: S105 - the name of a variable, not a credential


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
    if proposal.name:
        logger.key_value("Name there", proposal.name)
    if proposal.source:
        logger.key_value(
            "Source",
            proposal.source + (f" ({proposal.branch})" if proposal.branch else ""),
        )
    if proposal.base_directory:
        logger.key_value("Base directory", proposal.base_directory)
    if proposal.watch_paths:
        logger.key_value("Watch paths", ", ".join(proposal.watch_paths))
    if proposal.memory_max_mb or proposal.cpu_quota_percent:
        logger.key_value(
            "Limits",
            ", ".join(
                part
                for part in (
                    f"{proposal.memory_max_mb} MB" if proposal.memory_max_mb else "",
                    f"{proposal.cpu_quota_percent}% CPU" if proposal.cpu_quota_percent else "",
                )
                if part
            ),
        )
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
    for job in proposal.cron:
        logger.key_value(f"  cron {job.name}", f"{job.schedule}  {job.command}")
    for storage in proposal.storage:
        where = storage.volume or storage.host_path or "?"
        kept = (
            f"--persist {storage.persistent_path}" if storage.persistent_path else "not persisted"
        )
        logger.key_value(f"  storage {storage.mount_path}", f"{where} ({kept})")
    for database in proposal.database_details:
        used = "used" if database.linked else "same environment, not named"
        logger.key_value(
            f"  database {database.name}", f"{database.engine}, {database.reference} ({used})"
        )
    if proposal.equivalent:
        logger.key_value("Create it with", proposal.equivalent)
    for warning in proposal.warnings:
        logger.warning(warning)


def _token(token_file: Path | None) -> str:
    """
    Read the token of another platform's API.

    Args:
        token_file: A file whose first line is the token.

    Returns:
        The token.

    Raises:
        ValidationError: Neither a file nor ``NOUST_IMPORT_TOKEN`` gives one.
    """
    if token_file is not None:
        lines = token_file.read_text(encoding="utf-8").splitlines()
        return lines[0].strip() if lines else ""
    value = os.environ.get(TOKEN_ENV, "").strip()
    if not value:
        raise ValidationError(
            "A token is needed to read the server's API",
            details=f"Give --token-file FILE (its first line is the token) or set {TOKEN_ENV}. "
            "A token is never taken as an argument: every local user could read it.",
            field="token",
        )
    return value


def _read_hint(platform: str, client: Any, ssh: str | None) -> str:
    """
    Spell the command that reads a server again, for the next step.

    Args:
        platform: The platform.
        client: The client that was used.
        ssh: Dokku's SSH target, if any.

    Returns:
        The command up to (not including) ``--app``.
    """
    if platform in COMMAND_PLATFORMS:
        return f"noust import --from {platform}" + (f" --ssh {ssh}" if ssh else "")
    return f"noust import --from {platform} --url {client.base_url} --token-file FILE"


def _pick(applications: list[RemoteApplication], wanted: str) -> RemoteApplication:
    """
    Find the application the operator named, by id or by name.

    Args:
        applications: What the server lists.
        wanted: An id, or a name (case-insensitive).

    Returns:
        The application.

    Raises:
        ValidationError: None matches, or a name matches several.
    """
    exact = [item for item in applications if item.ref == wanted]
    named = [item for item in applications if item.name.lower() == wanted.lower()]
    found = exact or named
    if not found:
        raise ValidationError(
            f"The server has no application {wanted!r}",
            details="List them with: noust import --from PLATFORM --url URL --token-file FILE",
            field="app",
        )
    if len(found) > 1:
        raise ValidationError(
            f"{len(found)} applications are named {wanted!r}",
            details="Name it by id: " + ", ".join(item.ref for item in found),
            field="app",
        )
    return found[0]


def _remote(
    ctx: Context,
    platform: str,
    url: str | None,
    token_file: Path | None,
    app: str | None,
    ssh: str | None = None,
) -> tuple[Proposal | None, Any]:
    """
    Read from another platform: list its applications, or describe one.

    Args:
        ctx: The CLI context.
        platform: ``coolify``, ``dokploy``, ``caprover`` or ``dokku``.
        url: The server's address (not for Dokku).
        token_file: Where the token is (not for Dokku).
        app: The application to read; None to list them.
        ssh: For Dokku: the server to ask over SSH; None for this machine.

    Returns:
        The proposal, or None after listing; and the client that was used.

    Raises:
        click.UsageError: No address was given.
        NoustError: The server cannot be read.
    """
    if platform in COMMAND_PLATFORMS:
        if url or token_file:
            raise click.UsageError(f"{platform} has no API: use --ssh dokku@host, or run it there.")
        module = COMMAND_PLATFORMS[platform]
        client: Any = module.connect(get_runner(), ssh)
    else:
        if ssh:
            raise click.UsageError("--ssh is for dokku.")
        if not url:
            raise click.UsageError(f"--url is required for {platform}: the server to read")
        module = REMOTE_PLATFORMS[platform]
        client = module.connect(url, _token(token_file))
    applications = module.list_applications(client)
    if app is None:
        if ctx.json_output:
            click.echo(json.dumps([dataclasses.asdict(item) for item in applications]))
            return None, client
        if not applications:
            ctx.logger.info(f"{platform.capitalize()} at {client.base_url} has no applications")
            return None, client
        for item in applications:
            where = f" [{item.project}]" if item.project else ""
            domains = ", ".join(item.domains) or "no domain"
            ctx.logger.key_value(
                item.name, f"{item.ref}  {item.build or '-'}  {domains}  {item.source}{where}"
            )
        ctx.logger.info(f"Read one with: {_read_hint(platform, client, ssh)} --app NAME")
        return None, client
    chosen = _pick(applications, app)
    return module.read_application(client, chosen.ref), client


@click.command("import", cls=NoustCommand)
@click.option(
    "--from",
    "platform",
    required=True,
    type=click.Choice([*PLATFORMS, *REMOTE_PLATFORMS, *COMMAND_PLATFORMS, *UNSUPPORTED_PLATFORMS]),
    help="The platform whose configuration the repository carries, or whose server to read "
    "(coolify, dokploy, caprover: with --url; dokku: on its server or with --ssh).",
)
@click.option(
    "--url",
    help="coolify, dokploy and caprover: the server's address, https://coolify.example.com.",
)
@click.option(
    "--token-file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help=f"coolify, dokploy and caprover: a file whose first line is a read-only API token "
    f"(CapRover: the dashboard's password) (or set {TOKEN_ENV}).",
)
@click.option(
    "--ssh",
    "ssh_target",
    metavar="[dokku@]HOST",
    help="dokku: the server to ask over SSH, with a key Dokku accepts and a host key already "
    "known. Without it, the dokku command of this machine.",
)
@click.option(
    "--app",
    "remote_app",
    metavar="NAME",
    help="coolify, dokploy, caprover and dokku: the application to read, by name or id. Without "
    "it they are listed.",
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
    url: str | None,
    token_file: Path | None,
    ssh_target: str | None,
    remote_app: str | None,
    path: Path,
    deploy_domain: str | None,
    source: str | None,
    branch: str | None,
    env_file: Path | None,
    env_pairs: tuple[str, ...],
) -> None:
    """
    Read Vercel, Railway, Render or Heroku configuration from a repository, or
    a Coolify, Dokploy, CapRover or Dokku application from its server.

    PATH is the checked-out repository (default: here). Prints what Noust would
    deploy - type, commands, port, health check, variables, databases,
    domains - and a warning for everything without an equivalent. With
    --deploy DOMAIN it deploys that, from --source or PATH, like 'noust app
    import'. Coolify, Dokploy and CapRover keep their configuration in their own
    database: give --url and a token (--token-file, or NOUST_IMPORT_TOKEN; CapRover
    has none that only reads, so it is the dashboard's password) to list their
    applications, and --app to read one. Dokku is asked with its own commands, on
    its server or with --ssh. Nothing is written to them. What an application
    kept (storage, databases) comes with the commands that copy it.
    """
    if platform in REMOTE_PLATFORMS or platform in COMMAND_PLATFORMS:
        proposal, _client = _remote(ctx, platform, url, token_file, remote_app, ssh_target)
        if proposal is None:
            return
        if deploy_domain is None:
            if source or branch or env_file or env_pairs:
                raise click.UsageError("--source, --branch, --env-file and --env go with --deploy.")
            if ctx.json_output:
                click.echo(json.dumps(dataclasses.asdict(proposal)))
                return
            print_proposal(ctx.logger, proposal)
            ctx.logger.info(
                f"Deploy it with: {_read_hint(platform, _client, ssh_target)} "
                f"--app {remote_app} --deploy DOMAIN"
            )
            return
        _deploy_proposal(ctx, proposal, deploy_domain, source, branch, env_file, env_pairs)
        return
    if url or token_file or remote_app or ssh_target:
        raise click.UsageError(
            "--url, --token-file, --ssh and --app are for coolify, dokploy, caprover and dokku."
        )
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

    _deploy_proposal(
        ctx, proposal, deploy_domain, source or str(path.resolve()), branch, env_file, env_pairs
    )


def _deploy_proposal(
    ctx: Context,
    proposal: Proposal,
    domain: str,
    source: str | None,
    branch: str | None,
    env_file: Path | None,
    env_pairs: tuple[str, ...],
) -> None:
    """
    Deploy a proposal through the normal import path.

    Args:
        ctx: The CLI context.
        proposal: What the platform says.
        domain: The domain to create the application on.
        source: The repository to deploy; the proposal's own when None.
        branch: The branch; the proposal's own when None.
        env_file: ``NAME=value`` lines for the variables the platform kept.
        env_pairs: Variables given as ``NAME=VALUE``.

    Raises:
        click.UsageError: The platform's application is not one Noust can
            create from a proposal (an image, a Dockerfile, a Compose file),
            or no repository is known.
    """
    if proposal.equivalent and proposal.app_type in ("container", "docker-compose"):
        raise click.UsageError(
            f"This one is not a repository Noust builds by detection. Create it with: "
            f"{proposal.equivalent}"
        )
    if not (source or proposal.source):
        raise click.UsageError(
            "The platform names no repository for it: give --source with the one to deploy."
        )
    env = gather_env(env_file, env_pairs, ctx.logger)
    document = proposal_document(
        proposal,
        domain=domain,
        source=source or "",
        branch=branch,
        env=env,
    )
    if not ctx.json_output:
        for warning in proposal.warnings:
            ctx.logger.warning(warning)
        if len(proposal.domains) > 1 or (proposal.domains and proposal.domains[0] != domain):
            ctx.logger.info(
                "It answered on "
                + ", ".join(proposal.domains)
                + " there. Add the names that should come here, once their DNS points at this "
                "server, with: noust domain add"
            )
    run_import(ctx, plan_import(document, env=env))
