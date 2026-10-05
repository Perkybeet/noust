# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The accounts that can reach root on this server, as sshd and sudo see them.

Everything here is read in Python from the files themselves - ``passwd``,
``group``, ``shadow``, ``login.defs``, ``shells`` and the sudoers files - under
:class:`~noust.managers.server.host.HostPaths`, so a test points the whole
module at a directory of its own. No process is run: ``getent`` would give the
same answer for local accounts and cost a fork per question, and the module
``spwd`` that used to read ``shadow`` is gone from Python 3.13.

Two questions matter to the SSH fixes, and both are answered conservatively,
because a wrong "yes" is how an operator is locked out and a wrong "no" only
turns an automatic fix into a guided one:

- **Can this account become root?** A sudoers rule names it, one of its
  groups or ``ALL`` (read by :mod:`~noust.managers.server.security_sudoers`,
  the one reader of those files). Membership of ``sudo`` or ``wheel`` alone is not enough:
  openSUSE ships the ``%wheel`` line commented out, and a group nobody granted
  anything is a group, not a way to root.
- **Can it actually use sudo from an SSH key session?** Only when the rule is
  ``NOPASSWD`` or sudo has a password to ask for (the account's own, or root's
  under ``Defaults rootpw``/``targetpw``). An account whose only credential is a
  key, with a rule that wants a password, can log in and still never become
  root - the case that makes ``PermitRootLogin no`` a trap.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from noust.managers.server.host import HostPaths, read_text
from noust.managers.server.security_sudoers import Identity, Sudoers, read_sudoers

#: Groups the distributions use for administrators: ``sudo`` (Debian, Ubuntu),
#: ``wheel`` (RHEL, Fedora, SUSE) and ``admin`` (Ubuntu before 12.04, still
#: present on upgraded machines). Membership puts an account on the list whose
#: keys are shown; only a sudoers rule makes it able to become root.
SUDO_GROUPS = ("sudo", "wheel", "admin")

#: ``UID_MIN`` and ``UID_MAX`` when ``/etc/login.defs`` does not say.
DEFAULT_UID_RANGE = (1000, 60000)

#: Shells that exist only to refuse a login.
NO_LOGIN_SHELLS = frozenset({"nologin", "false", "sync", "halt", "shutdown"})

#: The account a central's tunnel logs in as since 3.1 (``fleet/authorize.py``).
TUNNEL_ACCOUNT = "noust-tunnel"


@dataclass(frozen=True)
class Account:
    """
    One line of ``/etc/passwd``.

    Attributes:
        name: The login name.
        uid: Its user id.
        gid: Its primary group id.
        home: Its home directory, as recorded.
        shell: Its login shell, as recorded.
    """

    name: str
    uid: int
    gid: int
    home: str
    shell: str


@dataclass(frozen=True)
class SudoRule:
    """
    What sudoers says about one account.

    Attributes:
        granted: A rule lets the account run commands as root.
        nopasswd: That rule does not ask for the account's password.
        sources: Where it was found: a group name (``group sudo``) or a file
            and line (``/etc/sudoers.d/90-cloud-init-users:2``).
    """

    granted: bool = False
    nopasswd: bool = False
    sources: tuple[str, ...] = ()


@dataclass
class HostAccounts:
    """
    Read the account files of one host, once.

    Attributes:
        host: Where the files are.
    """

    host: HostPaths = field(default_factory=HostPaths)
    _passwd: list[Account] | None = field(default=None, init=False, repr=False)
    _groups: dict[str, tuple[int, tuple[str, ...]]] | None = field(
        default=None, init=False, repr=False
    )
    _shadow: dict[str, str] | None = field(default=None, init=False, repr=False)
    _sudoers: Sudoers | None = field(default=None, init=False, repr=False)

    # Files ------------------------------------------------------------------

    def accounts(self) -> list[Account]:
        """
        Every account in ``/etc/passwd``, in file order.

        Returns:
            The accounts; empty when the file cannot be read.
        """
        if self._passwd is None:
            entries: list[Account] = []
            for line in (read_text(self.host.at("/etc/passwd")) or "").splitlines():
                parts = line.split(":")
                if len(parts) < 7 or not parts[0] or line.startswith("#"):
                    continue
                try:
                    uid, gid = int(parts[2]), int(parts[3])
                except ValueError:
                    continue
                entries.append(Account(parts[0], uid, gid, parts[5], parts[6]))
            self._passwd = entries
        return self._passwd

    def account(self, name: str) -> Account | None:
        """
        Look one account up by name.

        Args:
            name: The login name.

        Returns:
            The account, or None when there is none by that name.
        """
        return next((entry for entry in self.accounts() if entry.name == name), None)

    def groups(self) -> dict[str, tuple[int, tuple[str, ...]]]:
        """
        Every group in ``/etc/group``.

        Returns:
            Group name to its gid and its listed members.
        """
        if self._groups is None:
            groups: dict[str, tuple[int, tuple[str, ...]]] = {}
            for line in (read_text(self.host.at("/etc/group")) or "").splitlines():
                parts = line.split(":")
                if len(parts) < 4 or not parts[0]:
                    continue
                try:
                    gid = int(parts[2])
                except ValueError:
                    continue
                members = tuple(member for member in parts[3].split(",") if member)
                groups[parts[0]] = (gid, members)
            self._groups = groups
        return self._groups

    def groups_of(self, account: Account) -> set[str]:
        """
        Every group an account is in: its primary group and those that list it.

        Args:
            account: The account.

        Returns:
            Group names.
        """
        names = set()
        for name, (gid, members) in self.groups().items():
            if gid == account.gid or account.name in members:
                names.add(name)
        return names

    def shadow(self) -> dict[str, str] | None:
        """
        The password field of every account, from ``/etc/shadow``.

        Returns:
            Login name to its second field, or None when the file cannot be
            read (not root): "unknown" is a different answer from "no password".
        """
        if self._shadow is None:
            text = read_text(self.host.at("/etc/shadow"))
            if text is None:
                return None
            fields: dict[str, str] = {}
            for line in text.splitlines():
                parts = line.split(":")
                if len(parts) >= 2 and parts[0]:
                    fields[parts[0]] = parts[1]
            self._shadow = fields
        return self._shadow

    def uid_range(self) -> tuple[int, int]:
        """
        ``UID_MIN`` and ``UID_MAX`` from ``/etc/login.defs``.

        Returns:
            The range human accounts are created in.
        """
        low, high = DEFAULT_UID_RANGE
        for line in (read_text(self.host.at("/etc/login.defs")) or "").splitlines():
            words = line.split()
            if len(words) >= 2 and words[1].isdigit():
                if words[0] == "UID_MIN":
                    low = int(words[1])
                elif words[0] == "UID_MAX":
                    high = int(words[1])
        return low, high

    def login_shells(self) -> set[str]:
        """
        The shells ``/etc/shells`` lists.

        Returns:
            Their paths; empty when the file is missing.
        """
        return {
            line.strip()
            for line in (read_text(self.host.at("/etc/shells")) or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }

    # Questions --------------------------------------------------------------

    def can_log_in(self, account: Account) -> bool:
        """
        Report whether an account has a shell a session can start.

        Args:
            account: The account.

        Returns:
            False for ``nologin``, ``false`` and anything ``/etc/shells`` does
            not list (when it lists anything).
        """
        if not account.shell or Path(account.shell).name in NO_LOGIN_SHELLS:
            return False
        shells = self.login_shells()
        return not shells or account.shell in shells

    def password_state(self, name: str) -> str:
        """
        Say whether an account has a password that works.

        Args:
            name: The login name.

        Returns:
            ``usable``; ``locked`` (``!`` or ``*``: no password login, though a
            key still works when PAM is on); ``empty`` (a login with no password
            at all); or ``unknown`` when ``/etc/shadow`` cannot be read or does
            not name the account.
        """
        shadow = self.shadow()
        if shadow is None or name not in shadow:
            return "unknown"
        value = shadow[name]
        if value == "":
            return "empty"
        if value.startswith(("!", "*")):
            return "locked"
        return "usable"

    def empty_passwords(self) -> list[str]:
        """
        Accounts whose password field is empty: anyone may log in as them.

        Returns:
            Their names; empty when there are none or shadow cannot be read.
        """
        shadow = self.shadow() or {}
        return [name for name, value in shadow.items() if value == ""]

    def other_uid0(self) -> list[str]:
        """
        Accounts other than root whose user id is 0.

        Returns:
            Their names: each one is root under another name.
        """
        return [entry.name for entry in self.accounts() if entry.uid == 0 and entry.name != "root"]

    def human_accounts(self) -> list[Account]:
        """
        Accounts a person logs in as: in the login uid range, with a login shell.

        Returns:
            The accounts, in file order.
        """
        low, high = self.uid_range()
        return [
            entry
            for entry in self.accounts()
            if low <= entry.uid <= high and self.can_log_in(entry) and entry.name != TUNNEL_ACCOUNT
        ]

    # sudo -------------------------------------------------------------------

    def sudoers(self) -> Sudoers:
        """
        What ``/etc/sudoers`` and its drop-ins say, read once.

        Returns:
            The parsed rules (see :mod:`~noust.managers.server.security_sudoers`).
        """
        if self._sudoers is None:
            self._sudoers = read_sudoers(self.host)
        return self._sudoers

    def identity(self, account: Account) -> Identity:
        """
        An account as sudoers rules see it: its name, uid and every group.

        A rule for ``%sudo`` applies through the account's primary group (the
        gid in ``passwd``) as well as through the groups that list it.

        Args:
            account: The account.

        Returns:
            Its name, uid, group names and group ids.
        """
        groups = self.groups_of(account)
        gids = {account.gid} | {self.groups()[name][0] for name in groups}
        return Identity(account.name, account.uid, frozenset(groups), frozenset(gids))

    def sudo_rule(self, account: Account) -> SudoRule:
        """
        Find what sudo grants an account.

        Args:
            account: The account.

        Returns:
            Whether a rule grants it root, whether that rule wants a password,
            and where it was found. Root itself needs no rule.
        """
        if account.uid == 0:
            return SudoRule(granted=True, nopasswd=True, sources=("uid 0",))
        grant = self.sudoers().grant(self.identity(account))
        return SudoRule(grant.granted, grant.nopasswd, grant.sources)

    def sudo_usable(self, account: Account) -> bool:
        """
        Report whether an account that logs in with a key can then use sudo.

        Args:
            account: The account.

        Returns:
            True when a rule grants it root and either the rule does not ask
            for a password or the account has one sudo can check.
        """
        rule = self.sudo_rule(account)
        if not rule.granted:
            return False
        if rule.nopasswd:
            return True
        # Under rootpw or targetpw sudo asks for root's password, not the
        # account's: what makes sudo usable is then root's.
        asked = (
            "root" if self.sudoers().asks_root_password(self.identity(account)) else account.name
        )
        return self.password_state(asked) == "usable"

    def admins(self) -> list[Account]:
        """
        The accounts that can become root and could log in to do it, root first.

        Returns:
            root, then every human account a sudo rule grants root or that is in
            one of :data:`SUDO_GROUPS`.
        """
        found: list[Account] = []
        root = self.account("root")
        if root is not None:
            found.append(root)
        for entry in self.human_accounts():
            if self.sudo_rule(entry).granted or self.groups_of(entry) & set(SUDO_GROUPS):
                found.append(entry)
        return found


def glob_matches(name: str, patterns: tuple[str, ...]) -> bool:
    """
    Match a name against sshd's ``AllowUsers``-style patterns.

    Args:
        name: A user or group name.
        patterns: sshd's patterns (``*`` and ``?``; ``user@host`` is matched
            on the user part only, which errs on the side of "allowed").

    Returns:
        True when any pattern matches.
    """
    for pattern in patterns:
        user_part = pattern.split("@", 1)[0]
        if fnmatch.fnmatchcase(name, user_part):
            return True
    return False
