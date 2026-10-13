# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust logdrain``: where the logs of applications are shipped to.

``add`` makes a destination (Loki, Axiom, a JSON endpoint such as Fluent Bit's
``http`` input, an OpenTelemetry collector or a remote syslog), ``test`` sends it
one line, ``list`` says how each is doing, and ``run`` is the shipper itself, which
``noust-logship.service`` supervises. All of it is :mod:`noust.managers.log_drains`
and :mod:`noust.managers.log_shipping`, which ``/api/log-drains`` calls too.

A token is never an argument: it is read from standard input (``--token-stdin``) or
typed at a prompt, and kept in the secret store.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
from types import FrameType
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.log_drain_store import KINDS
from noust.managers import log_shipping
from noust.managers.log_drains import SYSLOG_TRANSPORTS, LogDrains


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _pairs(values: tuple[str, ...]) -> dict[str, str]:
    """
    Read ``name=value`` pairs.

    Args:
        values: What the operator gave.

    Returns:
        The labels.

    Raises:
        ValidationError: One is not a pair.
    """
    labels: dict[str, str] = {}
    for item in values:
        name, separator, value = item.partition("=")
        if not separator or not name:
            raise ValidationError(f"{item!r} is not name=value", field="labels")
        labels[name] = value
    return labels


@click.group("logdrain", cls=NoustGroup)
def cli() -> None:
    """Ship the logs of applications to Loki, Axiom, Fluent Bit, OTLP or a remote syslog."""


@cli.command("list", read_only=True)
@json_option("Print the destinations as JSON.")
@pass_context
def list_command(ctx: Context, **_: Any) -> None:
    """
    List the destinations and how each is doing.
    """
    manager = LogDrains()
    drains = manager.list_drains()
    state = log_shipping.service_state()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "service": state,
                    "drains": [
                        {
                            "name": drain.name,
                            "kind": drain.kind,
                            "settings": drain.settings,
                            "apps": list(drain.apps),
                            "enabled": drain.enabled,
                            "has_secret": manager.has_secret(drain.name),
                            "last_ok_at": drain.last_ok_at,
                            "last_error": drain.last_error,
                            "shipped": drain.shipped,
                        }
                        for drain in drains
                    ],
                }
            )
        )
        return
    if not drains:
        ctx.logger.info("No destinations")
        ctx.logger.blank()
        ctx.logger.info("Add one with: noust logdrain add loki --kind loki --url https://...")
        return
    ctx.logger.table(
        ["Name", "Kind", "Applications", "State", "Last delivery", "Lines"],
        [
            [
                drain.name,
                drain.kind,
                ", ".join(drain.apps) or "all",
                ("on" if drain.enabled else "off") + (" - failing" if drain.last_error else ""),
                drain.last_ok_at or "never",
                str(drain.shipped),
            ]
            for drain in drains
        ],
    )
    for drain in drains:
        if drain.last_error:
            ctx.logger.warning(f"{drain.name}: {drain.last_error}")
    if drains and not state["active"]:
        ctx.logger.warning(
            "The shipper is not running: nothing is sent. Start it with: "
            "noust logdrain service install"
        )


@cli.command("add")
@click.argument("name")
@click.option("--kind", type=click.Choice(KINDS), required=True, help="What the destination is.")
@click.option("--url", help="Its address (not for syslog).")
@click.option("--tenant", help="Loki: the tenant (X-Scope-OrgID).")
@click.option("--username", help="Loki, http, otlp: the user, for basic authentication.")
@click.option(
    "--auth",
    type=click.Choice(["none", "bearer", "basic"]),
    help="http, otlp: how to authenticate.",
)
@click.option("--dataset", help="Axiom: the dataset.")
@click.option("--transport", type=click.Choice(SYSLOG_TRANSPORTS), help="Syslog: udp, tcp or tls.")
@click.option("--host", help="Syslog: the receiver.")
@click.option("--port", type=int, help="Syslog: its port (6514 for tls, 514 otherwise).")
@click.option("--facility", type=int, help="Syslog: the facility, 0 to 23 (16 by default).")
@click.option("--ca", help="Syslog over tls: the CA file the receiver must chain to.")
@click.option("--pin-sha256", "pin_sha256", help="Syslog over tls: the receiver's certificate pin.")
@click.option(
    "--label", "labels", multiple=True, metavar="NAME=VALUE", help="Added to every line; repeat it."
)
@click.option(
    "--app",
    "apps",
    multiple=True,
    metavar="DOMAIN",
    help="Only this application; repeat it. Without any, all.",
)
@click.option(
    "--token-stdin",
    is_flag=True,
    default=False,
    help="Read the token or password from standard input.",
)
@click.option("--disabled", is_flag=True, default=False, help="Make it, but do not ship to it yet.")
@pass_context
def add_command(
    ctx: Context,
    name: str,
    kind: str,
    url: str | None,
    tenant: str | None,
    username: str | None,
    auth: str | None,
    dataset: str | None,
    transport: str | None,
    host: str | None,
    port: int | None,
    facility: int | None,
    ca: str | None,
    pin_sha256: str | None,
    labels: tuple[str, ...],
    apps: tuple[str, ...],
    token_stdin: bool,
    disabled: bool,
) -> None:
    """
    Make a destination, or change the one of that name.

    The token or password is read from standard input with --token-stdin, or
    typed (hidden) at a prompt when there is a terminal; it is kept in the secret
    store and never shown again. Each line is scrubbed of the secrets of its
    application before it is sent.
    """
    settings: dict[str, Any] = {}
    for key, value in (
        ("url", url),
        ("tenant", tenant),
        ("username", username),
        ("auth", auth),
        ("dataset", dataset),
        ("transport", transport),
        ("host", host),
        ("port", port),
        ("facility", facility),
        ("ca", ca),
        ("pin_sha256", pin_sha256),
    ):
        if value is not None:
            settings[key] = value
    if labels:
        settings["labels"] = _pairs(labels)
    secret: str | None = None
    if token_stdin:
        secret = sys.stdin.read().strip() or None
    elif sys.stdin.isatty() and kind != "syslog":
        secret = (
            click.prompt(
                "Token or password (Enter for none)",
                default="",
                hide_input=True,
                show_default=False,
            )
            or None
        )
    saved = LogDrains().save(
        name,
        kind,
        settings,
        apps=list(apps),
        secret=secret,
        keep_secret=secret is None,
        enabled=not disabled,
        actor=_actor(),
    )
    ctx.logger.success(f"Destination {saved.name} saved")
    if saved.enabled:
        state = log_shipping.ensure_service()
        if state == "no_systemd":
            ctx.logger.warning(
                "This machine has no systemd: run `noust logdrain run` under your own supervisor"
            )
        else:
            ctx.logger.info("The shipper is running (noust-logship.service)")
    ctx.logger.info(f"Check it with: noust logdrain test {saved.name}")


@cli.command("test")
@click.argument("name")
@pass_context
def test_command(ctx: Context, name: str) -> None:
    """
    Send one line to a destination, and say what it answered.
    """
    sent = LogDrains().test(name, actor=_actor())
    ctx.logger.success(f"Accepted: {sent}")


@cli.command("remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, name: str, yes: bool) -> None:
    """
    Remove a destination and its token.
    """
    if not (yes or ctx.dry_run):
        click.confirm(f"Stop shipping logs to {name} and forget it?", abort=True)
    LogDrains().remove(name, actor=_actor())
    ctx.logger.success(f"Removed {name}")


@cli.command("enable")
@click.argument("name")
@pass_context
def enable_command(ctx: Context, name: str) -> None:
    """
    Ship to a destination again.
    """
    LogDrains().set_enabled(name, True, actor=_actor())
    log_shipping.ensure_service()
    ctx.logger.success(f"Shipping to {name}")


@cli.command("disable")
@click.argument("name")
@pass_context
def disable_command(ctx: Context, name: str) -> None:
    """
    Stop shipping to a destination, keeping it.
    """
    LogDrains().set_enabled(name, False, actor=_actor())
    ctx.logger.success(f"No longer shipping to {name}")


@cli.command("run")
@click.option("--once", is_flag=True, default=False, help="Do one cycle and exit.")
@click.option(
    "--interval", type=float, default=float(log_shipping.CYCLE_SECONDS), show_default=True
)
@pass_context
def run_command(ctx: Context, once: bool, interval: float) -> None:
    """
    The shipper: read the logs of applications and send what is new, in a loop.

    This is what noust-logship.service runs. It reads the journal from where each
    destination got to, so stopping it loses nothing.
    """
    shipper = log_shipping.LogShipper()
    if once:
        report = shipper.cycle()
        for name, lines in report.sent.items():
            ctx.logger.info(f"{name}: {lines} lines sent")
        for name, message in report.failed.items():
            ctx.logger.warning(f"{name}: {message}")
        return
    stop = threading.Event()

    def end(signum: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, end)
    signal.signal(signal.SIGINT, end)
    ctx.logger.info("Shipping logs; stop with Ctrl+C")
    shipper.run(stop, interval=interval)


@cli.group("service", cls=NoustGroup)
def service_group() -> None:
    """The unit that runs the shipper."""


@service_group.command("install")
@pass_context
def service_install(ctx: Context) -> None:
    """
    Install the shipper's unit and start it.
    """
    state = log_shipping.ensure_service()
    if state == "no_systemd":
        ctx.logger.warning("This machine has no systemd: run `noust logdrain run` yourself")
        return
    ctx.logger.success("The shipper is running")


@service_group.command("remove")
@pass_context
def service_remove(ctx: Context) -> None:
    """
    Stop the shipper and remove its unit. Destinations are kept.
    """
    if log_shipping.remove_service():
        ctx.logger.success("The shipper is stopped and its unit removed")
    else:
        ctx.logger.info("The shipper was not installed")


@service_group.command("status", read_only=True)
@json_option("Print the state as JSON.")
@pass_context
def service_status(ctx: Context, **_: Any) -> None:
    """
    Say whether the shipper is installed and running.
    """
    state = log_shipping.service_state()
    if ctx.json_output:
        click.echo(json.dumps(state))
        return
    ctx.logger.key_value("Installed", "yes" if state["installed"] else "no")
    ctx.logger.key_value("Running", "yes" if state["active"] else f"no ({state['state']})")
