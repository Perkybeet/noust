# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust push``: deploy an application with ``git push``, to a repository on this server.

``setup`` prepares the server once (the account pushes log in as and sshd's rules for it);
``enable`` gives an application a repository, a hook and a branch; ``key`` says who may push,
one key per application; ``show`` and ``status`` say how it stands; ``refresh`` writes the hook
again; ``disable`` puts the application's source back. Each is :mod:`noust.pushdeploy`, which
``/api/push`` and ``/api/apps/{d}/push`` call too. This module only parses and presents.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.pushdeploy.host import PushHost
from noust.pushdeploy.keys import MAX_KEY_LINE
from noust.pushdeploy.repos import PushInfo, PushRepos, ssh_config_snippet
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _repos() -> PushRepos:
    """
    Returns:
        The push deployments of this server, acting for whoever runs the command.
    """
    return PushRepos(actor=_actor())


def _read_key(key: str | None, key_file: str | None) -> str | None:
    """
    Read a public key from where it may be given.

    Args:
        key: The key's line, given as an argument.
        key_file: A ``.pub`` file, or ``-`` for standard input.

    Returns:
        The line; None when none was given.

    Raises:
        ValidationError: Both were given, or the file cannot be read.
    """
    if key is not None and key_file is not None:
        raise ValidationError(
            "Give the key as --key or as --key-file, not both",
            details="--key-file ~/.ssh/id_ed25519.pub is the usual way.",
            field="key",
        )
    if key_file is None:
        return key
    try:
        text = (
            sys.stdin.read(MAX_KEY_LINE + 1)
            if key_file == "-"
            else Path(key_file).expanduser().read_text("utf-8")
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"Cannot read {key_file}: {exc}",
            details="Give a readable .pub file: the public half of the key.",
            field="key",
        ) from exc
    if len(text) > MAX_KEY_LINE:
        raise ValidationError(
            f"{key_file} is larger than a public key", details="Give the .pub file.", field="key"
        )
    return text.strip()


def _show(ctx: Context, info: PushInfo, *, with_ssh_config: bool = False) -> None:
    """
    Print one application's push deployment, for a person or as JSON.

    Args:
        ctx: The CLI context.
        info: What to say.
        with_ssh_config: Also print the lines of ``~/.ssh/config`` that select its key.
    """
    if ctx.json_output:
        click.echo(json.dumps(info.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Application", info.domain)
    if not info.enabled:
        logger.key_value("Git push", "off")
        logger.info(f"Turn it on with: noust push enable {info.domain}")
        return
    logger.key_value("Git push", "on")
    logger.key_value("Branch", info.branch or "-")
    logger.key_value("Repository", info.repository or "-")
    logger.key_value("Remote", info.remote or "-")
    logger.key_value("Last push", info.last_push_at or "never")
    if info.keys:
        for key in info.keys:
            logger.key_value(f"Key {key.label}", f"{key.key_type} {key.fingerprint}")
    else:
        logger.warning(
            "No key may push yet. Add one: "
            f"noust push key add {info.domain} LABEL --key-file ~/.ssh/id_ed25519.pub"
        )
    for problem in info.problems:
        logger.warning(problem)
    if with_ssh_config and info.remote:
        host = info.remote.split("@", 1)[1].split(":", 1)[0].removeprefix("ssh://")
        logger.blank()
        logger.info("In ~/.ssh/config, so that this application's key is the one used:")
        logger.info(ssh_config_snippet(info.domain, host).rstrip())


@click.group("push", cls=NoustGroup)
def cli() -> None:
    """Deploy an application with git push, to a repository on this server."""


@cli.command("setup")
@json_option("Print the result as JSON.")
@pass_context
def setup_command(ctx: Context) -> None:
    """
    Prepare this server to take git pushes (once, as root).

    Creates the account pushes log in as (noust-git, with git-shell for a shell) and the
    directory that holds the repositories, and restricts the account in sshd: only public keys,
    no terminal, no forwarding, no agent. A key can then only run git-receive-pack on the one
    repository it was added for. sshd is checked before it is reloaded, and the change is
    undone if sshd does not apply it.
    """
    result = PushHost().setup(dry_run=ctx.dry_run, actor=_actor())
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    prefix = "Would prepare" if ctx.dry_run else "Prepared"
    ctx.logger.success(f"{prefix} this server for git push")
    ctx.logger.key_value(
        "Account", result.account + (" (created)" if result.account_created else "")
    )
    ctx.logger.key_value("Repositories in", result.home)
    ctx.logger.key_value("sshd", result.sshd_policy + (" (written)" if result.changed else ""))
    ctx.logger.key_value("Keys file", result.keys_file)
    ctx.logger.info("Turn it on for an application with: noust push enable DOMAIN --key-file ...")


@cli.command("status", read_only=True)
@json_option("Print the status as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Say whether this server takes git pushes, and what stops it."""
    status = PushHost().status()
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    ctx.logger.key_value("Takes pushes", "yes" if status.ready else "no")
    ctx.logger.key_value("Account", "exists" if status.account else "missing")
    ctx.logger.key_value("sshd port", str(status.ssh_port))
    ctx.logger.key_value("Repositories in", status.home)
    for problem in status.problems:
        ctx.logger.warning(problem)


@cli.command("list", read_only=True)
@json_option("Print the applications as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the applications that deploy from git push."""
    infos = _repos().list()
    if ctx.json_output:
        click.echo(json.dumps([info.to_dict() for info in infos]))
        return
    if not infos:
        ctx.logger.info(
            "No application deploys from git push. Turn one on: noust push enable DOMAIN"
        )
        return
    for info in infos:
        ctx.logger.key_value(
            info.domain,
            f"{info.branch}  {len(info.keys)} key(s)  last push {info.last_push_at or 'never'}"
            + ("" if info.hook_ok else "  (hook needs: noust push refresh)"),
        )


@cli.command("show", read_only=True)
@click.argument("domain")
@click.option("--host", metavar="NAME", help="The server's name, for the remote (default: DOMAIN).")
@json_option("Print the deployment as JSON.")
@pass_context
def show_command(ctx: Context, domain: str, host: str | None) -> None:
    """Show how an application's push deployment stands: branch, remote, keys, hook."""
    _show(ctx, _repos().describe(validate_domain(domain), host=host), with_ssh_config=True)


@cli.command("enable")
@click.argument("domain")
@click.option(
    "--branch", metavar="NAME", help="The branch a push deploys (default: the pinned one, or main)."
)
@click.option(
    "--key-file",
    metavar="FILE",
    help="A public key that may push (a .pub file; '-' reads standard input).",
)
@click.option("--key", metavar="LINE", help="A public key that may push, as its line.")
@click.option("--label", metavar="NAME", help="What to call that key: laptop, ci...")
@click.option("--host", metavar="NAME", help="The server's name, for the remote (default: DOMAIN).")
@json_option("Print the deployment as JSON.")
@pass_context
def enable_command(
    ctx: Context,
    domain: str,
    branch: str | None,
    key_file: str | None,
    key: str | None,
    label: str | None,
    host: str | None,
) -> None:
    """
    Make an application deploy from git push.

    The application gets a repository on this server, a hook, and a branch; its source becomes
    that repository. The next git push to that branch updates the application the way a code
    host's webhook does (the same queue, the same health gate and the previous release one
    command away). It starts empty: the first push replaces what is deployed.

    The application must be on the release layout; Docker Compose stacks and containers keep
    their own sources.
    """
    validated = validate_domain(domain)
    line = _read_key(key, key_file)
    if ctx.dry_run:
        ctx.logger.info(f"Would turn git push on for {validated}")
        return
    info = _repos().enable(validated, branch=branch, key=line, label=label, host=host)
    if ctx.json_output:
        click.echo(json.dumps(info.to_dict()))
        return
    ctx.logger.success(f"{validated} deploys from git push to {info.branch}")
    _show(ctx, info)
    if info.remote:
        ctx.logger.blank()
        ctx.logger.info("From your computer:")
        ctx.logger.info(f"  git remote add noust {info.remote}")
        ctx.logger.info(f"  git push noust {info.branch}")


@cli.command("disable")
@click.argument("domain")
@click.option(
    "--delete-repository", is_flag=True, help="Also delete the repository (the code pushed to it)."
)
@json_option("Print the result as JSON.")
@pass_context
def disable_command(ctx: Context, domain: str, delete_repository: bool) -> None:
    """
    Make an application stop deploying from git push.

    Its source and pinned branch go back to what they were before. The keys that could push
    are removed; the repository is kept unless asked.
    """
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would turn git push off for {validated}")
        return
    info = _repos().disable(validated, delete_repository=delete_repository)
    if ctx.json_output:
        click.echo(json.dumps(info.to_dict()))
        return
    ctx.logger.success(f"{validated} no longer deploys from git push")
    ctx.logger.info("Its source is what it was before; the next update builds that.")


@cli.command("refresh")
@click.argument("domain")
@json_option("Print the deployment as JSON.")
@pass_context
def refresh_command(ctx: Context, domain: str) -> None:
    """
    Write an application's hook and its settings again.

    For a console that moved to another port, a hook that was edited by hand, or a webhook
    secret that was rotated while the hook could not be reached.
    """
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would write the hook of {validated} again")
        return
    info = _repos().refresh(validated)
    if ctx.json_output:
        click.echo(json.dumps(info.to_dict()))
        return
    ctx.logger.success(f"Wrote the hook of {validated} again")
    _show(ctx, info)


@cli.group("key", cls=NoustGroup)
def key_group() -> None:
    """The keys that may push to an application's repository (one key per application)."""


@key_group.command("list", read_only=True)
@click.argument("domain")
@json_option("Print the keys as JSON.")
@pass_context
def key_list_command(ctx: Context, domain: str) -> None:
    """List the keys that may push to an application."""
    validated = validate_domain(domain)
    keys = _repos().keys.list(validated)
    if ctx.json_output:
        click.echo(json.dumps([key.to_dict() for key in keys]))
        return
    if not keys:
        ctx.logger.info(f"No key may push to {validated}.")
        return
    for key in keys:
        ctx.logger.key_value(key.label, f"{key.key_type} {key.fingerprint}")


@key_group.command("add")
@click.argument("domain")
@click.argument("label")
@click.option("--key-file", metavar="FILE", help="The public key (a .pub file; '-' reads stdin).")
@click.option("--key", metavar="LINE", help="The public key, as its line.")
@json_option("Print the keys as JSON.")
@pass_context
def key_add_command(
    ctx: Context, domain: str, label: str, key_file: str | None, key: str | None
) -> None:
    """
    Let a key push to an application's repository.

    A key serves one application: sshd reads the first line a key has, so make one for each
    (ssh-keygen -t ed25519) and name it in ~/.ssh/config (noust push show prints the lines).
    """
    validated = validate_domain(domain)
    line = _read_key(key, key_file)
    if line is None:
        raise click.UsageError("Give the key with --key-file or --key")
    if ctx.dry_run:
        ctx.logger.info(f"Would let the key {label} push to {validated}")
        return
    keys = _repos().add_key(validated, label, line)
    if ctx.json_output:
        click.echo(json.dumps([entry.to_dict() for entry in keys]))
        return
    ctx.logger.success(f"The key {label} may now push to {validated}")


@key_group.command("remove")
@click.argument("domain")
@click.argument("which", metavar="LABEL_OR_FINGERPRINT")
@json_option("Print the keys that are left as JSON.")
@pass_context
def key_remove_command(ctx: Context, domain: str, which: str) -> None:
    """Take a key's permission to push to an application away."""
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would remove the key {which} of {validated}")
        return
    keys = _repos().remove_key(validated, which)
    if ctx.json_output:
        click.echo(json.dumps([entry.to_dict() for entry in keys]))
        return
    ctx.logger.success(f"The key {which} may no longer push to {validated}")
