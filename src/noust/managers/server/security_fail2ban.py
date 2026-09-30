# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
fail2ban: whether it runs, what it has banned, lifting a ban, and installing it.

``fail2ban-client`` has no JSON output and its manual documents no exit codes,
so its status is read from the text it prints, which has been stable for a
decade (``Jail list:``, ``Currently banned:``, ``Banned IP list:``).

Installing it is where panels lock their own operator out: a jail that bans
after five failures bans the operator who mistyped a password five times, or
the central whose tunnel reconnects. Noust's jail file puts the addresses of
every SSH session open now (the operator's and the central's) in ``ignoreip``
before fail2ban starts, uses the journal (Debian 12 has no ``auth.log`` by
default, and a ``sshd`` jail with nothing to read keeps fail2ban from starting),
and bans through whichever firewall is active. On RHEL and its rebuilds
fail2ban lives in EPEL, a third-party repository, so enabling it is a separate,
explicit yes (:class:`~noust.managers.server.errors.ConfirmationRequiredError`).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from noust.core import paths
from noust.core.exceptions import SecurityError, ValidationError
from noust.core.runner import CommandResult
from noust.managers.server.errors import ConfirmationRequiredError, UnsupportedHostError
from noust.managers.server.host import PROBE_TIMEOUT, PackageFamily, detect_platform
from noust.managers.server.security_pending import FAIL2BAN_JAIL, audit
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_sshd import read_no_follow

#: Environment of every fail2ban and package command.
ENV = {"LC_ALL": "C", "TERM": "dumb", "DEBIAN_FRONTEND": "noninteractive"}

#: How long installing packages may take.
INSTALL_TIMEOUT = 600

#: Brute-force protections that stand in for fail2ban.
SUBSTITUTES = ("crowdsec", "sshguard")

#: Distributions whose fail2ban comes from EPEL, and the package that enables it.
EPEL_PACKAGES = {
    "almalinux": "epel-release",
    "rocky": "epel-release",
    "centos": "epel-release",
    "ol": "oracle-epel-release-el{major}",
}

#: A jail name as fail2ban accepts it.
_JAIL = re.compile(r"[A-Za-z0-9_.-]{1,64}")


@dataclass(frozen=True)
class Jail:
    """
    One fail2ban jail.

    Attributes:
        name: Its name.
        currently_failed: Failures counted now.
        total_failed: Failures since it started.
        currently_banned: Addresses banned now.
        total_banned: Bans since it started.
        banned: The addresses banned now.
        reads: What it watches: files, or journal matches.
    """

    name: str
    currently_failed: int = 0
    total_failed: int = 0
    currently_banned: int = 0
    total_banned: int = 0
    banned: tuple[str, ...] = ()
    reads: str = ""


@dataclass
class Fail2banState:
    """
    fail2ban on this server.

    Attributes:
        installed: ``fail2ban-client`` is installed.
        running: The server answered ``ping``.
        jails: Its jails.
        substitutes: Other brute-force protections that are active.
        needs_epel: Installing it here enables EPEL first.
        install_supported: Noust can install it here.
        install_hint: Why not, or how by hand.
        error: What fail2ban-client said when it failed, verbatim.
    """

    installed: bool = False
    running: bool = False
    jails: list[Jail] = field(default_factory=list)
    substitutes: list[str] = field(default_factory=list)
    needs_epel: bool = False
    install_supported: bool = True
    install_hint: str = ""
    error: str = ""

    def jail(self, name: str) -> Jail | None:
        """
        Find a jail.

        Args:
            name: Its name.

        Returns:
            The jail, or None.
        """
        return next((jail for jail in self.jails if jail.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the state for the API and ``--json``.

        Returns:
            Every field.
        """
        return asdict(self)


def _field(text: str, label: str) -> str:
    """
    Read a ``label:<tab>value`` field of fail2ban-client's tree.

    Args:
        text: The output.
        label: The label.

    Returns:
        The value, or empty.
    """
    match = re.search(rf"{re.escape(label)}:\s*(.*)", text)
    return match.group(1).strip() if match else ""


def _int(text: str) -> int:
    return int(text) if text.isdigit() else 0


def parse_jail_list(text: str) -> list[str]:
    """
    Read ``fail2ban-client status``.

    Args:
        text: Its output.

    Returns:
        The jail names.
    """
    return [name.strip() for name in _field(text, "Jail list").split(",") if name.strip()]


def parse_jail(name: str, text: str) -> Jail:
    """
    Read ``fail2ban-client status <jail>``.

    Args:
        name: The jail.
        text: Its output.

    Returns:
        The jail.
    """
    return Jail(
        name=name,
        currently_failed=_int(_field(text, "Currently failed")),
        total_failed=_int(_field(text, "Total failed")),
        currently_banned=_int(_field(text, "Currently banned")),
        total_banned=_int(_field(text, "Total banned")),
        banned=tuple(_field(text, "Banned IP list").split()),
        reads=_field(text, "File list") or _field(text, "Journal matches"),
    )


def render_jail(ignore: list[str], ports: list[int], banaction: str) -> str:
    """
    Write Noust's jail file.

    Args:
        ignore: Addresses never banned, already validated.
        ports: sshd's ports.
        banaction: How bans are enforced.

    Returns:
        The file's content.
    """
    return (
        f"# {paths.UNIT_MARKER}. Noust's fail2ban settings; change them with Noust.\n"
        "# The addresses in ignoreip were connected over SSH when this was written:\n"
        "# the operator and any central's tunnel, which a ban would lock out.\n"
        "[DEFAULT]\n"
        f"ignoreip = {' '.join(['127.0.0.1/8', '::1', *ignore])}\n"
        "bantime = 1h\n"
        "findtime = 10m\n"
        "maxretry = 5\n"
        f"banaction = {banaction}\n"
        "\n"
        "[sshd]\n"
        "enabled = true\n"
        "backend = systemd\n"
        f"port = {','.join(str(port) for port in ports)}\n"
    )


class Fail2ban:
    """
    Read fail2ban, lift a ban, install it.

    Args:
        probe: This pass's look at the machine.
        on_output: Receives every command and its output, verbatim.
    """

    def __init__(
        self, probe: SecurityProbe, *, on_output: Callable[[str], None] | None = None
    ) -> None:
        self.probe = probe
        self.on_output = on_output

    def _run(self, argv: list[str], timeout: int = PROBE_TIMEOUT) -> CommandResult:
        return self.probe.runner.run(argv, timeout=timeout, env=ENV)

    def _say(self, argv: list[str], result: CommandResult) -> None:
        if self.on_output:
            self.on_output("$ " + " ".join(argv))
            for line in (result.stdout + result.stderr).splitlines():
                self.on_output(line)

    def _install_plan(self) -> tuple[list[list[str]], bool, str]:
        """
        How fail2ban is installed here.

        Returns:
            The commands (EPEL's first, when needed), whether EPEL is needed,
            and why it cannot be installed (empty when it can).
        """
        platform = detect_platform(self.probe.runner, self.probe.host)
        os_id = platform.os.id
        major = platform.os.version_id.split(".")[0]
        if platform.family is PackageFamily.APT:
            return (
                [["apt-get", "install", "-y", "fail2ban", "python3-systemd"]],
                False,
                "",
            )
        if platform.family is PackageFamily.ZYPPER:
            return [["zypper", "--non-interactive", "install", "fail2ban"]], False, ""
        if platform.family is PackageFamily.DNF:
            if os_id == "fedora":
                return [["dnf", "install", "-y", "fail2ban"]], False, ""
            if os_id == "rhel":
                return (
                    [],
                    True,
                    "On RHEL, fail2ban comes from EPEL, which needs your subscription's "
                    "CodeReady Builder repository: subscription-manager repos --enable "
                    f"codeready-builder-for-rhel-{major}-$(arch)-rpms; dnf install -y "
                    f"https://dl.fedoraproject.org/pub/epel/epel-release-latest-{major}.noarch.rpm; "
                    "dnf install -y fail2ban. Then install it again here to configure it.",
                )
            epel = EPEL_PACKAGES.get(os_id)
            if epel is None and "rhel" in platform.os.id_like:
                epel = "epel-release"
            if epel is not None:
                return (
                    [
                        ["dnf", "install", "-y", epel.format(major=major)],
                        ["dnf", "install", "-y", "fail2ban"],
                    ],
                    True,
                    "",
                )
            return [["dnf", "install", "-y", "fail2ban"]], False, ""
        return [], False, platform.why_updates_unsupported() or "No supported package manager."

    def state(self) -> Fail2banState:
        """
        Read fail2ban.

        Returns:
            Whether it is installed and running, its jails and bans, what else
            protects SSH, and how it would be installed.
        """
        runner = self.probe.runner
        commands, needs_epel, blocker = self._install_plan()
        state = Fail2banState(
            needs_epel=needs_epel,
            install_supported=bool(commands) and not blocker,
            install_hint=blocker,
        )
        substitutes = self._run(["systemctl", "is-active", *[f"{s}.service" for s in SUBSTITUTES]])
        state.substitutes = [
            name
            for name, answer in zip(SUBSTITUTES, substitutes.stdout.splitlines(), strict=False)
            if answer.strip() == "active"
        ]
        if not runner.exists("fail2ban-client"):
            return state
        state.installed = True
        ping = self._run(["fail2ban-client", "ping"])
        if not ping.success or "pong" not in ping.stdout:
            state.error = (ping.stderr or ping.stdout).strip()
            return state
        state.running = True
        status = self._run(["fail2ban-client", "status"])
        for name in parse_jail_list(status.stdout):
            if not _JAIL.fullmatch(name):
                continue
            detail = self._run(["fail2ban-client", "status", name])
            state.jails.append(parse_jail(name, detail.stdout))
        return state

    def unban(self, address: str, jail: str | None = None) -> dict[str, Any]:
        """
        Lift a ban.

        Args:
            address: The banned address.
            jail: The jail; every jail that bans it when omitted.

        Returns:
            The jails it was lifted in.

        Raises:
            ValidationError: The address is not one, or the jail does not exist.
            SecurityError: fail2ban refused.
        """
        try:
            ip = str(ipaddress.ip_address(address.strip()))
        except ValueError as exc:
            raise ValidationError(f"{address!r} is not an IP address", field="address") from exc
        state = self.state()
        if not state.running:
            raise SecurityError("fail2ban is not running", output=state.error or None)
        if jail is not None:
            if state.jail(jail) is None:
                raise ValidationError(
                    f"There is no fail2ban jail {jail!r}",
                    details=f"Jails: {', '.join(j.name for j in state.jails) or 'none'}.",
                    field="jail",
                )
            jails = [jail]
        else:
            jails = [each.name for each in state.jails if ip in each.banned]
            if not jails:
                raise ValidationError(f"{ip} is not banned by any jail", field="address")
        for name in jails:
            argv = ["fail2ban-client", "set", name, "unbanip", ip]
            result = self._run(argv)
            self._say(argv, result)
            if not result.success:
                raise SecurityError(
                    f"fail2ban did not lift the ban of {ip} in {name}",
                    output=(result.stderr or result.stdout).strip() or None,
                )
        audit("server.fail2ban", f"address:{ip}", action="unban", jails=jails)
        return {"address": ip, "jails": jails}

    def banaction(self) -> str:
        """
        How bans are enforced: through the active firewall.

        Returns:
            fail2ban's action name.
        """
        from noust.managers.server.security_firewall import Firewall

        backend = Firewall(self.probe).state()
        if backend.active and backend.backend == "ufw":
            return "ufw"
        if backend.active and backend.backend == "firewalld":
            return "firewallcmd-rich-rules"
        return "nftables-multiport" if self.probe.runner.exists("nft") else "iptables-multiport"

    def install(self, *, epel: bool = False, ignore: list[str] | None = None) -> dict[str, Any]:
        """
        Install fail2ban with an ``sshd`` jail that cannot ban who is connected now.

        Args:
            epel: The operator agreed to enable EPEL, where it is needed.
            ignore: More addresses never to ban (the console's client).

        Returns:
            The jail file, the addresses ignored, and the ``sshd`` jail's status.

        Raises:
            ConfirmationRequiredError: EPEL is needed and ``epel`` is False.
            UnsupportedHostError: It cannot be installed here.
            SecurityError: A step failed; the jail file was put back.
        """
        commands, needs_epel, blocker = self._install_plan()
        if blocker or not commands:
            raise UnsupportedHostError("Noust cannot install fail2ban here", blocker)
        if needs_epel and not epel:
            raise ConfirmationRequiredError(
                "fail2ban comes from EPEL on this system",
                details="EPEL is a third-party repository maintained by Fedora. Confirm that it "
                "may be enabled to install fail2ban from it.",
                required={"epel": True, "packages": [argv[-1] for argv in commands]},
            )
        for argv in commands:
            result = self.probe.runner.run(argv, timeout=INSTALL_TIMEOUT, env=ENV)
            self._say(argv, result)
            if not result.success:
                raise SecurityError(
                    f"Installing failed: {' '.join(argv)}",
                    output=(result.stderr or result.stdout).strip() or None,
                )
        extra: list[str] = []
        for address in [*(ignore or [])]:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if not ip.is_loopback:
                extra.append(str(ip))
        sessions = sorted(
            {
                session.connection.peer_address
                for session in self.probe.sessions()
                if not ipaddress.ip_address(session.connection.peer_address).is_loopback
            }
        )
        addresses = list(dict.fromkeys([*sessions, *extra]))
        ports = sorted(self.probe.ssh_ports())
        target = self.probe.host.at(FAIL2BAN_JAIL)
        previous = read_no_follow(target)
        text = render_jail(addresses, ports, self.banaction())
        self.probe.fs.write_text(target, text, mode=0o644)
        steps = [
            ["fail2ban-client", "-t"],
            ["systemctl", "enable", "--now", "fail2ban"],
            ["fail2ban-client", "reload"],
            ["fail2ban-client", "status", "sshd"],
        ]
        for argv in steps:
            result = self._run(argv, timeout=60)
            self._say(argv, result)
            if not result.success:
                if previous is None:
                    self.probe.fs.remove(target, missing_ok=True)
                else:
                    self.probe.fs.write_text(target, previous, mode=0o644)
                self._run(["fail2ban-client", "reload"])
                audit(
                    "server.fail2ban",
                    FAIL2BAN_JAIL,
                    outcome="failure",
                    action="install",
                    reason=(result.stderr or result.stdout).strip(),
                )
                raise SecurityError(
                    f"fail2ban did not accept the sshd jail ({' '.join(argv)}), so "
                    f"{FAIL2BAN_JAIL} was put back",
                    output=(result.stderr or result.stdout).strip() or None,
                )
        audit("server.fail2ban", FAIL2BAN_JAIL, action="install", ignored=addresses, ports=ports)
        return {"jail_file": FAIL2BAN_JAIL, "ignored": addresses, "ports": ports}
