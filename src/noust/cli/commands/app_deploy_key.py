# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app deploy-key``: an SSH key of its own for each repository.

``generate`` makes an ed25519 key for the repository an application is built
from and prints the public half to add at the code host; ``import`` takes a key
the operator already has (read from a file or from standard input, never from
an argument); ``rotate`` makes the key that will replace it while the old one
keeps working. This module only parses, asks and prints: everything it does is
:class:`noust.managers.source_keys.DeployKeyManager`, which the console's API
calls too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.deploy_keys import DeployKey
from noust.core.exceptions import ValidationError
from noust.core.fs import is_rehearsal
from noust.core.logger import Logger
from noust.managers.source_keys import MAX_PRIVATE_KEY, DeployKeyManager, DeployKeyView


def _when(iso: str | None) -> str:
    """
    Show a stored timestamp the way a terminal wants it.

    Args:
        iso: An ISO 8601 timestamp, or None.

    Returns:
        ``2026-09-29 10:15:02``, or ``-`` for none.
    """
    return iso[:19].replace("T", " ") if iso else "-"


def key_payload(key: DeployKey, apps: list[str] | None = None) -> dict[str, Any]:
    """
    Describe a key as JSON, without its private half.

    Args:
        key: The key.
        apps: The applications it reaches, when known.

    Returns:
        Plain data.
    """
    return {
        "name": key.name,
        "origin": key.origin,
        "key_type": key.key_type,
        "fingerprint": key.fingerprint,
        "public_key": key.public_key,
        "created_at": key.created_at,
        "rotated_at": key.rotated_at,
        "last_used_at": key.last_used_at,
        "repositories": list(key.repositories),
        "applications": apps if apps is not None else [],
        "pending": (
            {
                "fingerprint": key.pending.fingerprint,
                "public_key": key.pending.public_key,
                "since": key.pending.since,
            }
            if key.pending
            else None
        ),
    }


def _tell_what_to_do(logger: Logger, key: DeployKey, view: DeployKeyView) -> None:
    """
    Say where the public key goes, which is what the operator does next.

    Args:
        logger: Where to write.
        key: The key.
        view: The application's view.
    """
    logger.key_value("Public key", key.public_key)
    logger.key_value("Fingerprint", key.fingerprint)
    host = (view.repository or "").partition("/")[0]
    where = {
        "github.com": "the repository's Settings > Deploy keys (read-only is enough)",
        "gitlab.com": "the project's Settings > Repository > Deploy keys",
        "bitbucket.org": "the repository's Repository settings > Access keys",
    }.get(host, "the repository's deploy keys at its code host")
    logger.info(
        f"Add the public key to {where}. A deploy key is read-only unless you allow writes."
    )
    if view.ssh_url:
        logger.warning(
            f"{view.domain} is cloned over https, which a deploy key does not sign in to. "
            f"Deploy it from {view.ssh_url} to use the key."
        )


def _describe(logger: Logger, view: DeployKeyView) -> None:
    """
    Print an application's deploy key for a person.

    Args:
        logger: Where to write.
        view: What :meth:`DeployKeyManager.view` found.
    """
    logger.key_value("Application", view.domain)
    logger.key_value("Repository", view.repository or "not a git repository")
    key = view.key
    if key is None:
        logger.key_value("Deploy key", "none: this repository is reached with the server-wide key")
        if view.server_key_fingerprint:
            logger.key_value("Server-wide key", view.server_key_fingerprint)
        logger.info(f"Make one with: noust app deploy-key generate {view.domain}")
    else:
        logger.key_value("Deploy key", f"{key.name} ({key.origin}, {key.key_type})")
        logger.key_value("Public key", key.public_key)
        logger.key_value("Fingerprint", key.fingerprint)
        logger.key_value("Created", _when(key.created_at))
        logger.key_value("Last rotated", _when(key.rotated_at))
        logger.key_value("Last used", _when(key.last_used_at))
        if len(key.repositories) > 1:
            logger.key_value("Also reaches", ", ".join(r for r in key.repositories))
        if key.pending is not None:
            logger.warning(
                "A rotation is waiting. Add this public key at the code host, keeping the "
                "old one there until it works, then run "
                f"'noust app deploy-key rotate {view.domain} --finish':"
            )
            logger.key_value("New public key", key.pending.public_key)
            logger.key_value("New fingerprint", key.pending.fingerprint)
    if view.shared_with:
        logger.key_value("Shared with", ", ".join(view.shared_with))
    for warning in view.warnings:
        logger.warning(warning)


@click.group("deploy-key", cls=NoustGroup)
def deploy_key() -> None:
    """An SSH key of its own for the repository an application is built from."""


@deploy_key.command("show", read_only=True)
@click.argument("domain")
@global_flags
@json_option("Print the key (never its private half) as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """
    Show the deploy key of an application's repository, and the public half to add at the code host.

    Without one, the repository is reached with the server-wide key that
    'noust setup ssh' manages.
    """
    manager = DeployKeyManager()
    view = manager.view(domain)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": view.domain,
                    "repository": view.repository,
                    "source_is_ssh": view.source_is_ssh,
                    "ssh_url": view.ssh_url,
                    "shared_with": list(view.shared_with),
                    "server_key_fingerprint": view.server_key_fingerprint,
                    "warnings": list(view.warnings),
                    "key": key_payload(view.key, manager.apps_of(view.key)) if view.key else None,
                }
            )
        )
        return
    _describe(ctx.logger, view)


@deploy_key.command("list", read_only=True)
@global_flags
@json_option("Print the keys as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List every deploy key and the applications it reaches."""
    manager = DeployKeyManager()
    keys = manager.keys()
    if ctx.json_output:
        click.echo(json.dumps({"keys": [key_payload(key, manager.apps_of(key)) for key in keys]}))
        return
    if not keys:
        ctx.logger.info("No deploy keys: every repository is reached with the server-wide key.")
        return
    ctx.logger.table(
        ["Name", "Origin", "Fingerprint", "Applications", "Last used", "Rotation"],
        [
            [
                key.name,
                key.origin,
                key.fingerprint,
                ", ".join(manager.apps_of(key)) or "none (noust app deploy-key prune)",
                _when(key.last_used_at),
                "waiting" if key.pending else "-",
            ]
            for key in keys
        ],
    )


@deploy_key.command("generate")
@click.argument("domain")
@click.option("--name", help="The key's name. The application's name by default.")
@global_flags
@json_option("Print the key as JSON.")
@pass_context
def generate_command(ctx: Context, domain: str, name: str | None) -> None:
    """
    Make an ed25519 key, with no passphrase, for the application's repository.

    The private key is kept in the secret store (0600) and never printed. Add
    the public key printed here to the repository at the code host, and deploy
    from its ssh URL.
    """
    manager = DeployKeyManager()
    if is_rehearsal():
        ctx.logger.info(f"Would generate a deploy key for {domain}")
        return
    key = manager.generate(domain, name=name)
    view = manager.view(domain)
    if ctx.json_output:
        click.echo(json.dumps(key_payload(key, manager.apps_of(key))))
        return
    ctx.logger.success(f"Deploy key {key.name} created for {view.repository}")
    _tell_what_to_do(ctx.logger, key, view)


def _read_private_key(path: str) -> str:
    """
    Read the private key to import, from a file or from standard input.

    Never an argument: everything on a command line is visible in ``ps``.

    Args:
        path: A file, or ``-`` for standard input.

    Returns:
        The key's text.

    Raises:
        ValidationError: When it cannot be read or is not text.
    """
    try:
        if path == "-":
            return sys.stdin.read(MAX_PRIVATE_KEY + 1)
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"Cannot read the private key from {path}",
            details=str(exc),
            field="private_key",
        ) from exc


@deploy_key.command("import")
@click.argument("domain")
@click.option(
    "--private-key-file",
    "key_file",
    required=True,
    help="File with the private key, or - to read it from standard input. It must have no "
    "passphrase.",
)
@click.option("--name", help="The key's name. The application's name by default.")
@global_flags
@json_option("Print the key as JSON.")
@pass_context
def import_command(ctx: Context, domain: str, key_file: str, name: str | None) -> None:
    """
    Use a private key you already have as the application's deploy key.

    The public half is derived from it. The key is copied into the secret
    store; the file you read it from is left alone, so remove it yourself.
    """
    manager = DeployKeyManager()
    text = _read_private_key(key_file)
    if is_rehearsal():
        ctx.logger.info(f"Would import a deploy key for {domain}")
        return
    key = manager.import_key(domain, text, name=name)
    view = manager.view(domain)
    if ctx.json_output:
        click.echo(json.dumps(key_payload(key, manager.apps_of(key))))
        return
    ctx.logger.success(f"Deploy key {key.name} imported for {view.repository}")
    _tell_what_to_do(ctx.logger, key, view)


@deploy_key.command("assign")
@click.argument("domain")
@click.argument("key")
@global_flags
@pass_context
def assign_command(ctx: Context, domain: str, key: str) -> None:
    """
    Make an existing key the one that reaches this application's repository.

    For a key the code host accepts on several repositories, such as a machine
    user's. GitHub does not accept one deploy key on two repositories.
    """
    assigned = DeployKeyManager().assign(domain, key)
    if is_rehearsal():
        ctx.logger.info(f"Would assign {key} to the repository of {domain}")
        return
    ctx.logger.success(f"{assigned.name} now reaches {', '.join(assigned.repositories)}")


@deploy_key.command("rotate")
@click.argument("domain")
@click.option(
    "--finish",
    is_flag=True,
    help="Ask the code host with the new key now; if it answers, the new key replaces the old.",
)
@click.option("--cancel", is_flag=True, help="Give up the rotation; the old key stays.")
@global_flags
@json_option("Print the key as JSON.")
@pass_context
def rotate_command(ctx: Context, domain: str, finish: bool, cancel: bool) -> None:
    """
    Make the key that will replace the application's, keeping the old one working.

    Add the new public key at the code host, keeping the old one there. The next
    clone or fetch that the code host answers with the new key makes it the key
    and removes the old; --finish does that check right now.
    """
    if finish and cancel:
        raise ValidationError("Choose --finish or --cancel, not both")
    manager = DeployKeyManager()
    if finish:
        ctx.logger.info(manager.verify(domain))
        return
    if cancel:
        manager.rotate_cancel(domain)
        ctx.logger.success(f"The rotation of the key of {domain} was cancelled")
        return
    if is_rehearsal():
        ctx.logger.info(f"Would make the key that replaces the deploy key of {domain}")
        return
    key = manager.rotate_start(domain)
    view = manager.view(domain)
    if ctx.json_output:
        click.echo(json.dumps(key_payload(key, manager.apps_of(key))))
        return
    if key.pending is None:  # pragma: no cover - rotate_start always sets it
        return
    ctx.logger.success(f"A new key waits to replace {key.name}; the old one still works")
    ctx.logger.key_value("New public key", key.pending.public_key)
    ctx.logger.key_value("New fingerprint", key.pending.fingerprint)
    ctx.logger.info(
        f"Add it to {view.repository} at the code host, keeping the old one there, then run "
        f"'noust app deploy-key rotate {domain} --finish'. The first clone or fetch the host "
        "answers with it makes it the key, and the old one is removed from this server."
    )


@deploy_key.command("test", read_only=True)
@click.argument("domain")
@global_flags
@pass_context
def test_command(ctx: Context, domain: str) -> None:
    """Ask the repository for its branches with the deploy key, as a deploy would."""
    ctx.logger.success(DeployKeyManager().verify(domain))


@deploy_key.command("remove")
@click.argument("domain")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@global_flags
@pass_context
def remove_command(ctx: Context, domain: str, yes: bool) -> None:
    """
    Take the repository off its deploy key, and delete the key when nothing else uses it.

    The repository goes back to the server-wide key. Remove the public key at
    the code host as well: this server can no longer prove it is allowed to
    use it, but the host still trusts it.
    """
    manager = DeployKeyManager()
    view = manager.view(domain)
    if view.key is None:
        ctx.logger.info(f"{domain} has no deploy key of its own")
        return
    if not yes and not ctx.dry_run:
        click.confirm(
            f"Remove the deploy key {view.key.name} from {view.repository}? A deploy that "
            "needs it fails until another key is in place",
            abort=True,
        )
    if is_rehearsal():
        ctx.logger.info(f"Would remove the deploy key {view.key.name} from {view.repository}")
        return
    manager.remove(domain)
    ctx.logger.success(f"Removed the deploy key {view.key.name} from {view.repository}")
    ctx.logger.info(
        f"Remove its public key ({view.key.fingerprint}) at the code host too: it still trusts it."
    )


@deploy_key.command("prune")
@global_flags
@pass_context
def prune_command(ctx: Context) -> None:
    """Delete the deploy keys whose repositories no application is built from any more."""
    if is_rehearsal():
        ctx.logger.info("Would delete the deploy keys no application uses")
        return
    deleted = DeployKeyManager().prune()
    if deleted:
        ctx.logger.success(f"Deleted: {', '.join(deleted)}")
    else:
        ctx.logger.info("Every deploy key has an application")


@deploy_key.command("hosts", read_only=True)
@global_flags
@json_option("Print the pinned host keys as JSON.")
@pass_context
def hosts_command(ctx: Context) -> None:
    """List the SSH host keys Noust pinned the first time it connected to each code host."""
    hosts = DeployKeyManager().pinned_hosts()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "hosts": [
                        {"host": h.host, "fingerprint": h.fingerprint, "key_type": h.key_type}
                        for h in hosts
                    ]
                }
            )
        )
        return
    if not hosts:
        ctx.logger.info(
            "No host key is pinned yet: the first connection with a deploy key pins it."
        )
        return
    ctx.logger.table(
        ["Host", "Type", "Fingerprint"], [[h.host, h.key_type, h.fingerprint] for h in hosts]
    )


@deploy_key.command("pin-host")
@click.argument("host")
@click.option(
    "--fingerprint",
    required=True,
    metavar="SHA256:...",
    help="The host key's fingerprint, from the code host's own published list.",
)
@global_flags
@json_option("Print the pinned host key as JSON.")
@pass_context
def pin_host_command(ctx: Context, host: str, fingerprint: str) -> None:
    """
    Pin a code host's SSH key before the first connection, by the fingerprint it publishes.

    The host is asked for its keys and only the one with that fingerprint is recorded, so the
    first connection from this server is already to the host it should be, not to whatever
    answers first. GitHub and GitLab print their fingerprints in their documentation. A host
    that shows no key with that fingerprint is refused, with the ones it showed.
    """
    if is_rehearsal():
        ctx.logger.info(f"Would pin the host key of {host} with {fingerprint}")
        return
    pinned = DeployKeyManager().pin_host(host, fingerprint)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "host": pinned.host,
                    "fingerprint": pinned.fingerprint,
                    "key_type": pinned.key_type,
                }
            )
        )
        return
    ctx.logger.success(f"Pinned the {pinned.key_type} host key of {host}: {pinned.fingerprint}")


@deploy_key.command("forget-host")
@click.argument("host")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@global_flags
@pass_context
def forget_host_command(ctx: Context, host: str, yes: bool) -> None:
    """
    Forget a pinned host key, so the next connection pins the one the host shows now.

    This is the one way to accept a host key that changed. Compare the new
    fingerprint with the ones the code host publishes first: a key that
    changes for no reason is what a machine answering in the host's place looks
    like.
    """
    if not yes and not ctx.dry_run:
        click.confirm(
            f"Forget the pinned host key of {host}? The next connection will trust whatever "
            "key it shows",
            abort=True,
        )
    if is_rehearsal():
        ctx.logger.info(f"Would forget the pinned host key of {host}")
        return
    if DeployKeyManager().forget_host(host):
        ctx.logger.success(f"Forgot the pinned host key of {host}")
    else:
        ctx.logger.info(f"No host key of {host} was pinned")
