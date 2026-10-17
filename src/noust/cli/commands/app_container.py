# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app container``: how a container application is built and run.

``show`` says which image the application is made from (a Dockerfile, Railpack
or a registry) and how it runs; ``set`` changes what it is told to, validated
whole before anything is written, and takes effect with the next ``noust
update``. Both are :mod:`noust.deployers.helpers.container_settings`, which
``/api/apps/{d}/container`` calls too and which records the change in the
audit trail. This module only parses, presents and asks.

It also holds the options ``noust create`` shares with ``set``
(:func:`container_options`, :func:`changes_from_options`): one definition of
what a person may say about a container, so the two commands cannot drift.
A secret never travels on the command line: a registry password is read from
standard input and a build secret from a file.
"""

from __future__ import annotations

import json
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.deployers.helpers.container_settings import (
    ContainerView,
    change_container,
    describe_container,
)
from noust.validators.container import METHODS
from noust.validators.domain import validate_domain

F = TypeVar("F", bound=Callable[..., Any])

#: The largest secret read from a file or from standard input.
_MAX_SECRET = 64 * 1024

#: What ``--clear`` may name, spelled as the other options are.
CLEARABLE = (
    "dockerfile",
    "target",
    "container-port",
    "registry-server",
    "command",
    "entrypoint",
)


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _pairs(values: tuple[str, ...], what: str) -> dict[str, str]:
    """
    Read ``NAME=VALUE`` options.

    Args:
        values: The options as given.
        what: What they are, for the error.

    Returns:
        Name to value.

    Raises:
        click.UsageError: One has no ``=``.
    """
    pairs: dict[str, str] = {}
    for item in values:
        name, separator, value = item.partition("=")
        if not separator or not name:
            raise click.UsageError(f"{what} {item!r} is not NAME=VALUE")
        pairs[name] = value
    return pairs


def _read_secret_file(path: str) -> str:
    """
    Read a secret from a file, so it is never on a command line.

    Args:
        path: The file.

    Returns:
        Its text, without the newline an editor adds at the end.

    Raises:
        ValidationError: It cannot be read or is larger than a secret is.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"Cannot read {path}: {exc}",
            details="Give a readable UTF-8 file that holds the secret.",
            field="build_secrets",
        ) from exc
    if len(text) > _MAX_SECRET:
        raise ValidationError(
            f"{path} is larger than {_MAX_SECRET // 1024} KiB", field="build_secrets"
        )
    return text[:-1] if text.endswith("\n") else text


def _read_password() -> str:
    """
    Read the registry password from standard input.

    Returns:
        The password, without its trailing newline.

    Raises:
        ValidationError: Nothing was piped in, or it is too large.
    """
    text = sys.stdin.read(_MAX_SECRET + 1)
    if not text.strip() or len(text) > _MAX_SECRET:
        raise ValidationError(
            "The registry password must be piped in on standard input",
            details="For example: printenv REGISTRY_TOKEN | noust app container set DOMAIN "
            "--registry-user USER --registry-password-stdin",
            field="registry_password",
        )
    return text.rstrip("\r\n")


def container_options(command: F) -> F:
    """
    Add the options that describe a container application to a command.

    Shared by ``noust create`` and ``noust app container set``.

    Args:
        command: The Click command function.

    Returns:
        The command, with the options.
    """
    options = [
        click.option("--image", metavar="REFERENCE", help="Deploy this image from a registry: "
                     "name[:tag][@sha256:digest]. Needs no source."),
        click.option("--builder", type=click.Choice([m for m in METHODS if m != "image"]),
                     help="How a repository becomes an image: its Dockerfile, or Railpack, which "
                     "needs none (noust setup railpack)."),
        click.option("--dockerfile", metavar="PATH",
                     help="The Dockerfile, relative to the repository. Default: Dockerfile or "
                     "Containerfile in the context."),
        click.option("--context", "build_context", metavar="DIR",
                     help="The build context, relative to the repository. Default: its root."),
        click.option("--target", metavar="STAGE",
                     help="The stage of a multi-stage Dockerfile to build."),
        click.option("--build-arg", "build_args", metavar="NAME=VALUE", multiple=True,
                     help="A build argument (not a secret: it is written into the image). "
                     "Repeat for several."),
        click.option("--build-secret", "build_secrets", metavar="ID=FILE", multiple=True,
                     help="A secret the build mounts, read from FILE and kept in the secret "
                     "store; never an argument of the build. Repeat for several."),
        click.option("--container-port", type=click.IntRange(1, 65535),
                     help="Where the process listens inside the container. Default: what the "
                     "Dockerfile or the image exposes."),
        click.option("--volume", "volumes", metavar="PATH", multiple=True,
                     help="A path in the container whose data survives every deployment, such "
                     "as /data. Repeat for several."),
        click.option("--restart", "restart_policy", metavar="POLICY",
                     help="Docker's restart policy: no, always, unless-stopped (default) or "
                     "on-failure."),
        click.option("--entrypoint", metavar="LINE",
                     help="Run this program instead of the image's ENTRYPOINT: the line is split "
                     "into words the way a shell would, and no shell runs it. An empty line "
                     "clears the image's entrypoint."),
        click.option("--command", "command_line", metavar="LINE",
                     help="Give the entrypoint these arguments instead of the image's CMD "
                     "(split into words, no shell). An empty line clears the image's CMD."),
        click.option("--registry-server", metavar="HOST",
                     help="The registry the account is for. Default: the image's own."),
        click.option("--registry-user", metavar="USER",
                     help="The account of a private registry."),
        click.option("--registry-password-stdin", is_flag=True,
                     help="Read the account's password from standard input."),
    ]  # fmt: skip
    for option in reversed(options):
        command = option(command)
    return command


def changes_from_options(
    *,
    image: str | None = None,
    builder: str | None = None,
    dockerfile: str | None = None,
    build_context: str | None = None,
    target: str | None = None,
    build_args: tuple[str, ...] = (),
    build_secrets: tuple[str, ...] = (),
    container_port: int | None = None,
    volumes: tuple[str, ...] = (),
    restart_policy: str | None = None,
    registry_server: str | None = None,
    registry_user: str | None = None,
    registry_password_stdin: bool = False,
    entrypoint: str | None = None,
    command_line: str | None = None,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Turn the options of :func:`container_options` into a change.

    Args:
        image: ``--image``.
        builder: ``--builder``.
        dockerfile: ``--dockerfile``.
        build_context: ``--context``.
        target: ``--target``.
        build_args: ``--build-arg``, ``NAME=VALUE`` each.
        build_secrets: ``--build-secret``, ``ID=FILE`` each.
        container_port: ``--container-port``.
        volumes: ``--volume``.
        restart_policy: ``--restart``.
        registry_server: ``--registry-server``.
        registry_user: ``--registry-user``.
        registry_password_stdin: ``--registry-password-stdin``.
        entrypoint: ``--entrypoint``, a line.
        command_line: ``--command``, a line.
        current: The settings now, when changing an application: build
            arguments and volumes given are added to these, not instead of them.

    Returns:
        The change, in the vocabulary of
        :mod:`noust.deployers.helpers.container_settings`; only what was given.

    Raises:
        click.UsageError: An option is malformed.
        ValidationError: A file or the password cannot be read.
    """
    changes: dict[str, Any] = {}
    for name, value in (
        ("image", image),
        ("method", builder),
        ("dockerfile", dockerfile),
        ("context", build_context),
        ("target", target),
        ("container_port", container_port),
        ("restart_policy", restart_policy),
        ("registry_server", registry_server),
        ("registry_username", registry_user),
    ):
        if value is not None:
            changes[name] = value
    if image is not None:
        changes["method"] = "image"
    if build_args:
        merged = dict((current or {}).get("build_args") or {})
        merged.update(_pairs(build_args, "--build-arg"))
        changes["build_args"] = merged
    if volumes:
        listed = list((current or {}).get("volumes") or [])
        changes["volumes"] = listed + [v for v in volumes if v not in listed]
    if build_secrets:
        changes["build_secret_values"] = {
            secret_id: _read_secret_file(path)
            for secret_id, path in _pairs(build_secrets, "--build-secret").items()
        }
    if registry_password_stdin:
        changes["registry_password"] = _read_password()
    # A line, which the settings split into words (and refuse when a quote is left open).
    if entrypoint is not None:
        changes["entrypoint"] = entrypoint
    if command_line is not None:
        changes["command"] = command_line
    return changes


def _print(ctx: Context, view: ContainerView) -> None:
    """
    Show the settings, for a human or as JSON.

    Args:
        ctx: The CLI context.
        view: The settings.
    """
    if ctx.json_output:
        click.echo(json.dumps(view.to_dict()))
        return
    settings = view.settings
    ctx.logger.key_value("Application", view.domain)
    ctx.logger.key_value("Image comes from", {
        "dockerfile": "a Dockerfile in the repository",
        "railpack": "Railpack, from the repository",
        "image": f"the registry: {settings['image']}",
    }[settings["method"]])  # fmt: skip
    if settings["method"] != "image":
        ctx.logger.key_value("Dockerfile", settings["dockerfile"] or "Dockerfile or Containerfile")
        ctx.logger.key_value("Context", settings["context"])
        if settings["target"]:
            ctx.logger.key_value("Target stage", settings["target"])
        for name, value in settings["build_args"].items():
            ctx.logger.key_value("Build argument", f"{name}={value}")
        for secret_id in settings["build_secrets"]:
            ctx.logger.key_value("Build secret", f"{secret_id} (value kept in the secret store)")
    ctx.logger.key_value("Container port", str(settings["container_port"]))
    for label, key in (("Entrypoint", "entrypoint"), ("Command", "command")):
        if settings.get(key) is not None:
            ctx.logger.key_value(label, shlex.join(settings[key]) or "(cleared)")
    ctx.logger.key_value("Restart", settings["restart_policy"])
    for volume in settings["volumes"]:
        ctx.logger.key_value("Volume", volume)
    if settings["registry_username"]:
        ctx.logger.key_value(
            "Registry account",
            f"{settings['registry_username']} at {settings['registry_server']} "
            "(password kept in the secret store)",
        )
    if settings["digest"]:
        ctx.logger.key_value("Digest", settings["digest"])
    ctx.logger.key_value("Release serving", view.release or "none")


@click.group("container", cls=NoustGroup)
def container() -> None:
    """How a container application is built and run: see it, change it."""


@container.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the settings as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """
    Show how a container application is built and run.

    The registry's password and the build secrets are never printed.
    """
    _print(ctx, describe_container(validate_domain(domain)))


@container.command("set")
@click.argument("domain")
@container_options
@click.option(
    "--remove-build-arg",
    "removed_args",
    metavar="NAME",
    multiple=True,
    help="Remove a build argument.",
)
@click.option(
    "--remove-build-secret",
    "removed_secrets",
    metavar="ID",
    multiple=True,
    help="Remove a build secret, and its value.",
)
@click.option(
    "--remove-volume", "removed_volumes", metavar="PATH", multiple=True,
    help="Stop keeping this path across deployments. The volume's data stays on the server.",
)  # fmt: skip
@click.option(
    "--clear",
    "cleared",
    type=click.Choice(CLEARABLE),
    multiple=True,
    help="Go back to the default of a setting: it is read from the Dockerfile or the image again.",
)
@click.option("--clear-registry", is_flag=True, help="Remove the registry account.")
@json_option("Print the settings now as JSON.")
@pass_context
def set_command(
    ctx: Context,
    domain: str,
    removed_args: tuple[str, ...],
    removed_secrets: tuple[str, ...],
    removed_volumes: tuple[str, ...],
    cleared: tuple[str, ...],
    clear_registry: bool,
    **options: Any,
) -> None:
    """
    Change how a container application is built and run.

    What is not named stays as it is. The whole change is checked before
    anything is written, and takes effect with the next 'noust update'.
    """
    validated = validate_domain(domain)
    current = describe_container(validated).settings
    changes = changes_from_options(current=current, **options)
    if removed_args:
        remaining = {
            name: value
            for name, value in (changes.get("build_args") or current["build_args"]).items()
            if name not in removed_args
        }
        changes["build_args"] = remaining
    if removed_volumes:
        changes["volumes"] = [
            path
            for path in (changes.get("volumes") or current["volumes"])
            if path not in removed_volumes
        ]
    if removed_secrets:
        values = changes.setdefault("build_secret_values", {})
        values.update(dict.fromkeys(removed_secrets))
    for name in cleared:
        changes[name.replace("-", "_")] = None
    if clear_registry:
        changes["clear_registry"] = True
    if not changes:
        raise click.UsageError("Nothing to change: name what to set (see --help).")
    if ctx.dry_run:
        ctx.logger.info(f"Would change {validated}: {', '.join(sorted(changes))}")
        return
    outcome = change_container(validated, changes, actor=_actor())
    if ctx.json_output:
        click.echo(json.dumps(describe_container(validated).to_dict()))
        return
    if not outcome.changed:
        ctx.logger.info(f"{validated} already has these settings")
        return
    ctx.logger.success(
        f"{validated}: changed {', '.join(outcome.changed)}. It takes effect with: "
        f"noust update {validated}"
    )


@container.command("check")
@click.argument("domain")
@json_option("Print the finding as JSON.")
@pass_context
def check_command(ctx: Context, domain: str) -> None:
    """
    Ask the registry whether the tag of an image application points at a new image.

    The tag is pulled (one request when this server has it, the layers when it moved) and its
    digest is compared with the one that runs. Nothing is deployed: 'noust update' does that,
    and a schedule ('noust deploy schedule set DOMAIN --at hourly') runs this question before
    every update, so the container is recreated only when there is something new.
    """
    from noust.managers.image_updates import check_image

    found = check_image(validate_domain(domain), logger=ctx.logger)
    if ctx.json_output:
        click.echo(json.dumps(found.to_dict()))
        return
    ctx.logger.key_value("Application", found.domain)
    ctx.logger.key_value("Image", found.reference)
    ctx.logger.key_value("Running digest", found.deployed or "not recorded")
    ctx.logger.key_value("Tag now", found.current or "not given")
    if found.changed:
        ctx.logger.success(f"New: {found.reason}. Deploy it with: noust update {found.domain}")
    else:
        ctx.logger.info(f"Nothing new: {found.reason}")
