# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What sudoers grants root: the one reader of ``/etc/sudoers`` and its drop-ins.

Debian and Ubuntu separate the fields of ``%sudo\\tALL=(ALL:ALL) ALL`` with a
tab, Red Hat's ``%wheel`` line has tabs on both sides of the runas list, and
sudo itself accepts blanks around ``=``, after the commas of a list and between
``NOPASSWD:`` and its command. Splitting a rule on one space (what this
replaces) read none of the stock files, so a member of ``sudo`` looked like an
account with no way to root and ``PermitRootLogin no`` was refused.

This is a reader of what *grants root*, not an implementation of sudoers, and
it errs one way: a line it cannot read grants nothing, because a wrong "no"
only turns an automatic fix into a guided one while a wrong "yes" locks an
operator out. It follows sudo where that matters to the answer:

- fields are split on any blank, and a rule grants root only to the account a
  user list names (a name, ``%group``, ``%#gid``, ``#uid``, ``ALL``, a
  ``User_Alias``), on a host list that names this host (``ALL``, the machine's
  name, a ``Host_Alias``), as ``root`` (a runas list naming ``root``, ``#0``,
  ``ALL`` or a ``Runas_Alias``, or none at all) and for the command ``ALL``
  (or a ``Cmnd_Alias`` that holds it): ``alice ALL=(www-data) ALL`` and
  ``alice ALL=(ALL) /usr/bin/systemctl`` do not make alice root;
- ``!`` negates and, in every list, the last entry that matches wins;
- the last rule in file order that matches decides, as sudo does, so a later
  ``%sudo ... ALL`` takes ``NOPASSWD`` away from an earlier drop-in;
- ``Defaults rootpw``, ``targetpw`` and ``runaspw`` make sudo ask for root's
  password and ``Defaults !authenticate`` makes it ask for none, scoped by
  ``Defaults:user`` and ``Defaults@host``;
- ``#include``/``@include`` and ``#includedir``/``@includedir`` are followed
  where they appear, skipping the names sudo skips.

What it does not read, and so never grants through: netgroups (``+name``),
non-Unix groups (``%:name``), a rule whose ``NOTBEFORE``/``NOTAFTER`` limits it
in time, a user whose rights come from LDAP or SSSD sudoers, and a host list
naming an address or a wildcard. Only the first ``host = commands`` section of
a line is read.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from noust.managers.server.host import HostPaths, read_text

#: How deep an alias may refer to another, and a file to another file. sudo's own
#: limits are far higher; here a chain that long is a loop.
MAX_DEPTH = 20

#: ``Defaults`` flags that make sudo ask for root's password, not the invoker's.
ROOT_PASSWORD_FLAGS = ("rootpw", "targetpw", "runaspw")

_ALIAS_KINDS = {
    "User": "user",
    "Runas": "runas",
    "Host": "host",
    "Cmnd": "cmnd",
    "Cmd": "cmnd",
}
_ALIAS_LINE = re.compile(r"^(User|Runas|Host|Cmnd|Cmd)_Alias[ \t]+(.+)$")
_ALIAS_SPLIT = re.compile(r"\s*:\s*(?=[A-Z][A-Z0-9_]*\s*=)")
_ALIAS_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
# A scope sits right against the word: ``Defaults:alice``, never ``Defaults !flag``.
_DEFAULTS_LINE = re.compile(r"^Defaults(?:([:@!>])([^\s,]+(?:\s*,\s*[^\s,]+)*))?\s+(.+)$")
_INCLUDE = re.compile(r"^[#@](include|includedir)[ \t]+(.+)$")
_TAG = re.compile(r"^([A-Z_]+)[ \t]*:[ \t]*")
_OPTION_NAMES = (
    "ROLE",
    "TYPE",
    "PRIVS",
    "LIMITPRIVS",
    "CWD",
    "CHROOT",
    "TIMEOUT",
    "NOTBEFORE",
    "NOTAFTER",
)
_OPTION = re.compile(rf"^({'|'.join(_OPTION_NAMES)})=(\"[^\"]*\"|\S+)\s*")
# The start of a second ``Host_List = Cmnd_Spec_List`` on the same line.
_NEXT_SECTION = re.compile(r"\s:\s*([^\s=:,()]+(?:\s*,\s*[^\s=:,()]+)*)\s*=")
# A comment runs from a '#' that is not a uid ('#1000') to the end of the line.
_COMMENT = re.compile(r"(?<!\\)#(?!\d).*$")


@dataclass(frozen=True)
class Identity:
    """
    Who a rule is read for.

    Attributes:
        name: The login name.
        uid: Its user id.
        groups: The names of its groups, primary and supplementary.
        gids: Their ids.
    """

    name: str
    uid: int
    groups: frozenset[str] = frozenset()
    gids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Item:
    """
    One member of a list, with its ``!``.

    Attributes:
        value: The member as written, unescaped.
        negated: An odd number of ``!`` stood before it.
    """

    value: str
    negated: bool = False


@dataclass(frozen=True)
class Command:
    """
    One command of a rule, with what carries over from the ones before it.

    Attributes:
        runas: Who it may run as; a rule with no runas list runs as root.
        command: The command, with its ``!``.
        nopasswd: ``NOPASSWD:`` is in force (until a ``PASSWD:``).
        timed: ``NOTBEFORE`` or ``NOTAFTER`` limits it in time.
    """

    runas: tuple[Item, ...]
    command: Item
    nopasswd: bool
    timed: bool


@dataclass(frozen=True)
class Spec:
    """
    One ``User_List Host_List = Cmnd_Spec_List``.

    Attributes:
        where: ``file:line`` it was read from.
        users: Whom it applies to.
        hosts: On which machines.
        commands: What it allows.
    """

    where: str
    users: tuple[Item, ...]
    hosts: tuple[Item, ...]
    commands: tuple[Command, ...]


@dataclass(frozen=True)
class DefaultsLine:
    """
    The boolean flags of one ``Defaults`` line.

    Attributes:
        scope: ``""`` (every rule), ``:`` (users), ``@`` (hosts), ``!``
            (commands) or ``>`` (runas users).
        targets: The list the scope names.
        flags: ``(name, enabled)`` for each flag; ``name=value`` settings are
            not kept, nothing here reads them.
    """

    scope: str
    targets: tuple[Item, ...]
    flags: tuple[tuple[str, bool], ...]


@dataclass(frozen=True)
class Grant:
    """
    What the rules say about one account.

    Attributes:
        granted: A rule lets it run any command as root.
        nopasswd: The rule that decides asks for no password.
        sources: The ``file:line`` of every rule that granted it.
    """

    granted: bool
    nopasswd: bool
    sources: tuple[str, ...]


def _split(text: str, separator: str = ",") -> list[str]:
    """
    Split on a separator that is not escaped, quoted or inside parentheses.

    Args:
        text: The text.
        separator: The character to split on.

    Returns:
        The parts, untrimmed.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quoted = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted and char == "(":
            depth += 1
        elif not quoted and char == ")" and depth:
            depth -= 1
        elif char == separator and depth == 0 and not quoted:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _words(text: str) -> list[str]:
    """
    Split on blanks, keeping an escaped blank or a quoted run inside one word.

    Args:
        text: The text.

    Returns:
        The words, still escaped.
    """
    words: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char in " \t" and not quoted:
            if current:
                words.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        words.append("".join(current))
    return words


def _unescape(text: str) -> str:
    """
    Remove sudoers' backslashes and the quotes around a name.

    Args:
        text: A member as written.

    Returns:
        The member itself.
    """
    return re.sub(r'\\(.)|"', lambda found: found.group(1) or "", text)


def _items(text: str) -> tuple[Item, ...]:
    """
    Read a comma-separated list, with its ``!``.

    Args:
        text: The list.

    Returns:
        Its members; an empty one is dropped.
    """
    items: list[Item] = []
    for part in _split(text):
        part = part.strip()
        negated = False
        while part.startswith("!"):
            negated = not negated
            part = part[1:].lstrip()
        if value := _unescape(part):
            items.append(Item(value, negated))
    return tuple(items)


def _first_section(text: str) -> str:
    """
    Cut a command list at the ``: Host = ...`` that starts a second section.

    Args:
        text: What follows the first ``=`` of a rule.

    Returns:
        The first section's command list.
    """
    for match in _NEXT_SECTION.finditer(text):
        before = text[: match.start()]
        # "CWD=/tmp" is an option, not a host list, and a colon inside "(a:b)"
        # is a runas list's own.
        if before.count("(") == before.count(")") and match.group(1) not in _OPTION_NAMES:
            return before
    return text


def _commands(text: str) -> tuple[Command, ...] | None:
    """
    Read a ``Cmnd_Spec_List``: runas lists, tags, options and commands.

    The runas list and the tags stay in force for the commands that follow
    until another one is given, as in sudo.

    Args:
        text: What follows the ``=`` of a rule.

    Returns:
        The commands, or None when the text is not a command list.
    """
    runas: tuple[Item, ...] = (Item("root"),)
    nopasswd = False
    timed = False
    commands: list[Command] = []
    for element in _split(_first_section(text)):
        rest = element.strip()
        if rest.startswith("("):
            close = rest.find(")")
            if close < 0:
                return None
            # Only the user half decides who the command runs as; "(:group)" and
            # "()" are the invoking user, which is an empty list here.
            runas = _items(rest[1:close].partition(":")[0])
            rest = rest[close + 1 :].lstrip()
        while True:
            if tag := _TAG.match(rest):
                if tag.group(1) in ("NOPASSWD", "PASSWD"):
                    nopasswd = tag.group(1) == "NOPASSWD"
                rest = rest[tag.end() :]
            elif option := _OPTION.match(rest):
                timed = timed or option.group(1) in ("NOTBEFORE", "NOTAFTER")
                rest = rest[option.end() :]
            else:
                break
        items = _items(rest)
        if len(items) != 1:
            return None
        commands.append(Command(runas, items[0], nopasswd, timed))
    return tuple(commands) or None


def _flags(text: str) -> tuple[tuple[str, bool], ...]:
    """
    Read the boolean flags of a ``Defaults`` line.

    Args:
        text: What follows ``Defaults``, or its scope.

    Returns:
        ``(name, enabled)`` for ``flag`` and ``!flag``; settings with a value are
        not flags and are skipped.
    """
    flags: list[tuple[str, bool]] = []
    for part in _split(text):
        part = part.strip()
        if not part or "=" in part:
            continue
        flags.append((part.lstrip("! \t"), not part.startswith("!")))
    return tuple(flags)


@dataclass
class Sudoers:
    """
    The rules, aliases and defaults of one host, in the order sudo reads them.

    Attributes:
        hostname: This machine's name, for host lists that name it.
        specs: Every rule.
        aliases: Alias kind (``user``, ``runas``, ``host``, ``cmnd``) to its
            names and what they stand for.
        defaults: Every ``Defaults`` line.
    """

    hostname: str = ""
    specs: list[Spec] = field(default_factory=list)
    aliases: dict[str, dict[str, tuple[Item, ...]]] = field(default_factory=dict)
    defaults: list[DefaultsLine] = field(default_factory=list)

    def add(self, where: str, line: str) -> None:
        """
        Add one logical line, comments already removed.

        A line that is not understood adds nothing.

        Args:
            where: ``file:line``, kept with the rule for the report.
            line: The line.
        """
        if match := _DEFAULTS_LINE.match(line):
            scope, targets, rest = match.groups()
            self.defaults.append(DefaultsLine(scope or "", _items(targets or ""), _flags(rest)))
        elif match := _ALIAS_LINE.match(line):
            kind = _ALIAS_KINDS[match.group(1)]
            for definition in _ALIAS_SPLIT.split(match.group(2)):
                name, found, members = definition.partition("=")
                if found and _ALIAS_NAME.fullmatch(name.strip()):
                    self.aliases.setdefault(kind, {})[name.strip()] = _items(members)
        elif (spec := self._spec(where, line)) is not None:
            self.specs.append(spec)

    @staticmethod
    def _spec(where: str, line: str) -> Spec | None:
        """
        Read ``User_List Host_List = Cmnd_Spec_List``.

        Args:
            where: ``file:line``.
            line: The rule.

        Returns:
            The rule, or None when it is not one.
        """
        head, found, tail = line.partition("=")
        if not found:
            return None
        # Blanks around the commas of a list are part of it, not a separator
        # between the user list and the host list.
        words = _words(re.sub(r"\s*,\s*", ",", head.strip()))
        commands = _commands(tail)
        if len(words) != 2 or commands is None:
            return None
        return Spec(where, _items(words[0]), _items(words[1]), commands)

    # Matching ---------------------------------------------------------------

    def _matches(self, items: tuple[Item, ...], kind: str, who: Identity, depth: int = 0) -> bool:
        """
        Match an account against a list: the last member that matches decides.

        Args:
            items: The list.
            kind: ``user``, ``runas`` or ``host``.
            who: The account.
            depth: How many aliases deep this is.

        Returns:
            True when the last member that matches is not negated.
        """
        matched = False
        for item in items:
            if self._member_matches(item.value, kind, who, depth):
                matched = not item.negated
        return matched

    def _member_matches(self, value: str, kind: str, who: Identity, depth: int) -> bool:
        """
        Match one member of a list.

        Args:
            value: The member.
            kind: ``user``, ``runas`` or ``host``.
            who: The account.
            depth: How many aliases deep this is.

        Returns:
            True when it names the account (or root, or this host).
        """
        if value == "ALL":
            return True
        members = self.aliases.get(kind, {}).get(value)
        if members is not None:
            return depth < MAX_DEPTH and self._matches(members, kind, who, depth + 1)
        if kind == "runas":
            return value in ("root", "#0")
        if kind == "host":
            short = self.hostname.split(".")[0]
            return bool(self.hostname) and value in (self.hostname, short)
        if value.startswith("%#"):
            return value[2:].isdigit() and int(value[2:]) in who.gids
        if value.startswith("%:") or value.startswith("+"):
            return False
        if value.startswith("%"):
            return value[1:] in who.groups
        if value.startswith("#"):
            return value[1:].isdigit() and int(value[1:]) == who.uid
        return value == who.name

    def _runs_anything(self, value: str, depth: int = 0) -> bool:
        """
        Say whether a command is ``ALL`` or an alias that ends up holding it.

        Args:
            value: The command, without its ``!``.
            depth: How many aliases deep this is.

        Returns:
            True when it allows every command.
        """
        if value == "ALL":
            return True
        members = self.aliases.get("cmnd", {}).get(value)
        if members is None or depth >= MAX_DEPTH:
            return False
        result = False
        for member in members:
            if self._runs_anything(member.value, depth + 1):
                result = not member.negated
        return result

    # Answers ----------------------------------------------------------------

    def grant(self, who: Identity) -> Grant:
        """
        Find whether the rules let an account run any command as root.

        Args:
            who: The account.

        Returns:
            The verdict of the last rule that matches, as sudo gives it.
        """
        granted = False
        nopasswd = False
        sources: list[str] = []
        for spec in self.specs:
            if not (
                self._matches(spec.users, "user", who) and self._matches(spec.hosts, "host", who)
            ):
                continue
            for command in spec.commands:
                if (
                    command.timed
                    or not self._runs_anything(command.command.value)
                    or not self._matches(command.runas, "runas", who)
                ):
                    continue
                if command.command.negated:
                    granted, nopasswd, sources = False, False, []
                else:
                    granted, nopasswd = True, command.nopasswd
                    sources.append(spec.where)
        if granted and self._flags_for(who).get("authenticate") is False:
            nopasswd = True
        return Grant(granted, nopasswd, tuple(dict.fromkeys(sources)))

    def asks_root_password(self, who: Identity) -> bool:
        """
        Say whether sudo asks an account for root's password, not its own.

        Args:
            who: The account.

        Returns:
            True under ``Defaults rootpw``, ``targetpw`` or ``runaspw``.
        """
        flags = self._flags_for(who)
        return any(flags.get(name) for name in ROOT_PASSWORD_FLAGS)

    def _flags_for(self, who: Identity) -> dict[str, bool]:
        """
        Fold the ``Defaults`` lines that apply to an account, in order.

        A line scoped to a command or a runas user cannot be placed without
        the command, so only what makes sudo ask for root's password is taken
        from it: that can only turn a "yes" into a "no".

        Args:
            who: The account.

        Returns:
            Flag name to its last setting.
        """
        state: dict[str, bool] = {}
        for line in self.defaults:
            if line.scope == ":" and not self._matches(line.targets, "user", who):
                continue
            if line.scope == "@" and not self._matches(line.targets, "host", who):
                continue
            for name, enabled in line.flags:
                if line.scope in ("!", ">") and not (enabled and name in ROOT_PASSWORD_FLAGS):
                    continue
                state[name] = enabled
        return state


def _logical_lines(text: str) -> Iterator[tuple[int, str]]:
    """
    Join the lines a trailing backslash continues.

    Args:
        text: A sudoers file.

    Yields:
        The line number it starts at and its text, stripped.
    """
    pending = ""
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        if not pending:
            start = number
        line = raw.rstrip()
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        yield start, (pending + line).strip()
        pending = ""
    if pending.strip():
        yield start, pending.strip()


def _read_file(host: HostPaths, path: Path, model: Sudoers, stack: tuple[Path, ...]) -> None:
    """
    Read one file into the model, and what it includes where it includes it.

    Args:
        host: Where the files are.
        path: The file.
        model: What is being filled.
        stack: The files being read, so a file that includes itself ends.
    """
    text = read_text(path)
    if text is None or path in stack or len(stack) >= MAX_DEPTH:
        return
    shown = "/" + str(path.relative_to(host.root)).lstrip("/")
    for number, line in _logical_lines(text):
        include = _INCLUDE.match(line)
        if include:
            target = re.split(r"\s+#", include.group(2))[0].strip().strip('"')
            # A relative path is relative to the file that includes it.
            local = host.at(str(Path(shown).parent / target))
            if include.group(1) == "include":
                _read_file(host, local, model, (*stack, path))
            else:
                _read_directory(host, local, model, (*stack, path))
            continue
        line = _COMMENT.sub("", line).strip()
        if line:
            model.add(f"{shown}:{number}", line)


def _read_directory(
    host: HostPaths, directory: Path, model: Sudoers, stack: tuple[Path, ...]
) -> None:
    """
    Read what an ``includedir`` pulls in.

    sudo skips names that end in ``~`` or contain a dot, so an editor's backup
    or a ``.dpkg-old`` never grants anything; this does the same.

    Args:
        host: Where the files are.
        directory: The directory.
        model: What is being filled.
        stack: The files being read.
    """
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_file() and "." not in entry.name and not entry.name.endswith("~"):
            _read_file(host, entry, model, stack)


def read_sudoers(host: HostPaths) -> Sudoers:
    """
    Read ``/etc/sudoers`` and everything it includes.

    Args:
        host: Where the files are.

    Returns:
        The rules, aliases and defaults; empty when the file cannot be read.
    """
    hostname = (read_text(host.at("/etc/hostname")) or "").strip()
    model = Sudoers(hostname=hostname)
    _read_file(host, host.at("/etc/sudoers"), model, ())
    return model
