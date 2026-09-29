# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The node's side of enrollment: authorize a central, or stop trusting it.

``noust fleet authorize`` runs on the node, as root, by the node's own
operator - the central never gets a shell here. It:

1. installs the central's key in the SSH user's ``authorized_keys``, as::

       restrict,port-forwarding,permitopen="127.0.0.1:<console port>",command="/usr/bin/false" ssh-ed25519 AAAA... noust-central:<central>

   ``restrict`` turns off everything (pty, agent and X11 forwarding, user rc);
   ``port-forwarding`` turns back on local forwarding only, and
   ``permitopen`` narrows it to the console on loopback; the forced command
   makes any attempt to run something exit 1. The key can forward one port
   and do nothing else;
2. makes sure the console runs as a service, bound to loopback only;
3. creates a ``fleet`` token, ``fleet-<central>``;
4. prints the join code the central needs: this server's host key, the SSH
   user and port, the console port and the token.

``noust fleet deauthorize`` undoes 1 and 3.
"""

from __future__ import annotations

import errno
import os
import pwd
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust import __version__
from noust.core.exceptions import NodeError, SecurityError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandError, CommandRunner, get_runner
from noust.fleet.joincode import JoinCode
from noust.fleet.models import (
    PublicKey,
    local_node_name,
    parse_public_key,
    validate_central_name,
    validate_ssh_user,
)

if TYPE_CHECKING:
    from noust.web.auth import TokenManager

#: The server configuration read when ``sshd -T`` cannot run.
SSHD_CONFIG = Path("/etc/ssh/sshd_config")

#: This server's ed25519 host key, which the join code carries.
HOST_KEY_FILE = Path("/etc/ssh/ssh_host_ed25519_key.pub")

#: Comment of every key line this module writes: ``noust-central:<central>``.
KEY_COMMENT_PREFIX = "noust-central:"

#: What any command sent with the central's key runs instead.
FORCED_COMMAND = "/usr/bin/false"

#: How long ``sshd -T`` and ``chown`` may take.
SSHD_TIMEOUT = 15

#: Mode of the ``.ssh`` directory this module creates.
SSH_DIR_MODE = 0o700

#: Mode of ``authorized_keys``.
AUTHORIZED_KEYS_MODE = 0o600

#: The fleet token scope. Spelled here because this module must import
#: without the console's dependencies; ``noust.web.auth.FLEET_SCOPE``.
FLEET_SCOPE = "fleet"


@dataclass(frozen=True)
class SshdSettings:
    """
    The parts of sshd's configuration enrollment depends on.

    Attributes:
        port: The first port sshd listens on.
        allow_tcp_forwarding: ``yes``, ``all``, ``local``, ``remote`` or ``no``.
        disable_forwarding: Whether ``DisableForwarding`` turns every kind off.
        permit_root_login: ``PermitRootLogin``.
        authorized_keys_files: ``AuthorizedKeysFile``, unexpanded.
        source: ``sshd -T``, the config file, or ``defaults``.
    """

    port: int = 22
    allow_tcp_forwarding: str = "yes"
    disable_forwarding: bool = False
    permit_root_login: str = "prohibit-password"
    authorized_keys_files: tuple[str, ...] = (".ssh/authorized_keys", ".ssh/authorized_keys2")
    source: str = "defaults"


def _parse_sshd(lines: list[str], source: str) -> SshdSettings:
    """
    Read sshd settings from ``sshd -T`` output or ``sshd_config`` lines.

    The first value of each keyword wins, as in sshd; a ``Match`` block ends
    what applies to everyone.

    Args:
        lines: The lines.
        source: Where they came from.

    Returns:
        The settings, defaults for what is not named.
    """
    found: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keyword, _, value = line.partition(" ")
        if "=" in keyword:
            keyword, _, value = line.partition("=")
        keyword = keyword.strip().lower()
        if keyword == "match":
            break
        found.setdefault(keyword, value.strip())
    defaults = SshdSettings()
    port_text = found.get("port", "").split()[0] if found.get("port") else ""
    files = tuple(found["authorizedkeysfile"].split()) if found.get("authorizedkeysfile") else ()
    return SshdSettings(
        port=int(port_text)
        if port_text.isdigit() and 0 < int(port_text) < 65536
        else defaults.port,
        allow_tcp_forwarding=found.get("allowtcpforwarding", defaults.allow_tcp_forwarding).lower(),
        disable_forwarding=found.get("disableforwarding", "no").lower() == "yes",
        permit_root_login=found.get("permitrootlogin", defaults.permit_root_login).lower(),
        authorized_keys_files=files or defaults.authorized_keys_files,
        source=source,
    )


def read_sshd_settings(
    runner: CommandRunner | None = None, config_path: Path = SSHD_CONFIG
) -> SshdSettings:
    """
    Learn sshd's effective settings: ``sshd -T``, else its config file, else defaults.

    Args:
        runner: The runner ``sshd -T`` goes through.
        config_path: The file read when ``sshd -T`` cannot run.

    Returns:
        The settings.
    """
    result = (runner or get_runner()).run(["sshd", "-T"], timeout=SSHD_TIMEOUT)
    if result.success and result.stdout.strip():
        return _parse_sshd(result.stdout.splitlines(), "sshd -T")
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return SshdSettings()
    return _parse_sshd(text.splitlines(), str(config_path))


def sshd_blockers(settings: SshdSettings, user: str) -> list[str]:
    """
    List what in sshd's configuration would stop the central's tunnel.

    Args:
        settings: sshd's settings.
        user: The account the key is installed for.

    Returns:
        One actionable sentence per problem.
    """
    blockers: list[str] = []
    if settings.disable_forwarding or settings.allow_tcp_forwarding in ("no", "remote"):
        blockers.append(
            "sshd does not allow local TCP forwarding (AllowTcpForwarding or "
            "DisableForwarding), which is all the central's key may do. Set "
            "'AllowTcpForwarding local' (or yes) in /etc/ssh/sshd_config, reload sshd, "
            "and authorize again."
        )
    if user == "root" and settings.permit_root_login == "no":
        blockers.append(
            "sshd refuses root logins (PermitRootLogin no). Authorize for another account "
            "with --ssh-user, or allow key logins for root (PermitRootLogin prohibit-password)."
        )
    return blockers


def authorized_keys_path(settings: SshdSettings, account: pwd.struct_passwd) -> Path:
    """
    Find the ``authorized_keys`` file sshd reads for an account.

    Args:
        settings: sshd's settings.
        account: The account.

    Returns:
        The first ``AuthorizedKeysFile`` entry, expanded; relative entries
        are under the account's home.

    Raises:
        NodeError: When sshd reads no authorized_keys file at all.
    """
    for entry in settings.authorized_keys_files:
        if entry.lower() == "none":
            continue
        expanded = (
            entry.replace("%%", "\x00")
            .replace("%h", account.pw_dir)
            .replace("%u", account.pw_name)
            .replace("%U", str(account.pw_uid))
            .replace("\x00", "%")
        )
        path = Path(expanded)
        return path if path.is_absolute() else Path(account.pw_dir) / path
    raise NodeError(
        "sshd reads no authorized_keys file (AuthorizedKeysFile none)",
        details="Set AuthorizedKeysFile in /etc/ssh/sshd_config, reload sshd, and authorize again.",
    )


def key_comment(central: str) -> str:
    """
    The comment that marks a central's line in ``authorized_keys``.

    Args:
        central: The central's name.

    Returns:
        ``noust-central:<central>``.
    """
    return f"{KEY_COMMENT_PREFIX}{validate_central_name(central)}"


def authorized_key_line(central: str, key: PublicKey, console_port: int) -> str:
    """
    Spell the central's restricted ``authorized_keys`` line.

    Args:
        central: The central's name.
        key: The central's public key for this node.
        console_port: The console's loopback port, the only one it may reach.

    Returns:
        The line, without a newline.
    """
    options = (
        f'restrict,port-forwarding,permitopen="127.0.0.1:{int(console_port)}",'
        f'command="{FORCED_COMMAND}"'
    )
    return f"{options} {key.bare} {key_comment(central)}"


def _is_line_of(line: str, central: str) -> bool:
    """
    Report whether an ``authorized_keys`` line is a given central's.

    Args:
        line: The line.
        central: The central's name.

    Returns:
        True when its last field is the central's comment.
    """
    fields = line.split()
    return bool(fields) and fields[-1] == key_comment(central)


def _read_no_follow(path: Path) -> str | None:
    """
    Read a file that must not be a symlink.

    ``authorized_keys`` is in a directory its user owns; a link there to
    ``/etc/shadow`` would otherwise be rewritten by root.

    Args:
        path: The file.

    Returns:
        Its text, or None when it does not exist.

    Raises:
        NodeError: When it is a symlink or cannot be read.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise NodeError(
                f"Refusing to write through the symlink {path}",
                details="Replace it with a regular file, then authorize again.",
            ) from exc
        raise NodeError(f"Cannot read {path}", details=exc.strerror or str(exc)) from exc
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        return handle.read()


class AuthorizedKeys:
    """
    Edit one account's ``authorized_keys`` through the filesystem seam.

    Args:
        path: The file.
        owner: ``(uid, gid)`` the file and a created ``.ssh`` belong to, or
            None to leave them root's (a file outside the account's home).
        fs: The filesystem seam.
        runner: The runner ``chown`` goes through.
    """

    def __init__(
        self,
        path: Path,
        owner: tuple[int, int] | None,
        *,
        fs: FileSystem | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.path = path
        self.owner = owner
        self._fs = fs
        self._runner = runner

    @property
    def fs(self) -> FileSystem:
        """The filesystem seam."""
        return self._fs or get_fs()

    @property
    def runner(self) -> CommandRunner:
        """The runner chown goes through."""
        return self._runner or get_runner()

    def read(self) -> str | None:
        """
        Read the file.

        Returns:
            Its text, or None when it does not exist.

        Raises:
            NodeError: When it, or its directory, is a symlink.
        """
        if self.path.parent.is_symlink():
            raise NodeError(
                f"Refusing to write through the symlink {self.path.parent}",
                details="Replace it with a real directory, then authorize again.",
            )
        return _read_no_follow(self.path)

    def _chown(self, path: Path) -> None:
        """
        Give a path to the account, without following a link.

        Args:
            path: The file or directory.

        Raises:
            NodeError: When chown fails.
        """
        if self.owner is None or self.owner[0] == 0:
            return
        uid, gid = self.owner
        try:
            self.runner.run(
                ["chown", "-h", f"{uid}:{gid}", str(path)], timeout=SSHD_TIMEOUT, check=True
            )
        except CommandError as exc:
            raise NodeError(f"Could not give {path} to its user", details=exc.details) from exc

    def write(self, text: str | None) -> None:
        """
        Replace the file's content atomically, 0600, owned by the account.

        Args:
            text: The new content, or None to remove the file.
        """
        if text is None:
            self.fs.remove(self.path, missing_ok=True)
            return
        directory = self.path.parent
        if not directory.exists():
            self.fs.make_dir(directory, mode=SSH_DIR_MODE, parents=True)
            self._chown(directory)
        self.fs.write_text(self.path, text, mode=AUTHORIZED_KEYS_MODE)
        self._chown(self.path)

    def install(self, line: str, central: str) -> bool:
        """
        Make ``line`` the one line of ``central`` in the file.

        Any older line of the same central (another key, another port) is
        replaced; every other line is kept exactly as it was.

        Args:
            line: :func:`authorized_key_line`'s output.
            central: The central's name.

        Returns:
            True when the file changed; False when it already held exactly this.
        """
        existing = self.read()
        lines = existing.splitlines() if existing else []
        ours = [candidate for candidate in lines if _is_line_of(candidate, central)]
        if ours == [line]:
            return False
        kept = [candidate for candidate in lines if not _is_line_of(candidate, central)]
        self.write("\n".join([*kept, line]) + "\n")
        return True

    def remove(self, central: str) -> int:
        """
        Remove every line of a central.

        Args:
            central: The central's name.

        Returns:
            How many lines were removed.
        """
        existing = self.read()
        if not existing:
            return 0
        lines = existing.splitlines()
        kept = [candidate for candidate in lines if not _is_line_of(candidate, central)]
        removed = len(lines) - len(kept)
        if removed:
            self.write("\n".join(kept) + "\n" if kept else "")
        return removed


def _account(user: str, passwd: Callable[[str], pwd.struct_passwd]) -> pwd.struct_passwd:
    """
    Look up the SSH user.

    Args:
        user: The account name.
        passwd: ``pwd.getpwnam``.

    Returns:
        The account.

    Raises:
        NodeError: When there is no such account.
    """
    try:
        return passwd(validate_ssh_user(user))
    except KeyError as exc:
        raise NodeError(
            f"There is no account named {user} on this server",
            details="Pass --ssh-user with an account that can log in over SSH.",
        ) from exc


def _authorized_keys_for(
    account: pwd.struct_passwd,
    settings: SshdSettings,
    fs: FileSystem | None,
    runner: CommandRunner | None,
) -> AuthorizedKeys:
    """
    Build the editor of an account's ``authorized_keys``.

    Args:
        account: The account.
        settings: sshd's settings.
        fs: The filesystem seam.
        runner: The runner.

    Returns:
        The editor; the file belongs to the account when it is in its home.
    """
    path = authorized_keys_path(settings, account)
    home = Path(account.pw_dir)
    owned = home in path.parents
    return AuthorizedKeys(
        path, (account.pw_uid, account.pw_gid) if owned else None, fs=fs, runner=runner
    )


def token_base_name(central: str) -> str:
    """
    Name a central's token on this server.

    Args:
        central: The central's name.

    Returns:
        ``fleet-<central>``.
    """
    return f"fleet-{validate_central_name(central)}"


def _is_token_of(name: str, central: str) -> bool:
    """
    Report whether a token name is a given central's.

    Token names are unique for good (revoked ones included), so a central
    authorized again gets ``fleet-<central>.2``, ``.3``...

    Args:
        name: A token name.
        central: The central's name.

    Returns:
        True for ``fleet-<central>`` and ``fleet-<central>.<n>``.
    """
    return re.fullmatch(re.escape(token_base_name(central)) + r"(?:\.[0-9]+)?", name) is not None


def live_fleet_tokens(tokens: TokenManager, central: str) -> list[dict[str, Any]]:
    """
    List a central's unrevoked fleet tokens on this server.

    Args:
        tokens: The token manager.
        central: The central's name.

    Returns:
        Their records.
    """
    return [
        record
        for record in tokens.list_api_tokens()
        if record["scope"] == FLEET_SCOPE
        and record["revoked_at"] is None
        and _is_token_of(str(record["name"]), central)
    ]


def next_token_name(tokens: TokenManager, central: str) -> str:
    """
    Pick the name of a central's next token: unique across every token ever issued.

    Args:
        tokens: The token manager.
        central: The central's name.

    Returns:
        ``fleet-<central>``, or ``fleet-<central>.<n>`` when that was used.
    """
    taken = {str(record["name"]) for record in tokens.list_api_tokens()}
    base = token_base_name(central)
    if base not in taken:
        return base
    number = 2
    while f"{base}.{number}" in taken:
        number += 1
    return f"{base}.{number}"


@dataclass
class AuthorizeResult:
    """
    What ``noust fleet authorize`` did, and the code to paste into the central.

    Attributes:
        join_code: The code. It holds the token: shown once, like a token.
        central: The central's name.
        token_name: The token's name on this server.
        node_name: The name this server suggests for itself.
        ssh_user: The account the key was installed for.
        ssh_port: sshd's port.
        console_port: The console's loopback port.
        authorized_keys: The file the key line is in.
        key_fingerprint: The central key's fingerprint.
        host_key_fingerprint: This server's host key fingerprint.
        key_changed: False when the exact line was already there.
        replaced_tokens: Older tokens of this central, now revoked.
    """

    join_code: str
    central: str
    token_name: str
    node_name: str
    ssh_user: str
    ssh_port: int
    console_port: int
    authorized_keys: str
    key_fingerprint: str
    host_key_fingerprint: str
    key_changed: bool
    replaced_tokens: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the result for ``--json``.

        Returns:
            Every field.
        """
        return asdict(self)


def authorize(
    *,
    central_key: str,
    central: str,
    tokens: TokenManager,
    ensure_console: Callable[[], int],
    confirm_replace: Callable[[list[str]], bool],
    ssh_user: str = "root",
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
    host_key_file: Path = HOST_KEY_FILE,
    sshd_config: Path = SSHD_CONFIG,
    passwd: Callable[[str], pwd.struct_passwd] = pwd.getpwnam,
) -> AuthorizeResult:
    """
    Authorize a central on this server and build its join code.

    Every check runs before anything changes; the console is enabled next,
    then the key line, then the token. A token that cannot be issued puts
    ``authorized_keys`` back as it was.

    Args:
        central_key: The central's public key line for this node.
        central: The central's name.
        tokens: This server's token manager.
        ensure_console: Makes sure the console runs as a loopback service and
            returns its port; raises when it cannot.
        confirm_replace: Asked with the names of this central's live tokens,
            when there are any; False cancels before anything changes.
        ssh_user: The account the central logs in as.
        runner: The runner ``sshd -T`` and ``chown`` go through.
        fs: The filesystem seam.
        host_key_file: This server's ed25519 host key.
        sshd_config: Read when ``sshd -T`` cannot run.
        passwd: Account lookup.

    Returns:
        What was done and the join code.

    Raises:
        NodeError: When any input or precondition is wrong, the operator
            cancels, or the token cannot be issued.
    """
    central = validate_central_name(central)
    key = parse_public_key(central_key, what="central key")
    account = _account(ssh_user, passwd)
    try:
        host_key = parse_public_key(
            host_key_file.read_text(encoding="utf-8"), what="SSH host key of this server"
        )
    except FileNotFoundError as exc:
        raise NodeError(
            f"This server has no ed25519 SSH host key ({host_key_file})",
            details="Generate the missing host keys with 'ssh-keygen -A', reload sshd, "
            "and authorize again.",
        ) from exc
    settings = read_sshd_settings(runner, sshd_config)
    blockers = sshd_blockers(settings, account.pw_name)
    if blockers:
        raise NodeError("sshd would refuse the central's tunnel", details="\n".join(blockers))
    keys_file = _authorized_keys_for(account, settings, fs, runner)
    previous = keys_file.read()

    older = live_fleet_tokens(tokens, central)
    if older and not confirm_replace([str(record["name"]) for record in older]):
        raise NodeError("Cancelled: nothing was changed")

    console_port = ensure_console()
    changed = keys_file.install(authorized_key_line(central, key, console_port), central)
    try:
        issued = tokens.create_fleet_token(next_token_name(tokens, central))
    except SecurityError as exc:
        if changed:
            keys_file.write(previous)
        raise NodeError(
            "Could not create the fleet token", details=exc.details or exc.message
        ) from exc
    for record in older:
        tokens.revoke_api_token(int(record["id"]))

    code = JoinCode(
        ssh_host_key=host_key.bare,
        ssh_user=account.pw_name,
        ssh_port=settings.port,
        console_port=console_port,
        token=str(issued["token"]),
        noust_version=__version__,
        central_key_fp=key.fingerprint,
        node_name=local_node_name(),
        token_name=str(issued["name"]),
        central=central,
    )
    return AuthorizeResult(
        join_code=code.encode(),
        central=central,
        token_name=str(issued["name"]),
        node_name=local_node_name(),
        ssh_user=account.pw_name,
        ssh_port=settings.port,
        console_port=console_port,
        authorized_keys=str(keys_file.path),
        key_fingerprint=key.fingerprint,
        host_key_fingerprint=host_key.fingerprint,
        key_changed=changed,
        replaced_tokens=[str(record["name"]) for record in older],
    )


@dataclass
class DeauthorizeResult:
    """
    What ``noust fleet deauthorize`` removed.

    Attributes:
        central: The central's name.
        authorized_keys: The file edited.
        removed_keys: Key lines removed.
        revoked_tokens: Token names revoked.
    """

    central: str
    authorized_keys: str
    removed_keys: int
    revoked_tokens: list[str]

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the result for ``--json``.

        Returns:
            Every field.
        """
        return asdict(self)


def deauthorize(
    *,
    central: str,
    tokens: TokenManager,
    ssh_user: str = "root",
    runner: CommandRunner | None = None,
    fs: FileSystem | None = None,
    sshd_config: Path = SSHD_CONFIG,
    passwd: Callable[[str], pwd.struct_passwd] = pwd.getpwnam,
) -> DeauthorizeResult:
    """
    Stop trusting a central: remove its key lines and revoke its tokens.

    The central's tunnel stops at its next connection, and its requests stop
    authenticating at once.

    Args:
        central: The central's name.
        tokens: This server's token manager.
        ssh_user: The account the key was installed for.
        runner: The runner.
        fs: The filesystem seam.
        sshd_config: Read when ``sshd -T`` cannot run.
        passwd: Account lookup.

    Returns:
        What was removed.

    Raises:
        NodeError: When the name or the account is invalid.
    """
    central = validate_central_name(central)
    account = _account(ssh_user, passwd)
    keys_file = _authorized_keys_for(account, read_sshd_settings(runner, sshd_config), fs, runner)
    removed = keys_file.remove(central)
    revoked: list[str] = []
    for record in live_fleet_tokens(tokens, central):
        tokens.revoke_api_token(int(record["id"]))
        revoked.append(str(record["name"]))
    return DeauthorizeResult(
        central=central,
        authorized_keys=str(keys_file.path),
        removed_keys=removed,
        revoked_tokens=revoked,
    )
