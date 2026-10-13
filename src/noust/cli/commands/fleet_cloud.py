# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust fleet cloud``: create servers at Hetzner, DigitalOcean or Vultr and bring them into the fleet.

The commands are the front end of :mod:`noust.fleet.cloud`, which ``/api/fleet/cloud`` also
uses:

- ``account``: the provider tokens this central holds, as secrets. A token is read from the
  terminal without echo (or from standard input) and never from an argument, where ``ps``
  shows it to every local user.
- ``catalog``: what an account can create (locations, plans, images).
- ``script``: the cloud-init library a new server can boot with.
- ``server``: create a machine and enroll it, list the machines this central created, power
  one on and delete it.

Attached to the ``noust fleet`` group, which imports this module.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.fleet import cli as fleet_cli
from noust.core.exceptions import NodeError

if TYPE_CHECKING:
    from noust.fleet.cloud.accounts import CloudAccounts
    from noust.fleet.cloud.init import CloudInitLibrary
    from noust.fleet.cloud.provision import ServerProvisioner

#: How the job's log levels read on a terminal.
_LEVELS = {"success": "success", "warning": "warning", "error": "error"}


@fleet_cli.group("cloud", cls=NoustGroup)
def cloud_cli() -> None:
    """Create servers at a cloud provider and bring them into the fleet."""


def _accounts() -> CloudAccounts:
    """Returns: The cloud accounts manager."""
    from noust.fleet.cloud.accounts import CloudAccounts

    return CloudAccounts()


def _library() -> CloudInitLibrary:
    """Returns: The cloud-init library."""
    from noust.fleet.cloud.init import CloudInitLibrary

    return CloudInitLibrary()


def _provisioner() -> ServerProvisioner:
    """Returns: The provisioner."""
    from noust.fleet.cloud.provision import ServerProvisioner

    return ServerProvisioner()


def _operator() -> str:
    """Returns: ``cli:<user>``, for the records."""
    from noust.fleet.nodes import cli_actor

    return cli_actor()


def _interactive() -> bool:
    """
    Report whether someone is at the terminal to be asked.

    Returns:
        True when standard input is a terminal.
    """
    return sys.stdin.isatty()


def _read_token(from_stdin: bool, account: str) -> str:
    """
    Read a provider token without it ever being an argument.

    Args:
        from_stdin: Read one line of standard input instead of asking.
        account: The account, for the prompt.

    Returns:
        The token.

    Raises:
        NodeError: When none can be had.
    """
    if from_stdin:
        line = sys.stdin.readline().strip()
        if not line:
            raise NodeError("--token-stdin read an empty line", field="token")
        return line
    if not _interactive():
        raise NodeError(
            "There is no terminal to ask the token on",
            details="Pass --token-stdin and give it on standard input.",
            field="token",
        )
    return str(click.prompt(f"API token of {account}", hide_input=True)).strip()


def _dump(ctx: Context, data: Any) -> bool:
    """
    Print JSON when asked.

    Args:
        ctx: The CLI context.
        data: What to print.

    Returns:
        True when it printed, so the caller stops.
    """
    if ctx.json_output:
        click.echo(json.dumps(data))
        return True
    return False


@cloud_cli.command("providers", read_only=True)
@json_option("Print the providers as JSON.")
@pass_context
def providers_command(ctx: Context) -> None:
    """List the providers a server can be created at, and where each one's token is made."""
    from noust.fleet.cloud.providers import describe_providers

    providers = describe_providers()
    if _dump(ctx, providers):
        return
    ctx.logger.table(
        ["Provider", "Name", "Create a token at"],
        [[p["kind"], p["name"], p["token_url"]] for p in providers],
    )


# ---------------------------------------------------------------- accounts


@cloud_cli.group("account", cls=NoustGroup)
def account_cli() -> None:
    """The provider accounts this central creates servers with."""


@account_cli.command("list", read_only=True)
@json_option("Print the accounts as JSON.")
@pass_context
def account_list(ctx: Context) -> None:
    """List the accounts. A token is never shown, only its last characters."""
    accounts = [account.to_dict() for account in _accounts().list()]
    if _dump(ctx, accounts):
        return
    if not accounts:
        ctx.logger.info(
            "No cloud account yet. Add one: noust fleet cloud account add NAME --provider hetzner"
        )
        return
    ctx.logger.table(
        ["Account", "Provider", "Token", "Verified", "Last used", "Note"],
        [
            [
                a["name"],
                a["provider"],
                a["token_hint"],
                a["verified_at"] or "-",
                a["last_used_at"] or "-",
                a["note"] or "",
            ]
            for a in accounts
        ],
    )


@account_cli.command("add")
@click.argument("name")
@click.option("--provider", required=True, help="hetzner, digitalocean or vultr.")
@click.option("--token-stdin", is_flag=True, help="Read the token from one line of standard input.")
@click.option("--note", default=None, help="A note, such as which project the token belongs to.")
@click.option(
    "--no-verify", is_flag=True, help="Do not ask the provider whether it accepts the token."
)
@json_option("Print the account as JSON.")
@pass_context
def account_add(
    ctx: Context, name: str, provider: str, token_stdin: bool, note: str | None, no_verify: bool
) -> None:
    """
    Keep a provider API token as NAME.

    The token is asked for without echo, checked with the provider and kept as a secret
    of this central, sealed with the rest on a sealed one. Scope it to the one project
    this central creates servers in.
    """
    if ctx.dry_run:
        ctx.logger.info(f"would keep a {provider} token as {name} after the provider accepted it")
        return
    token = _read_token(token_stdin, name)
    account = _accounts().add(
        name, provider, token, note=note, actor=_operator(), verify=not no_verify
    )
    if _dump(ctx, account.to_dict()):
        return
    ctx.logger.success(
        f"Account {name} added ({provider}, token {account.to_dict()['token_hint']})"
    )


@account_cli.command("token")
@click.argument("name")
@click.option("--token-stdin", is_flag=True, help="Read the token from one line of standard input.")
@click.option(
    "--no-verify", is_flag=True, help="Do not ask the provider whether it accepts the token."
)
@pass_context
def account_token(ctx: Context, name: str, token_stdin: bool, no_verify: bool) -> None:
    """Replace the token of an account, after the provider accepted the new one."""
    if ctx.dry_run:
        ctx.logger.info(f"would replace the token of {name}")
        return
    token = _read_token(token_stdin, name)
    account = _accounts().replace_token(name, token, actor=_operator(), verify=not no_verify)
    ctx.logger.success(f"The token of {name} was replaced ({account.to_dict()['token_hint']})")


@account_cli.command("verify")
@click.argument("name")
@pass_context
def account_verify(ctx: Context, name: str) -> None:
    """Ask the provider whether it still accepts the token of NAME."""
    account = _accounts().verify(name)
    ctx.logger.success(f"{name}: the provider accepted the token ({account.verified_at})")


@account_cli.command("remove")
@click.argument("name")
@click.option(
    "--force", is_flag=True, help="Remove it although servers made with it are on record."
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@pass_context
def account_remove(ctx: Context, name: str, force: bool, yes: bool) -> None:
    """Forget an account and its token."""
    if ctx.dry_run:
        ctx.logger.info(f"would remove the account {name} and its token")
        return
    if not yes and not click.confirm(f"Remove the account {name} and its token?", default=False):
        raise NodeError("Cancelled: nothing was changed")
    _accounts().remove(name, actor=_operator(), force=force)
    ctx.logger.success(f"Account {name} removed")


@cloud_cli.command("catalog", read_only=True)
@click.argument("account")
@click.option(
    "--only",
    type=click.Choice(["locations", "plans", "images", "ssh_keys", "firewalls", "networks"]),
    default=None,
    help="Show one list only.",
)
@json_option("Print the catalog as JSON.")
@pass_context
def catalog_command(ctx: Context, account: str, only: str | None) -> None:
    """Show what ACCOUNT can create: locations, plans and images (with prices where known)."""
    provider = _accounts().provider(account)
    catalog = provider.catalog().to_dict()
    if _dump(
        ctx, {"provider": provider.kind, "capabilities": provider.capabilities.to_dict(), **catalog}
    ):
        return
    for name, choices in catalog.items():
        if only is not None and name != only:
            continue
        ctx.logger.info(f"{name} ({len(choices)})")
        ctx.logger.table(
            ["Id", "Name", "Detail"],
            [[c["id"], c["name"], c["detail"]] for c in choices],
        )


# ---------------------------------------------------------------- scripts


@cloud_cli.group("script", cls=NoustGroup)
def script_cli() -> None:
    """The cloud-init library: scripts and fragments a new server can boot with."""


@script_cli.command("list", read_only=True)
@json_option("Print the library as JSON.")
@pass_context
def script_list(ctx: Context) -> None:
    """List the library: what ships with Noust, then your own."""
    entries = [entry.to_dict(content=False) for entry in _library().list()]
    if _dump(ctx, entries):
        return
    ctx.logger.table(
        ["Name", "Kind", "Size", "Description"],
        [
            [
                e["name"] + (" (built in)" if e["builtin"] else ""),
                e["kind"],
                f"{e['bytes']} B",
                e["description"],
            ]
            for e in entries
        ],
    )


@script_cli.command("show", read_only=True)
@click.argument("name")
@pass_context
def script_show(ctx: Context, name: str) -> None:
    """Print the text of a library entry."""
    entry = _library().get(name)
    if ctx.json_output:
        click.echo(json.dumps(entry.to_dict()))
        return
    click.echo(entry.content, nl=False)


@script_cli.command("save")
@click.argument("name")
@click.option(
    "--kind",
    type=click.Choice(["script", "cloud-config"]),
    default=None,
    help="A shell script, or a #cloud-config fragment. Guessed from the first line.",
)
@click.option(
    "--file",
    "path",
    type=click.File("r"),
    default="-",
    help="Read it from this file (default: standard input).",
)
@click.option("--description", default="", help="What it does, in a line.")
@json_option("Print the entry as JSON.")
@pass_context
def script_save(ctx: Context, name: str, kind: str | None, path: Any, description: str) -> None:
    """Add a script or fragment to the library, or replace your own of the same name."""
    content = path.read()
    if kind is None:
        kind = "cloud-config" if content.startswith("#cloud-config") else "script"
    if ctx.dry_run:
        ctx.logger.info(f"would save the {kind} {name} ({len(content)} characters)")
        return
    entry = _library().save(name, kind, content, description=description, actor=_operator())
    if _dump(ctx, entry.to_dict(content=False)):
        return
    ctx.logger.success(f"Saved {name} ({entry.kind})")


@script_cli.command("delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@pass_context
def script_delete(ctx: Context, name: str, yes: bool) -> None:
    """Delete one of your own library entries."""
    if ctx.dry_run:
        ctx.logger.info(f"would delete the script {name}")
        return
    if not yes and not click.confirm(f"Delete {name}?", default=False):
        raise NodeError("Cancelled: nothing was changed")
    _library().delete(name, actor=_operator())
    ctx.logger.success(f"Deleted {name}")


@script_cli.command("preview", read_only=True)
@click.argument("names", nargs=-1)
@pass_context
def script_preview(ctx: Context, names: tuple[str, ...]) -> None:
    """Print the document a new server would boot with when given NAMES, its host key withheld."""
    from noust.fleet.cloud.init import preview

    click.echo(preview(_library().resolve(list(names))), nl=False)


# ---------------------------------------------------------------- servers


@cloud_cli.group("server", cls=NoustGroup)
def server_cli() -> None:
    """The machines this central created at a provider."""


@server_cli.command("create")
@click.argument("name")
@click.option("--account", required=True, help="The cloud account to create it with.")
@click.option("--location", required=True, help="A location id from 'noust fleet cloud catalog'.")
@click.option("--plan", required=True, help="A plan id from the catalog.")
@click.option("--image", required=True, help="An image id from the catalog.")
@click.option(
    "--access",
    type=click.Choice(["read", "deploy", "admin"]),
    default="admin",
    show_default=True,
    help="The most this central may do on the server.",
)
@click.option(
    "--allow-host-access/--no-host-access",
    default=False,
    help="Also let this central reach the host itself.",
)
@click.option(
    "--ssh-key",
    "ssh_keys",
    multiple=True,
    help="Id of one of the account's SSH keys to authorize for root too (repeat).",
)
@click.option(
    "--firewall",
    "firewalls",
    multiple=True,
    help="Id of a firewall to attach (repeat where the provider allows).",
)
@click.option(
    "--network",
    "networks",
    multiple=True,
    help="Id of a private network to attach (repeat where the provider allows).",
)
@click.option("--ipv4/--no-ipv4", default=True, show_default=True, help="A public IPv4 address.")
@click.option("--ipv6/--no-ipv6", default=True, show_default=True, help="A public IPv6 address.")
@click.option("--backups", is_flag=True, help="Turn on the provider's backups (they cost extra).")
@click.option(
    "--script", "scripts", multiple=True, help="A library entry to boot with, in order (repeat)."
)
@click.option(
    "--delete-on-failure", is_flag=True, help="Delete the machine when it does not join the fleet."
)
@click.option(
    "--yes", "-y", is_flag=True, help="Do not ask: the machine costs money until it is deleted."
)
@json_option("Print the node as JSON.")
@pass_context
def server_create(
    ctx: Context,
    name: str,
    account: str,
    location: str,
    plan: str,
    image: str,
    access: str,
    allow_host_access: bool,
    ssh_keys: tuple[str, ...],
    firewalls: tuple[str, ...],
    networks: tuple[str, ...],
    ipv4: bool,
    ipv6: bool,
    backups: bool,
    scripts: tuple[str, ...],
    delete_on_failure: bool,
    yes: bool,
) -> None:
    """
    Create the server NAME at a provider, install Noust on it and join it to this central.

    The central chooses the machine's SSH host key before it exists, so it pins it instead
    of trusting whatever answers; logs in once with a key it generates and then forgets;
    and joins it exactly as 'noust fleet enroll' does. Needs two-factor sign-in on this
    central, like 'noust node add'.
    """
    from noust.fleet.cloud.provision import ProvisionRequest

    request = ProvisionRequest(
        name=name,
        account=account,
        location=location,
        plan=plan,
        image=image,
        access=access,
        host_access=allow_host_access,
        ssh_keys=list(ssh_keys),
        firewalls=list(firewalls),
        networks=list(networks),
        ipv4=ipv4,
        ipv6=ipv6,
        backups=backups,
        scripts=list(scripts),
        delete_on_failure=delete_on_failure,
    )
    provisioner = _provisioner()
    provider, _entries = provisioner.validate(request)
    logger = ctx.logger
    if ctx.dry_run:
        logger.info(
            f"would create {name} at {provider.display_name} ({plan} in {location}, {image}), "
            f"install Noust on it and join it (access {access})"
        )
        return
    if not yes:
        if not _interactive():
            raise NodeError(
                "There is no terminal to confirm on",
                details="The machine costs money until it is deleted: pass --yes.",
            )
        if not click.confirm(
            f"Create {name} ({plan}, {location}) at {provider.display_name}? It is billed until you delete it.",
            default=False,
        ):
            raise NodeError("Cancelled: nothing was created")

    def log(message: str, level: str = "info") -> None:
        if ctx.json_output:
            return
        getattr(logger, _LEVELS.get(level, "info"))(message)

    node = provisioner.provision(request, actor=_operator(), log=log)
    if _dump(ctx, {"node": node.to_dict(), "machine": provisioner.machine(name)}):
        return
    logger.success(
        f"Server {name} created and added: Noust {node.version or 'unknown'}, {node.status}"
    )


@server_cli.command("list", read_only=True)
@json_option("Print the machines as JSON.")
@pass_context
def server_list(ctx: Context) -> None:
    """List the machines this central created."""
    machines = [machine.to_dict() for machine in _provisioner().servers.list()]
    if _dump(ctx, machines):
        return
    if not machines:
        ctx.logger.info(
            "This central has created no machine yet. Create one: noust fleet cloud server create"
        )
        return
    ctx.logger.table(
        ["Server", "State", "Provider", "Account", "Address", "Plan", "Location", "Created"],
        [
            [
                m["node"],
                m["state"],
                m["provider"],
                m["account"],
                m["address"] or "-",
                m["plan"] or "-",
                m["location"] or "-",
                m["created_at"],
            ]
            for m in machines
        ],
    )


@server_cli.command("show", read_only=True)
@click.argument("name")
@click.option("--live", is_flag=True, help="Ask the provider for the machine's state too.")
@json_option("Print the machine as JSON.")
@pass_context
def server_show(ctx: Context, name: str, live: bool) -> None:
    """Describe a machine this central created."""
    machine = _provisioner().machine(name, live=live)
    if _dump(ctx, machine):
        return
    for key, value in machine.items():
        if value not in (None, ""):
            click.echo(f"{key}: {value}")


@server_cli.command("power-on")
@click.argument("name")
@pass_context
def server_power_on(ctx: Context, name: str) -> None:
    """Start a machine that is off."""
    if ctx.dry_run:
        ctx.logger.info(f"would power {name} on at its provider")
        return
    _provisioner().power_on(name, actor=_operator())
    ctx.logger.success(f"{name} is starting")


@server_cli.command("delete")
@click.argument("name")
@click.option(
    "--remove-node", is_flag=True, help="Also remove the server from this central's fleet."
)
@click.option(
    "--yes", "-y", is_flag=True, help="Do not ask. The machine and its disk are destroyed."
)
@pass_context
def server_delete(ctx: Context, name: str, remove_node: bool, yes: bool) -> None:
    """Delete the machine NAME at its provider: it is destroyed, and the charge for it stops."""
    if ctx.dry_run:
        ctx.logger.info(f"would delete the machine {name} at its provider")
        return
    if not yes:
        if not _interactive():
            raise NodeError(
                "There is no terminal to confirm on",
                details="Deleting a machine destroys it and everything on it: pass --yes.",
            )
        typed = click.prompt(f"Type {name} to delete the machine and everything on it", default="")
        if typed != name:
            raise NodeError("Cancelled: nothing was deleted")
    warnings = _provisioner().delete_machine(name, remove_node=remove_node, actor=_operator())
    ctx.logger.success(f"{name} was deleted at its provider")
    for warning in warnings:
        ctx.logger.warning(warning)
