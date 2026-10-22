# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust context`` command group: the other servers this machine operates (J-12).

A context is a name, an address and *where to find the token*: a private file,
or a variable. Once one is in use (``noust context use prod``), the commands
that have a form on another server run there, through its API, with the role the
token holds; ``--local`` runs one here instead. These commands only keep that
list: they never reach a server's managers and never print a token.

    noust context add prod --url https://noust.acme.test --token - < token.txt
    noust --context prod update shop.acme.test
    noust --remote https://noust.acme.test --token-file ~/.noust-token list
"""

from __future__ import annotations

import json
import sys

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.client import ApiClient, ContextStore, RemoteError
from noust.client.contexts import parse_token_source


@click.group("context", cls=NoustGroup)
def cli() -> None:
    """Save and choose the servers this machine operates through their API."""


@cli.command("add")
@click.argument("name")
@click.option(
    "--url", required=True, help="The server's address, such as https://noust.example.com."
)
@click.option(
    "--token",
    metavar="-|@FILE|env:NAME",
    default=None,
    help="Keep the token in a private file of its own, read from standard input (-), a file "
    "(@FILE) or a variable (env:NAME). Never the token itself.",
)
@click.option(
    "--token-file",
    metavar="FILE",
    default=None,
    help="A private file that already holds the token.",
)
@click.option(
    "--token-env", metavar="VARIABLE", default=None, help="A variable that holds the token."
)
@click.option("--ca-file", metavar="FILE", default=None, help="Trust this certificate authority.")
@click.option("--use", is_flag=True, help="Also make it the server commands run on.")
@pass_context
def add_command(
    ctx: Context,
    name: str,
    url: str,
    token: str | None,
    token_file: str | None,
    token_env: str | None,
    ca_file: str | None,
    use: bool,
) -> None:
    """
    Save a server.

    The token is a file only you can read, or a variable: it is not written in
    the list of contexts, so reading the list tells nobody how to get in.
    """
    secret = parse_token_source(token, stdin=sys.stdin) if token else None
    context = ContextStore().add(
        name,
        url,
        token=secret,
        token_file=token_file,
        token_env=token_env,
        ca_file=ca_file,
        use=use,
    )
    if ctx.json_output:
        click.echo(json.dumps({"context": context.to_dict()}))
        return
    ctx.logger.success(f"Context saved: {context.name} ({context.url})")
    if ContextStore().current() == context:
        ctx.logger.info(f"Commands now run on {context.name}; 'noust --local ...' runs one here.")


@cli.command("list", read_only=True)
@json_option("Print the contexts as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the saved servers, marking the one in use."""
    store = ContextStore()
    current = store.current()
    contexts = store.list()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "current": current.name if current else None,
                    "contexts": [c.to_dict() for c in contexts],
                }
            )
        )
        return
    if not contexts:
        ctx.logger.info("No saved servers. Add one with 'noust context add'.")
        return
    ctx.logger.table(
        ["", "Name", "Address", "Token from"],
        [
            [
                "*" if current is not None and current.name == c.name else "",
                c.name,
                c.url,
                f"file {c.token_file}" if c.token_file else f"variable {c.token_env}",
            ]
            for c in contexts
        ],
    )


@cli.command("use")
@click.argument("name")
@pass_context
def use_command(ctx: Context, name: str) -> None:
    """Run commands on this server from now on."""
    context = ContextStore().use(name)
    ctx.logger.success(f"Commands now run on {context.name} ({context.url}).")


@cli.command("current", read_only=True)
@pass_context
def current_command(ctx: Context) -> None:
    """Print the server commands run on, or say they run here."""
    context = ContextStore().current()
    if context is None:
        ctx.logger.info("Commands run on this machine.")
        return
    click.echo(context.name)


@cli.command("remove")
@click.argument("name")
@pass_context
def remove_command(ctx: Context, name: str) -> None:
    """Forget a saved server, and the token file this tool kept for it."""
    context = ContextStore().remove(name)
    ctx.logger.success(f"Context removed: {context.name}")


@cli.command("verify", read_only=True)
@click.argument("name", required=False)
@json_option("Print the answer as JSON.")
@pass_context
def verify_command(ctx: Context, name: str | None) -> None:
    """
    Check that a saved server answers and the token is accepted.

    Says which Noust it is and who the token is there. Nothing is changed.
    """
    store = ContextStore()
    context = store.require(name) if name else store.current()
    if context is None:
        raise click.UsageError("No server in use. Name one: 'noust context verify NAME'.")
    client = ApiClient(context.url, store.token_of(context), ca_file=context.ca_file)
    try:
        answer = client.request("GET", "/auth/session")
    except RemoteError as exc:
        raise RemoteError(
            f"{context.name}: {exc.message}",
            status=exc.status,
            code=exc.code,
            details=exc.details,
            output=exc.output,
            body=exc.body,
        ) from exc
    body = answer.body
    if not body.get("authenticated"):
        raise RemoteError(
            f"{context.url} did not accept the token of {context.name}",
            status=401,
            code="unauthorized",
            details="It may be revoked or expired: issue a new one ('noust token create').",
        )
    result = {
        "context": context.name,
        "url": context.url,
        "server": body.get("hostname"),
        "version": body.get("version"),
        "api_version": answer.api_version,
        "role": body.get("role"),
        "permissions": len(body.get("permissions", [])),
    }
    if ctx.json_output:
        click.echo(json.dumps(result))
        return
    ctx.logger.success(f"{context.name}: {body.get('hostname')} runs Noust {body.get('version')}")
    ctx.logger.key_value("role", str(body.get("role") or body.get("grant") or "-"))
    ctx.logger.key_value("permissions", str(result["permissions"]))
    ctx.logger.key_value("API version", str(answer.api_version or "-"))
