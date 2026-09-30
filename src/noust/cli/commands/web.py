# Copyright (c) 2024-2026 Yago Lopez Prado
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

import importlib.util
import json
import logging
import os
import shlex
import signal
import socket
import sys
import time
from argparse import ArgumentParser, Namespace
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core import paths
from noust.core.config import Config
from noust.core.exceptions import NoustError, SecurityError, ServiceError
from noust.core.fs import get_fs
from noust.core.logger import Logger
from noust.core.net import (
    ALL_INTERFACES,
    host_addresses,
    is_loopback_host,
    loopback_access_lines,
    normalize_host,
    strip_brackets,
)
from noust.core.runner import CommandRunner, get_runner
from noust.core.utils import find_noust_executable

if TYPE_CHECKING:
    from noust.web.auth import SecurityConfig

log = logging.getLogger(__name__)

# PID file location. A console started by WASM before 3.0 wrote the legacy
# name, which is still read so it can be stopped after the upgrade.
PID_FILE = paths.WEB_PID_FILE
LEGACY_PID_FILE = paths.LEGACY_WEB_PID_FILE
PID_FILE_USER = Path.home() / f".{paths.NAME}" / "web.pid"
LEGACY_PID_FILE_USER = Path.home() / f".{paths.LEGACY_NAME}" / "web.pid"

#: Where the pair minted by ``--self-signed`` lives. A fixed place, so the
#: pair is reused across restarts and an operator can point a monitoring
#: check, or a replacement certificate, at it.
PANEL_TLS_DIR = paths.config_dir() / "panel-tls"
PANEL_TLS_CERT = PANEL_TLS_DIR / "panel.crt"
PANEL_TLS_KEY = PANEL_TLS_DIR / "panel.key"

#: Package installation is slow on a cold cache but must not hang a session.
INSTALL_TIMEOUT = 900

#: Seconds between stopping and starting again, so the port is released.
RESTART_PAUSE = 1

#: How long the service waits for a previous console to release the port.
PORT_RELEASE_WAIT = 10.0

#: Seconds between two looks at a port a previous console still holds.
PORT_RELEASE_POLL = 0.25

#: How far past a taken port to look for a free one to name. The panel is not
#: moved automatically - see :func:`_start` - but the port offered instead has
#: to be one that will actually work, and the search has to finish while the
#: operator is still reading the error.
PORT_SUGGESTION_SPAN = 20

#: The unit ``noust web enable`` writes. It carries the ``noust-`` prefix that
#: marks Noust's own units, next to ``noust-monitor``.
WEB_UNIT = paths.WEB_UNIT
WEB_UNIT_FILE = f"{WEB_UNIT}.service"
#: What WASM called it. Until the migration renames it, a 2.x server's console
#: still runs under this name, and status, stop and disable find it there.
LEGACY_WEB_UNIT = paths.LEGACY_WEB_UNIT

#: The template it is rendered from, under ``templates/systemd``.
WEB_UNIT_TEMPLATE = "noust-web"

#: How long ``noust web enable`` waits for the service to listen before it
#: calls the start a failure. A console comes up in a second or two; the
#: margin is for a slow disk importing FastAPI cold.
SERVICE_START_TIMEOUT = 30

#: Seconds between two looks at the starting service.
SERVICE_POLL_INTERVAL = 0.5

#: Journal lines shown when the service does not come up.
SERVICE_JOURNAL_LINES = 30

F = TypeVar("F", bound=Callable[..., Any])

#: ``--daemon``, for the two commands that may background the console.
_daemon_option = click.option(
    "-d",
    "--daemon",
    is_flag=True,
    help="Run in the background, until 'noust web stop' or the next reboot. "
    "'noust web enable' survives reboots.",
)


def get_pid_file() -> Path:
    """
    Return the PID file this process may write.

    Returns:
        ``/run/noust-web.pid`` for root, a path under ``~/.noust`` otherwise;
        the WASM name instead while only it exists, which is a console a 2.x
        release started and this one must still be able to stop.
    """
    current, legacy = (
        (PID_FILE, LEGACY_PID_FILE) if os.geteuid() == 0 else (PID_FILE_USER, LEGACY_PID_FILE_USER)
    )
    if not current.exists() and legacy.is_file():
        return legacy
    return current


def _exit(code: int) -> NoReturn:
    """
    End the current command with an exit status.

    Args:
        code: Process exit status.

    Raises:
        click.exceptions.Exit: Always; this is how Click unwinds a command.
    """
    click.get_current_context().exit(code)


# ---------------------------------------------------------------------------
# How the panel is exposed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StartOptions:
    """
    Everything that decides how reachable a panel is.

    Attributes:
        host: Interface to bind to.
        port: TCP port to listen on.
        daemon: Whether to detach into the background.
        require_https: Whether the operator demanded TLS in so many words.
            TLS material present implies it; this flag only exists so the
            demand can be spelled out and refused loudly when the material
            is missing.
        tls_cert: Path to the certificate chain.
        tls_key: Path to the private key.
        self_signed: Serve TLS with a pair minted under ``/etc/noust/panel-tls``.
        insecure_http: Serve cleartext beyond loopback, in so many words.
        allow_ip: Addresses or CIDRs allowed to connect, empty for anyone.
        trusted_proxy: Peers whose forwarding headers are believed.
        under_systemd: Run as ``noust-web.service`` runs it: serve the token
            ``noust web enable`` issued and print none, since standard output
            is the journal; write no PID file, since systemd tracks the
            process; and do not refuse to start because the service is up,
            since this is the service.
        keep_token: With ``daemon``, serve the access token already issued
            instead of issuing one: how ``noust fleet authorize`` puts back a
            background console it could not turn into a service, without
            retiring the token its operator holds.
    """

    host: str = "127.0.0.1"
    port: int = 8080
    daemon: bool = False
    require_https: bool = False
    tls_cert: str | None = None
    tls_key: str | None = None
    self_signed: bool = False
    insecure_http: bool = False
    allow_ip: tuple[str, ...] = ()
    trusted_proxy: tuple[str, ...] = ()
    under_systemd: bool = False
    keep_token: bool = False


def _option_argv(options: StartOptions, *, explicit: bool = True) -> list[str]:
    """
    Spell exposure options back as the command line flags that produce them.

    One spelling serves both places the options are written down: the
    ``ExecStart`` of ``noust-web.service`` and the ``noust web enable`` line a
    foreground start suggests. TLS paths are made absolute, because systemd
    starts the service from ``/`` and the operator typed them relative to a
    shell it never sees.

    Args:
        options: The options to spell.
        explicit: Write the host and the port even when they are the defaults.
            The unit states them; a suggestion for a human leaves them out.

    Returns:
        The flags, one argv element each.
    """
    defaults = StartOptions()
    argv: list[str] = []
    if explicit or normalize_host(options.host) != defaults.host:
        argv += ["--host", normalize_host(options.host)]
    if explicit or options.port != defaults.port:
        argv += ["--port", str(options.port)]
    if options.require_https:
        argv.append("--require-https")
    if options.self_signed:
        argv.append("--self-signed")
    if options.tls_cert:
        argv += ["--tls-cert", os.path.abspath(options.tls_cert)]
    if options.tls_key:
        argv += ["--tls-key", os.path.abspath(options.tls_key)]
    if options.insecure_http:
        argv.append("--insecure-http")
    for entry in options.allow_ip:
        argv += ["--allow-ip", entry]
    for entry in options.trusted_proxy:
        argv += ["--trusted-proxy", entry]
    return argv


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


def _web_config_overrides() -> dict[str, Any]:
    """
    Read the ``web.*`` security keys the layered configuration declares.

    These keys have no command line flag of their own, so config.yaml is the
    only place an operator can set them - which is exactly why ignoring them
    silently was a defect: the file accepted the value, ``noust config show``
    displayed it, and the panel ran with something else.

    Returns:
        :class:`~noust.web.auth.SecurityConfig` field overrides for every key
        the configuration answers.
    """
    overrides: dict[str, Any] = {}
    config = Config()
    for key, cast in (
        ("rate_limit_enabled", bool),
        ("rate_limit_requests", int),
        ("rate_limit_authenticated_requests", int),
        ("rate_limit_window", int),
        ("max_failed_attempts", int),
        ("lockout_duration", int),
        ("token_expiration_hours", int),
    ):
        value = config.get(f"web.{key}")
        if value is not None:
            overrides[key] = cast(value)
    return overrides


def _config_ip_whitelist() -> list[str]:
    """
    Read the client whitelist config.yaml declares.

    Returns:
        The ``web.ip_whitelist`` entries, empty when none are declared.
    """
    return [str(entry) for entry in Config().get("web.ip_whitelist") or []]


def _build_security_config(options: StartOptions) -> SecurityConfig:
    """
    Turn start options into a security configuration.

    Keys that exist both as a flag and in config.yaml follow one precedence,
    decided per key: an explicit command line flag wins, then the ``web.*``
    section of ``/etc/noust/config.yaml``, then the shipped default.
    ``web.host`` and ``web.port`` are deliberately not read from the file: how
    far the panel reaches is decided by the operator, in the same command that
    refuses an unsafe exposure.

    Serving beyond loopback requires the panel to terminate TLS - with a pair
    the operator brings (``--tls-cert``/``--tls-key``) or one Noust mints
    (``--self-signed``) - unless cleartext is accepted in so many words with
    ``--insecure-http``. A whitelist restricts who may connect but encrypts
    nothing, so it does not lift the requirement.

    Args:
        options: How the operator asked for the panel to be exposed.

    Returns:
        The configuration the server should run with.

    Raises:
        SecurityError: When the requested combination would put a root panel on
            a network unprotected, or when the TLS options contradict each
            other.
    """
    from noust.web.auth import SecurityConfig

    # Normalised first, so the exposure decision, the message and the address
    # that is finally bound all talk about the same thing.
    host = normalize_host(options.host)
    port = options.port
    whitelist = list(options.allow_ip) or _config_ip_whitelist()
    proxies = list(options.trusted_proxy)
    local_only = is_loopback_host(host)

    tls_cert = options.tls_cert
    tls_key = options.tls_key
    if options.self_signed:
        if tls_cert or tls_key:
            raise SecurityError(
                "--self-signed and --tls-cert/--tls-key contradict each other",
                details=(
                    "Bring your own pair with --tls-cert and --tls-key, or let Noust "
                    "mint one with --self-signed. Not both."
                ),
            )
        tls_cert = str(PANEL_TLS_CERT)
        tls_key = str(PANEL_TLS_KEY)

    if bool(tls_cert) != bool(tls_key):
        missing = "--tls-key" if tls_cert else "--tls-cert"
        raise SecurityError(
            "A TLS certificate and its private key travel together",
            details=f"Pass {missing} as well, or drop both to serve without TLS.",
        )

    serves_tls = bool(tls_cert and tls_key)

    if options.require_https and not serves_tls:
        raise SecurityError(
            "--require-https needs a certificate and a private key",
            details=(
                "Pass --tls-cert and --tls-key. For a public domain: "
                "'certbot certonly --standalone -d panel.example.com' then "
                "--tls-cert /etc/letsencrypt/live/panel.example.com/fullchain.pem "
                "--tls-key /etc/letsencrypt/live/panel.example.com/privkey.pem. "
                f"Without a domain, --self-signed mints a pair under {PANEL_TLS_DIR}."
            ),
        )

    if options.insecure_http and serves_tls:
        raise SecurityError(
            "--insecure-http contradicts serving TLS",
            details=(
                "Drop --insecure-http to serve the certificate you configured, or "
                "drop the TLS options to serve cleartext on loopback."
            ),
        )

    if not local_only and not serves_tls and not options.insecure_http:
        unresolved = (
            f"Noust could not resolve {host!r}, so it cannot show that only this machine "
            "would reach the panel, and treats it as exposed.\n"
            if not host_addresses(host)
            else ""
        )
        raise SecurityError(
            f"Refusing to expose the Noust panel on {host} without TLS",
            details=(
                "The panel drives systemd, nginx and certbot as root, and over plain "
                "HTTP its access token and session cookie cross the network readable "
                "by anyone on the path.\n"
                f"{unresolved}"
                "Pick one:\n"
                "  - keep it local and reach it over SSH: "
                f"noust web start --host 127.0.0.1 --port {port} "
                f"(then 'ssh -L {port}:127.0.0.1:{port} user@server')\n"
                "  - bring a certificate: --tls-cert CERT --tls-key KEY\n"
                f"  - let Noust mint one: --self-signed (kept under {PANEL_TLS_DIR}; "
                "browsers warn until you trust it)\n"
                "  - accept cleartext in so many words: --insecure-http\n"
                "--allow-ip restricts who may connect but encrypts nothing, so it does "
                "not lift this requirement. If a reverse proxy already terminates TLS, "
                "bind to 127.0.0.1 and declare it with --trusted-proxy so the session "
                "cookie is issued with the Secure flag."
            ),
        )

    config = SecurityConfig(
        host=host,
        port=port,
        # TLS material present means the panel terminates TLS and refuses
        # cleartext. There is no separate switch to forget: material that was
        # handed over and then served as plain HTTP is how the old
        # require_https flag became a trap.
        require_https=serves_tls,
        ssl_certfile=tls_cert,
        ssl_keyfile=tls_key,
        ip_whitelist=whitelist,
        trusted_proxies=proxies,
        **_web_config_overrides(),
    )

    if not local_only:
        # The Host header of a panel reachable by address or by name is not
        # something we can enumerate for the operator.
        config.allowed_hosts = []

    return config


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


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

#: Import name to the packages that provide it, as (system, pip). Only what the
#: panel actually imports belongs here: a dependency check that asks for
#: packages nothing imports sends operators to install software they do not
#: need, and makes a real missing dependency harder to see.
WEB_DEPENDENCIES = {
    "fastapi": ("python3-fastapi", "fastapi>=0.109.0"),
    "uvicorn": ("python3-uvicorn", "uvicorn[standard]>=0.27.0"),
    "psutil": ("python3-psutil", "psutil>=5.9.0"),
}


def _check_dependencies() -> tuple[bool, list[str], list[str]]:
    """
    Check whether the web dependencies are importable.

    Returns:
        Whether everything is present, the missing system packages, and the
        missing pip requirements, in that order.
    """
    missing_apt: list[str] = []
    missing_pip: list[str] = []

    for module, (apt_package, pip_requirement) in WEB_DEPENDENCIES.items():
        # find_spec asks the import system without executing the module, so a
        # dependency check cannot have side effects of its own.
        if importlib.util.find_spec(module) is None:
            missing_apt.append(apt_package)
            missing_pip.append(pip_requirement)

    return (not missing_apt, missing_apt, missing_pip)


def _get_install_instructions(missing_apt: list[str], missing_pip: list[str]) -> list[str]:
    """
    Suggest how to install the missing packages on this distribution.

    Args:
        missing_apt: Missing system package names.
        missing_pip: Missing pip requirements.

    Returns:
        Command lines to show the operator, pip last as the fallback.
    """
    instructions = []

    # Check if running on a Debian-based system
    if Path("/etc/debian_version").exists():
        instructions.append(f"sudo apt install {' '.join(missing_apt)}")
    # Check if running on a Fedora/RHEL-based system
    elif Path("/etc/fedora-release").exists() or Path("/etc/redhat-release").exists():
        # Fedora uses different package names
        instructions.append(f"sudo dnf install {' '.join(missing_apt)}")
    # Check if running on openSUSE
    elif Path("/etc/SuSE-release").exists() or Path("/etc/os-release").exists():
        try:
            with open("/etc/os-release") as f:
                if "opensuse" in f.read().lower():
                    instructions.append(f"sudo zypper install {' '.join(missing_apt)}")
        except OSError as exc:
            # /etc/os-release existing does not guarantee it stays readable;
            # this is only ever a nicety (naming zypper), so a stale or
            # permission-denied file falls back to the pip line below rather
            # than crashing the whole install-instructions report. Anything
            # that is not a read failure is a bug and must not land here.
            log.debug("Could not read /etc/os-release to detect openSUSE: %s", exc)

    # Always add pip as fallback option
    instructions.append(f"pip install {' '.join(missing_pip)}")

    return instructions


def _is_externally_managed() -> bool:
    """
    Report whether this interpreter refuses unmanaged installs (PEP 668).

    Returns:
        True when the standard library carries an EXTERNALLY-MANAGED marker.
    """
    import sysconfig

    stdlib_path = sysconfig.get_path("stdlib")
    if stdlib_path:
        marker = Path(stdlib_path) / "EXTERNALLY-MANAGED"
        return marker.exists()
    return False


def _install_with_pip(packages: list[str], verbose: bool = False, force: bool = False) -> bool:
    """
    Install packages with pip.

    Args:
        packages: Pip requirement specifiers.
        verbose: Show verbose output.
        force: Pass --break-system-packages even if the marker is absent.

    Returns:
        True when the installation succeeded.
    """
    logger = Logger(verbose=verbose)

    cmd = [sys.executable, "-m", "pip", "install", "--user"]
    # Add --break-system-packages for externally managed environments
    if force or _is_externally_managed():
        cmd.append("--break-system-packages")
    cmd.extend(packages)

    logger.info(f"Installing: {' '.join(packages)}")

    result = get_runner().run(cmd, timeout=INSTALL_TIMEOUT)
    if not result.success:
        logger.error(result.stderr or result.stdout or f"pip exited with {result.exit_code}")
        return False
    return True


def _install_with_apt(packages: list[str], verbose: bool = False) -> bool:
    """
    Install packages with apt.

    Args:
        packages: System package names.
        verbose: Show verbose output.

    Returns:
        True when the installation succeeded.
    """
    logger = Logger(verbose=verbose)

    logger.info(f"Installing: {' '.join(packages)}")

    # Noust requires root; there is no sudo to escalate with and nothing to
    # escalate from. See the v1 design note on privilege.
    result = get_runner().run(
        ["apt-get", "install", "-y", *packages],
        timeout=INSTALL_TIMEOUT,
        env={"DEBIAN_FRONTEND": "noninteractive"},
    )
    if not result.success:
        logger.error(result.stderr or result.stdout or f"apt-get exited with {result.exit_code}")
        return False
    return True


def _prompt_install(missing_apt: list[str], missing_pip: list[str], verbose: bool = False) -> bool:
    """
    Offer to install the missing dependencies right now.

    Args:
        missing_apt: Missing system package names.
        missing_pip: Missing pip requirements.
        verbose: Show verbose output.

    Returns:
        True when the operator accepted and the installation succeeded.
    """
    logger = Logger(verbose=verbose)

    # Check if we're in an interactive terminal
    if not sys.stdin.isatty():
        return False

    logger.blank()
    print("Would you like to install the missing dependencies now?")
    print("")

    # Determine installation method
    is_debian = Path("/etc/debian_version").exists()

    if is_debian:
        print("  [1] Using apt (system packages, recommended)")
        print("  [2] Using pip (user packages)")
        print("  [n] No, show manual instructions")
        print("")

        try:
            choice = input("Your choice [1/2/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("")
            return False

        if choice == "1":
            return _install_with_apt(missing_apt, verbose)
        elif choice == "2":
            return _install_with_pip(missing_pip, verbose)
        else:
            return False
    else:
        print("  [y] Yes, install with pip")
        print("  [n] No, show manual instructions")
        print("")

        try:
            choice = input("Your choice [y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("")
            return False

        if choice == "y":
            return _install_with_pip(missing_pip, verbose)
        else:
            return False


# ---------------------------------------------------------------------------
# What the commands actually do
# ---------------------------------------------------------------------------


def _port_in_use(host: str, port: int) -> bool:
    """
    Ask whether the panel's address is already taken.

    Binding is the only reliable answer: a connection attempt says nothing
    about a socket bound with no backlog, and reading ``ss`` output means
    parsing a different format on every distribution.

    The probe binds exactly as uvicorn will, with ``SO_REUSEADDR``, so it
    answers the question that matters: would the console's own bind fail?
    Without the option the bind also fails against the connections the
    previous console closed, which wait in TIME_WAIT for a minute holding
    nothing - and every restart of the service, the package upgrade's among
    them, refused to start over a port no process held until systemd gave up.

    Args:
        host: Address the panel would bind.
        port: Port the panel would bind.

    Returns:
        True when binding would fail because something holds the address.
    """
    family = socket.AF_INET6 if ":" in strip_brackets(host) else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((strip_brackets(host), port))
    except OSError:
        return True
    except (ValueError, socket.gaierror):
        # An address this process cannot even parse is not this check's problem;
        # the bind that follows will report it properly.
        return False
    finally:
        probe.close()
    return False


def _is_console_process(cmdline: Sequence[str], cgroup: str) -> bool:
    """
    Tell whether a process is a Noust console, or one of its children.

    Args:
        cmdline: The process's argument vector.
        cgroup: The text of its ``/proc/<pid>/cgroup``.

    Returns:
        True for a process in the console's unit, whichever name the unit
        has, and for a ``noust web start`` (or ``wasm web start``) anywhere.
    """
    units = (f"/{WEB_UNIT_FILE}", f"/{LEGACY_WEB_UNIT}.service")
    if any(line.rstrip().endswith(units) for line in cgroup.splitlines()):
        return True
    programs = {Path(arg).name for arg in cmdline[:2]}
    if not programs & {"noust", "wasm"}:
        return False
    rest = list(cmdline)
    return any(rest[i : i + 2] == ["web", "start"] for i in range(len(rest) - 1))


def _console_holds_port(host: str, port: int) -> bool:
    """
    Tell whether what listens on the console's port is a previous console.

    Args:
        host: The address the console binds.
        port: The port it binds.

    Returns:
        True when a listener on that port belongs to a Noust console or a
        process of its unit. False when it belongs to anything else, or when
        its owner cannot be named: waiting is only for a known end.
    """
    try:
        import psutil
    except ImportError:
        return False
    try:
        connections = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError) as exc:
        logging.getLogger(__name__).debug("Cannot list listening sockets: %s", exc)
        return False
    wanted = {strip_brackets(host), "0.0.0.0", "::"}  # noqa: S104 - compared, never bound
    for connection in connections:
        address = connection.laddr
        if (
            connection.status != psutil.CONN_LISTEN
            or not address
            or address.port != port
            or address.ip not in wanted
            or connection.pid is None
        ):
            continue
        try:
            cmdline = psutil.Process(connection.pid).cmdline()
            cgroup = Path(f"/proc/{connection.pid}/cgroup").read_text(encoding="utf-8")
        except (psutil.Error, OSError):
            continue
        if _is_console_process(cmdline, cgroup):
            return True
    return False


def _wait_for_previous_console(host: str, port: int) -> bool:
    """
    Give a console that is still exiting a few seconds to release the port.

    Only the service waits, and only for a console: systemd restarts it over
    whatever the previous instance is still finishing, and an operator at a
    terminal is better told at once.

    Args:
        host: The address the console binds.
        port: The port it binds.

    Returns:
        True once the port is free; False when something else holds it, or
        the previous console did not let go within :data:`PORT_RELEASE_WAIT`.
    """
    deadline = time.monotonic() + PORT_RELEASE_WAIT
    while _port_in_use(host, port):
        if time.monotonic() >= deadline or not _console_holds_port(host, port):
            return not _port_in_use(host, port)
        time.sleep(PORT_RELEASE_POLL)
    return True


def _first_free_port(host: str, port: int) -> int | None:
    """
    Find a port to offer in place of one that is taken.

    Args:
        host: Address the panel would bind.
        port: The port that turned out to be taken.

    Returns:
        The first free port above it, or None when the whole search span is
        held. Naming a port without checking it is what made the old suggestion
        useless: it was the requested port plus one, and on a machine busy
        enough to hit this at all that is frequently taken too.
    """
    for candidate in range(port + 1, min(port + 1 + PORT_SUGGESTION_SPAN, 65536)):
        if not _port_in_use(host, candidate):
            return candidate
    return None


def _report_taken_port(host: str, port: int, logger: Logger) -> None:
    """
    Explain a taken port, including why the panel does not move itself.

    Moving to the next free port would be the obliging thing to do and it is
    the wrong thing to do. The port is an address other things point at - an
    SSH tunnel, a reverse proxy, a bookmark, a script - so a panel that lands
    somewhere new whenever the old place is busy cannot be automated. It would
    also paper over the case this check exists to catch: a second panel, still
    running, still holding a root session.

    None of that is obvious from the outside, so it is said rather than
    implied.

    Args:
        host: Address the panel would have bound.
        port: The port something else holds.
        logger: Logger for the report.
    """
    logger.error(f"Something is already listening on {host}:{port}")
    logger.info("Noust does not move the panel to another port on its own:")
    logger.info("  the port is what your SSH tunnel, proxy and bookmarks point at,")
    logger.info("  and moving it would hide a panel that is still running here.")
    logger.info("If it is a panel this machine has forgotten about:")
    logger.info("  noust web stop")
    logger.info("To see what holds the port:")
    logger.info(f"  ss -ltnp 'sport = :{port}'")

    free = _first_free_port(host, port)
    if free is None:
        logger.info(
            f"Nothing up to {port + PORT_SUGGESTION_SPAN} is free either. "
            "Choose a port with --port."
        )
        return
    logger.info(f"Or serve on {free}, which is free right now:")
    logger.info(f"  noust web start --port {free}")


def _self_signed_subject(host: str) -> str:
    """
    Choose the name a minted panel certificate is issued to.

    Args:
        host: The normalised bind address.

    Returns:
        The bind address when it names one interface, this machine's hostname
        when the panel answers on all of them.
    """
    bare = strip_brackets(host)
    if bare in (ALL_INTERFACES, "::"):
        return socket.gethostname() or "noust-panel"
    return bare


def _ensure_self_signed(host: str, logger: Logger, verbose: bool) -> None:
    """
    Put a self-signed pair at the panel's TLS paths, minting it if needed.

    Args:
        host: The normalised bind address, which names the certificate subject.
        logger: Logger for the report.
        verbose: Whether the certificate manager should log verbosely.

    Raises:
        NoustError: When the pair cannot be created.
    """
    from noust.managers.cert_manager import CertManager

    subject = _self_signed_subject(host)
    minted = CertManager(verbose=verbose).generate_self_signed(
        subject, PANEL_TLS_CERT, PANEL_TLS_KEY
    )
    if minted:
        logger.info(f"Minted a self-signed certificate for {subject} under {PANEL_TLS_DIR}")
    else:
        logger.info(f"Reusing the self-signed certificate under {PANEL_TLS_DIR}")
    logger.warning(
        "Browsers warn about a self-signed certificate until you trust it. "
        "For a clean padlock, bring a CA-issued pair with --tls-cert/--tls-key."
    )


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


def _set_apart(lines: list[str], block: Sequence[str]) -> list[str]:
    """
    Put a blank line on each side of a run of lines, where it occurs.

    Args:
        lines: The banner.
        block: The lines to set apart, in order.

    Returns:
        The banner, with the block framed by blank lines when it is present.
    """
    size = len(block)
    for index in range(len(lines) - size + 1) if size else ():
        if lines[index : index + size] == list(block):
            before = [] if index and lines[index - 1] == "" else [""]
            return [*lines[:index], *before, *block, "", *lines[index + size :]]
    return lines


def _print_banner(config: SecurityConfig, token: str, notes: Sequence[str]) -> None:
    """
    Print the banner that hands the operator the console: token, address, notes.

    Every front door that issues a token prints through here - the foreground
    start, the background start and ``noust web enable`` - so the handover is
    the same whichever way the console runs. The SSH tunnel line, the only way
    to open a loopback console from another machine, is set apart so it is not
    read past; ``notes`` say how this console stops and whether it survives a
    reboot.

    Args:
        config: The configuration the console runs with.
        token: The access token issued for it.
        notes: Lines closing the banner, before its last rule.
    """
    from noust.web.server import banner_address, startup_banner

    scheme = "https" if config.require_https else "http"
    address = banner_address(config.host)
    lines = list(startup_banner(token, address, config.port, scheme))
    lines = _set_apart(lines, loopback_access_lines(address, config.port, scheme=scheme))
    if notes:
        lines = [*lines[:-1], "", *notes, lines[-1]]
    print("\n".join(lines))
    print(flush=True)


def _enable_hint(options: StartOptions | None) -> str:
    """
    Spell the ``noust web enable`` that would keep this console running.

    Args:
        options: The options this console was started with, when known.

    Returns:
        A command line to paste.
    """
    flags = _option_argv(options, explicit=False) if options is not None else []
    return shlex.join([paths.NAME, "web", "enable", *flags])


def _report_daemon_started(
    config: SecurityConfig,
    pid: int,
    token: str,
    logger: Logger,
    *,
    options: StartOptions | None = None,
) -> None:
    """
    Report a backgrounded panel: its token, how to reach it, how to stop it.

    The banner is the one a foreground start prints, so the two front doors
    hand the operator the same thing.

    Args:
        config: The configuration it was started with.
        pid: Process id of the panel.
        token: The access token issued for this start.
        logger: Logger for the report.
        options: The options it was started with, for the ``noust web enable``
            line that keeps it running across reboots.
    """
    _print_banner(
        config,
        token,
        (
            "Runs in the background until 'noust web stop' or the next reboot.",
            f"To keep it running across reboots: noust web stop && {_enable_hint(options)}",
        ),
    )
    logger.success(f"Web server started in background (PID: {pid})")
    logger.info("Use 'noust web status' to check status")
    logger.info("Use 'noust web stop' to stop the server")


def _dependencies_ready(logger: Logger, verbose: bool, *, dry_run: bool) -> bool:
    """
    Check the console's packages, offering to install what is missing.

    Checked before anything else: SecurityConfig lives in noust.web.auth, which
    imports fastapi, so building the configuration on a host without the
    panel's packages would answer a missing dependency with an ImportError
    traceback.

    Args:
        logger: Logger for the report.
        verbose: Whether to log verbosely.
        dry_run: A rehearsal never offers to install: accepting would install
            packages, which is exactly what it promised not to do.

    Returns:
        True when every package is importable.
    """
    all_installed, missing_apt, missing_pip = _check_dependencies()
    if all_installed:
        return True

    logger.error("Web dependencies not installed")
    logger.info(f"Missing packages: {', '.join(missing_apt)}")

    if not dry_run and _prompt_install(missing_apt, missing_pip, verbose):
        all_installed, _, _ = _check_dependencies()
        if all_installed:
            logger.success("Dependencies installed successfully!")
            logger.blank()
            return True
        logger.error("Some dependencies could not be installed")
        return False

    logger.blank()
    logger.info("Install manually with one of the following:")
    for instruction in _get_install_instructions(missing_apt, missing_pip):
        logger.info(f"  {instruction}")
    logger.blank()
    logger.info("Or run: noust web install")
    return False


def _running_daemon_pid() -> int | None:
    """
    Name the background console this machine has a record of, if it runs.

    Returns:
        Its PID, or None when there is no live one. A stale PID file is
        removed on the way, through the fs seam, so a rehearsal keeps it.
    """
    pid_file = get_pid_file()
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        get_fs().remove(pid_file, missing_ok=True)
        return None
    return pid


def _service_manager(verbose: bool) -> Any:
    """
    Build the manager every systemd operation on the console's unit goes through.

    Args:
        verbose: Whether to log verbosely.

    Returns:
        A :class:`~noust.managers.service_manager.ServiceManager`.
    """
    from noust.managers.service_manager import ServiceManager

    return ServiceManager(verbose=verbose)


def _installed_web_unit() -> str:
    """
    Name the console's installed unit: ``noust-web``, or WASM's ``wasm-web``.

    A 2.x server keeps running ``wasm-web.service`` until the migration from
    WASM renames it; until then status, stop and disable must act on that one
    rather than report that no console is installed.

    Returns:
        ``noust-web`` when its file exists or neither does; ``wasm-web`` when
        only the WASM unit is installed.
    """
    from noust.managers.service_manager import ServiceManager

    directory = Path(ServiceManager.SYSTEMD_DIR)
    if (directory / WEB_UNIT_FILE).exists():
        return WEB_UNIT
    if (directory / f"{LEGACY_WEB_UNIT}.service").exists():
        return LEGACY_WEB_UNIT
    return WEB_UNIT


def _service_unit_path() -> Path:
    """
    Where the console's unit is: the one ``noust web enable`` writes, or the
    one WASM wrote on a server not migrated yet.

    Returns:
        ``noust-web.service`` (or ``wasm-web.service``) in the directory
        ServiceManager manages.
    """
    from noust.managers.service_manager import ServiceManager

    return Path(ServiceManager.SYSTEMD_DIR) / f"{_installed_web_unit()}.service"


def _service_status(verbose: bool) -> dict[str, Any] | None:
    """
    Ask systemd about the console's unit, when it is installed.

    The file is checked first, and only then is ServiceManager asked: the
    manager resolves ``noust-web`` to ``web`` when only ``web.service`` exists,
    and an application named ``web`` is not the console.

    Args:
        verbose: Whether to log verbosely.

    Returns:
        :meth:`~noust.managers.service_manager.ServiceManager.get_status`'s
        answer, or None when the unit is not installed.
    """
    if not _service_unit_path().exists():
        return None
    status: dict[str, Any] = _service_manager(verbose).get_status(_installed_web_unit())
    return status


def _refuse_while_service_runs(verbose: bool, *, action: str) -> None:
    """
    Refuse to bring up a second console while ``noust-web.service`` runs.

    A second console would print a new token, retiring the one the service's
    operator holds, and then fail to bind the port the service holds.

    Args:
        verbose: Whether to log verbosely.
        action: What was refused, for the message.

    Raises:
        ServiceError: When the service is running.
    """
    status = _service_status(verbose)
    if status is None or not status["active"]:
        return
    raise ServiceError(
        f"Refusing to {action} a second console: it already runs as {WEB_UNIT_FILE}",
        details=(
            "The service holds its port and serves the token 'noust web enable' printed.\n"
            "  - to change its options: noust web enable <new options>\n"
            "  - to stop it and remove it: noust web disable\n"
            f"  - to stop it until the next boot: systemctl stop {WEB_UNIT}\n"
            "  - to see how it runs: noust web status"
        ),
    )


def _issue_token(config: SecurityConfig) -> str:
    """
    Issue the access token a console about to serve will accept.

    Args:
        config: The configuration naming the state directory.

    Returns:
        The token, the only readable copy there is.
    """
    from noust.web.auth import TokenManager

    manager = TokenManager(config)
    try:
        return manager.generate_master_token()
    finally:
        # The daemon forks next, and an SQLite connection must not cross a fork.
        manager.sessions.close()


def _start(options: StartOptions, verbose: bool, *, dry_run: bool = False) -> int:
    """
    Start the panel, refusing an unsafe exposure first.

    Args:
        options: How the operator asked for the panel to be exposed.
        verbose: Whether to log verbosely.
        dry_run: Report what would be served instead of serving it.

    Returns:
        Exit code.

    Raises:
        SecurityError: When the requested exposure is not protected.
        ServiceError: When the console already runs as ``noust-web.service``.
    """
    logger = Logger(verbose=verbose)

    if not _dependencies_ready(logger, verbose, dry_run=dry_run):
        return 1

    # Refuses an unsafe exposure before a stale PID file is removed or a socket
    # is bound. Everything this call does is a read, so a rehearsal reaches it
    # too and reports the same refusal a real run would.
    config = _build_security_config(options)
    host = config.host

    if not options.under_systemd:
        _refuse_while_service_runs(verbose, action="start")

    # Check if already running
    pid_file = get_pid_file()
    running = _running_daemon_pid()
    if running is not None:
        logger.warning(f"Web server already running (PID: {running})")
        logger.info("Use 'noust web stop' to stop it first")
        return 1

    # The PID file only catches a panel this machine still has a record of. A
    # panel whose PID file was removed, or anything else on the port, walked
    # past that check: the token was printed and then uvicorn failed with a
    # bare OSError, leaving an operator holding a credential for a server that
    # never started.
    if (
        not dry_run
        and _port_in_use(host, config.port)
        and not (options.under_systemd and _wait_for_previous_console(host, config.port))
    ):
        _report_taken_port(host, config.port, logger)
        return 1

    if config.ip_whitelist:
        logger.info(f"Only these clients may connect: {', '.join(config.ip_whitelist)}")
    if config.trusted_proxies:
        logger.info(f"Forwarding headers believed from: {', '.join(config.trusted_proxies)}")
    elif is_loopback_host(host) and not config.require_https:
        logger.warning(
            "Serving plain HTTP on loopback. Behind a TLS proxy, pass --trusted-proxy "
            "so the session cookie is issued with the Secure flag."
        )

    if options.insecure_http and not is_loopback_host(host):
        logger.warning(
            "Serving plain HTTP beyond loopback: the access token and the session "
            "cookie cross the network unencrypted."
        )

    if options.self_signed:
        if dry_run:
            # Minting is an openssl run and two writes, and both seams would
            # skip them - but the skipped reuse probe would read as "still
            # valid" and the rehearsal would report a pair it never checked.
            # Saying what would happen is more honest than acting it out.
            logger.info(f"would mint or reuse a self-signed certificate under {PANEL_TLS_DIR}")
        else:
            _ensure_self_signed(host, logger, verbose)

    if dry_run:
        # Serving is neither a subprocess nor a file write, so neither seam
        # would have stopped it: a rehearsal that binds a root panel to a port
        # is the defect this flag exists to prevent.
        scheme = "https" if config.require_https else "http"
        placement = "in the background" if options.daemon else "in the foreground"
        logger.info(f"would serve the panel {placement} at {scheme}://{host}:{config.port}")
        if not options.under_systemd:
            logger.info(f"would record the process in {pid_file}")
        return 0

    if options.under_systemd:
        # The unit's options are where the service listens, whichever release
        # wrote the unit: a start corrects a file that says otherwise.
        remember_exposure(host, config.port, logger)

    if options.daemon:
        return _start_daemon(
            config,
            verbose,
            insecure_http=options.insecure_http,
            options=options,
            issue_token=not options.keep_token,
        )
    return _start_foreground(config, insecure_http=options.insecure_http, options=options)


def _start_foreground(
    config: SecurityConfig,
    *,
    insecure_http: bool = False,
    options: StartOptions | None = None,
) -> int:
    """
    Start the web server in the foreground.

    The token is issued and printed here, not by the server, so the banner can
    say how this console stops and how to keep it running; the server then
    serves that token and issues none. Under systemd no token is issued at
    all: standard output is the journal, and the token the service serves is
    the one ``noust web enable`` printed on the operator's terminal.

    Args:
        config: The security configuration to serve with.
        insecure_http: Whether cleartext beyond loopback was accepted in so
            many words, forwarded to :func:`noust.web.server.run_server` - the
            chokepoint that actually binds the socket, and which refuses the
            exposure again on its own if this is not passed through.
        options: The options it was started with: whether it runs under
            systemd, and what ``noust web enable`` would need to be told.

    Returns:
        Exit code.
    """
    from noust.web.server import run_server, verify_tls_material

    under_systemd = options is not None and options.under_systemd
    scheme = "https" if config.require_https else "http"

    if config.require_https:
        # Checked before the token is issued, so a missing certificate is
        # reported instead of handing over a credential for nothing.
        verify_tls_material(config)

    fs = get_fs()
    pid_file = get_pid_file()

    if under_systemd:
        print(
            f"Noust console serving {scheme}://{config.host}:{config.port} as {WEB_UNIT_FILE}. "
            "Sign in with the token 'noust web enable' printed; "
            "'noust web token --new' issues another.",
            flush=True,
        )
    else:
        _print_banner(
            config,
            _issue_token(config),
            (
                "Press Ctrl+C to stop the console. To keep it running across reboots: "
                f"{_enable_hint(options)}",
            ),
        )
        # systemd tracks the service's process itself; a PID file next to it
        # would only invite 'noust web stop' to kill what systemd supervises.
        fs.make_dir(pid_file.parent)
        fs.write_text(pid_file, str(os.getpid()))

    try:
        run_server(
            host=config.host,
            port=config.port,
            config=config,
            show_token=False,
            insecure_http=insecure_http,
        )
        return 0
    finally:
        if not under_systemd:
            fs.remove(pid_file, missing_ok=True)


def _start_daemon(
    config: SecurityConfig,
    verbose: bool,
    *,
    insecure_http: bool = False,
    options: StartOptions | None = None,
    issue_token: bool = True,
) -> int:
    """
    Start the web server as a daemon, printing the access token it serves.

    The same banner a foreground start prints: ``noust web start -d`` used to
    print no token at all while the child issued one nobody saw, retiring the
    one the operator held.

    Args:
        config: The security configuration to serve with.
        verbose: Whether to log verbosely.
        insecure_http: Whether cleartext beyond loopback was accepted in so
            many words, forwarded to :func:`noust.web.server.run_server`.
        options: The options it was started with, for the ``noust web enable``
            line the banner suggests.
        issue_token: Issue and print a new token. False serves the one on
            disk and prints none (``--keep-token``).

    Returns:
        Exit code.
    """
    logger = Logger(verbose=verbose)

    # Issued here, before the fork, because the parent is the only process
    # still attached to the terminal. The child serves this token and issues
    # none of its own (show_token=False below).
    token = _issue_token(config) if issue_token else None

    pid = os.fork()

    if pid > 0:
        if token is not None:
            _report_daemon_started(config, pid, token, logger, options=options)
        else:
            logger.success(
                f"Web server started in background (PID: {pid}), serving the access "
                "token it already had"
            )
        return 0

    # Child process
    os.setsid()

    # Second fork
    pid = os.fork()
    if pid > 0:
        os._exit(0)

    # Redirect standard file descriptors
    sys.stdout.flush()
    sys.stderr.flush()

    with open("/dev/null") as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())

    fs = get_fs()

    log_file = paths.LOG_DIR / "web.log"
    if not log_file.parent.exists():
        log_file = PID_FILE_USER.parent / "web.log"
    fs.make_dir(log_file.parent)

    with open(log_file, "a") as log:
        os.dup2(log.fileno(), sys.stdout.fileno())
        os.dup2(log.fileno(), sys.stderr.fileno())

    # Write PID file
    pid_file = get_pid_file()
    fs.make_dir(pid_file.parent)
    fs.write_text(pid_file, str(os.getpid()))

    # Start server
    try:
        from noust.web.server import run_server

        run_server(
            host=config.host,
            port=config.port,
            config=config,
            show_token=False,
            insecure_http=insecure_http,
        )
    finally:
        fs.remove(pid_file, missing_ok=True)

    os._exit(0)


def _stop(verbose: bool, *, dry_run: bool = False) -> int:
    """
    Stop the running panel.

    Only the background console is this command's to stop. A console running
    as ``noust-web.service`` is named instead: killing systemd's process would
    leave a unit that comes back at the next boot, and saying "not running"
    next to it would be false.

    Args:
        verbose: Whether to log verbosely.
        dry_run: Report the signal instead of sending it.

    Returns:
        Exit code.
    """
    logger = Logger(verbose=verbose)
    fs = get_fs()

    pid_file = get_pid_file()

    if not pid_file.exists():
        status = _service_status(verbose)
        if status is not None and status["active"]:
            logger.error(f"The console runs as {WEB_UNIT_FILE}, not as a background process")
            logger.info("To stop it and remove it:  noust web disable")
            logger.info(f"To stop it until the next boot:  systemctl stop {WEB_UNIT}")
            return 1
        logger.info("Web server is not running")
        return 0

    try:
        pid = int(pid_file.read_text().strip())

        if dry_run:
            # A signal is neither a subprocess nor a file write, so nothing
            # below the CLI would have held it back.
            logger.info(f"would send SIGTERM to PID {pid} and remove {pid_file}")
            return 0

        # Send SIGTERM
        os.kill(pid, signal.SIGTERM)
        logger.success(f"Web server stopped (PID: {pid})")

        # Remove PID file
        fs.remove(pid_file, missing_ok=True)

        return 0

    except ProcessLookupError:
        logger.info("Web server is not running (stale PID file removed)")
        fs.remove(pid_file, missing_ok=True)
        return 0
    except ValueError:
        logger.error("Invalid PID file")
        fs.remove(pid_file, missing_ok=True)
        return 1
    except PermissionError:
        logger.error("Permission denied. Try running with sudo.")
        return 1


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


def _restart(options: StartOptions, verbose: bool, *, dry_run: bool = False) -> int:
    """
    Stop the panel and start it again with new options.

    Args:
        options: How the operator asked for the panel to be exposed.
        verbose: Whether to log verbosely.
        dry_run: Report both halves instead of performing them.

    Returns:
        Exit code.

    Raises:
        SecurityError: When the requested exposure is not protected.
        ServiceError: When the console runs as ``noust-web.service``, which
            'noust web enable' restarts with new options.
    """
    logger = Logger(verbose=verbose)

    # Before anything is stopped: half a restart of the wrong console is worse
    # than none.
    _refuse_while_service_runs(verbose, action="restart")

    logger.info("Restarting web server...")

    _stop(verbose, dry_run=dry_run)

    # The old process needs a moment to release the listening socket. There is
    # no socket to wait for when nothing was stopped.
    if not dry_run:
        time.sleep(RESTART_PAUSE)

    return _start(options, verbose, dry_run=dry_run)


def _noust_executable() -> str:
    """
    Locate the noust entry point the unit's ExecStart runs.

    systemd has no PATH of the operator's, so a relative command in a unit is
    a service that never starts.

    Returns:
        The absolute path ``shutil.which`` finds.

    Raises:
        ServiceError: When noust is not on PATH.
    """
    found = find_noust_executable()
    if not found:
        raise ServiceError(
            "Could not find the noust executable to run from the systemd unit",
            details=(
                "Install Noust system-wide (the distribution package, or pip install "
                "noust as root) so that 'noust' is on PATH, then run 'noust web enable' again."
            ),
        )
    return found


def _service_exec_start(options: StartOptions) -> str:
    """
    Build the unit's ExecStart: this machine's noust, running the console.

    Args:
        options: The exposure the operator asked for, already validated.

    Returns:
        The value written after ``ExecStart=``.

    Raises:
        ServiceError: When noust is not on PATH.
        ValidationError: When a value carries a character a unit line cannot
            hold.
    """
    from noust.managers.cron_manager import CronManager
    from noust.validators.environment import validate_unit_value

    argv = [_noust_executable(), "web", "start", "--under-systemd", *_option_argv(options)]
    for value in argv:
        validate_unit_value(value, field="ExecStart")
        if "$" in value:
            # systemd expands $NAME in ExecStart even inside quotes.
            raise ServiceError(
                f"Refusing to write {value!r} into the console's unit",
                details="systemd would expand the '$' in it. Use a path without one.",
            )
    return str(CronManager.build_exec_start(argv))


def _wait_until_serving(manager: Any, host: str, port: int) -> None:
    """
    Wait until the started service listens, or report why it does not.

    ``systemctl start`` returns once the process is forked, not once it
    serves, so a console that fails to bind would otherwise be reported as
    running - with a token printed for it.

    Args:
        manager: The service manager.
        host: The address the console binds.
        port: The port it binds.

    Raises:
        ServiceError: When the unit fails, or does not listen in time. The
            details carry the journal verbatim.
    """
    deadline = time.monotonic() + SERVICE_START_TIMEOUT
    while True:
        status = manager.get_status(WEB_UNIT)
        if status["active"] and _port_in_use(host, port):
            return
        restarting = status.get("sub_state") == "auto-restart"
        failed = status.get("active_state") in ("failed", "inactive") or restarting
        if failed or time.monotonic() >= deadline:
            journal = manager.logs(WEB_UNIT, lines=SERVICE_JOURNAL_LINES).strip()
            what = (
                "failed to start" if failed else f"is not listening after {SERVICE_START_TIMEOUT}s"
            )
            # The fix first, then systemd's own words, verbatim.
            advice = (
                "Fix what the journal says and run 'noust web enable' again, or remove "
                f"the unit with 'noust web disable'. More: journalctl -u {WEB_UNIT} -n 100"
            )
            raise ServiceError(
                f"{WEB_UNIT_FILE} {what}",
                details=f"{advice}\n\n{journal}" if journal else advice,
            )
        time.sleep(SERVICE_POLL_INTERVAL)


@dataclass(frozen=True)
class ConsoleService:
    """
    The console just started as ``noust-web.service``.

    Attributes:
        unit: The unit file.
        token: The access token issued for it, or None when the one already
            on disk is kept (it is served whatever started the console).
        port: The loopback or public port it listens on.
    """

    unit: Path
    token: str | None
    port: int


def _install_and_start(
    options: StartOptions,
    config: SecurityConfig,
    verbose: bool,
    logger: Logger,
    *,
    dry_run: bool = False,
    issue_token: bool = True,
) -> ConsoleService | None:
    """
    Write the console's unit, enable it and restart it until it serves.

    The part of ``noust web enable`` that ``noust fleet authorize`` shares; the
    checks before it (dependencies, a background console, a taken port) are
    each caller's, because they answer them differently.

    Args:
        options: The exposure, already validated into ``config``.
        config: The security configuration.
        verbose: Whether to log verbosely.
        logger: For what a rehearsal would do.
        dry_run: Report instead of acting.
        issue_token: Issue a new access token (retiring the one in use).
            False keeps the one on disk, which the service serves.

    Returns:
        The service, or None under a rehearsal.

    Raises:
        ServiceError: When the unit is not Noust's to write, noust is not on
            PATH, or the service does not come up (the journal in the details).
    """
    host = config.host
    exec_start = _service_exec_start(options)

    if options.self_signed:
        if dry_run:
            logger.info(f"would mint or reuse a self-signed certificate under {PANEL_TLS_DIR}")
        else:
            # Minted now, so a failure is shown here rather than in the journal.
            _ensure_self_signed(host, logger, verbose)

    manager = _service_manager(verbose)
    path = manager.install_unit(WEB_UNIT, WEB_UNIT_TEMPLATE, {"exec_start": exec_start})

    if dry_run:
        # Nothing was written, so ServiceManager would find no unit to enable.
        logger.info(f"would enable and start {WEB_UNIT_FILE}")
        if issue_token:
            logger.info("would issue a new access token and print it here")
        return None

    manager.enable(WEB_UNIT)
    # The hash is written before the restart: every console verifies against
    # the file on each request, so the token being replaced stops working now
    # rather than after the wait - or never, when the wait fails. The service
    # serves whatever hash is on disk and issues none of its own.
    token = _issue_token(config) if issue_token else None
    try:
        manager.restart(WEB_UNIT)
        _wait_until_serving(manager, host, config.port)
    except ServiceError as exc:
        if token is not None:
            retired = (
                "The previous access token no longer works. Once the console serves, "
                "the next 'noust web enable' prints a new one (or 'noust web token --new')."
            )
            exc.details = f"{retired}\n{exc.details}" if exc.details else retired
        raise
    remember_exposure(host, config.port, logger)
    return ConsoleService(unit=Path(path), token=token, port=config.port)


def _unit_exec_start(text: str) -> str | None:
    """
    Read the ``ExecStart=`` of a unit file.

    Args:
        text: The unit.

    Returns:
        Its value as written, or None when it has none.
    """
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            return line.removeprefix("ExecStart=").strip() or None
    return None


def _refresh_unit(verbose: bool, *, dry_run: bool = False) -> int:
    """
    Rewrite the console's unit from this version's template, keeping its ExecStart.

    Only ``noust web enable`` wrote the unit, so a fix to the template never
    reached a server that enabled the console before it: every one kept the
    ``User=root`` that left the console without CAP_SETUID and apt unable to
    update from it. The package runs this before it restarts the console.
    Where the console listens is the unit's ExecStart, which is kept as it is.

    Args:
        verbose: Whether to log verbosely.
        dry_run: Report instead of writing.

    Returns:
        The exit code: 0 when rewritten or when there is no unit.

    Raises:
        ServiceError: When the unit has no ExecStart or is not Noust's.
    """
    from noust.managers.service_manager import ServiceManager

    logger = Logger(verbose=verbose)
    path = Path(ServiceManager.SYSTEMD_DIR) / WEB_UNIT_FILE
    if not path.is_file():
        logger.info(f"No {WEB_UNIT_FILE} to refresh: the console does not run as a service")
        return 0
    exec_start = _unit_exec_start(path.read_text(encoding="utf-8"))
    if exec_start is None:
        raise ServiceError(
            f"{path} has no ExecStart line",
            details="Write it again with: noust web enable",
        )
    _service_manager(verbose).install_unit(WEB_UNIT, WEB_UNIT_TEMPLATE, {"exec_start": exec_start})
    if dry_run:
        logger.info(f"would rewrite {path} for this version, keeping: {exec_start}")
        return 0
    logger.success(f"Rewrote {path} for this version; it applies from the console's next restart")
    return 0


def remember_exposure(host: str, port: int, logger: Logger | None = None) -> None:
    """
    Record where the console service listens as ``web.host`` and ``web.port``.

    The unit's command line decides where the service listens; config.yaml is
    what everything else reads (the security checks, the hooks site, links to
    the console). A service enabled on another port while the file kept the
    old one made the checks blame the console for whatever held that port
    (Docker's, on one server). Written only when it differs.

    Args:
        host: The address the service binds.
        port: Its port.
        logger: Told when the file cannot be written; the console serves anyway.
    """
    from noust.core.exceptions import ConfigError

    config = Config()
    host = normalize_host(host)
    if config.get("web.host") == host and config.get("web.port") == port:
        return
    try:
        config.set("web.host", host)
        config.set("web.port", port)
        config.write()
    except (ConfigError, SecurityError, OSError) as exc:
        # The service is up either way; a stale file is what this repairs, not
        # a reason to stop the console.
        (logger or Logger()).warning(
            f"Could not record web.host and web.port in the configuration: {exc}"
        )


def master_token_exists(config: SecurityConfig) -> bool:
    """
    Report whether a master access token was ever issued on this server.

    Args:
        config: The security configuration naming the token file.

    Returns:
        True when its hash is on disk: someone holds a token that works.
    """
    try:
        return config.token_file.is_file() and bool(config.token_file.read_text().strip())
    except OSError:
        return False


def enable_loopback_service(
    port: int,
    verbose: bool,
    *,
    dry_run: bool = False,
    issue_token: bool = False,
    port_just_freed: bool = False,
) -> ConsoleService | None:
    """
    Run the console as ``noust-web.service`` on 127.0.0.1, quietly: what a fleet node needs.

    Unlike ``noust web enable`` it prints no banner and keeps the access
    token already issued (``noust fleet authorize`` must not retire the one
    the operator holds); a token is issued only when none ever was, and is
    returned for the caller to show.

    Args:
        port: The port.
        verbose: Whether to log verbosely.
        dry_run: Report instead of acting.
        issue_token: Issue a token even when one exists.
        port_just_freed: The caller just stopped the console that held the
            port. Its connections linger in TIME_WAIT for a minute, which the
            taken-port check counts as taken (on purpose, for a forgotten
            console) and the service's own bind does not; the check is skipped,
            and anything else holding the port fails the start instead.

    Returns:
        The service, or None under a rehearsal.

    Raises:
        ServiceError: When the console's dependencies are missing, it still
            runs as WASM's unit, a background console holds the port or
            something else does, or the service does not come up.
    """
    options = StartOptions(host="127.0.0.1", port=port)
    all_installed, missing_apt, _ = _check_dependencies()
    if not all_installed:
        raise ServiceError(
            "The console's packages are not installed",
            details=f"Missing: {', '.join(missing_apt)}. Run 'noust web install', then again.",
        )
    if _installed_web_unit() == LEGACY_WEB_UNIT:
        raise ServiceError(
            f"The console still runs as WASM's {LEGACY_WEB_UNIT}.service",
            details="Move this server onto Noust's names first: noust migrate-from-wasm. "
            "It renames the unit and keeps the console running; then run this again.",
        )
    config = _build_security_config(options)
    running = _running_daemon_pid()
    if running is not None:
        raise ServiceError(
            f"A console already runs in the background (PID: {running})",
            details="Stop it first, so the service can take its port: noust web stop",
        )
    previous = _service_status(verbose)
    was_active = previous is not None and bool(previous["active"])
    if not dry_run and not was_active and not port_just_freed and _port_in_use(config.host, port):
        free = _first_free_port(config.host, port)
        raise ServiceError(
            f"Something is already listening on {config.host}:{port}",
            details=(
                f"See what holds it: ss -ltnp 'sport = :{port}'. Stop it, or use another "
                "port for the console"
                + (f" ({free} is free right now)" if free is not None else "")
                + ", then run this again."
            ),
        )
    return _install_and_start(
        options,
        config,
        verbose,
        Logger(verbose=verbose, stream=sys.stderr),
        dry_run=dry_run,
        issue_token=issue_token or not master_token_exists(config),
    )


#: How long a background console may take to exit after SIGTERM.
DAEMON_STOP_TIMEOUT = 15.0


@dataclass(frozen=True)
class DaemonConsole:
    """
    A console running in the background (``noust web start -d``).

    Attributes:
        pid: Its process.
        options: How it was started, read from its command line.
    """

    pid: int
    options: StartOptions

    def exposure_changes(self) -> list[str]:
        """
        Say what a loopback service on the same port would no longer do.

        Returns:
            One sentence per difference; empty when the service is the same console.
        """
        changes: list[str] = []
        options = self.options
        if not is_loopback_host(options.host):
            changes.append(f"it listens on {options.host}; the service listens on 127.0.0.1 only")
        if options.self_signed or options.tls_cert or options.require_https:
            changes.append("it serves TLS; the service serves plain HTTP on loopback")
        if options.allow_ip:
            changes.append(f"it only answers {', '.join(options.allow_ip)}")
        if options.trusted_proxy:
            changes.append(f"it trusts forwarding headers from {', '.join(options.trusted_proxy)}")
        return changes


def _argv_value(argv: list[str], *flags: str) -> list[str]:
    """
    Read every value given to a flag in an argv, as ``--flag value`` or ``--flag=value``.

    Args:
        argv: The argv.
        flags: The flag's spellings, such as ``-p`` and ``--port``.

    Returns:
        The values, in order.
    """
    values: list[str] = []
    for index, arg in enumerate(argv):
        if arg in flags and index + 1 < len(argv):
            values.append(argv[index + 1])
            continue
        for flag in flags:
            if flag.startswith("--") and arg.startswith(f"{flag}="):
                values.append(arg.split("=", 1)[1])
    return values


def options_from_argv(argv: list[str]) -> StartOptions:
    """
    Read the exposure a ``noust web start`` command line asked for.

    Args:
        argv: The process's command line.

    Returns:
        The options; the defaults for what it does not say.
    """
    defaults = StartOptions()
    hosts = _argv_value(argv, "-H", "--host")
    ports = _argv_value(argv, "-p", "--port")
    port = int(ports[-1]) if ports and ports[-1].isdigit() else defaults.port
    certs = _argv_value(argv, "--tls-cert")
    keys = _argv_value(argv, "--tls-key")
    return StartOptions(
        host=hosts[-1] if hosts else defaults.host,
        port=port,
        daemon=True,
        require_https="--require-https" in argv,
        tls_cert=certs[-1] if certs else None,
        tls_key=keys[-1] if keys else None,
        self_signed="--self-signed" in argv,
        insecure_http="--insecure-http" in argv,
        allow_ip=tuple(_argv_value(argv, "--allow-ip")),
        trusted_proxy=tuple(_argv_value(argv, "--trusted-proxy")),
    )


def running_daemon(proc: Path = Path("/proc")) -> DaemonConsole | None:
    """
    Find the background console, and how it was started.

    Args:
        proc: Where the process table is.

    Returns:
        The console, or None when none runs.
    """
    pid = _running_daemon_pid()
    if pid is None:
        return None
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes()
    except OSError:
        raw = b""
    argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
    return DaemonConsole(pid=pid, options=options_from_argv(argv))


def stop_daemon(pid: int, *, timeout: float = DAEMON_STOP_TIMEOUT) -> None:
    """
    Stop the background console and wait until it is gone and its port is free.

    Args:
        pid: Its process.
        timeout: How long it may take.

    Raises:
        ServiceError: When it is still running after the timeout.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            get_fs().remove(get_pid_file(), missing_ok=True)
            return
        time.sleep(SERVICE_POLL_INTERVAL)
    raise ServiceError(
        f"The background console (PID {pid}) did not stop within {timeout:.0f}s",
        details=f"Stop it yourself (kill {pid}), then run this again.",
    )


def restart_daemon(options: StartOptions, runner: CommandRunner | None = None) -> None:
    """
    Start a background console again, with its options and the token it already served.

    Args:
        options: How it ran.
        runner: The runner; the process-wide one by default.

    Raises:
        ServiceError: When it does not start. The details carry its output.
    """
    argv = [
        _noust_executable(),
        "web",
        "start",
        "--daemon",
        "--keep-token",
        *_option_argv(options),
    ]
    result = (runner or get_runner()).run(argv, timeout=SERVICE_START_TIMEOUT)
    if not result.success:
        raise ServiceError(
            "The background console could not be started again",
            details=f"Start it yourself: {shlex.join(['noust', *argv[1:]])}",
            output=(result.stderr or result.stdout).strip(),
        )


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

    if _installed_web_unit() == LEGACY_WEB_UNIT:
        # Writing noust-web next to Noust's running noust-web would start a
        # second console on the same port; the migration renames the unit
        # (and keeps it running) instead.
        raise ServiceError(
            f"The console still runs as WASM's {LEGACY_WEB_UNIT}.service",
            details="Move this server onto Noust's names first: noust migrate-from-wasm. "
            "It renames the unit and keeps the console running; then run this again.",
        )

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

    unit = _installed_web_unit()
    _service_manager(verbose).delete_service(unit)

    if dry_run:
        logger.info(f"would stop, disable and remove {unit}.service")
        return 0

    logger.success(f"{unit}.service stopped, disabled and removed")
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
    in_use = config.token_file.exists() or (regenerate and config.secret_file.exists())
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
    logger.header("Noust Web Access Token")

    if not config.token_file.exists():
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


def _console_upstream(port: int | None) -> tuple[int, str]:
    """
    Find where the console listens, for the hooks site to forward to.

    The service's own command line is the truth when there is one: it is
    what runs. Otherwise the configured port, over plain HTTP.

    Args:
        port: The port the operator named, which wins.

    Returns:
        The port and the scheme (``https`` when the console serves TLS).
    """
    scheme = "http"
    found: int | None = None
    unit = _service_unit_path()
    if unit.exists():
        for line in unit.read_text(encoding="utf-8").splitlines():
            if not line.startswith("ExecStart="):
                continue
            argv = shlex.split(line.removeprefix("ExecStart="))
            if any(a in ("--self-signed", "--tls-cert", "--require-https") for a in argv):
                scheme = "https"
            if "--port" in argv and argv.index("--port") + 1 < len(argv):
                value = argv[argv.index("--port") + 1]
                found = int(value) if value.isdigit() else None
    configured = Config().get("web.port")
    return port or found or int(configured or StartOptions().port), scheme


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
