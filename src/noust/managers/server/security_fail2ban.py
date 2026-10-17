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
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from noust.core import paths
from noust.core.exceptions import SecurityError, ValidationError
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.managers.server.errors import ConfirmationRequiredError, UnsupportedHostError
from noust.managers.server.host import PROBE_TIMEOUT, PackageFamily, detect_platform
from noust.managers.server.security_pending import FAIL2BAN_JAIL, FAIL2BAN_WEB_JAIL, audit
from noust.managers.server.security_probe import SecurityProbe
from noust.managers.server.security_sshd import read_no_follow

#: Environment of every fail2ban and package command.
ENV = {"LC_ALL": "C", "TERM": "dumb", "DEBIAN_FRONTEND": "noninteractive"}

#: How long installing packages may take.
INSTALL_TIMEOUT = 600

#: How many times, half a second apart, a fail2ban that was just started is asked whether it
#: answers. Its unit is ``Type=simple``: systemctl returns when the process started, which is
#: before the socket every ``fail2ban-client`` command talks to exists.
READY_ATTEMPTS = 60

#: What ``fail2ban-client get <jail> actions`` prints for a jail that has none, and what Noust says.
FAIL2BAN_NO_ACTIONS = "No actions"
NO_ACTION = "has no ban action"
READY_PAUSE = 0.5

#: Brute-force protections that stand in for fail2ban.
SUBSTITUTES = ("crowdsec", "sshguard")

#: Distributions whose fail2ban comes from EPEL, and the package that enables it.
EPEL_PACKAGES = {
    "almalinux": "epel-release",
    "rocky": "epel-release",
    "centos": "epel-release",
    "ol": "oracle-epel-release-el{major}",
}

#: The jails that watch nginx, all of which fail2ban ships a filter for: a password refused by
#: ``auth_basic``, a scanner asking for paths no site has, and a client over a rate limit.
WEB_JAILS = ("nginx-http-auth", "nginx-botsearch", "nginx-limit-req")

#: Where the filters of those jails are.
WEB_FILTERS = tuple(f"/etc/fail2ban/filter.d/{name}.conf" for name in WEB_JAILS)

#: The logs of the sites Noust writes: one pair per site, named after its domain.
WEB_LOG_DIR = "/var/log/nginx"

_log = logging.getLogger(__name__)

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
        web_available: nginx is installed, so its logs can be watched.
        web_jails: The jails that watch nginx (:data:`WEB_JAILS`) are all running.
    """

    installed: bool = False
    running: bool = False
    jails: list[Jail] = field(default_factory=list)
    substitutes: list[str] = field(default_factory=list)
    needs_epel: bool = False
    install_supported: bool = True
    install_hint: str = ""
    error: str = ""
    web_available: bool = False
    web_jails: bool = False

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


def render_web_jails(ignore: list[str]) -> str:
    """
    Write the jails that watch nginx.

    The sites Noust writes log to ``<domain>.access.log`` and ``<domain>.error.log`` under
    ``/var/log/nginx``, so each jail reads the whole directory by pattern: a site added tomorrow
    is watched without touching this file. The ignore list repeats the addresses of the sshd jail's
    file and adds the proxies a ban must never reach.

    Args:
        ignore: Addresses and networks never banned, already validated.

    Returns:
        The file's content.
    """
    never = " ".join(["127.0.0.1/8", "::1", *ignore])
    errors = f"{WEB_LOG_DIR}/*.error.log"
    access = f"{WEB_LOG_DIR}/*.access.log"
    return (
        f"# {paths.UNIT_MARKER}. fail2ban watching nginx; change it with Noust.\n"
        "# ignoreip holds who is connected over SSH, the console's client and the proxies in front\n"
        "# of the sites (Cloudflare's ranges and every declared proxy): a ban of one of them\n"
        "# would take every site behind it down.\n"
        "[nginx-http-auth]\n"
        "enabled = true\n"
        "port = http,https\n"
        f"logpath = {errors}\n"
        f"ignoreip = {never}\n"
        "\n"
        "[nginx-botsearch]\n"
        "enabled = true\n"
        "port = http,https\n"
        f"logpath = {access}\n"
        f"ignoreip = {never}\n"
        "\n"
        "[nginx-limit-req]\n"
        "enabled = true\n"
        "port = http,https\n"
        f"logpath = {errors}\n"
        f"ignoreip = {never}\n"
    )


class Fail2ban:
    """
    Read fail2ban, lift a ban, install it.

    Args:
        probe: This pass's look at the machine.
        on_output: Receives every command and its output, verbatim.
        sleep: Waits between looks at a fail2ban that is starting; replaced in tests.
    """

    def __init__(
        self,
        probe: SecurityProbe,
        *,
        on_output: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.probe = probe
        self.on_output = on_output
        self._sleep = sleep

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

    def _wait_until_answering(self) -> CommandResult:
        """
        Wait for a fail2ban that was just started to open the socket its client talks to.

        Returns:
            The last ``ping``: a success once it answers, the failure after
            :data:`READY_ATTEMPTS` looks when it never does.
        """
        result = self._run(["fail2ban-client", "ping"])
        for _ in range(READY_ATTEMPTS):
            if result.success and "pong" in result.stdout:
                return result
            self._sleep(READY_PAUSE)
            result = self._run(["fail2ban-client", "ping"])
        return result

    def _ensure_ban_actions(self, jails: Sequence[str]) -> CommandResult | None:
        """
        Make sure every jail has an action that refuses the addresses it bans.

        After a reload that changes ``banaction`` (Debian's own is ``nftables``, Noust picks the
        action by the firewall in use) fail2ban 1.1 leaves a jail it already ran with no action
        at all: it counts failures and "bans", and nothing is ever refused. Restarting the jail
        adds the action; its bans come back from fail2ban's database.

        Args:
            jails: The jails that must refuse what they ban.

        Returns:
            None when every jail has an action; otherwise a failed result naming the first that
            has none even after a restart.
        """
        for jail in jails:
            asked = ["fail2ban-client", "get", jail, "actions"]
            answer = self._run(asked, timeout=60)
            if FAIL2BAN_NO_ACTIONS not in answer.stdout:
                continue
            restart = ["fail2ban-client", "restart", jail]
            self._say(restart, self._run(restart, timeout=60))
            answer = self._run(asked, timeout=60)
            if FAIL2BAN_NO_ACTIONS in answer.stdout:
                return CommandResult(
                    argv=tuple(asked),
                    exit_code=1,
                    stderr=f"The {jail} jail {NO_ACTION}, so it would record failures and "
                    "refuse nobody.",
                )
        return None

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
        state.web_available = runner.exists("nginx")
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
        state.web_jails = all(state.jail(name) is not None for name in WEB_JAILS)
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

    def _never_banned(self, ignore: list[str] | None) -> list[str]:
        """
        The addresses no jail may ban: who is connected over SSH now and whoever the caller names.

        Args:
            ignore: More addresses (the console's client).

        Returns:
            The addresses, without loopback, without repeats.
        """
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
        return list(dict.fromkeys([*sessions, *extra]))

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
        addresses = self._never_banned(ignore)
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
            never_started = False
            if result.success and argv[:2] == ["systemctl", "enable"]:
                result = self._wait_until_answering()
                never_started = not result.success
            if result.success and argv == ["fail2ban-client", "reload"]:
                unenforced = self._ensure_ban_actions(["sshd"])
                result = result if unenforced is None else unenforced
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
                if never_started:
                    what = "fail2ban never answered after it was started"
                elif NO_ACTION in result.stderr:
                    what = "fail2ban left the sshd jail with no ban action"
                else:
                    what = f"fail2ban did not accept the sshd jail ({' '.join(argv)})"
                raise SecurityError(
                    f"{what}, so {FAIL2BAN_JAIL} was put back",
                    details="See why it did not start: journalctl -u fail2ban"
                    if never_started
                    else "",
                    output=(result.stderr or result.stdout).strip() or None,
                )
        audit("server.fail2ban", FAIL2BAN_JAIL, action="install", ignored=addresses, ports=ports)
        return {"jail_file": FAIL2BAN_JAIL, "ignored": addresses, "ports": ports}

    # -- the web server's jails ------------------------------------------------

    def enable_web(self, *, ignore: list[str] | None = None) -> dict[str, Any]:
        """
        Make fail2ban watch nginx as well: wrong passwords, scanners and clients over a limit.

        A ban is made from what nginx logs as the client's address. Behind a CDN that is the
        CDN's address unless the site reads the real one from a header, and banning a CDN's
        edge takes every site behind it down, so the ranges Cloudflare publishes and every
        proxy a site declares (``real_ip custom``) are never banned, besides who is connected
        over SSH now and the console's client.

        Args:
            ignore: More addresses never to ban (the console's client).

        Returns:
            The jail file, the jails and the addresses ignored.

        Raises:
            SecurityError: fail2ban is not running, nginx is not installed, a filter is missing,
                or fail2ban refused the jails (the file was put back).
        """
        state = self.state()
        if not state.running:
            raise SecurityError(
                "fail2ban is not running",
                details="Install it first: noust server security fail2ban install",
                output=state.error or None,
            )
        if not state.web_available:
            raise SecurityError("nginx is not installed here, so there is no log to watch")
        missing = [f for f in WEB_FILTERS if not self.probe.host.at(f).exists()]
        if missing:
            raise SecurityError(
                "This fail2ban has no filter for nginx",
                details="Missing: " + ", ".join(missing) + ". Update fail2ban.",
            )
        addresses = self._web_addresses(ignore)
        target = self.probe.host.at(FAIL2BAN_WEB_JAIL)
        previous = read_no_follow(target)
        self.probe.fs.write_text(target, render_web_jails(addresses), mode=0o644)
        self._reload_or_restore(
            target, previous, "web", ["fail2ban-client", "status", WEB_JAILS[-1]]
        )
        audit("server.fail2ban", FAIL2BAN_WEB_JAIL, action="web", ignored=addresses)
        return {"jail_file": FAIL2BAN_WEB_JAIL, "jails": list(WEB_JAILS), "ignored": addresses}

    def _web_addresses(self, ignore: list[str] | None) -> list[str]:
        """
        Returns:
            Who the nginx jails never ban: who is connected over SSH now, the addresses given
            (the console's client), and every CDN or proxy a site declares.
        """
        return list(dict.fromkeys([*self._never_banned(ignore), *self._proxy_ranges()]))

    def sync_web(self, *, ignore: list[str] | None = None) -> bool:
        """
        Bring the nginx jails' ignore list up to date with the proxies the sites declare.

        The list is written when the jails are turned on, so a proxy declared afterwards (the usual
        order: a CDN is put in front of a site that already exists) was banned at its first wrong
        password until somebody turned the jails on again. Whatever changes a site's trusted
        proxies calls this.

        Args:
            ignore: More addresses never to ban (the console's client).

        Returns:
            True when the file changed; False when the jails are not turned on or the list is
            what it was.

        Raises:
            SecurityError: fail2ban refused the new file (the old one was put back).
        """
        target = self.probe.host.at(FAIL2BAN_WEB_JAIL)
        previous = read_no_follow(target)
        if previous is None:
            return False
        text = render_web_jails(self._web_addresses(ignore))
        if text == previous:
            return False
        self.probe.fs.write_text(target, text, mode=0o644)
        ping = self._run(["fail2ban-client", "ping"])
        if ping.success and "pong" in ping.stdout:
            # When it is stopped the file is all there is to update: it is what it reads next.
            self._reload_or_restore(
                target, previous, "sync", ["fail2ban-client", "status", WEB_JAILS[-1]]
            )
        audit("server.fail2ban", FAIL2BAN_WEB_JAIL, action="web sync")
        return True

    def rescan_web(self) -> bool:
        """
        Make the nginx jails read the logs of the sites created since they started.

        fail2ban expands the wildcard of a ``logpath`` when a jail starts and never again, so a
        site created later was never read, and a scanner or a wrong password on it counted for
        nothing. Reloading is what reads the directory again; bans survive it.

        Returns:
            True when a log was not being read and fail2ban was asked to read it; False when the
            jails are not turned on, fail2ban does not answer, or every log is already read.

        Raises:
            SecurityError: fail2ban refused the reload.
        """
        if read_no_follow(self.probe.host.at(FAIL2BAN_WEB_JAIL)) is None:
            return False
        status = self._run(["fail2ban-client", "status", WEB_JAILS[0]])
        if not status.success:
            return False
        watched = set(parse_jail(WEB_JAILS[0], status.stdout).reads.split())
        directory = self.probe.host.at(WEB_LOG_DIR)
        present = {f"{WEB_LOG_DIR}/{path.name}" for path in directory.glob("*.error.log")}
        if present <= watched:
            return False
        argv = ["fail2ban-client", "reload"]
        result = self._run(argv, timeout=60)
        self._say(argv, result)
        if not result.success:
            raise SecurityError(
                "fail2ban did not read the logs of the new sites",
                output=(result.stderr or result.stdout).strip() or None,
            )
        return True

    def disable_web(self) -> dict[str, Any]:
        """
        Stop watching nginx: the jail file goes and fail2ban reads its jails again.

        Returns:
            The file that was removed (None when there was none).

        Raises:
            SecurityError: fail2ban refused to read its jails again; the file was put back.
        """
        target = self.probe.host.at(FAIL2BAN_WEB_JAIL)
        previous = read_no_follow(target)
        if previous is None:
            return {"jail_file": None}
        self.probe.fs.remove(target, missing_ok=True)
        self._reload_or_restore(target, previous, "web off", ["fail2ban-client", "ping"])
        audit("server.fail2ban", FAIL2BAN_WEB_JAIL, action="web off")
        return {"jail_file": FAIL2BAN_WEB_JAIL}

    def _proxy_ranges(self) -> list[str]:
        """
        Returns:
            The networks a CDN or a declared proxy speaks from: Cloudflare's published ranges and
            the proxies the sites' policies declare. Banning one of them takes sites down.
        """
        from noust.managers.site_policy import SitePolicyManager
        from noust.managers.trusted_proxies import current_ranges

        ranges = list(current_ranges().networks)
        manager = SitePolicyManager()
        for name in manager.records.domains_with_policy():
            ranges.extend(manager.get(name).trusted_proxies)
        return list(dict.fromkeys(ranges))

    def _reload_or_restore(
        self, target: Any, previous: str | None, action: str, last: list[str]
    ) -> None:
        """
        Make fail2ban read a jail file it was just given, putting the file back if it refuses.

        Args:
            target: The jail file.
            previous: What it held before, or None when there was none.
            action: What is being done, for the audit record.
            last: The command whose success proves the change took.

        Raises:
            SecurityError: A step failed; the file is as it was.
        """
        steps = [["fail2ban-client", "-t"], ["fail2ban-client", "reload"], last]
        for argv in steps:
            result = self._run(argv, timeout=60)
            self._say(argv, result)
            if result.success and argv == last:
                unenforced = self._ensure_ban_actions(WEB_JAILS)
                if unenforced is not None:
                    result, argv = unenforced, ["fail2ban-client", "get", "<jail>", "actions"]
            if result.success:
                continue
            if previous is None:
                self.probe.fs.remove(target, missing_ok=True)
            else:
                self.probe.fs.write_text(target, previous, mode=0o644)
            self._run(["fail2ban-client", "reload"])
            audit(
                "server.fail2ban",
                FAIL2BAN_WEB_JAIL,
                outcome="failure",
                action=action,
                reason=(result.stderr or result.stdout).strip(),
            )
            what = (
                "fail2ban left an nginx jail with no ban action"
                if NO_ACTION in result.stderr
                else f"fail2ban did not accept the nginx jails ({' '.join(argv)})"
            )
            raise SecurityError(
                f"{what}, so {FAIL2BAN_WEB_JAIL} was put back",
                output=(result.stderr or result.stdout).strip() or None,
            )


def keep_web_jails_true(ignore: Sequence[str] = (), *, runner: CommandRunner | None = None) -> None:
    """
    Update the nginx jails' ignore list after a change to the proxies the sites declare.

    The change that called this has already been made, so a fail2ban that refuses the new file
    is a warning (the old file is back), never an error of that change.

    Args:
        ignore: More addresses never to ban (the address the operator is changing it from).
        runner: Runs the commands; the process-wide one by default.
    """
    try:
        Fail2ban(SecurityProbe(runner=runner or get_runner())).sync_web(ignore=list(ignore))
    except SecurityError as exc:
        _log.warning("fail2ban's nginx jails were not brought up to date: %s", exc.message)


def watch_new_web_logs(runner: CommandRunner | None = None) -> None:
    """
    Make the nginx jails read the logs of a site the web server has just loaded.

    Called after every successful reload of nginx; it asks fail2ban nothing unless the jails are
    turned on.

    Args:
        runner: Runs the commands; the process-wide one by default.
    """
    try:
        Fail2ban(SecurityProbe(runner=runner or get_runner())).rescan_web()
    except SecurityError as exc:
        _log.warning("fail2ban did not read the logs of the new site: %s", exc.message)
