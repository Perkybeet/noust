"""
Shared pieces of the link tests: a central's sshd and accounts, and the wiring of a central.

A central that takes servers that dial in has an sshd, an account (``noust-link``)
and a key file; a server's link leaves a listener on a loopback port, which the
central reads from ``/proc/net/tcp``. All of it is in the test's directory.
"""

from __future__ import annotations

import pwd
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core.exceptions import NodeError
from noust.core.runner import CommandResult
from noust.fleet.link.central import LINK_DROPIN_NAME, LinkManager
from noust.fleet.link.code import LinkCode
from noust.fleet.link.listener import LINK_USER
from noust.fleet.nodes import NodeManager
from noust.fleet.tunnels import TunnelManager
from tests.fleet_support import (
    HOST_KEY,
    TOKEN,
    Fleet,
    KeygenRunner,
    build_fleet,
    ed25519_line,
)

#: The uid of the link account on the test's central.
LINK_UID = 998

#: The key a server dials in with, and its code's nonce.
LINK_KEY = ed25519_line(41, "noust-link:web-5@nas")
NONCE = "0123456789abcdef0123456789abcdef"

SSHD_T = """port 2222
allowtcpforwarding yes
disableforwarding no
permitrootlogin without-password
authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2
"""

PROC_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  "
    "timeout inode\n"
)


def _hex_address(port: int, host: str = "127.0.0.1") -> str:
    """
    Spell a listening address as ``/proc/net/tcp`` does.

    Args:
        port: The port.
        host: ``127.0.0.1`` or ``0.0.0.0``.

    Returns:
        ``0100007F:5DC0``.
    """
    octets = [int(part) for part in host.split(".")]
    return "".join(f"{octet:02X}" for octet in reversed(octets)) + f":{port:04X}"


class CentralRunner(KeygenRunner):
    """
    sshd, systemctl and useradd as a central has them.

    ``sshd -T`` prints :attr:`config`; evaluated for the link account, the
    settings of Noust's ``Match`` block come first, as sshd applies them unless
    :attr:`ignore_policy` says another block wins. ``useradd`` adds the account.
    """

    def __init__(self, central: Central) -> None:
        super().__init__()
        self.central = central
        self.config = SSHD_T
        self.ignore_policy = False
        self.script(["systemctl", "is-active", "ssh.service"], stdout="active\n")

    def _scripted_for(self, argv: list[str]) -> bool:
        return any(tuple(argv[: len(s.match)]) == s.match for s in self._scripted)

    def run(self, argv, **kwargs: Any) -> CommandResult:  # type: ignore[override]
        check = kwargs.pop("check", False)
        result = super().run(argv, **kwargs)
        args = [str(a) for a in argv]
        if args[:2] == ["sshd", "-T"] and not self._scripted_for(args):
            output = self.config
            if "-C" in args and f"user={LINK_USER}," in args[args.index("-C") + 1]:
                if not self.ignore_policy:
                    output = self.central.policy_lines() + output
            result = replace(result, stdout=output)
        if args and args[0] == "useradd" and result.success:
            self.central.add_account(args[-1], login=args[args.index("--shell") + 1])
        return result.check() if check else result


class Central:
    """A central's sshd, accounts, host key and socket tables, inside the test's directory."""

    def __init__(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp_path = tmp_path
        self.runner = CentralRunner(self)
        self.host_key_file = tmp_path / "ssh_host_ed25519_key.pub"
        self.host_key_file.write_text(HOST_KEY + " root@central\n")
        self.dropin_dir = tmp_path / "sshd_config.d"
        self.dropin_dir.mkdir()
        self.sshd_config = tmp_path / "sshd_config"
        self.sshd_config.write_text(f"Include {self.dropin_dir}/*.conf\nPort 2222\n")
        self.keys_dir = tmp_path / "etc-ssh-noust"
        self.proc_net = tmp_path / "proc-net"
        self.proc_net.mkdir()
        self.listeners: list[tuple[int, int, str]] = []
        self.write_sockets()
        self.homes: dict[str, tuple[int, Path, str]] = {
            "root": (0, tmp_path / "root", "/bin/bash"),
        }
        for _, home, _ in self.homes.values():
            home.mkdir(parents=True)
        self.now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)

    # -- accounts -------------------------------------------------------------------

    def add_account(self, user: str, *, login: str = "/usr/sbin/nologin") -> None:
        uid = LINK_UID if user == LINK_USER else 999
        self.homes[user] = (uid, Path("/nonexistent"), login)

    def passwd(self, user: str) -> pwd.struct_passwd:
        if user not in self.homes:
            raise KeyError(user)
        uid, home, shell = self.homes[user]
        return pwd.struct_passwd((user, "x", uid, uid, "", str(home), shell))

    # -- sshd ------------------------------------------------------------------------

    @property
    def dropin(self) -> Path:
        return self.dropin_dir / LINK_DROPIN_NAME

    @property
    def keys_file(self) -> Path:
        return self.keys_dir / f"{LINK_USER}.keys"

    def policy_lines(self) -> str:
        """What ``sshd -T -C`` prints for the link account from Noust's block, if it is there."""
        if self.dropin.exists():
            text = self.dropin.read_text()
        elif self.sshd_config.exists():
            text = self.sshd_config.read_text()
        else:
            return ""
        lines: list[str] = []
        inside = False
        for raw in text.splitlines():
            if raw.startswith(f"Match User {LINK_USER}"):
                inside = True
                continue
            if inside and raw.startswith("    "):
                keyword, _, value = raw.strip().partition(" ")
                lines.append(f"{keyword.lower()} {value}")
            elif inside:
                break
        return "\n".join(lines) + "\n" if lines else ""

    # -- listeners -------------------------------------------------------------------

    def listen(self, port: int, uid: int = LINK_UID, host: str = "127.0.0.1") -> None:
        """
        Make something listen on a loopback port, as its owner's uid.

        Args:
            port: The port.
            uid: The owner.
            host: The address it listens on.
        """
        self.listeners.append((port, uid, host))
        self.write_sockets()

    def hang_up(self, port: int) -> None:
        """
        Close whatever listens on a port.

        Args:
            port: The port.
        """
        self.listeners = [entry for entry in self.listeners if entry[0] != port]
        self.write_sockets()

    def write_sockets(self) -> None:
        lines = [PROC_HEADER]
        for index, (port, uid, host) in enumerate(self.listeners):
            lines.append(
                f"   {index}: {_hex_address(port, host)} 00000000:0000 0A 00000000:00000000 "
                f"00:00000000 00000000   {uid:5d}        0 {10000 + index} 1 0000000000000000 "
                "100 0 0 10 0\n"
            )
        (self.proc_net / "tcp").write_text("".join(lines))
        (self.proc_net / "tcp6").write_text(PROC_HEADER)

    # -- wiring ----------------------------------------------------------------------

    def manager(self, store: Any, *, clock: Callable[[], datetime] | None = None) -> LinkManager:
        """
        Build the link manager over this central.

        Args:
            store: The store.
            clock: What time it is; the test's by default.

        Returns:
            The manager.
        """
        return LinkManager(
            store=store,
            runner=self.runner,
            sshd_config=self.sshd_config,
            dropin_dir=self.dropin_dir,
            keys_dir=self.keys_dir,
            host_key_file=self.host_key_file,
            passwd=self.passwd,
            proc_net=self.proc_net,
            clock=clock or (lambda: self.now),
        )

    def prepared(self, store: Any) -> LinkManager:
        """
        Build the link manager of a central that was set up.

        Args:
            store: The store.

        Returns:
            The manager, with the account and the sshd block in place.
        """
        manager = self.manager(store)
        manager.setup()
        return manager


@dataclass
class LinkRig:
    """A central wired for servers that dial in: a fleet, its sshd and a link manager."""

    fleet: Fleet
    central: Central
    links: LinkManager
    tunnels: TunnelManager
    manager: NodeManager

    def invite(self, name: str = "web-5", **kwargs: Any):
        """
        Invite a server.

        Args:
            name: Its name.
            **kwargs: Overrides of the invitation's arguments.

        Returns:
            The invitation.
        """
        values: dict[str, Any] = {"host": "central.example.com"}
        values.update(kwargs)
        return self.links.invite(name, **values)

    def code(self, invite: Any, **overrides: Any) -> str:
        """
        Build the code the server would print for an invitation.

        Args:
            invite: The invitation.
            **overrides: Fields to change.

        Returns:
            The encoded code.
        """
        values: dict[str, Any] = {
            "node_name": invite.node,
            "central": invite.central,
            "link_key": " ".join(LINK_KEY.split()[:2]),
            "console_port": 8080,
            "token": TOKEN,
            "noust_version": "3.4.0",
            "nonce": invite.nonce,
            "token_name": "fleet-nas",
        }
        values.update(overrides)
        return LinkCode(**values).encode()


def build_link_rig(tmp_path: Path, *, blockers: Callable[[], list[str]] = list) -> LinkRig:
    """
    Wire a central that takes servers that dial in.

    Args:
        tmp_path: The test's directory.
        blockers: The registration policy (none by default).

    Returns:
        The wiring: the central is set up, and a server whose link listens on
        its port would be answered by the fake node.
    """
    fleet = build_fleet(tmp_path, blockers=blockers)
    central = Central(tmp_path)
    links = central.prepared(fleet.store)
    tunnels = TunnelManager(
        runner=fleet.runner,
        store=fleet.store,
        keys=fleet.keys,
        clock=fleet.clock,
        sleep=lambda seconds: fleet.clock.advance(seconds),
        probe=lambda port: True,
        free_port=lambda: 50000,
        link_owners=lambda: frozenset({0, LINK_UID}),
        proc_net=central.proc_net,
    )
    manager = NodeManager(
        store=fleet.store,
        secrets=fleet.secrets,
        runner=fleet.runner,
        tunnels=tunnels,
        blockers=blockers,
        transport=fleet.node.transport(),
        links=links,
        sleep=lambda seconds: None,
    )
    return LinkRig(fleet, central, links, tunnels, manager)


def refused(call: Callable[[], Any]) -> NodeError:
    """
    Run something that must be refused.

    Args:
        call: The call.

    Returns:
        The error it raised.

    Raises:
        AssertionError: When it was not refused.
    """
    try:
        call()
    except NodeError as exc:
        return exc
    raise AssertionError("it was not refused")
