# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The server's side of a link: dial the central and keep the way open.

``noust fleet link`` runs on the server, as root, by the server's own operator
(a central never gets a shell here, and a server behind NAT could not be
reached to give it one). The central prints the command with everything it
needs to be recognised: its address, its SSH host key, the loopback port that
is this server's alone and the invitation. It:

1. makes sure the console runs as a loopback service (the central's requests
   arrive on its port, from this machine's own ssh);
2. generates the key this server dials in with, at ``<state>/link/<central>/``,
   readable by root alone, and pins the central's host key beside it: the link
   never connects to a host it was not told the key of;
3. writes ``noust-link-<central>.service``, which runs

       ssh -N -T -F /dev/null -i <key> -l noust-link -R 127.0.0.1:<port>:127.0.0.1:<console>

   with the options every Noust connection carries (:func:`noust.fleet.tunnels.ssh_options`)
   and restarts it whenever it ends, however long the central was out of reach;
4. issues the fleet token, ``fleet-<central>``, and prints the link code: the
   public half of the key, the token, the console port and the invitation.
   The operator pastes it into the central.

What the central's account may do with that key is decided on the central
(:mod:`noust.fleet.link.central`): listen on one loopback port, and nothing
else. What this server offers through it is what ``noust fleet access`` says,
as for any central.

``noust fleet unlink`` stops the link and removes its key, unit and tokens.
"""

from __future__ import annotations

import builtins
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader

from noust import __version__
from noust.core import paths
from noust.core.exceptions import NodeError, SecurityError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandError, CommandRunner, get_runner
from noust.fleet.authorize import (
    Progress,
    ceiling_in_force,
    live_fleet_tokens,
    next_token_name,
)
from noust.fleet.keys import run_keygen
from noust.fleet.link.code import NONCE, LinkCode
from noust.fleet.link.listener import LINK_USER
from noust.fleet.models import (
    local_node_name,
    parse_public_key,
    validate_central_name,
    validate_host,
    validate_node_name,
    validate_port,
)
from noust.fleet.policy import FleetAccess, current_access, set_access
from noust.fleet.tunnels import LOOPBACK, ssh_algorithms, ssh_forward_argv, ssh_options

if TYPE_CHECKING:
    from noust.core.store import NoustStore
    from noust.web.auth import TokenManager

#: Where systemd units are written.
UNIT_DIR = Path("/etc/systemd/system")

#: The link's private key, public key and pinned central host key.
KEY_NAME = "id_ed25519"
KNOWN_HOSTS_NAME = "known_hosts"

#: Mode of the unit file and of the pinned host key (nothing secret).
UNIT_MODE = 0o644
KNOWN_HOSTS_MODE = 0o600

#: How long a systemctl call may take.
SYSTEMCTL_TIMEOUT = 30

#: Ports a link may be asked to listen on at the central: sshd refuses a listen
#: below 1024 to every account but root.
MIN_REMOTE_PORT = 1024


def unit_name(central: str) -> str:
    """
    Name the unit that keeps the link to a central up.

    Args:
        central: The central's name.

    Returns:
        ``noust-link-<central>.service``.
    """
    return f"{paths.LINK_UNIT_PREFIX}{validate_central_name(central)}.service"


def link_dir(central: str, root: Path | None = None) -> Path:
    """
    Name the directory a link's key and pinned host key are in.

    Args:
        central: The central's name.
        root: The directory all links are under; Noust's by default.

    Returns:
        ``<root>/<central>``.
    """
    return (root if root is not None else paths.link_dir()) / validate_central_name(central)


def known_hosts_host(host: str, port: int) -> str:
    """
    Spell the host field of a ``known_hosts`` line for a host and port.

    Args:
        host: A name or an address (IPv6 without brackets).
        port: The SSH port.

    Returns:
        ``host``, or ``[host]:port`` when the port is not 22.
    """
    return host if port == 22 else f"[{host}]:{port}"


def link_argv(
    *,
    host: str,
    ssh_port: int,
    remote_port: int,
    console_port: int,
    key: Path,
    known_hosts: Path,
    algorithms: dict[str, str],
    ssh: str = "ssh",
) -> builtins.list[str]:
    """
    Build the ssh command line of a link.

    Args:
        host: The central's address.
        ssh_port: Its SSH port.
        remote_port: The loopback port the forward leaves on the central.
        console_port: This server's console port, the only thing it forwards.
        key: The link's private key.
        known_hosts: The pinned host key of the central.
        algorithms: :func:`~noust.fleet.tunnels.ssh_algorithms`' choice.
        ssh: The ssh program; an absolute path in a unit.

    Returns:
        The argv.
    """
    argv = ssh_forward_argv(
        direction="-R",
        spec=f"{LOOPBACK}:{remote_port}:{LOOPBACK}:{console_port}",
        identity=key,
        port=ssh_port,
        user=LINK_USER,
        host=host,
        options=ssh_options(known_hosts, algorithms=algorithms),
    )
    return [ssh, *argv[1:]]


def render_unit(central: str, argv: builtins.list[str]) -> str:
    """
    Render the unit that keeps a link up.

    Args:
        central: The central's name.
        argv: The ssh command line.

    Returns:
        The unit file.
    """
    from noust.managers.cron_manager import CronManager

    environment = Environment(
        loader=PackageLoader("noust", "templates/systemd"),
        trim_blocks=True,
        lstrip_blocks=True,
        # A systemd unit, not markup: the templates escape through _escape.j2.
        autoescape=False,  # noqa: S701
    )
    return environment.get_template("link.service.j2").render(
        central=validate_central_name(central), exec_start=CronManager.build_exec_start(argv)
    )


@dataclass
class LinkResult:
    """
    What ``noust fleet link`` did, and the code to paste into the central.

    Attributes:
        link_code: The code. It holds the token: shown once, like a token.
            Empty under a rehearsal.
        central: The central's name.
        node_name: The name this server goes by on the central.
        unit: The unit that keeps the link up.
        host: The address it dials.
        ssh_port: The SSH port it dials.
        remote_port: The loopback port its link listens on, at the central.
        console_port: This server's console port.
        key_fingerprint: The link key's fingerprint (empty under a rehearsal).
        host_key_fingerprint: The central's pinned host key.
        token_name: The token's name here.
        replaced_tokens: Older tokens of this central, now revoked.
        access: This server's ceiling for centrals, as in force.
        active: Whether the unit is running now.
        warnings: What the operator should know.
    """

    link_code: str
    central: str
    node_name: str
    unit: str
    host: str
    ssh_port: int
    remote_port: int
    console_port: int
    key_fingerprint: str
    host_key_fingerprint: str
    token_name: str
    replaced_tokens: builtins.list[str] = field(default_factory=builtins.list)
    access: dict[str, Any] = field(default_factory=dict)
    active: bool = False
    warnings: builtins.list[str] = field(default_factory=builtins.list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return asdict(self)


def _systemctl(runner: CommandRunner, *args: str, check: bool = False) -> Any:
    """
    Run one systemctl verb.

    Args:
        runner: The runner.
        args: What follows ``systemctl``.
        check: Raise when it fails.

    Returns:
        The result.
    """
    return runner.run(["systemctl", *args], timeout=SYSTEMCTL_TIMEOUT, check=check)


def link(
    *,
    central: str,
    host: str,
    ssh_port: int,
    host_key: str,
    remote_port: int,
    nonce: str,
    node_name: str | None = None,
    tokens: TokenManager,
    ensure_console: Callable[[], int],
    confirm_replace: Callable[[builtins.list[str]], bool],
    access: FleetAccess | None = None,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
    link_root: Path | None = None,
    unit_dir: Path = UNIT_DIR,
    store: NoustStore | None = None,
    progress: Progress | None = None,
    actor: str | None = None,
    ssh_program: str | None = None,
    dry_run: bool = False,
) -> LinkResult:
    """
    Link this server to a central that cannot reach it, and build the code for it.

    Every check runs before anything changes. A step that fails puts back what
    the ones before it wrote: the files, the unit, the token.

    Args:
        central: The central's name, as its invitation says.
        host: The address to dial.
        ssh_port: The central's SSH port.
        host_key: The central's SSH host key, which is pinned.
        remote_port: The loopback port the forward listens on at the central.
        nonce: The invitation's nonce, which the code carries back.
        node_name: The name this server goes by on the central; its host name by default.
        tokens: This server's token manager.
        ensure_console: Makes sure the console runs as a loopback service and
            returns its port; raises when it cannot.
        confirm_replace: Asked with the names of this central's live tokens,
            when there are any; False cancels before anything changes.
        access: This server's new ceiling for its centrals; None keeps the one in force.
        runner: The runner.
        fs: The filesystem seam.
        link_root: Where links' keys are kept; Noust's directory by default.
        unit_dir: Where the unit is written.
        store: The store the ceiling is kept in.
        progress: Told when each step starts: ``(step, total, what)``.
        actor: Who links, recorded with the ceiling.
        ssh_program: The ssh client the unit runs; found on the path by default.
        dry_run: Rehearse: every check runs, nothing is written, started or issued.

    Returns:
        What was done (or, under a rehearsal, what would be) and the link code.

    Raises:
        NodeError: When any input is wrong, ssh is missing, the operator cancels,
            or a step fails (what it had changed is put back).
    """
    central = validate_central_name(central)
    address = validate_host(host.strip().strip("[]"))
    dial_port = validate_port(ssh_port, "SSH port")
    remote = validate_port(remote_port, "remote port")
    if remote < MIN_REMOTE_PORT:
        raise NodeError(
            f"The remote port {remote} is a privileged port",
            details="sshd only lets an unprivileged account listen above 1023; "
            "invite the server again.",
            field="remote_port",
        )
    pinned = parse_public_key(host_key, what="the central's SSH host key")
    if not NONCE.fullmatch(nonce or ""):
        raise NodeError(
            "The invitation is malformed",
            details="Copy the command the central printed whole, with its --invite value.",
            field="invite",
        )
    name = validate_node_name(node_name or local_node_name())
    runner = runner or get_runner()
    fs = fs or get_fs()
    report = progress or (lambda step, total, what: None)
    total = 5

    report(1, total, f"Checking this server can dial {central}")
    if not runner.exists("ssh"):
        raise NodeError(
            "The ssh client is not installed on this server",
            details="Install it (openssh-client on Debian and Ubuntu, openssh-clients on "
            "RHEL, Fedora and SUSE), then run the command again.",
        )
    ssh = ssh_program or shutil.which("ssh") or "/usr/bin/ssh"
    algorithms = ssh_algorithms(runner)
    older = live_fleet_tokens(tokens, central)
    if older and not confirm_replace([str(record["name"]) for record in older]):
        raise NodeError("Cancelled: nothing was changed")

    report(2, total, "Making sure the console runs on 127.0.0.1 as a service")
    console_port = ensure_console()

    directory = link_dir(central, link_root)
    key_path = directory / KEY_NAME
    known_hosts = directory / KNOWN_HOSTS_NAME
    unit = unit_name(central)
    unit_path = unit_dir / unit
    argv = link_argv(
        host=address,
        ssh_port=dial_port,
        remote_port=remote,
        console_port=console_port,
        key=key_path,
        known_hosts=known_hosts,
        algorithms=algorithms,
        ssh=ssh,
    )
    token_name = next_token_name(tokens, central)
    in_force = access or ceiling_in_force(store)
    if dry_run:
        report(total, total, f"Would write {unit_path} and issue the token {token_name}")
        return LinkResult(
            link_code="",
            central=central,
            node_name=name,
            unit=unit,
            host=address,
            ssh_port=dial_port,
            remote_port=remote,
            console_port=console_port,
            key_fingerprint="",
            host_key_fingerprint=pinned.fingerprint,
            token_name=token_name,
            replaced_tokens=[str(record["name"]) for record in older],
            access=in_force.to_dict(),
        )

    report(3, total, f"Generating the key this server dials {central} with")
    previous_unit = unit_path.read_text(encoding="utf-8") if unit_path.exists() else None
    try:
        run_keygen(runner, fs, key_path, f"noust-link:{name}@{central}")
    except CommandError as exc:
        raise NodeError(
            "Could not generate the link's key",
            details="ssh-keygen must be installed; run as root.",
            output=exc.details,
        ) from exc
    link_key = parse_public_key((directory / f"{KEY_NAME}.pub").read_text(encoding="utf-8"))
    fs.write_text(
        known_hosts,
        f"{known_hosts_host(address, dial_port)} {pinned.bare}\n",
        mode=KNOWN_HOSTS_MODE,
    )
    fs.chmod(known_hosts, KNOWN_HOSTS_MODE)

    report(4, total, f"Issuing the token {token_name}")
    try:
        issued = tokens.create_fleet_token(token_name)
    except SecurityError as exc:
        _remove_directory(fs, directory)
        raise NodeError(
            "Could not create the fleet token", details=exc.details or exc.message
        ) from exc
    for record in older:
        tokens.revoke_api_token(int(record["id"]))
    in_force = set_access(access, actor=actor, store=store) if access else current_access(store)

    report(5, total, f"Starting {unit}")
    try:
        fs.write_text(unit_path, render_unit(central, argv), mode=UNIT_MODE)
        fs.chmod(unit_path, UNIT_MODE)
        _systemctl(runner, "daemon-reload", check=True)
        _systemctl(runner, "enable", unit, check=True)
        # A link made again replaces the key of one already running: restart, not start.
        _systemctl(runner, "restart", unit, check=True)
    except (CommandError, OSError) as exc:
        _undo_unit(runner, fs, unit, unit_path, previous_unit)
        tokens.revoke_api_token(int(issued["id"]))
        _remove_directory(fs, directory)
        output = exc.details if isinstance(exc, CommandError) else str(exc)
        raise NodeError(
            f"{unit} could not be started",
            details="Nothing was kept: the key, the unit and the token were removed.",
            output=output,
        ) from exc
    active = _systemctl(runner, "is-active", unit).stdout.strip() == "active"
    code = LinkCode(
        node_name=name,
        central=central,
        link_key=link_key.bare,
        console_port=console_port,
        token=str(issued["token"]),
        noust_version=__version__,
        nonce=nonce,
        token_name=str(issued["name"]),
    )
    return LinkResult(
        link_code=code.encode(),
        central=central,
        node_name=name,
        unit=unit,
        host=address,
        ssh_port=dial_port,
        remote_port=remote,
        console_port=console_port,
        key_fingerprint=link_key.fingerprint,
        host_key_fingerprint=pinned.fingerprint,
        token_name=str(issued["name"]),
        replaced_tokens=[str(record["name"]) for record in older],
        access=in_force.to_dict(),
        active=active,
    )


def _remove_directory(fs: FileSystem, path: Path) -> None:
    """
    Remove a directory when it exists.

    Args:
        fs: The filesystem seam.
        path: The directory.
    """
    if path.exists():
        fs.remove_tree(path)


def _undo_unit(
    runner: CommandRunner, fs: FileSystem, unit: str, path: Path, previous: str | None
) -> None:
    """
    Put a unit back as it was after a failed start.

    Args:
        runner: The runner.
        fs: The filesystem seam.
        unit: The unit.
        path: Its file.
        previous: What it held before, None when it did not exist.
    """
    if previous is None:
        _systemctl(runner, "disable", "--now", unit)
        fs.remove(path, missing_ok=True)
    else:
        fs.write_text(path, previous, mode=UNIT_MODE)
    _systemctl(runner, "daemon-reload")


@dataclass
class UnlinkResult:
    """
    What ``noust fleet unlink`` removed.

    Attributes:
        central: The central's name.
        unit: The unit that kept the link up.
        stopped: Whether the unit existed and was stopped.
        removed_files: Whether the key, the pinned host key and the unit were removed.
        revoked_tokens: Token names revoked.
    """

    central: str
    unit: str
    stopped: bool
    removed_files: bool
    revoked_tokens: builtins.list[str]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return asdict(self)


def unlink(
    *,
    central: str,
    tokens: TokenManager,
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
    link_root: Path | None = None,
    unit_dir: Path = UNIT_DIR,
    dry_run: bool = False,
) -> UnlinkResult:
    """
    Stop dialling a central: stop the link, remove its key and unit, revoke its tokens.

    Args:
        central: The central's name.
        tokens: This server's token manager.
        runner: The runner.
        fs: The filesystem seam.
        link_root: Where links' keys are kept.
        unit_dir: Where the unit is.
        dry_run: Report what would be removed without touching anything.

    Returns:
        What was removed (or would be).

    Raises:
        NodeError: When the name is invalid.
    """
    central = validate_central_name(central)
    runner = runner or get_runner()
    fs = fs or get_fs()
    unit = unit_name(central)
    unit_path = unit_dir / unit
    directory = link_dir(central, link_root)
    live = live_fleet_tokens(tokens, central)
    existed = unit_path.exists()
    present = existed or directory.exists()
    if dry_run:
        return UnlinkResult(
            central=central,
            unit=unit,
            stopped=existed,
            removed_files=present,
            revoked_tokens=[str(record["name"]) for record in live],
        )
    if existed:
        _systemctl(runner, "disable", "--now", unit)
    fs.remove(unit_path, missing_ok=True)
    if existed:
        _systemctl(runner, "daemon-reload")
    _remove_directory(fs, directory)
    revoked: builtins.list[str] = []
    for record in live:
        tokens.revoke_api_token(int(record["id"]))
        revoked.append(str(record["name"]))
    return UnlinkResult(
        central=central,
        unit=unit,
        stopped=existed,
        removed_files=present,
        revoked_tokens=revoked,
    )
