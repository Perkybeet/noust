# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console as a process Noust starts and stops: the daemon, the systemd unit it can be, the
port it holds and the trusted proxies it is told about.

What ``noust web`` does to the console, without the command line: the layers that look at the
running console (the ENS checks, the exposure of the console on a name, the push deploy's
hook) ask this module, and the commands in ``noust.cli.commands.web`` are its front door.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shlex
import signal
import socket
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core import paths
from noust.core.config import Config
from noust.core.exceptions import NoustError, SecurityError, ServiceError
from noust.core.exceptions import PermissionError as NoustPermissionError
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
from noust.core.package_family import install_apt_packages
from noust.core.runner import CommandRunner, get_runner
from noust.core.utils import check_root, find_noust_executable

if TYPE_CHECKING:
    from noust.web.auth import SecurityConfig


log = logging.getLogger("noust.cli.commands.web")


# PID file location.
PID_FILE = paths.WEB_PID_FILE
PID_FILE_USER = Path.home() / f".{paths.NAME}" / "web.pid"

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


def get_pid_file() -> Path:
    """
    Return the PID file this process may write.

    Returns:
        ``/run/noust-web.pid`` for root, a path under ``~/.noust`` otherwise.
    """
    return PID_FILE if os.geteuid() == 0 else PID_FILE_USER


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
    result = install_apt_packages(
        get_runner(),
        packages,
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
        has, and for a ``noust web start`` anywhere.
    """
    if any(line.rstrip().endswith(f"/{WEB_UNIT_FILE}") for line in cgroup.splitlines()):
        return True
    programs = {Path(arg).name for arg in cmdline[:2]}
    if "noust" not in programs:
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


def _service_unit_path() -> Path:
    """
    Where the console's unit is: the one ``noust web enable`` writes.

    Returns:
        ``noust-web.service`` in the directory ServiceManager manages.
    """
    from noust.managers.service_manager import ServiceManager

    return Path(ServiceManager.SYSTEMD_DIR) / WEB_UNIT_FILE


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
    status: dict[str, Any] = _service_manager(verbose).get_status(WEB_UNIT)
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


def _require_root_or_own_state(*, dry_run: bool) -> None:
    """
    Refuse to start a console that cannot read the state it is made of.

    The signing key, the token hash, the sessions and the audit log live in the
    configuration directory, which belongs to root. Anyone else used to get
    there by way of an audit warning and then a traceback out of whichever
    file was touched first, with nothing saying that the console needs root.
    A directory the operator names, with ``NOUST_WEB_STATE_DIR`` or by putting
    the whole installation under ``NOUST_DATA_DIR``, is theirs to use, so an
    unprivileged console (a development one, a test) still starts.

    Args:
        dry_run: A rehearsal reads and writes nothing, so it is not refused.

    Raises:
        PermissionError: Not root, and no directory of the user's own.
    """
    if dry_run or check_root() or paths.getenv("WEB_STATE_DIR") or paths.DATA_DIR is not None:
        return
    from noust.web.auth import STATE_DIR_ENV

    raise NoustPermissionError(
        "'noust web start' needs root",
        details=(
            "The console keeps its signing key, sessions and audit log in "
            f"{paths.config_dir()}, which only root can read. Run it with sudo. "
            "To run a console of your own that does not manage this server, set "
            f"{STATE_DIR_ENV} to a directory you own."
        ),
    )


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
        PermissionError: When not root and no state directory of the user's own
            was named.
    """
    logger = Logger(verbose=verbose)

    if not _dependencies_ready(logger, verbose, dry_run=dry_run):
        return 1

    # Before the configuration is read: for a user who cannot search the
    # configuration directory, reading it is the first thing that fails.
    _require_root_or_own_state(dry_run=dry_run)

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


def _keeping_published_console(options: StartOptions) -> StartOptions:
    """
    Keep the trusted proxy a published console depends on when its unit is written again.

    ``noust console expose`` makes the service believe nginx's forwarding
    headers with ``--trusted-proxy``. Anything that writes the unit afterwards
    (``noust web enable`` with other options, a server becoming a node) is given
    options that do not name it, and without it every client is the loopback
    address and no cookie is ``Secure``: the exposure would keep answering and
    stop being safe. This is the one place the unit is written, so this is where
    it is kept.

    Args:
        options: What the caller asked the unit to be.

    Returns:
        The same options, with the exposure's proxy added when one is recorded
        and they do not already trust it.
    """
    import sqlite3

    from noust.core.console_exposure_store import ConsoleExposureStore

    try:
        state = ConsoleExposureStore().get()
    except (NoustError, sqlite3.Error) as exc:
        log.debug("Could not read whether the console is published: %s", exc)
        return options
    if state is None or state.proxy_added is None or state.proxy_added in options.trusted_proxy:
        return options
    return replace(options, trusted_proxy=(*options.trusted_proxy, state.proxy_added))


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
    exec_start = _service_exec_start(_keeping_published_console(options))

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
        ServiceError: When the console's dependencies are missing, a background
            console holds the port or something else does, or the service does
            not come up.
    """
    options = StartOptions(host="127.0.0.1", port=port)
    all_installed, missing_apt, _ = _check_dependencies()
    if not all_installed:
        raise ServiceError(
            "The console's packages are not installed",
            details=f"Missing: {', '.join(missing_apt)}. Run 'noust web install', then again.",
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


def change_trusted_proxies(
    *,
    add: Sequence[str] = (),
    remove: Sequence[str] = (),
    verbose: bool = False,
) -> bool:
    """
    Add or remove peers whose forwarding headers the console service believes.

    A console behind nginx must read the client's address and the scheme from
    ``X-Forwarded-For`` and ``X-Forwarded-Proto`` (the rate limit, the lockout,
    the ``Secure`` cookie and the passkey origin all depend on them), and it
    believes those headers only from a peer it was started with
    ``--trusted-proxy``. This rewrites the service's ``ExecStart`` with the
    change and keeps every other option it has; the new unit applies from the
    console's next restart, which is the caller's to make (a console cannot
    restart itself in the middle of the request that asked).

    Args:
        add: Peers to trust, such as ``127.0.0.1``.
        remove: Peers to stop trusting.
        verbose: Whether to log verbosely.

    Returns:
        True when the unit was rewritten; False when it already said this.

    Raises:
        ServiceError: When the console is not a service of Noust's (a background
            console), or its unit has no ``ExecStart``.
    """
    unit = _service_unit_path()
    if not unit.is_file():
        raise ServiceError(
            "The console does not run as a service on this server",
            details="Make it one first: noust web enable. A console started with "
            "'noust web start' cannot be published.",
        )
    exec_start = _unit_exec_start(unit.read_text(encoding="utf-8"))
    if exec_start is None:
        raise ServiceError(
            f"{unit} has no ExecStart line",
            details="Write it again with: noust web enable",
        )
    options = options_from_argv(shlex.split(exec_start))
    wanted = [peer for peer in options.trusted_proxy if peer not in remove]
    wanted += [peer for peer in add if peer not in wanted]
    if tuple(wanted) == options.trusted_proxy:
        return False
    changed = replace(options, trusted_proxy=tuple(wanted))
    _service_manager(verbose).install_unit(
        WEB_UNIT, WEB_UNIT_TEMPLATE, {"exec_start": _service_exec_start(changed)}
    )
    return True


def restart_console_service(verbose: bool = False) -> None:
    """
    Restart the console service and wait until it serves again.

    The unit is read for the address it binds, so the wait is on the right port
    whatever ``noust web enable`` was given.

    Args:
        verbose: Whether to log verbosely.

    Raises:
        ServiceError: When the service is not installed, or does not come back
            (the journal is in the details).
    """
    unit = _service_unit_path()
    if not unit.is_file():
        raise ServiceError(
            "The console does not run as a service on this server",
            details="Make it one first: noust web enable.",
        )
    exec_start = _unit_exec_start(unit.read_text(encoding="utf-8"))
    options = options_from_argv(shlex.split(exec_start or ""))
    manager = _service_manager(verbose)
    manager.restart(WEB_UNIT)
    _wait_until_serving(manager, normalize_host(options.host), options.port)


def console_service_port() -> tuple[int, str]:
    """
    Say where the console service listens on loopback, and over which scheme.

    Returns:
        The port and ``http`` or ``https`` (the console terminating TLS itself).
    """
    return _console_upstream(None)


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
