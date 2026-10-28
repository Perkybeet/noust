# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust web`` command group.

This is the only way the panel is started in practice, so the security posture
of a deployment is decided here. These rules follow from the panel being a
root shell with a login form:

- **Everything ``SecurityConfig`` can enforce is reachable from the command
  line.** A flag that only exists in Python is a flag nobody sets, which is how
  a panel ends up running with ``require_https=False`` and an empty whitelist.
- **Binding beyond loopback without TLS is an error, not a warning.** A
  warning scrolls past; the operator wanted the panel up, and it comes up. So
  ``--host 0.0.0.0`` is refused unless the panel terminates TLS itself - with
  a pair the operator brings, or a self-signed one Noust mints - or cleartext
  is accepted in so many words with ``--insecure-http``. A whitelist restricts
  who may connect but encrypts nothing, so it does not lift the requirement.
  The decision is made about the address that would be bound, not about the
  string typed: "", ``*``, ``0``, ``::`` and a name that resolves to 0.0.0.0
  are all the same socket, and a set of known-good host strings recognised one
  of them.
- **A console that must survive a reboot is a systemd unit, not a daemon.**
  ``noust web enable`` writes ``noust-web.service`` through ServiceManager,
  validated by the same rules as ``start``, and prints the token on the
  operator's terminal. The unit runs ``noust web start --under-systemd``, which
  prints no token: its standard output is the journal.
- **``noust web token`` reports; it does not rotate.** The command people run to
  look the root credential up cannot be the command that revokes it. Issuing
  takes ``--new`` and a confirmation that names what stops working.

Two structural notes about the Click migration:

- The command bodies hold no logic. Every command parses its options and hands
  them to a private ``_start`` / ``_stop`` / ``_token`` helper, which is also
  what the surviving ``handle_web`` argparse-shaped entry point calls. One
  implementation, two front doors: ``noust.cli.parser`` itself is gone, but
  ``handle_web`` is kept and tested directly so it cannot drift from the Click
  commands.
- ``--verbose``, ``--dry-run`` and ``--no-color`` are accepted after the
  subcommand, as they always were, but they do not become per-command
  parameters: :func:`global_flags` declares them with ``expose_value=False`` and
  folds them into the shared :class:`~noust.cli.app.Context`. That is what
  distinguishes re-exposing a global flag from redeclaring it, and redeclaring
  it is the argparse bug this migration exists to remove.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.fs import get_fs
from noust.core.logger import Logger
from noust.managers.console_service import (
    PANEL_TLS_CERT,
    WEB_UNIT,
    WEB_UNIT_FILE,
    StartOptions,
    _build_security_config,
    _check_dependencies,
    _console_upstream,
    _dependencies_ready,
    _ensure_self_signed,
    _get_install_instructions,
    _install_and_start,
    _install_with_apt,
    _install_with_pip,
    _port_in_use,
    _print_banner,
    _refresh_unit,
    _report_taken_port,
    _restart,
    _running_daemon_pid,
    _service_manager,
    _service_status,
    _service_unit_path,
    _start,
    _stop,
    get_pid_file,
)

if TYPE_CHECKING:
    from noust.web.auth import SecurityConfig


log = logging.getLogger("noust.cli.commands.web")


F = TypeVar("F", bound=Callable[..., Any])

#: ``--daemon``, for the two commands that may background the console.
_daemon_option = click.option(
    "-d",
    "--daemon",
    is_flag=True,
    help="Run in the background, until 'noust web stop' or the next reboot. "
    "'noust web enable' survives reboots.",
)


def _exit(code: int) -> NoReturn:
    """
    End the current command with an exit status.

    Args:
        code: Process exit status.

    Raises:
        click.exceptions.Exit: Always; this is how Click unwinds a command.
    """
    click.get_current_context().exit(code)


def add_start_arguments(parser: ArgumentParser) -> None:
    """
    Register the options that decide how exposed a panel is.

    The legacy argparse parser calls this for ``web start`` and ``web restart``
    so that both accept exactly the same security flags.

    Args:
        parser: The subcommand parser to extend.
    """
    parser.add_argument(
        "--host",
        "-H",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1). Anything else requires TLS "
        "(--tls-cert/--tls-key or --self-signed) or --insecure-http",
    )
    parser.add_argument("--port", "-p", type=int, default=8080, help="Port to listen on")
    parser.add_argument("--daemon", "-d", action="store_true", help="Run in background as daemon")
    parser.add_argument(
        "--require-https",
        action="store_true",
        help="Serve TLS directly and refuse cleartext requests. Needs --tls-cert and --tls-key, "
        "or --self-signed",
    )
    parser.add_argument("--tls-cert", default=None, help="Path to the TLS certificate chain")
    parser.add_argument("--tls-key", default=None, help="Path to the TLS private key")
    parser.add_argument(
        "--self-signed",
        action="store_true",
        help="Serve TLS with a self-signed certificate, minted under /etc/noust/panel-tls "
        "and reused while it is valid",
    )
    parser.add_argument(
        "--insecure-http",
        action="store_true",
        help="Serve cleartext HTTP beyond loopback. The access token and the session "
        "cookie cross the network unencrypted",
    )
    parser.add_argument(
        "--allow-ip",
        action="append",
        default=None,
        metavar="ADDR/CIDR",
        help="Only answer this address or network. Repeatable",
    )
    parser.add_argument(
        "--trusted-proxy",
        action="append",
        default=None,
        metavar="ADDR/CIDR",
        help="Believe X-Forwarded-For and X-Forwarded-Proto from this peer. Declare your "
        "TLS terminating proxy here so the session cookie is issued with Secure",
    )


def _start_options(args: Namespace) -> StartOptions:
    """
    Read start options off an argparse namespace.

    Args:
        args: Parsed command line arguments.

    Returns:
        The same options the Click command builds directly.
    """
    return StartOptions(
        host=getattr(args, "host", None) or "127.0.0.1",
        port=getattr(args, "port", None) or 8080,
        daemon=bool(getattr(args, "daemon", False)),
        require_https=bool(getattr(args, "require_https", False)),
        tls_cert=getattr(args, "tls_cert", None),
        tls_key=getattr(args, "tls_key", None),
        self_signed=bool(getattr(args, "self_signed", False)),
        insecure_http=bool(getattr(args, "insecure_http", False)),
        allow_ip=tuple(getattr(args, "allow_ip", None) or ()),
        trusted_proxy=tuple(getattr(args, "trusted_proxy", None) or ()),
    )


def build_security_config(args: Namespace) -> SecurityConfig:
    """
    Turn parsed argparse arguments into a security configuration.

    Args:
        args: Parsed command line arguments.

    Returns:
        The configuration the server should run with.

    Raises:
        SecurityError: When the requested combination would put a root panel on
            a network unprotected.
    """
    return _build_security_config(_start_options(args))


def console_security_config(options: StartOptions) -> SecurityConfig:
    """
    Build the console's security configuration for another front door.

    ``noust central run`` serves the same console as ``noust web start``, so
    it goes through the same checks: the refusal to expose it without TLS,
    the TLS pair, the allowlist.

    Args:
        options: How the console is to be exposed.

    Returns:
        The configuration to serve with.

    Raises:
        SecurityError: When the combination would expose it unprotected.
    """
    return _build_security_config(options)


def ensure_console_certificate(host: str, logger: Logger, verbose: bool) -> Path:
    """
    Put a self-signed pair at the console's TLS paths, minting it if needed.

    Args:
        host: The normalised bind address, which names the certificate subject.
        logger: Logger for the report.
        verbose: Whether the certificate manager should log verbosely.

    Returns:
        The certificate's path.

    Raises:
        NoustError: When the pair cannot be created.
    """
    _ensure_self_signed(host, logger, verbose)
    return PANEL_TLS_CERT


def print_console_banner(config: SecurityConfig, token: str, notes: Sequence[str]) -> None:
    """
    Print the banner that hands the operator a console and its token.

    Args:
        config: The configuration the console runs with.
        token: The access token issued for it.
        notes: Lines closing the banner.
    """
    _print_banner(config, token, notes)


def _service_payload(status: dict[str, Any] | None) -> dict[str, Any]:
    """
    Describe the console's unit for ``noust web status``.

    Args:
        status: What systemd said, or None when the unit is not installed.

    Returns:
        ``installed``, and when it is, whether it runs, starts at boot, and
        systemd's own state words.
    """
    if status is None:
        return {"unit": WEB_UNIT_FILE, "installed": False}
    return {
        "unit": WEB_UNIT_FILE,
        "installed": True,
        "active": bool(status["active"]),
        "enabled": bool(status["enabled"]),
        "active_state": status.get("active_state", ""),
        "sub_state": status.get("sub_state", ""),
    }


def _process_detail(pid: int, payload: dict[str, Any]) -> None:
    """
    Add memory and start time for a running console to a status payload.

    Extra detail is a nicety; psutil may be missing or the process may have
    exited between the check and the query. Either is expected and reported at
    debug level; anything else is a bug in this function and must not be
    reported as a cosmetic warning.

    Args:
        pid: The console's process.
        payload: The payload to extend in place.
    """
    try:
        import psutil
    except ImportError:
        log.debug("psutil is not installed; skipping extra process detail")
        return
    try:
        proc = psutil.Process(pid)
        payload["memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
        payload["started"] = proc.create_time()
    except psutil.Error as exc:
        log.debug("Could not read extra process detail for pid %s: %s", pid, exc)


def _status(verbose: bool, *, json_output: bool = False) -> int:
    """
    Report whether the panel is running, and how: as the service or the daemon.

    How it runs decides whether it survives a reboot, which is the thing an
    operator who started it with ``-d`` did not know.

    Args:
        verbose: Whether to log verbosely.
        json_output: Print the status as JSON instead of a report.

    Returns:
        Exit code.

    Raises:
        NoustError: The PID file exists but does not hold a PID, and JSON was
            asked for - the ordinary error path, not an invalid JSON body.
    """
    logger = Logger(verbose=verbose)
    service = _service_status(verbose)
    payload: dict[str, Any] = {"status": "not running", "mode": None}

    if service is not None and service["active"]:
        payload.update(status="running", mode="service")
        pid = str(service.get("pid", "")).strip()
        if pid.isdigit() and int(pid) > 0:
            payload["pid"] = int(pid)
            _process_detail(int(pid), payload)
    else:
        pid_file = get_pid_file()
        if pid_file.exists():
            try:
                pid_value = int(pid_file.read_text().strip())
                os.kill(pid_value, 0)
            except ProcessLookupError:
                payload["status"] = "not running (stale PID)"
                get_fs().remove(pid_file, missing_ok=True)
            except ValueError:
                if json_output:
                    raise NoustError("Invalid PID file") from None
                logger.header("Noust Web Interface Status")
                logger.error("Invalid PID file")
                return 1
            else:
                payload.update(status="running", mode="daemon", pid=pid_value)
                _process_detail(pid_value, payload)

    payload["service"] = _service_payload(service)

    if json_output:
        click.echo(json.dumps(payload))
        return 0

    logger.header("Noust Web Interface Status")
    logger.key_value("Status", payload["status"])
    if payload["mode"] == "service":
        boot = "starts at boot" if service and service["enabled"] else "does not start at boot"
        logger.key_value("Runs as", f"{WEB_UNIT_FILE} ({boot})")
    elif payload["mode"] == "daemon":
        logger.key_value("Runs as", "background process (does not survive a reboot)")
    if "pid" in payload:
        logger.key_value("PID", str(payload["pid"]))
    if "memory_mb" in payload:
        logger.key_value("Memory", f"{payload['memory_mb']:.1f} MB")
    if "started" in payload:
        logger.key_value("Started", str(payload["started"]))

    if service is not None and not service["active"]:
        state = service.get("active_state") or "inactive"
        logger.key_value("Service", f"{WEB_UNIT_FILE} is {state}")
        logger.info(f"See why: journalctl -u {WEB_UNIT} -n 50")
    elif payload["mode"] == "daemon":
        logger.info("To keep it running across reboots: noust web stop && noust web enable")
    return 0


def _enable(options: StartOptions, verbose: bool, *, dry_run: bool = False) -> int:
    """
    Run the console as ``noust-web.service``: started now, and at every boot.

    The options are validated by the very function ``noust web start`` uses, so
    a service cannot be written for an exposure a start would refuse. The
    token is issued here, before the restart, so the one it replaces stops
    working at once, and printed on this terminal once the service listens;
    the service serves it and prints none, because its output is the journal.
    Running it again with other options rewrites the unit, issues a new token
    and restarts the service.

    Args:
        options: How the operator asked for the console to be exposed.
        verbose: Whether to log verbosely.
        dry_run: Report what would be written and started instead.

    Returns:
        Exit code.

    Raises:
        SecurityError: When the requested exposure is not protected.
        ServiceError: When the unit is not Noust's to write, noust is not on
            PATH, or the service does not come up.
    """
    logger = Logger(verbose=verbose)

    if not _dependencies_ready(logger, verbose, dry_run=dry_run):
        return 1

    config = _build_security_config(options)
    host = config.host

    running = _running_daemon_pid()
    if running is not None:
        logger.error(f"A console already runs in the background (PID: {running})")
        logger.info("Stop it first, so the service can take its port:  noust web stop")
        return 1

    previous = _service_status(verbose)
    was_active = previous is not None and bool(previous["active"])
    # A service already running holds its own port; anything else on it would
    # only make the new service crash-loop.
    if not dry_run and not was_active and _port_in_use(host, config.port):
        _report_taken_port(host, config.port, logger)
        return 1

    service = _install_and_start(options, config, verbose, logger, dry_run=dry_run)
    if service is None:
        return 0
    path, token = service.unit, service.token
    if token is None:  # pragma: no cover - issue_token defaults to True
        return 0

    _print_banner(
        config,
        token,
        (
            f"Runs as {WEB_UNIT_FILE}: started at boot, restarted if it fails.",
            "Change its options with 'noust web enable'; stop and remove it with "
            "'noust web disable'.",
        ),
    )
    logger.success(f"{WEB_UNIT_FILE} {'restarted' if was_active else 'enabled and started'}")
    logger.info(f"Unit: {path}")
    logger.info(f"Logs: journalctl -u {WEB_UNIT}")
    return 0


def _disable(verbose: bool, *, dry_run: bool = False) -> int:
    """
    Stop ``noust-web.service``, keep it from starting at boot, and remove it.

    The access token and the console's state under ``/etc/noust`` stay: the
    next start, of either kind, issues a new token anyway.

    Args:
        verbose: Whether to log verbosely.
        dry_run: Report instead of acting; the runner and fs seams hold back
            every change.

    Returns:
        Exit code.

    Raises:
        ServiceError: When the unit at that path is not Noust's.
    """
    logger = Logger(verbose=verbose)

    # Checked before ServiceManager is asked anything: it falls back from
    # noust-web to web.service, which may well be an application's unit.
    if not _service_unit_path().exists():
        logger.info(f"The console is not installed as a service ({WEB_UNIT_FILE})")
        return 0

    _service_manager(verbose).delete_service(WEB_UNIT)

    if dry_run:
        logger.info(f"would stop, disable and remove {WEB_UNIT_FILE}")
        return 0

    logger.success(f"{WEB_UNIT_FILE} stopped, disabled and removed")
    logger.info(
        "Start the console by hand with 'noust web start', or again at boot with 'noust web enable'."
    )
    return 0


def _confirm_token_change(config: SecurityConfig, regenerate: bool) -> None:
    """
    Ask before invalidating credentials that are already in use.

    Nothing is asked when there is nothing to invalidate: the first token and
    the first signing key cost nobody anything.

    Args:
        config: Configuration naming the files that would be rewritten.
        regenerate: Whether the signing key and sessions go too.

    Raises:
        click.Abort: When the operator declines.
    """
    from noust.web.auth import state_file_exists

    in_use = state_file_exists(config.token_file) or (
        regenerate and state_file_exists(config.secret_file)
    )
    if not in_use:
        return

    if regenerate:
        message = (
            f"Rotate the signing key in {config.secret_file} and issue a new access token? "
            "Every open panel session is logged out, the current token stops working, and "
            "every API token and TOTP backup code - hashed with the key being replaced - "
            "stops verifying too"
        )
    else:
        message = (
            f"Replace the access token recorded in {config.token_file}? "
            "The token currently in use stops working immediately"
        )

    click.confirm(message, abort=True)


def _token_status(config: SecurityConfig, logger: Logger) -> int:
    """
    Report what is known about the access token, changing nothing.

    Args:
        config: Configuration naming the files that hold the credentials.
        logger: Logger for the report.

    Returns:
        Exit code.
    """
    from noust.web.auth import state_file_exists

    logger.header("Noust Web Access Token")

    if not state_file_exists(config.token_file):
        logger.key_value("Status", "no token has been issued yet")
        logger.blank()
        logger.info("Issue the first one with: noust web token --new")
        return 0

    issued = datetime.fromtimestamp(config.token_file.stat().st_mtime)
    logger.key_value("Status", "issued")
    logger.key_value("Token file", str(config.token_file))
    logger.key_value("Issued", issued.isoformat(sep=" ", timespec="seconds"))
    logger.blank()
    logger.info(
        "The token is stored as a salted hash, so it cannot be read back here. "
        "If you have lost it, issue a new one with 'noust web token --new'; "
        "the token currently in use stops working."
    )
    return 0


def _token(
    regenerate: bool,
    confirm: bool,
    verbose: bool,
    *,
    issue: bool = False,
    dry_run: bool = False,
) -> int:
    """
    Report the state of the access token, or issue a new one on request.

    Showing is the default because that is what operators run this for. The
    stored token is a salted hash and cannot be read back, so issuing a new one
    is the only way to hold a token again - and it invalidates the one in use,
    which is far too much to do to somebody who only typed ``noust web token``.
    So it takes ``--new`` (or ``--regenerate``) and a confirmation.

    Args:
        regenerate: Also rotate the signing key, revoking every session and
            invalidating every API token and TOTP backup code, all of which
            are hashed with it. Implies issuing a new token.
        confirm: Ask before invalidating credentials that are already in use.
        verbose: Whether to log verbosely.
        issue: Issue a new access token, replacing the one in use.
        dry_run: Report what would be replaced instead of replacing it.

    Returns:
        Exit code.

    Raises:
        click.Abort: When confirmation is asked for and declined.
    """
    logger = Logger(verbose=verbose)

    all_installed, missing_apt, missing_pip = _check_dependencies()
    if not all_installed:
        logger.error("Web dependencies not installed")
        logger.info(f"Missing packages: {', '.join(missing_apt)}")
        logger.blank()
        logger.info("Install with one of the following:")
        for instruction in _get_install_instructions(missing_apt, missing_pip):
            logger.info(f"  {instruction}")
        logger.blank()
        logger.info("Or run: noust web install")
        return 1

    from noust.web.auth import SecurityConfig, TokenManager

    config = SecurityConfig()

    if not (issue or regenerate):
        return _token_status(config, logger)

    if confirm:
        _confirm_token_change(config, regenerate)

    if dry_run:
        # TokenManager writes the signing key the moment it is constructed, so
        # the rehearsal has to stop before that and not after.
        logger.info(f"would issue a new access token and rewrite {config.token_file}")
        if regenerate:
            logger.info(f"would rotate the signing key in {config.secret_file}")
            logger.info(f"would revoke every session in {config.session_db}")
        return 0

    token_manager = TokenManager(config)

    if regenerate:
        new_token = token_manager.rotate_secrets()
    else:
        new_token = token_manager.generate_master_token()

    logger.success("New access token issued")
    logger.blank()
    print(f"Access Token: {new_token}")
    logger.blank()
    logger.info("Paste it into the login form. It is never accepted in a URL.")

    # A running console reads the token hash and the signing key from the
    # state directory on every verification, so both statements below hold
    # from the moment this command returns, with no restart in between.
    if regenerate:
        logger.warning(
            "All existing sessions have been revoked, and the token issued previously "
            "no longer works, including in a console that is already running"
        )
        logger.warning(
            "Every API token and TOTP backup code was hashed with the signing key just "
            "replaced and no longer verifies. Issue new ones with 'noust token create NAME' "
            "and 'noust 2fa backup-codes'."
        )
    else:
        logger.warning(
            "The token issued previously no longer works, including in a console "
            "that is already running"
        )
    logger.info("A console that is already running accepts the new token now.")

    return 0


def _install(use_apt: bool, use_pip: bool, verbose: bool) -> int:
    """
    Install the packages the panel needs.

    Args:
        use_apt: Install system packages.
        use_pip: Install pip requirements.
        verbose: Whether to log verbosely.

    Returns:
        Exit code.
    """
    logger = Logger(verbose=verbose)

    logger.header("Installing Noust Web Dependencies")

    # Check if already installed
    all_installed, missing_apt, missing_pip = _check_dependencies()
    if all_installed:
        logger.success("All web dependencies are already installed!")
        return 0

    logger.info(f"Missing packages: {', '.join(missing_apt)}")
    logger.blank()

    # Determine installation method
    is_debian = Path("/etc/debian_version").exists()

    # If neither specified, prompt or use default
    if not use_apt and not use_pip:
        if is_debian and sys.stdin.isatty():
            print("Choose installation method:")
            print("  [1] apt (system packages, recommended)")
            print("  [2] pip (user packages)")
            print("")
            try:
                choice = input("Your choice [1/2]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("")
                return 1

            use_apt = choice == "1"
            use_pip = choice == "2"

            if not use_apt and not use_pip:
                logger.error("Invalid choice")
                return 1
        else:
            # Default to pip for non-Debian or non-interactive
            use_pip = True

    if use_apt:
        logger.info("Installing with apt...")
        if _install_with_apt(missing_apt, verbose):
            # Verify installation
            all_installed, _, _ = _check_dependencies()
            if all_installed:
                logger.success("Web dependencies installed successfully!")
                logger.blank()
                logger.info("You can now start the web server with: noust web start")
                return 0
            else:
                logger.warning("Some packages may not be available via apt")
                logger.info("Falling back to pip for remaining packages...")
                _, _, remaining_pip = _check_dependencies()
                if _install_with_pip(remaining_pip, verbose):
                    logger.success("Web dependencies installed successfully!")
                    return 0
        logger.error("Failed to install dependencies")
        return 1

    if use_pip:
        logger.info("Installing with pip...")
        if _install_with_pip(missing_pip, verbose):
            # Verify installation
            all_installed, _, _ = _check_dependencies()
            if all_installed:
                logger.success("Web dependencies installed successfully!")
                logger.blank()
                logger.info("You can now start the web server with: noust web start")
                return 0
        logger.error("Failed to install dependencies")
        return 1

    return 1


# ---------------------------------------------------------------------------
# The Click command tree
# ---------------------------------------------------------------------------


def _exposure_options(command: F) -> F:
    """
    Attach the options shared by ``web start``, ``web restart`` and ``web enable``.

    All three bring the panel up, so all three have to be able to say the same
    things about how it is exposed. Declaring them once is what keeps them
    from drifting apart. ``--daemon`` is not among them: a service is never
    backgrounded, so it is declared by the two commands that take it.

    Args:
        command: The command function being decorated.

    Returns:
        The same function, with the exposure options attached.
    """
    options = (
        click.option(
            "-H",
            "--host",
            default="127.0.0.1",
            show_default=True,
            metavar="ADDR",
            help="Interface to bind to. Anything but loopback needs TLS "
            "(--tls-cert/--tls-key or --self-signed) or --insecure-http.",
        ),
        click.option(
            "-p",
            "--port",
            type=click.INT,
            default=8080,
            show_default=True,
            help="Port to listen on.",
        ),
        click.option(
            "--require-https",
            is_flag=True,
            help="Serve TLS from the panel and refuse cleartext. Needs --tls-cert and "
            "--tls-key, or --self-signed.",
        ),
        click.option(
            "--tls-cert",
            type=click.Path(exists=True, dir_okay=False, readable=True),
            metavar="PATH",
            help="Certificate chain to serve.",
        ),
        click.option(
            "--tls-key",
            type=click.Path(exists=True, dir_okay=False, readable=True),
            metavar="PATH",
            help="Private key for the certificate.",
        ),
        click.option(
            "--self-signed",
            is_flag=True,
            help="Serve TLS with a self-signed certificate, minted under "
            "/etc/noust/panel-tls and reused while it is valid.",
        ),
        click.option(
            "--insecure-http",
            is_flag=True,
            help="Serve cleartext HTTP beyond loopback. The access token and the "
            "session cookie cross the network unencrypted.",
        ),
        click.option(
            "--allow-ip",
            multiple=True,
            metavar="ADDR/CIDR",
            help="Only answer this address or network. Repeat to allow several. "
            "Restricts who connects; encrypts nothing.",
        ),
        click.option(
            "--trusted-proxy",
            multiple=True,
            metavar="ADDR/CIDR",
            help="Believe forwarding headers from this peer. Declare the proxy that "
            "terminates TLS so the session cookie is issued with Secure.",
        ),
    )
    for option in reversed(options):
        command = option(command)
    return command


@click.group(name="web", cls=NoustGroup)
@global_flags
def cli() -> None:
    """
    Run the browser panel for this server.

    The panel acts as root, so it listens on 127.0.0.1 unless you give it TLS;
    reach it from your machine with 'ssh -L 8080:127.0.0.1:8080 user@server'.
    'noust web enable' keeps it running across reboots.
    """


@cli.command("start")
@_exposure_options
@_daemon_option
@click.option(
    "--under-systemd",
    is_flag=True,
    hidden=True,
    help="Run as noust-web.service does: print no token, write no PID file. "
    "Written into the unit by 'noust web enable'; not for interactive use.",
)
@click.option(
    "--keep-token",
    is_flag=True,
    hidden=True,
    help="With --daemon: serve the access token already issued and print none. "
    "How 'noust fleet authorize' puts a background console back; not for interactive use.",
)
@global_flags
@pass_context
def start_command(
    ctx: Context,
    host: str,
    port: int,
    daemon: bool,
    require_https: bool,
    tls_cert: str | None,
    tls_key: str | None,
    self_signed: bool,
    insecure_http: bool,
    allow_ip: tuple[str, ...],
    trusted_proxy: tuple[str, ...],
    under_systemd: bool,
    keep_token: bool,
) -> NoReturn:
    """
    Start the panel and print an access token.

    It runs in the foreground until Ctrl+C, or with -d in the background until
    'noust web stop' or the next reboot. 'noust web enable' takes the same
    options and keeps it running across reboots.
    """
    if under_systemd and daemon:
        raise click.UsageError(
            "--under-systemd runs the console in the foreground for systemd; drop --daemon."
        )
    if keep_token and not daemon:
        raise click.UsageError("--keep-token only applies to a background start (--daemon).")
    options = StartOptions(
        host=host,
        port=port,
        daemon=daemon,
        require_https=require_https,
        tls_cert=tls_cert,
        tls_key=tls_key,
        self_signed=self_signed,
        insecure_http=insecure_http,
        allow_ip=tuple(allow_ip),
        trusted_proxy=tuple(trusted_proxy),
        under_systemd=under_systemd,
        keep_token=keep_token,
    )
    _exit(_start(options, ctx.verbose, dry_run=ctx.dry_run))


@cli.command("enable")
@_exposure_options
@global_flags
@pass_context
def enable_command(
    ctx: Context,
    host: str,
    port: int,
    require_https: bool,
    tls_cert: str | None,
    tls_key: str | None,
    self_signed: bool,
    insecure_http: bool,
    allow_ip: tuple[str, ...],
    trusted_proxy: tuple[str, ...],
) -> NoReturn:
    """
    Run the panel as a systemd service that survives reboots.

    Writes noust-web.service with these options (the same ones 'noust web start'
    takes and checks), enables and starts it, and prints the access token.
    Run it again to change the options.
    """
    options = StartOptions(
        host=host,
        port=port,
        require_https=require_https,
        tls_cert=tls_cert,
        tls_key=tls_key,
        self_signed=self_signed,
        insecure_http=insecure_http,
        allow_ip=tuple(allow_ip),
        trusted_proxy=tuple(trusted_proxy),
    )
    _exit(_enable(options, ctx.verbose, dry_run=ctx.dry_run))


@cli.command("disable")
@global_flags
@pass_context
def disable_command(ctx: Context) -> NoReturn:
    """Stop the panel's systemd service and remove it."""
    _exit(_disable(ctx.verbose, dry_run=ctx.dry_run))


@cli.command("refresh-unit")
@global_flags
@pass_context
def refresh_unit_command(ctx: Context) -> NoReturn:
    """Rewrite noust-web.service for this version, keeping where it listens (run by the package)."""
    _exit(_refresh_unit(ctx.verbose, dry_run=ctx.dry_run))


@cli.command("stop")
@global_flags
@pass_context
def stop_command(ctx: Context) -> NoReturn:
    """Stop the panel started with 'noust web start -d'."""
    _exit(_stop(ctx.verbose, dry_run=ctx.dry_run))


@cli.command("status")
@global_flags
@json_option("Print the status as JSON.")
@pass_context
def status_command(ctx: Context) -> NoReturn:
    """Show whether the panel is running, and whether as a service or in the background."""
    _exit(_status(ctx.verbose, json_output=ctx.json_output))


@cli.command("restart")
@_exposure_options
@_daemon_option
@global_flags
@pass_context
def restart_command(
    ctx: Context,
    host: str,
    port: int,
    daemon: bool,
    require_https: bool,
    tls_cert: str | None,
    tls_key: str | None,
    self_signed: bool,
    insecure_http: bool,
    allow_ip: tuple[str, ...],
    trusted_proxy: tuple[str, ...],
) -> NoReturn:
    """Stop the panel and start it again."""
    options = StartOptions(
        host=host,
        port=port,
        daemon=daemon,
        require_https=require_https,
        tls_cert=tls_cert,
        tls_key=tls_key,
        self_signed=self_signed,
        insecure_http=insecure_http,
        allow_ip=tuple(allow_ip),
        trusted_proxy=tuple(trusted_proxy),
    )
    _exit(_restart(options, ctx.verbose, dry_run=ctx.dry_run))


@cli.command("token")
@click.option(
    "--new",
    "--rotate",
    "issue",
    is_flag=True,
    help="Issue a new access token. The token currently in use stops working.",
)
@click.option(
    "-r",
    "--regenerate",
    is_flag=True,
    help=(
        "Issue a new token and rotate the signing key, logging out every open session "
        "and invalidating every API token and TOTP backup code."
    ),
)
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask for confirmation.")
@global_flags
@pass_context
def token_command(ctx: Context, issue: bool, regenerate: bool, assume_yes: bool) -> NoReturn:
    """
    Show the state of the access token, or issue a new one with --new.

    Showing is the default: issuing a token invalidates the one the panel is
    being used with right now, and nobody types 'noust web token' meaning that.
    """
    _exit(
        _token(
            regenerate=regenerate,
            confirm=not assume_yes,
            verbose=ctx.verbose,
            issue=issue,
            dry_run=ctx.dry_run,
        )
    )


@cli.command("install")
@click.option("--apt", "use_apt", is_flag=True, help="Install the distribution packages.")
@click.option("--pip", "use_pip", is_flag=True, help="Install into this Python with pip.")
@global_flags
@pass_context
def install_command(ctx: Context, use_apt: bool, use_pip: bool) -> NoReturn:
    """Install the packages the panel needs."""
    if use_apt and use_pip:
        raise click.UsageError("Choose one of --apt or --pip, not both.")
    _exit(_install(use_apt, use_pip, ctx.verbose))


def _expose_hooks(
    domain: str,
    *,
    remove: bool,
    ssl: bool,
    port: int | None,
    verbose: bool,
    json_output: bool,
) -> int:
    """
    Put the console's ``/hooks/`` on a dedicated public name, or take it off.

    Args:
        domain: The dedicated name.
        remove: Remove the hooks site instead.
        ssl: Obtain a certificate.
        port: The console's port, when it is not the service's.
        verbose: Whether to log verbosely.
        json_output: Print the outcome as JSON.

    Returns:
        Exit code.
    """
    from noust.integrations import hooks_site

    logger = Logger(verbose=verbose)
    if remove:
        removed = hooks_site.unexpose(domain, verbose=verbose)
        if json_output:
            click.echo(json.dumps({"domain": domain, "removed": removed}))
        elif removed:
            logger.success(f"Removed the hooks site of {domain}")
        else:
            logger.info(f"There is no hooks site on {domain}")
        return 0

    console_port, scheme = _console_upstream(port)
    result = hooks_site.expose(domain, port=console_port, scheme=scheme, ssl=ssl, verbose=verbose)
    service = _service_status(verbose)
    if service is None or not service["active"]:
        result.notes.append(
            "The console is not running as a service, so nothing answers the hooks when it "
            "is stopped: run 'noust web enable'."
        )
    if json_output:
        click.echo(json.dumps(result.to_dict()))
        return 0
    logger.success(f"Webhooks are served at {result.hooks_url}")
    logger.key_value("Forwards to", f"{scheme}://127.0.0.1:{console_port}/hooks/")
    logger.key_value("GitHub App", f"{result.hooks_url}/github")
    logger.key_value("Per application", f"{result.hooks_url}/deploy/<domain>")
    for note in result.notes:
        logger.warning(note)
    return 0


@cli.command("expose-hooks")
@click.argument("domain")
@click.option("--remove", is_flag=True, help="Remove the hooks site of DOMAIN.")
@click.option(
    "--no-ssl",
    "no_ssl",
    is_flag=True,
    help="Serve plain HTTP instead of obtaining a certificate.",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="The console's port on loopback; read from noust-web.service by default.",
)
@global_flags
@json_option("Print the outcome as JSON.")
@pass_context
def expose_hooks_command(
    ctx: Context, domain: str, remove: bool, no_ssl: bool, port: int | None
) -> NoReturn:
    """
    Receive webhooks on DOMAIN, a name of its own, without exposing the console.

    Creates an nginx site for DOMAIN that forwards only /hooks/ to the console
    on 127.0.0.1 and answers 404 to everything else, with a certificate, and
    records https://DOMAIN/hooks as the public URL the GitHub App and the
    per-application webhooks use. DOMAIN must already point at this server and
    must not be an application's. The console should run as a service
    ('noust web enable'), or nothing answers when it is stopped.
    """
    _exit(
        _expose_hooks(
            domain,
            remove=remove,
            ssl=not no_ssl,
            port=port,
            verbose=ctx.verbose,
            json_output=ctx.json_output,
        )
    )


# ---------------------------------------------------------------------------
# Legacy argparse entry point
# ---------------------------------------------------------------------------


def _handle_start(args: Namespace) -> int:
    """
    Handle ``web start`` from the argparse tree.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _start(_start_options(args), args.verbose, dry_run=bool(getattr(args, "dry_run", False)))


def _handle_stop(args: Namespace) -> int:
    """
    Handle ``web stop`` from the argparse tree.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _stop(args.verbose, dry_run=bool(getattr(args, "dry_run", False)))


def _handle_status(args: Namespace) -> int:
    """
    Handle ``web status`` from the argparse tree.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _status(args.verbose)


def _handle_restart(args: Namespace) -> int:
    """
    Handle ``web restart`` from the argparse tree.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _restart(
        _start_options(args), args.verbose, dry_run=bool(getattr(args, "dry_run", False))
    )


def _handle_enable(args: Namespace) -> int:
    """
    Handle ``web enable`` from the argparse tree.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _enable(
        _start_options(args), args.verbose, dry_run=bool(getattr(args, "dry_run", False))
    )


def _handle_disable(args: Namespace) -> int:
    """
    Handle ``web disable`` from the argparse tree.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _disable(args.verbose, dry_run=bool(getattr(args, "dry_run", False)))


def _handle_token(args: Namespace) -> int:
    """
    Handle ``web token`` from the argparse tree.

    The default is a report here too. Two front doors that disagree on whether
    a bare ``token`` invalidates the credential in use is worse than either
    behaviour on its own.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _token(
        regenerate=bool(getattr(args, "regenerate", False)),
        confirm=False,
        verbose=args.verbose,
        issue=bool(getattr(args, "new", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
    )


def _handle_install(args: Namespace) -> int:
    """
    Handle ``web install`` from the argparse tree.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    return _install(
        bool(getattr(args, "apt", False)),
        bool(getattr(args, "pip", False)),
        args.verbose,
    )


def handle_web(args: Namespace) -> int:
    """
    Route a ``web`` subcommand parsed by a legacy argparse-shaped namespace.

    ``noust.cli.parser`` is gone and nothing calls this in production; it is
    kept, and tested directly, sharing every implementation with the Click
    commands above rather than duplicating them.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    action = args.action

    handlers: dict[str, Callable[[Namespace], int]] = {
        "start": _handle_start,
        "stop": _handle_stop,
        "status": _handle_status,
        "restart": _handle_restart,
        "enable": _handle_enable,
        "disable": _handle_disable,
        "token": _handle_token,
        "install": _handle_install,
    }

    handler = handlers.get(action)
    if not handler:
        print(f"Unknown action: {action}", file=sys.stderr)
        return 1

    try:
        return handler(args)
    except NoustError as e:
        logger = Logger(verbose=args.verbose)
        logger.error(str(e))
        return 1
    except KeyboardInterrupt:
        print("\nShutting down...")
        return 0
    except Exception as e:
        logger = Logger(verbose=args.verbose)
        logger.error(f"Unexpected error: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


__all__ = [
    "StartOptions",
    "add_start_arguments",
    "build_security_config",
    "cli",
    "get_pid_file",
    "global_flags",
    "handle_web",
]
