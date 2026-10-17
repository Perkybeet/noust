# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The text of a replica's files: the passfile it connects with and the settings it starts from.

Pure text in, text out; reading, writing and owning the files is the manager's
(:mod:`noust.managers.database.replication`).

**The passfile.** A replica's walreceiver connects as the replication account for as long as it
runs, so the password must be where the engine's own account can read it and nobody else can. It is
a ``PGPASSFILE`` (one line, ``host:port:replication:account:password``, mode 0600) named by
``passfile=`` in ``primary_conninfo``, which keeps the password out of ``postgresql.auto.conf``
(which backups copy and ``pg_settings`` shows to a superuser) and out of the process environment.

**The settings.** ``pg_basebackup`` copies ``postgresql.auto.conf`` from the primary, and with it
everything an operator did there with ``ALTER SYSTEM``: the certificate paths of the primary, its
archiving, the addresses it listens on. A replica is another machine, so none of that is right
for it. It keeps its own file, with what a standby must agree with its primary on (the capacity
settings a hot standby needs at least as high) and where it follows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

__all__ = [
    "auto_conf_value",
    "conninfo_options",
    "passfile_line",
    "quote_setting",
    "standby_auto_conf",
]

#: ``key = value`` in ``postgresql.auto.conf``.
_ASSIGNMENT = re.compile(r"\s*(?P<key>[A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(?P<value>.*?)\s*")

#: The settings of the copied file a replica keeps besides the capacity ones it is given.
_FOLLOWING = ("primary_conninfo", "primary_slot_name")


def _escape_field(value: str) -> str:
    """
    Escape a field of a passfile line (a colon or a backslash is escaped with a backslash).

    Args:
        value: The field.

    Returns:
        The escaped field.
    """
    return value.replace("\\", "\\\\").replace(":", "\\:")


def passfile_line(host: str, port: int, account: str, password: str) -> str:
    """
    Write the one line of a replica's passfile.

    Args:
        host: The primary's host as the replica connects to it.
        port: The primary's port.
        account: The replication account.
        password: Its password.

    Returns:
        The line, newline-terminated, for the ``replication`` pseudo-database.
    """
    fields = [_escape_field(host), str(port), "replication", _escape_field(account)]
    return ":".join([*fields, _escape_field(password)]) + "\n"


def quote_setting(value: str) -> str:
    """
    Quote a value for ``postgresql.auto.conf``.

    Args:
        value: The value.

    Returns:
        It in single quotes, with quotes and backslashes doubled as the file reads them.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _unquote(raw: str) -> str:
    """
    Read a value of ``postgresql.auto.conf``.

    Args:
        raw: What follows the equals sign.

    Returns:
        The value without its quotes and escapes.
    """
    if len(raw) >= 2 and raw.startswith("'") and raw.endswith("'"):
        inner = raw[1:-1]
        return re.sub(r"\\(.)|''", lambda hit: hit.group(1) or "'", inner)
    return raw


def auto_conf_value(text: str, key: str) -> str | None:
    """
    Read one setting from the text of ``postgresql.auto.conf``.

    Args:
        text: The file.
        key: The setting.

    Returns:
        Its last value, or None when the file does not set it.
    """
    found: str | None = None
    for line in text.splitlines():
        match = _ASSIGNMENT.fullmatch(line)
        if match is not None and match.group("key") == key:
            found = _unquote(match.group("value"))
    return found


def standby_auto_conf(own: str, copied: str, capacity: Mapping[str, int]) -> str:
    """
    Make the ``postgresql.auto.conf`` a replica starts with.

    Everything an operator did with ``ALTER SYSTEM`` on the primary came with the copy, and none
    of it is right for another machine (its certificate paths, its archiving, the addresses it
    listens on). What this server set for itself is. So the file is this server's own, with the
    capacity a standby needs at least as high as its primary's, and where it follows.

    Args:
        own: This server's ``postgresql.auto.conf`` from before the join.
        copied: The copy's, with the lines ``pg_basebackup -R`` appended.
        capacity: The settings a hot standby needs at least as high as its primary, with the
            values to give them.

    Returns:
        The file: this server's comments and settings, the capacity, ``primary_conninfo`` and
        ``primary_slot_name``. A setting of this server's own that only means something while
        recovering from an archive or following another primary is left out.
    """
    dropped = {*_FOLLOWING, *capacity, "restore_command", "recovery_target_time", "recovery_target"}
    dropped |= {"recovery_target_action", "recovery_target_timeline", "recovery_target_lsn"}
    kept: list[str] = []
    for line in own.splitlines():
        match = _ASSIGNMENT.fullmatch(line)
        if line.strip() and (match is None or match.group("key") not in dropped):
            kept.append(line)
    if not any(line.lstrip().startswith("#") for line in kept):
        kept.insert(0, "# Do not edit this file manually!")
    kept.append("# Set by Noust: a replica needs at least the primary's capacity.")
    kept += [f"{key} = {quote_setting(str(value))}" for key, value in sorted(capacity.items())]
    kept.append("# Set by Noust: where this server follows.")
    for line in copied.splitlines():
        match = _ASSIGNMENT.fullmatch(line)
        if match is not None and match.group("key") in _FOLLOWING:
            kept.append(line)
    return "\n".join(kept) + "\n"


def conninfo_options(conninfo: str) -> dict[str, str]:
    """
    Read a libpq connection string (``key=value key='quoted value'``).

    Args:
        conninfo: The string, such as ``primary_conninfo``.

    Returns:
        The options it sets.
    """
    options: dict[str, str] = {}
    pattern = re.compile(
        r"\s*(?P<key>[A-Za-z_]+)\s*=\s*(?:'(?P<quoted>(?:[^'\\]|\\.)*)'|(?P<bare>\S*))"
    )
    position = 0
    while position < len(conninfo):
        match = pattern.match(conninfo, position)
        if match is None:
            break
        if match.group("quoted") is not None:
            value = re.sub(r"\\(.)", r"\1", match.group("quoted"))
        else:
            value = match.group("bare")
        options[match.group("key")] = value
        position = match.end()
    return options
