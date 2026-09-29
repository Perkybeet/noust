# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust github``: this server's GitHub App (2.2).

A front end over :mod:`noust.integrations.github.service`, the same calls the
console's Integrations page makes. Creating the App is not here: GitHub's
manifest flow needs a browser signed in to GitHub, so it starts in the console
(``setup --print-manifest`` prints the manifest for a look, or to post by
hand).
"""

from __future__ import annotations

import json
from dataclasses import asdict

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.integrations.github import manifest, service
from noust.integrations.hooks_site import public_hooks_url

#: Said wherever the App does not exist yet.
CREATE_HINT = (
    "Create it in the console: Integrations, GitHub, Create the App. GitHub's creation "
    "flow needs a browser signed in to GitHub; the console works through an SSH tunnel."
)


@click.group("github", cls=NoustGroup)
def cli() -> None:
    """This server's GitHub App: private repositories, push and pull request events."""


@cli.command("status")
@json_option("Print the status as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Show the App, where it is installed, and where GitHub delivers its events."""
    status = service.status()
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    logger = ctx.logger
    logger.header("GitHub App")
    if not status.configured:
        logger.info("This server has no GitHub App.")
        logger.info(CREATE_HINT)
        return
    logger.key_value("App", f"{status.name or status.slug} (id {status.app_id})")
    logger.key_value("Owner", status.owner or "-")
    logger.key_value("Settings", status.settings_url or "-")
    logger.key_value("Install", status.install_url or "-")
    logger.key_value("Installations", str(len(status.installations)))
    if status.hooks_url:
        state = "active" if status.hooks_active else "not active yet"
        logger.key_value("Webhook", f"{status.hooks_url} ({state})")
    else:
        logger.key_value("Webhook", "none: pushes and pull requests are not received")
        logger.info("Give the hooks a public name: noust web expose-hooks hooks.example.com")


@cli.command("installations")
@click.option("--sync", is_flag=True, help="Ask GitHub first and record what it lists.")
@json_option("Print the installations as JSON.")
@pass_context
def installations_command(ctx: Context, sync: bool) -> None:
    """List the accounts the App is installed on."""
    if sync:
        items = service.sync_installations()
    else:
        items = service.status().installations
    if ctx.json_output:
        click.echo(json.dumps({"items": [asdict(item) for item in items]}))
        return
    if not items:
        ctx.logger.info("The App is installed nowhere yet.")
        install = service.status().install_url
        if install:
            ctx.logger.info(f"Install it: {install}")
        return
    ctx.logger.table(
        ["Installation", "Account", "Type", "Repositories"],
        [
            [
                str(item.installation_id),
                item.account,
                item.account_type or "-",
                item.repository_selection or "-",
            ]
            for item in items
        ],
    )


@cli.command("repos")
@json_option("Print the repositories as JSON.")
@pass_context
def repos_command(ctx: Context) -> None:
    """List the repositories the App can deploy, with the source to deploy each as."""
    items = service.list_repositories()
    if ctx.json_output:
        click.echo(json.dumps({"items": items}))
        return
    if not items:
        ctx.logger.info("No repository is covered by an installation of the App.")
        return
    ctx.logger.table(
        ["Repository", "Private", "Default branch", "Source"],
        [
            [
                item["full_name"],
                "yes" if item["private"] else "no",
                item.get("default_branch") or "-",
                item["source"],
            ]
            for item in items
        ],
    )


@cli.command("remove")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print the outcome as JSON.")
@pass_context
def remove_command(ctx: Context, assume_yes: bool) -> None:
    """
    Forget the App's credentials and installations on this server.

    Private repositories stop cloning, and pushes and pull requests stop
    arriving. The App stays on GitHub until you delete it there; the page
    to do it is printed.
    """
    status = service.status()
    if not status.configured:
        if ctx.json_output:
            click.echo(json.dumps({"removed": False, "settings_url": None}))
        else:
            ctx.logger.info("This server has no GitHub App.")
        return
    if not assume_yes and not click.confirm(
        f"Forget the GitHub App {status.slug} on this server? Private GitHub repositories "
        "stop cloning and GitHub events stop arriving.",
        default=False,
    ):
        raise click.Abort()
    outcome = service.remove()
    if ctx.json_output:
        click.echo(json.dumps(outcome))
        return
    ctx.logger.success("The GitHub App's credentials were removed from this server")
    if outcome["settings_url"]:
        ctx.logger.info(f"Delete the App on GitHub (bottom of the page): {outcome['settings_url']}")


@cli.command("setup")
@click.option(
    "--print-manifest",
    is_flag=True,
    help="Print the manifest the console would post to GitHub.",
)
@click.option(
    "--origin",
    default="http://localhost:8080",
    show_default=True,
    help="The console's address as your browser reaches it.",
)
@pass_context
def setup_command(ctx: Context, print_manifest: bool, origin: str) -> None:
    """
    Explain how the App is created; with --print-manifest, show its manifest.

    GitHub creates an App from a manifest posted by a browser signed in to
    GitHub, and sends that browser back to the console with a one-time code,
    so the creation happens in the console, not here.
    """
    if print_manifest:
        checked = manifest.console_origin(origin)
        body = manifest.build_manifest(checked, hooks_url=service.github_hooks_url())
        click.echo(json.dumps(body, indent=2))
        return
    ctx.logger.info(CREATE_HINT)
    if not public_hooks_url():
        ctx.logger.info(
            "To receive pushes and pull requests, first give the hooks a public name: "
            "noust web expose-hooks hooks.example.com"
        )
