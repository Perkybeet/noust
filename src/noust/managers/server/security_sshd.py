# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
sshd as it really runs: its effective configuration, and Noust's one file in it.

Three facts about sshd decide how this module works, and each one has locked
somebody out of a server:

- **The effective value is not the one in sshd_config.** "For each keyword, the
  first obtained value will be used", and Debian 12, Ubuntu 22.04+ and RHEL 9
  include ``sshd_config.d/*.conf`` at the top of the main file. A cloud image's
  ``50-cloud-init.conf`` with ``PasswordAuthentication yes`` therefore beats
  anything written further down. So the configuration is only ever read with
  ``sshd -T``, which prints what sshd would actually use.
- **Match blocks change it per connection.** ``sshd -T`` alone prints the
  global section; ``-C user=...,host=...,addr=...`` evaluates the ``Match``
  blocks for one connection. Every read here passes ``-C``: by default for
  root connecting from :data:`REMOTE_ADDR`, an address on the internet that
  matches no LAN or loopback exception, which is exactly the stranger a
  hardening check is about.
- **The first file wins.** Noust writes only :data:`DROPIN`,
  ``00-noust.conf``, which sorts before every other drop-in, and never edits
  the main file. After writing it, the caller verifies with ``sshd -T`` that
  the value changed; when it did not (the main file sets the keyword before
  its ``Include``, or has no ``Include`` at all), :func:`winning_source` says
  which line wins.
"""

from __future__ import annotations

import errno
import fnmatch
import glob as globmodule
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from noust.core import paths
from noust.core.exceptions import SecurityError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.managers.server.host import PROBE_TIMEOUT, HostPaths, read_text

#: The main configuration file.
SSHD_CONFIG = "/etc/ssh/sshd_config"

#: The only file Noust writes in sshd's configuration. ``00-`` sorts first.
DROPIN = "/etc/ssh/sshd_config.d/00-noust.conf"

#: Mode of :data:`DROPIN`: sshd reads it as root; nobody else needs to.
DROPIN_MODE = 0o600

#: The connection every read describes unless told otherwise: an address from
#: RFC 5737's documentation range, so no ``Match Address`` for a LAN or for
#: loopback applies to it.
REMOTE_ADDR = "203.0.113.1"

#: Environment of every probe: sshd's messages untranslated.
PROBE_ENV = {"LC_ALL": "C", "TERM": "dumb"}

#: How long a reload of sshd may take.
RELOAD_TIMEOUT = 20

#: The spelling sshd_config uses for each keyword Noust writes. ``sshd -T``
#: prints keywords in lower case; the file is written the way people read it.
DIRECTIVES: dict[str, str] = {
    "permitrootlogin": "PermitRootLogin",
    "passwordauthentication": "PasswordAuthentication",
    "kbdinteractiveauthentication": "KbdInteractiveAuthentication",
    "challengeresponseauthentication": "ChallengeResponseAuthentication",
    "permitemptypasswords": "PermitEmptyPasswords",
    "maxauthtries": "MaxAuthTries",
    "logingracetime": "LoginGraceTime",
    "x11forwarding": "X11Forwarding",
    "clientaliveinterval": "ClientAliveInterval",
    "clientalivecountmax": "ClientAliveCountMax",
    "loglevel": "LogLevel",
}

#: The first line of :data:`DROPIN`, which is also how Noust recognises it.
DROPIN_HEADER = (
    f"# {paths.UNIT_MARKER}. Noust's sshd settings: this file sorts first, and sshd "
    "keeps the first value it reads.\n"
    "# Change them with 'noust server security ssh' or the console, not here.\n"
)


class SshdUnavailableError(SecurityError):
    """``sshd -T`` could not report the effective configuration."""


@dataclass(frozen=True)
class SshdEffective:
    """
    sshd's effective configuration for one connection, as ``sshd -T`` printed it.

    Attributes:
        values: Keyword (lower case) to every value printed for it, in order;
            ``port``, ``hostkey`` and ``listenaddress`` repeat.
        spec: The ``-C`` connection it was evaluated for.
        text: The output, verbatim, for evidence.
    """

    values: dict[str, tuple[str, ...]]
    spec: str
    text: str = ""

    def first(self, keyword: str, default: str = "") -> str:
        """
        The value sshd uses for a keyword.

        Args:
            keyword: Lower-case keyword.
            default: What to answer when sshd printed nothing for it.

        Returns:
            The first value, as sshd printed it (lower case for yes/no).
        """
        found = self.values.get(keyword.lower())
        return found[0] if found else default

    def all(self, keyword: str) -> tuple[str, ...]:
        """
        Every value printed for a keyword.

        Args:
            keyword: Lower-case keyword.

        Returns:
            The values, in order.
        """
        return self.values.get(keyword.lower(), ())

    def line(self, keyword: str) -> str:
        """
        The line of ``sshd -T`` that shows a keyword, for evidence.

        Args:
            keyword: Lower-case keyword.

        Returns:
            ``keyword value``, or ``keyword (not printed)``.
        """
        value = self.first(keyword)
        return f"{keyword} {value}" if value else f"{keyword} (not printed)"

    @property
    def ports(self) -> list[int]:
        """Every port sshd listens on."""
        return sorted({int(value) for value in self.all("port") if value.isdigit()})

    @property
    def kbd_keyword(self) -> str:
        """
        The keyword for keyboard-interactive login in this OpenSSH.

        ``ChallengeResponseAuthentication`` was renamed in 8.7; the one
        ``sshd -T`` prints is the one this sshd understands.
        """
        if "kbdinteractiveauthentication" in self.values:
            return "kbdinteractiveauthentication"
        if "challengeresponseauthentication" in self.values:
            return "challengeresponseauthentication"
        return "kbdinteractiveauthentication"

    @property
    def passwords_accepted(self) -> bool:
        """Whether a password can open a session: directly, or through PAM's keyboard-interactive."""
        if self.first("passwordauthentication", "yes") == "yes":
            return True
        return self.first(self.kbd_keyword, "yes") == "yes" and self.first("usepam", "no") == "yes"


def parse_effective(text: str, spec: str) -> SshdEffective:
    """
    Read ``sshd -T`` output.

    Args:
        text: The output: one ``keyword value`` per line.
        spec: The ``-C`` connection it describes.

    Returns:
        The configuration.
    """
    values: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        keyword, _, value = line.partition(" ")
        values.setdefault(keyword.lower(), []).append(value.strip())
    return SshdEffective(
        values={key: tuple(found) for key, found in values.items()}, spec=spec, text=text
    )


def connection_spec(user: str = "root", addr: str = REMOTE_ADDR) -> str:
    """
    Build the ``-C`` argument for one connection.

    Args:
        user: The account logging in.
        addr: Where from.

    Returns:
        ``user=...,host=...,addr=...``; ``host`` is the address, as sshd has it
        before (or without) reverse DNS.
    """
    return f"user={user},host={addr},addr={addr}"


def read_effective(
    runner: CommandRunner | None = None, *, user: str = "root", addr: str = REMOTE_ADDR
) -> SshdEffective:
    """
    Ask sshd for its effective configuration for one connection.

    Args:
        runner: The command runner.
        user: The account the connection logs in as.
        addr: The address it comes from.

    Returns:
        The configuration.

    Raises:
        SshdUnavailableError: sshd is not installed, its configuration is
            invalid, or this is not root. The output travels verbatim.
    """
    spec = connection_spec(user, addr)
    result = (runner or get_runner()).run(
        ["sshd", "-T", "-C", spec], timeout=PROBE_TIMEOUT, env=PROBE_ENV
    )
    if not result.success or not result.stdout.strip():
        raise SshdUnavailableError(
            "sshd did not report its effective configuration",
            details="Run 'sshd -T' as root to see why; an sshd that cannot print its "
            "configuration would refuse to reload it too.",
            output=(result.stderr or result.stdout).strip() or None,
        )
    return parse_effective(result.stdout, spec)


def test_configuration(runner: CommandRunner | None = None) -> CommandResult:
    """
    Run ``sshd -t``: parse every file sshd would read, without applying anything.

    Args:
        runner: The command runner.

    Returns:
        The result; exit 0 when the configuration is valid.
    """
    return (runner or get_runner()).run(["sshd", "-t"], timeout=PROBE_TIMEOUT, env=PROBE_ENV)


# Noust's drop-in -----------------------------------------------------------


def parse_dropin(text: str | None) -> dict[str, str]:
    """
    Read the settings in Noust's drop-in.

    Args:
        text: Its content, or None when it does not exist.

    Returns:
        Lower-case keyword to value, in file order.
    """
    settings: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keyword, _, value = line.partition(" ")
        settings.setdefault(keyword.lower(), value.strip())
    return settings


def render_dropin(settings: dict[str, str]) -> str:
    """
    Write Noust's drop-in.

    Args:
        settings: Lower-case keyword to value; every keyword must be one of
            :data:`DIRECTIVES`, which is how a value from a request can never
            become a directive sshd was not meant to get.

    Returns:
        The file's content.

    Raises:
        SecurityError: A keyword is not one Noust writes, or a value carries
            anything but a plain word.
    """
    lines = [DROPIN_HEADER]
    for keyword, value in settings.items():
        name = DIRECTIVES.get(keyword)
        if name is None:
            raise SecurityError(f"Noust does not write the sshd keyword {keyword!r}")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,32}", value):
            raise SecurityError(f"Refusing the value {value!r} for {name}")
        lines.append(f"{name} {value}\n")
    return "".join(lines)


def read_no_follow(path: Path) -> str | None:
    """
    Read a file that must not be a symbolic link.

    Args:
        path: The file.

    Returns:
        Its text, or None when it does not exist.

    Raises:
        SecurityError: It is a link, or cannot be read.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SecurityError(
                f"Refusing to use {path}: it is a symbolic link",
                details="Noust writes this file itself; remove the link and try again.",
            ) from exc
        raise SecurityError(f"Cannot read {path}", details=exc.strerror or str(exc)) from exc
    with os.fdopen(descriptor, encoding="utf-8", errors="replace") as handle:
        return handle.read()


@dataclass
class SshdDropIn:
    """
    Noust's file in ``sshd_config.d``: read, merge, write.

    Attributes:
        host: Where the files are.
        fs: The filesystem seam every write goes through.
    """

    host: HostPaths = field(default_factory=HostPaths)
    fs: FileSystem | None = None

    @property
    def path(self) -> Path:
        """The drop-in's path on this host."""
        return self.host.at(DROPIN)

    def read(self) -> str | None:
        """
        The drop-in's content.

        Returns:
            Its text, or None when there is none.

        Raises:
            SecurityError: It is a link, or a file Noust did not write.
        """
        text = read_no_follow(self.path)
        if text is not None and text.strip() and not paths.carries_unit_marker(text):
            raise SecurityError(
                f"{DROPIN} exists and Noust did not write it",
                details="Noust keeps its sshd settings in that file only. Move what it holds "
                "to another file in /etc/ssh/sshd_config.d/, then try again.",
            )
        return text

    def merged(self, changes: dict[str, str]) -> tuple[str | None, str]:
        """
        The drop-in before and after a change.

        Args:
            changes: Lower-case keyword to the value to set.

        Returns:
            The current content (None when absent) and the new one.
        """
        previous = self.read()
        settings = parse_dropin(previous)
        settings.update(changes)
        return previous, render_dropin(settings)

    def write(self, text: str | None) -> None:
        """
        Replace the drop-in, or remove it.

        Args:
            text: The new content, or None to remove the file.
        """
        fs = self.fs or get_fs()
        if text is None:
            fs.remove(self.path, missing_ok=True)
            return
        fs.write_text(self.path, text, mode=DROPIN_MODE)


def include_present(host: HostPaths | None = None) -> bool:
    """
    Report whether the main configuration includes ``sshd_config.d``.

    Args:
        host: Where the files are.

    Returns:
        True when an ``Include`` line in ``sshd_config`` would read
        :data:`DROPIN`.
    """
    paths_ = host or HostPaths()
    for keyword, value, _where in _directives(paths_, paths_.at(SSHD_CONFIG), follow=False):
        if keyword == "include" and any(
            _include_matches(pattern, DROPIN) for pattern in value.split()
        ):
            return True
    return False


def _include_matches(pattern: str, target: str) -> bool:
    """
    Report whether an ``Include`` pattern reads a file.

    Args:
        pattern: The pattern, relative to ``/etc/ssh`` when not absolute.
        target: An absolute path.

    Returns:
        True when the pattern matches it.
    """
    absolute = pattern if pattern.startswith("/") else f"/etc/ssh/{pattern}"
    return fnmatch.fnmatchcase(target, absolute)


def _directives(
    host: HostPaths, path: Path, *, follow: bool = True, depth: int = 0
) -> list[tuple[str, str, str]]:
    """
    Every directive sshd reads from a file, in order, stopping at its first Match.

    Args:
        host: Where the files are.
        path: The file.
        follow: Expand ``Include`` lines into the directives of what they read.
        depth: How deep in includes this is; sshd stops at 16, so does this.

    Returns:
        ``(keyword, value, file:line)`` for each directive, keyword lower-cased.
    """
    text = read_text(path)
    if text is None or depth > 16:
        return []
    shown = "/" + str(path.relative_to(host.root)).lstrip("/")
    found: list[tuple[str, str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"[\s=]+", line, maxsplit=1)
        keyword = parts[0].lower()
        value = parts[1] if len(parts) > 1 else ""
        if keyword == "match":
            break
        found.append((keyword, value.strip(), f"{shown}:{number}"))
        if follow and keyword == "include":
            for pattern in value.split():
                absolute = pattern if pattern.startswith("/") else f"/etc/ssh/{pattern}"
                for match in sorted(globmodule.glob(str(host.at(absolute)))):
                    found.extend(_directives(host, Path(match), follow=True, depth=depth + 1))
    return found


def winning_source(keyword: str, host: HostPaths | None = None) -> str | None:
    """
    Find the line whose value sshd uses for a keyword.

    Args:
        keyword: The keyword, any case.
        host: Where the files are.

    Returns:
        ``file:line: text`` of the first occurrence outside a Match block, in
        the order sshd reads, or None when no file sets it (the default wins).
    """
    paths_ = host or HostPaths()
    wanted = keyword.lower()
    for found, value, where in _directives(paths_, paths_.at(SSHD_CONFIG)):
        if found == wanted:
            return f"{where}: {DIRECTIVES.get(found, found)} {value}"
    return None


# The unit ------------------------------------------------------------------


@dataclass(frozen=True)
class SshdUnit:
    """
    How systemd runs sshd here.

    Attributes:
        service: The service unit (``ssh.service`` on Debian and Ubuntu,
            ``sshd.service`` elsewhere), or None when neither is loaded.
        active: The service is running.
        socket: ``ssh.socket`` when it is loaded (Ubuntu 22.10 and later start
            sshd on the first connection), else None.
        socket_active: The socket is listening.
    """

    service: str | None
    active: bool
    socket: str | None = None
    socket_active: bool = False


def read_unit(runner: CommandRunner | None = None) -> SshdUnit:
    """
    Find sshd's unit.

    Args:
        runner: The command runner.

    Returns:
        What systemd reports for the ssh and sshd services and the ssh socket.
    """
    result = (runner or get_runner()).run(
        [
            "systemctl",
            "show",
            "ssh.service",
            "sshd.service",
            "ssh.socket",
            "--property=Id,LoadState,ActiveState",
        ],
        timeout=PROBE_TIMEOUT,
        env=PROBE_ENV,
    )
    units: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            if current:
                units.append(current)
            current = {}
            continue
        key, _, value = line.partition("=")
        current[key] = value
    if current:
        units.append(current)
    service: str | None = None
    active = False
    socket: str | None = None
    socket_active = False
    for unit in units:
        if unit.get("LoadState") != "loaded":
            continue
        name = unit.get("Id", "")
        if name.endswith(".socket") and socket is None:
            socket, socket_active = name, unit.get("ActiveState") == "active"
        elif name.endswith(".service") and service is None:
            service, active = name, unit.get("ActiveState") == "active"
    return SshdUnit(service=service, active=active, socket=socket, socket_active=socket_active)


def reload_argv(unit: SshdUnit) -> list[str] | None:
    """
    The command that makes the running sshd read its configuration again.

    ``reload`` and never ``restart``: a reload keeps every open session, which
    is what lets an operator whose new configuration is wrong still fix it
    from the session they are in.

    Args:
        unit: sshd's unit.

    Returns:
        The argv, or None when sshd is started per connection by its socket and
        is not running now: the next connection reads the new configuration.

    Raises:
        SecurityError: sshd is not running under systemd at all.
    """
    if unit.service and unit.active:
        return ["systemctl", "reload", unit.service]
    if unit.socket and unit.socket_active:
        return None
    raise SecurityError(
        "sshd is not running under systemd, so Noust cannot reload it",
        details="Start it (systemctl start ssh, or sshd on RHEL and SUSE) and try again.",
    )


def reload(
    unit: SshdUnit,
    runner: CommandRunner | None = None,
    on_output: Callable[[str], None] | None = None,
) -> CommandResult | None:
    """
    Reload sshd.

    Args:
        unit: sshd's unit.
        runner: The command runner.
        on_output: Receives the command and its output, verbatim.

    Returns:
        The result, or None when nothing had to be reloaded.
    """
    argv = reload_argv(unit)
    if argv is None:
        if on_output:
            on_output(
                f"{unit.socket} starts sshd for the next connection, which reads the new "
                "configuration; nothing to reload."
            )
        return None
    result = (runner or get_runner()).run(argv, timeout=RELOAD_TIMEOUT, env=PROBE_ENV)
    if on_output:
        on_output("$ " + " ".join(argv))
        for line in (result.stdout + result.stderr).splitlines():
            on_output(line)
    return result
