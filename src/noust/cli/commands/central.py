# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust central`` command group: run and look after a central.

``noust central run`` is what the container image runs. It prepares the data
directory, gives the console a TLS certificate (the operator's, or one it
mints on the first start), prints the first sign-in token once, limits the
console to the local networks unless told otherwise, and serves it in the
foreground until SIGTERM, which uvicorn turns into a graceful shutdown.

``seal``, ``unseal`` and ``unlock`` manage sealed secrets
(:mod:`noust.core.sealing`); ``status`` says what state all of that is in.
The commands hold no logic of their own: :mod:`noust.central` and
:mod:`noust.core.sealing` do the work, and the console is served by the same
code ``noust web start`` uses.
"""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from types import FrameType
from typing import Any

import click

from noust import central
from noust.central import setup
from noust.central.unlock import UnlockServer, UnlockUnavailableError, send_request, socket_path
from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core import paths, sealing
from noust.core.exceptions import DependencyError, NoustError
from noust.core.net import ALL_INTERFACES, normalize_host
from noust.core.secrets import secrets_dir

#: Where the console listens in a central. 8443 because the container runs
#: unprivileged and a NAS usually has 443 taken by its own interface.
DEFAULT_HOST = ALL_INTERFACES
DEFAULT_PORT = 8443

#: How to reach a shell on the central, for the messages that name one.
DOCKER_EXEC = "docker exec -it noust"


@click.group("central", cls=NoustGroup)
def cli() -> None:
    """Run and look after a central: the Noust that manages your servers."""


def _read_passphrase(*, confirm: bool) -> str:
    """
    Read a passphrase from the terminal, or from standard input in a script.

    Args:
        confirm: Ask twice, for a passphrase being chosen.

    Returns:
        The passphrase, without the line ending.

    Raises:
        NoustError: When standard input is closed without one.
    """
    stdin = sys.stdin
    if stdin.isatty():
        return str(click.prompt("Passphrase", hide_input=True, confirmation_prompt=confirm))
    line = stdin.readline().rstrip("\r\n")
    if not line:
        raise NoustError(
            "No passphrase on standard input",
            details="Type it at a terminal (docker exec -it), or pipe it in on one line.",
        )
    return line


# -- run ---------------------------------------------------------------------------

#: The signals that stop the central: SIGTERM from ``docker stop`` (through
#: tini), SIGINT from Ctrl+C in a foreground run.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def _stopped(signum: int, frame: FrameType | None) -> None:
    """
    Take the stop signal uvicorn raises again once it has shut down.

    Args:
        signum: The signal.
        frame: The interrupted frame.
    """


def _run(ctx: Context, host: str, port: int) -> None:
    """
    Prepare the central and serve its console until it is stopped.

    Args:
        ctx: The CLI context.
        host: Address to listen on.
        port: Port to listen on.

    Raises:
        NoustError: When the data directory, the certificate or the console
            cannot be set up.
    """
    try:
        # The API is imported here, not first by the server: a dependency
        # missing from it must stop the start before the one-time token is
        # issued and printed for a console that never comes up.
        import noust.web.api  # noqa: F401
        from noust.web.auth import TokenManager
        from noust.web.server import run_server, verify_tls_material
    except ImportError as exc:
        raise DependencyError(
            "The central needs the console's dependencies, which are not installed",
            details=f"pip install 'noust[web]'. Python said: {exc}",
        ) from exc
    from noust.cli.commands.web import (
        StartOptions,
        console_security_config,
        ensure_console_certificate,
        print_console_banner,
    )

    logger = ctx.logger
    setup.ensure_data_dir()
    role = central.role()
    allowed = tuple(setup.allowlist())
    bind = normalize_host(host)

    pair = setup.operator_tls_pair()
    if pair is None:
        ensure_console_certificate(bind, logger, ctx.verbose)
        options = StartOptions(host=bind, port=port, self_signed=True, allow_ip=allowed)
    else:
        options = StartOptions(
            host=bind, port=port, tls_cert=pair[0], tls_key=pair[1], allow_ip=allowed
        )
    config = console_security_config(options)
    cert_path, _ = verify_tls_material(config)

    manager = TokenManager(config)
    try:
        token = setup.issue_first_token(manager)
    finally:
        manager.sessions.close()

    if token is not None:
        print_console_banner(
            config,
            token,
            (
                "This is the central's first start: the token above is shown this once and "
                "never again. Sign in, then turn on two-factor authentication in the "
                "console's settings: a central needs it before its first server.",
                "In a container the address above is the container's own: open "
                "https://<the host's address>:<the published port> instead.",
                f"Lost it? '{DOCKER_EXEC} noust web token --new' issues another.",
            ),
        )
    else:
        logger.info(
            f"Noust central ({role}) serving https://{bind}:{port}. Sign in with the token "
            f"printed at its first start; '{DOCKER_EXEC} noust web token --new' issues another."
        )
    fingerprint = setup.certificate_fingerprint(Path(cert_path))
    if fingerprint:
        logger.info(f"Certificate SHA-256 fingerprint: {fingerprint}")
    logger.info(f"Only these clients may connect: {', '.join(allowed)}")

    root = secrets_dir()
    if sealing.is_sealed(root):
        logger.warning(
            "The secrets are sealed: no server can be reached until they are unlocked. "
            f"Unlock them in the console, or: {DOCKER_EXEC} noust central unlock"
        )
    unlock_server = UnlockServer(root)
    try:
        unlock_server.start()
    except NoustError as exc:
        logger.warning(f"{exc.message}. {exc.details}")
    # uvicorn shuts down gracefully on SIGTERM and then raises the signal
    # again against whatever handler was there before it. With the default
    # one the process would die by the signal (exit 143) and skip the
    # cleanup below; with this one it returns, cleans up and exits 0.
    previous = {sig: signal.signal(sig, _stopped) for sig in STOP_SIGNALS}
    try:
        run_server(
            host=config.host,
            port=config.port,
            config=config,
            show_token=False,
        )
    finally:
        unlock_server.stop()
        # Forgets the keys and removes the decrypted copies handed to ssh.
        sealing.lock(root)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    logger.info("Noust central stopped.")


@cli.command("run")
@click.option(
    "--host",
    default=DEFAULT_HOST,
    show_default=True,
    help="Address to listen on. The console is served over TLS whatever it is.",
)
@click.option(
    "--port",
    default=DEFAULT_PORT,
    show_default=True,
    type=click.IntRange(1, 65535),
    help="Port to listen on.",
)
@pass_context
def run_command(ctx: Context, host: str, port: int) -> None:
    """
    Serve the central's console in the foreground (what the container runs).

    The first start mints a self-signed certificate (unless NOUST_TLS_CERT and
    NOUST_TLS_KEY name one) and prints the sign-in token once. Clients are
    limited to loopback and the private networks unless NOUST_ALLOW_IP or
    web.ip_whitelist says otherwise. SIGTERM stops it cleanly.
    """
    if ctx.dry_run:
        ctx.logger.info(
            f"would prepare the data directory and serve the central's console at "
            f"https://{normalize_host(host)}:{port}"
        )
        return
    _run(ctx, host, port)


# -- status ------------------------------------------------------------------------


def _seal_state(root: Path) -> dict[str, Any]:
    """
    Describe the secrets' seal, asking the running central whether it is locked.

    Args:
        root: The secrets directory.

    Returns:
        ``sealed``, and ``locked`` as the running central reports it (None
        when no central is running here).
    """
    if not sealing.is_sealed(root):
        return {"sealed": False, "locked": False}
    try:
        reply = send_request({"action": "status"})
    except UnlockUnavailableError:
        return {"sealed": True, "locked": None}
    return {"sealed": True, "locked": bool(reply.get("locked"))}


def _certificate() -> Path:
    """
    Return the certificate the console serves.

    Returns:
        The operator's (``NOUST_TLS_CERT``) or the minted one.
    """
    from noust.cli.commands.web import PANEL_TLS_CERT

    pair = setup.operator_tls_pair()
    return Path(pair[0]) if pair else PANEL_TLS_CERT


def _status() -> dict[str, Any]:
    """
    Gather what ``noust central status`` reports.

    Returns:
        The facts, JSON-ready.
    """
    from noust.cli.web_state import token_manager
    from noust.core.store import get_store

    certificate = _certificate()
    return {
        "role": central.role(),
        "data_dir": str(paths.DATA_DIR) if paths.DATA_DIR is not None else None,
        "config_dir": str(paths.config_dir()),
        "state_dir": str(paths.state_dir()),
        "certificate": str(certificate),
        "fingerprint": setup.certificate_fingerprint(certificate),
        "allowed_clients": setup.allowlist(),
        **_seal_state(secrets_dir()),
        "nodes": setup.count_nodes(get_store().db_path),
        "two_factor": bool(token_manager().totp_enabled()),
    }


@cli.command("status")
@json_option("Print the status as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Report the central's role, data, certificate, seal, servers and 2FA."""
    status = _status()
    if ctx.json_output:
        click.echo(json.dumps(status))
        return

    logger = ctx.logger
    if status["sealed"]:
        locked = status["locked"]
        seal = (
            "sealed, locked"
            if locked
            else "sealed, unlocked"
            if locked is False
            else "sealed (no central running here to ask whether it is unlocked)"
        )
    else:
        seal = "not sealed"
    nodes = status["nodes"]
    logger.key_value("Role", status["role"])
    logger.key_value("Data directory", status["data_dir"] or "the system locations")
    logger.key_value("Configuration", status["config_dir"])
    logger.key_value("State", status["state_dir"])
    logger.key_value("Certificate", status["certificate"])
    logger.key_value("Fingerprint", status["fingerprint"] or "no certificate yet")
    logger.key_value("Allowed clients", ", ".join(status["allowed_clients"]))
    logger.key_value("Secrets", seal)
    logger.key_value("Servers", "none yet" if not nodes else str(nodes))
    logger.key_value("Two-factor sign-in", "on" if status["two_factor"] else "off")


# -- sealing -----------------------------------------------------------------------


def _unlock_running_central(passphrase: str) -> str:
    """
    Hand the passphrase to a running central, if there is one.

    Args:
        passphrase: The passphrase.

    Returns:
        What to tell the operator.
    """
    if not socket_path().exists():
        return "No central is running here; when it starts it will need the passphrase."
    try:
        reply = send_request({"action": "unlock", "passphrase": passphrase})
    except UnlockUnavailableError as exc:
        return f"{exc.message}; when the central starts it will need the passphrase."
    if reply.get("ok"):
        return "The running central is unlocked."
    return f"The running central refused it: {reply.get('error')}"


@cli.command("seal")
@pass_context
def seal_command(ctx: Context) -> None:
    """
    Encrypt every secret (node keys and tokens, integration keys) under a
    passphrase. After this, the central starts locked until it is given the
    passphrase. The passphrase cannot be recovered: keep it somewhere else.
    """
    root = secrets_dir()
    if ctx.dry_run:
        ctx.logger.info(f"would seal every secret under {root} with a passphrase")
        return
    passphrase = _read_passphrase(confirm=True)
    report = sealing.seal_store(root, passphrase)
    logger = ctx.logger
    logger.success(
        f"Sealed {len(report.rewritten)} secret(s)"
        + (f"; {report.already} were already sealed" if report.already else "")
    )
    logger.warning(
        "Nobody can recover the passphrase, Noust included. Without it the sealed "
        "secrets are lost, and every server must be enrolled again."
    )
    logger.info(_unlock_running_central(passphrase))


@cli.command("unseal")
@pass_context
def unseal_command(ctx: Context) -> None:
    """Decrypt every secret and remove the seal."""
    root = secrets_dir()
    if ctx.dry_run:
        ctx.logger.info(f"would decrypt every secret under {root} and remove the seal")
        return
    passphrase = _read_passphrase(confirm=False)
    report = sealing.unseal_store(root, passphrase)
    ctx.logger.success(f"Unsealed {len(report.rewritten)} secret(s); they are stored in clear.")


@cli.command("unlock")
@pass_context
def unlock_command(ctx: Context) -> None:
    """
    Give the running central the passphrase of its sealed secrets.

    Reads it at the terminal, or from standard input. The central keeps the
    key in memory only, until it stops.
    """
    if not sealing.is_sealed(secrets_dir()):
        ctx.logger.info("The secrets are not sealed; there is nothing to unlock.")
        return
    passphrase = _read_passphrase(confirm=False)
    reply = send_request({"action": "unlock", "passphrase": passphrase})
    if not reply.get("ok"):
        raise sealing.SealError(
            str(reply.get("error") or "The central refused the passphrase"),
            details=str(reply.get("details") or ""),
        )
    ctx.logger.success("Unlocked. The central can reach its servers again.")
