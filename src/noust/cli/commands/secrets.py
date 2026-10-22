# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust secrets`` command group: the secret managers variables can refer to.

    noust secrets add vault --kind vault --address https://vault.example.com:8200 --token-stdin
    noust env set shop.example.com 'STRIPE_KEY={{secret:vault/shop/stripe#key}}'

The value is fetched at every deploy and is not kept anywhere in Noust's store.
The token is read from standard input or asked for without echo, never taken
from the command line, where every user of the machine could read it.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.team_common import record, say, teams
from noust.core.exceptions import ValidationError
from noust.core.variables.providers import KINDS, ProviderRegistry


def _setting(text: str) -> tuple[str, str]:
    """
    Args:
        text: ``KEY=VALUE`` as typed after ``--setting``.

    Returns:
        The pair.

    Raises:
        click.BadParameter: There is no ``=``.
    """
    key, sep, value = text.partition("=")
    if not sep or not key.strip():
        raise click.BadParameter(f"{text!r} is not KEY=VALUE")
    return key.strip(), value.strip()


@click.group("secrets", cls=NoustGroup)
def cli() -> None:
    """Secret managers (Vault, Doppler, Infisical) that variables refer to."""


@cli.command("kinds", read_only=True)
@json_option("Print the kinds as JSON.")
@pass_context
def kinds_command(ctx: Context) -> None:
    """List the kinds of secret manager and the settings each takes."""
    rows: list[dict[str, Any]] = [
        {
            "kind": name,
            "address": cls.default_address,
            "required": dict(cls.required),
            "optional": dict(cls.optional),
        }
        for name, cls in sorted(KINDS.items())
    ]
    if ctx.json_output:
        click.echo(json.dumps({"kinds": rows}))
        return
    for row in rows:
        ctx.logger.header(row["kind"])
        ctx.logger.key_value("Address", row["address"] or "required (--address)")
        for key, text in {**row["required"], **row["optional"]}.items():
            ctx.logger.key_value(
                f"--setting {key}=", f"{text}{'' if key in row['required'] else ' (optional)'}"
            )


@cli.command("list", read_only=True)
@json_option("Print the providers as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the secret managers this server knows."""
    registry = ProviderRegistry()
    store = teams()
    rows = []
    for config in registry.list():
        team = store.team_by_id(config.team_id) if config.team_id is not None else None
        rows.append(
            {
                "name": config.name,
                "kind": config.kind,
                "address": config.address,
                "team": team.slug if team else None,
                "settings": dict(config.settings),
                "has_token": bool(registry.secrets.read(f"secret-providers/{config.name}")),
            }
        )
    if ctx.json_output:
        click.echo(json.dumps({"providers": rows}))
        return
    if not rows:
        ctx.logger.info("No secret manager is set up. Add one: noust secrets add NAME --kind KIND")
        return
    ctx.logger.table(
        ["Name", "Kind", "Address", "Team", "Token"],
        [
            [
                r["name"],
                r["kind"],
                r["address"] or "-",
                r["team"] or "every team",
                "set" if r["has_token"] else "missing",
            ]
            for r in rows
        ],
    )


@cli.command("add")
@click.argument("name")
@click.option("--kind", required=True, type=click.Choice(sorted(KINDS)), help="The manager.")
@click.option("--address", default=None, help="Where it is (https). Needed unless it is hosted.")
@click.option(
    "--setting",
    "settings",
    multiple=True,
    metavar="KEY=VALUE",
    help="A setting of the kind (see 'noust secrets kinds'); repeat for more.",
)
@click.option("--team", default=None, help="Only this team's applications may name it.")
@click.option("--token-stdin", "token_stdin", is_flag=True, help="Read the token from stdin.")
@click.option(
    "--keep-token",
    is_flag=True,
    help="Change its settings and keep the token it has.",
)
@pass_context
def add_command(
    ctx: Context,
    name: str,
    kind: str,
    address: str | None,
    settings: tuple[str, ...],
    team: str | None,
    token_stdin: bool,
    keep_token: bool,
) -> None:
    """Add a secret manager, or change one."""
    token: str | None = None
    if not keep_token:
        token = (
            sys.stdin.read().strip()
            if token_stdin
            else click.prompt(f"Token for {name}", hide_input=True)
        )
    team_id = teams().require_team(team).id if team else None
    registry = ProviderRegistry()
    config = registry.save(
        name,
        kind,
        token=token,
        address=address,
        settings=dict(_setting(item) for item in settings),
        team_id=team_id,
        actor="cli",
    )
    record(
        "secrets.provider.save",
        f"secret-provider:{name}",
        {"kind": config.kind, "token_replaced": token is not None},
    )
    say(
        ctx,
        {"name": name, "kind": kind},
        f"{name} is set up. Refer to a secret in it with {{{{secret:{name}/PATH}}}}",
    )


@cli.command("remove")
@click.argument("name")
@pass_context
def remove_command(ctx: Context, name: str) -> None:
    """Remove a secret manager and its token."""
    if not ProviderRegistry().delete(name):
        raise ValidationError(f"There is no secret manager called {name}")
    record("secrets.provider.remove", f"secret-provider:{name}")
    say(ctx, {"name": name}, f"Removed {name}. Variables that refer to it fail their next deploy.")


@cli.command("test")
@click.argument("name")
@pass_context
def test_command(ctx: Context, name: str) -> None:
    """Check that a secret manager answers and accepts its token."""
    message = ProviderRegistry().test(name)
    say(ctx, {"name": name}, message)
