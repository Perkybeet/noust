# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One look at the machine, shared by every security question asked in one pass.

The hardening checks, the proof that a key works, the anti-lockout guards and
the console's SSH tab all ask the same things: sshd's effective configuration,
who logged in with which key, what listens, who is connected now, which keys
each administrator has. :class:`SecurityProbe` asks each of them once per pass
and keeps the answer, so a check list costs one ``sshd -T`` per account and one
journal read instead of one per check, and every guard of one change reasons
about the same snapshot.

It is never cached across passes: a guard that decides whether a firewall
change would cut the operator off must look at the sessions open now.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import SecurityError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.host import HostPaths
from noust.managers.server.security_accounts import (
    TUNNEL_ACCOUNT,
    Account,
    HostAccounts,
    SudoRule,
    glob_matches,
)
from noust.managers.server.security_keys import (
    AuthorizedKey,
    expand_key_files,
    parse_file,
    strict_mode_problems,
)
from noust.managers.server.security_logins import LoginEvent, LoginHistory, LoginReader
from noust.managers.server.security_sockets import (
    Connection,
    Listener,
    read_established,
    read_listeners,
)
from noust.managers.server.security_sshd import (
    SshdEffective,
    SshdUnavailableError,
    read_effective,
    read_no_follow,
)

#: Processes that hold sshd's listening sockets: sshd itself, and systemd for
#: a socket-activated ``ssh.socket``.
SSHD_PROCESSES = frozenset({"sshd", "sshd-session"})


@dataclass(frozen=True)
class KeyFile:
    """
    One ``authorized_keys`` file of one account.

    Attributes:
        path: Its absolute path on the server.
        exists: Whether it exists.
        keys: The keys in it.
        problems: What would make ``StrictModes`` ignore it, with the fix.
        error: Why it could not be read, when it could not.
    """

    path: str
    exists: bool
    keys: tuple[AuthorizedKey, ...] = ()
    problems: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class AccountKeys:
    """
    An account that can become root, and every key that opens it.

    Attributes:
        account: The account.
        sudo: What sudoers grants it.
        sudo_usable: It can use sudo from a key session.
        password: ``usable``, ``locked``, ``empty`` or ``unknown``.
        login_allowed: sshd lets it open a session with a key.
        login_refusal: Why not, when it does not.
        files: Its key files, in the order sshd reads them.
    """

    account: Account
    sudo: SudoRule
    sudo_usable: bool
    password: str
    login_allowed: bool
    login_refusal: str
    files: tuple[KeyFile, ...]

    def keys(self, kind: str | None = None) -> list[AuthorizedKey]:
        """
        Every key of the account, optionally of one kind.

        Args:
            kind: ``operator``, ``central``, ``restricted``, ``cloud_disabled``.

        Returns:
            The keys, in file order.
        """
        return [key for file in self.files for key in file.keys if kind in (None, key.kind)]

    def usable_operator_keys(self) -> list[AuthorizedKey]:
        """
        The operator keys sshd would accept: in a file StrictModes does not reject.

        Returns:
            The keys.
        """
        return [
            key
            for file in self.files
            if not file.problems
            for key in file.keys
            if key.kind == "operator"
        ]


@dataclass(frozen=True)
class LiveSession:
    """
    An SSH connection open now, and the login that opened it when it is on record.

    Attributes:
        connection: The TCP connection.
        login: The matching ``Accepted`` line, or None.
    """

    connection: Connection
    login: LoginEvent | None


class SecurityProbe:
    """
    Ask the machine each security question once, and remember the answer for this pass.

    Args:
        runner: The command runner; the process-wide one by default.
        fs: The filesystem seam; the process-wide one by default.
        host: Where the system files are.
        clock: The current time, in epoch seconds.
    """

    def __init__(
        self,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()
        self.clock = clock
        self.accounts = HostAccounts(self.host)
        self._effective: dict[str, SshdEffective | SshdUnavailableError] = {}
        self._logins: LoginHistory | None = None
        self._listeners: tuple[list[Listener], str] | None = None
        self._established: list[Connection] | None = None
        self._account_keys: list[AccountKeys] | None = None

    @property
    def runner(self) -> CommandRunner:
        """The command runner."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem seam."""
        return self._fs or get_fs()

    def now(self) -> float:
        """The current time, in epoch seconds."""
        return float(self.clock())

    # sshd -------------------------------------------------------------------

    def effective(self, user: str = "root") -> SshdEffective:
        """
        sshd's effective configuration for a remote login as one account.

        Args:
            user: The account.

        Returns:
            The configuration.

        Raises:
            SshdUnavailableError: sshd could not print it; the same error is
                raised again for every later question in this pass.
        """
        cached = self._effective.get(user)
        if cached is None:
            try:
                cached = read_effective(self.runner, user=user)
            except SshdUnavailableError as exc:
                cached = exc
            self._effective[user] = cached
        if isinstance(cached, SshdUnavailableError):
            raise cached
        return cached

    def ssh_ports(self) -> set[int]:
        """
        Every port SSH is reached on: sshd's ``Port`` values and what sshd listens on.

        Returns:
            The ports; 22 when nothing could be read, since that is where sshd
            listens unless told otherwise.
        """
        ports: set[int] = set()
        try:
            ports.update(self.effective().ports)
        except SshdUnavailableError:
            pass
        listeners, _error = self.listeners()
        ports.update(
            listener.port
            for listener in listeners
            if listener.proto == "tcp" and listener.process in SSHD_PROCESSES
        )
        return ports or {22}

    # Logins and sessions ------------------------------------------------------

    def logins(self) -> LoginHistory:
        """
        The SSH logins of the evidence window.

        Returns:
            The history.
        """
        if self._logins is None:
            self._logins = LoginReader(self.runner, self.host, self.clock).read()
        return self._logins

    def logins_since(self, moment: float) -> LoginHistory:
        """
        The SSH logins after a moment, read now (never from this pass's memory).

        Args:
            moment: Epoch seconds.

        Returns:
            The history.
        """
        return LoginReader(self.runner, self.host, self.clock).read(since=moment)

    def listeners(self) -> tuple[list[Listener], str]:
        """
        What listens on this server.

        Returns:
            The sockets, and why they could not be read (empty when they could).
        """
        if self._listeners is None:
            self._listeners = read_listeners(self.runner)
        return self._listeners

    def sessions(self) -> list[LiveSession]:
        """
        The SSH connections open now, each with the login that opened it.

        Returns:
            The sessions.
        """
        if self._established is None:
            self._established = read_established(self.runner)
        ports = self.ssh_ports()
        logins = self.logins()
        found: list[LiveSession] = []
        for connection in self._established:
            if connection.local_port not in ports:
                continue
            login = next(
                (
                    event
                    for event in reversed(logins.events)
                    if event.source == connection.peer_address
                    and event.port == connection.peer_port
                ),
                None,
            )
            found.append(LiveSession(connection, login))
        return found

    # Keys ---------------------------------------------------------------------

    def _login_refusal(self, account: Account, effective: SshdEffective) -> str:
        """
        Say why sshd would refuse a key login for an account, if it would.

        Args:
            account: The account.
            effective: sshd's configuration for it.

        Returns:
            The reason, or empty when sshd lets it in with a key.
        """
        if effective.first("pubkeyauthentication", "yes") != "yes":
            return "PubkeyAuthentication is off"
        if account.uid == 0 and effective.first("permitrootlogin") in (
            "no",
            "forced-commands-only",
        ):
            return f"PermitRootLogin is {effective.first('permitrootlogin')}"
        groups = self.accounts.groups_of(account)
        deny_users = tuple(" ".join(effective.all("denyusers")).split())
        allow_users = tuple(" ".join(effective.all("allowusers")).split())
        deny_groups = tuple(" ".join(effective.all("denygroups")).split())
        allow_groups = tuple(" ".join(effective.all("allowgroups")).split())
        if deny_users and glob_matches(account.name, deny_users):
            return "DenyUsers names it"
        if allow_users and not glob_matches(account.name, allow_users):
            return "AllowUsers does not name it"
        if deny_groups and any(glob_matches(group, deny_groups) for group in groups):
            return "DenyGroups names one of its groups"
        if allow_groups and not any(glob_matches(group, allow_groups) for group in groups):
            return "AllowGroups names none of its groups"
        if (
            self.accounts.password_state(account.name) == "locked"
            and effective.first("usepam", "no") != "yes"
            and (self.accounts.shadow() or {}).get(account.name, "").startswith("!")
        ):
            return "the account is locked and UsePAM is off, so sshd refuses even its keys"
        if not self.accounts.can_log_in(account):
            return f"its shell {account.shell} does not start a session"
        return ""

    def _key_files(self, account: Account, effective: SshdEffective) -> tuple[KeyFile, ...]:
        """
        Read an account's key files.

        Args:
            account: The account.
            effective: sshd's configuration for it.

        Returns:
            The files sshd reads for it, parsed and checked.
        """
        entries = effective.all("authorizedkeysfile") or (
            ".ssh/authorized_keys .ssh/authorized_keys2",
        )
        strict = effective.first("strictmodes", "yes") == "yes"
        files: list[KeyFile] = []
        for path in expand_key_files(entries, account):
            local = self.host.at(path)
            try:
                text = read_no_follow(local)
            except SecurityError as exc:
                # A link, or unreadable: shown with the reason, never followed.
                files.append(KeyFile(path, True, error=exc.message))
                continue
            if text is None:
                files.append(KeyFile(path, False))
                continue
            problems = strict_mode_problems(self.host, account, path) if strict else []
            files.append(KeyFile(path, True, tuple(parse_file(text)), tuple(problems)))
        return tuple(files)

    def account_keys(self) -> list[AccountKeys]:
        """
        Every account that can become root, with its keys.

        Returns:
            root first, then the administrators; an account whose sshd
            configuration cannot be read is listed with no files.
        """
        if self._account_keys is None:
            found: list[AccountKeys] = []
            for account in self.accounts.admins():
                try:
                    effective = self.effective(account.name)
                except SshdUnavailableError as exc:
                    found.append(
                        AccountKeys(
                            account=account,
                            sudo=self.accounts.sudo_rule(account),
                            sudo_usable=self.accounts.sudo_usable(account),
                            password=self.accounts.password_state(account.name),
                            login_allowed=False,
                            login_refusal=exc.message,
                            files=(),
                        )
                    )
                    continue
                refusal = self._login_refusal(account, effective)
                found.append(
                    AccountKeys(
                        account=account,
                        sudo=self.accounts.sudo_rule(account),
                        sudo_usable=self.accounts.sudo_usable(account),
                        password=self.accounts.password_state(account.name),
                        login_allowed=not refusal,
                        login_refusal=refusal,
                        files=self._key_files(account, effective),
                    )
                )
            self._account_keys = found
        return self._account_keys

    def keys_of(self, user: str) -> AccountKeys | None:
        """
        One administrator's keys.

        Args:
            user: The account.

        Returns:
            Its entry, or None when it is not an account that can become root.
        """
        return next((entry for entry in self.account_keys() if entry.account.name == user), None)

    def central_fingerprints(self) -> set[str]:
        """
        Fingerprints of every central's tunnel key found here.

        Returns:
            The fingerprints: in any administrator's file, and in the tunnel
            account's (``/etc/ssh/noust/noust-tunnel.keys`` or its home).
        """
        found = {key.fingerprint for entry in self.account_keys() for key in entry.keys("central")}
        tunnel = self.accounts.account(TUNNEL_ACCOUNT)
        if tunnel is not None:
            try:
                effective = self.effective(TUNNEL_ACCOUNT)
            except SshdUnavailableError:
                return found
            for file in self._key_files(tunnel, effective):
                found.update(key.fingerprint for key in file.keys)
        return found

    def invalidate(self) -> None:
        """Forget every answer, so the next question asks the machine again."""
        from noust.managers.server.security_logins import forget_shared
        from noust.managers.server.security_proof import forget_proofs

        self._effective.clear()
        self._logins = None
        forget_shared()
        forget_proofs()
        self._listeners = None
        self._established = None
        self._account_keys = None
        self.accounts = HostAccounts(self.host)


def display_path(host: HostPaths, local: Path) -> str:
    """
    Show a host path the way it is on the server.

    Args:
        host: Where the files are.
        local: A path below the host's root.

    Returns:
        The absolute path on the server.
    """
    return "/" + str(local.relative_to(host.root)).lstrip("/")
