# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The journal of any unit on the machine, with filters.

``ServiceManager.logs`` is not this. It answers for a unit Noust manages, as
text, with a line count, and refuses everything else on purpose (rule 4: the
ownership guard is at that chokepoint). Reading the journal of nginx, sshd or
the kernel is a different capability with a different audience, so it is a
different door: entries as data, filters a person needs to find the one failure
in a day of noise, and one rule for who may read it, which is administrators
only, because the system journal carries addresses, user names and, now and then,
somebody else's secret.

Nothing a caller sends reaches journalctl except through this module's own
checks: a unit is a name, a priority is a level, a time is a shape journalctl
documents, a count is a number. ``--unit=`` is glued to its value, so a name
that starts with a dash cannot become an option.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server import logquery
from noust.managers.server.errors import ServerError

#: Most entries one request returns.
MAX_LINES = 1000

#: Longest message kept per entry, in characters; a runaway line is cut, not sent whole.
MAX_MESSAGE = 4096

#: Deadline of reading the journal.
READ_TIMEOUT = 60

#: Entries a query examines when it is not told how many, and the most it may be told: a query
#: reads the newest entries of the range and judges each one here, so this is what bounds the work.
DEFAULT_SCAN = 5000
MAX_SCAN = 20000

#: Entries on each side of the one a context shows, at most.
MAX_CONTEXT = 200

#: How long ``follow`` keeps a stream open. A follow never ends by itself, so the
#: deadline is what stops one that nobody is reading any more.
FOLLOW_TIMEOUT = 3600

#: A unit name: what systemd allows in one, and never a leading dash.
_UNIT = re.compile(r"^[A-Za-z0-9:_@\\][A-Za-z0-9:_.@\\-]{0,255}$")

#: The levels journalctl knows, by name and number.
PRIORITIES = {
    "emerg": 0,
    "alert": 1,
    "crit": 2,
    "err": 3,
    "warning": 4,
    "notice": 5,
    "info": 6,
    "debug": 7,
}

#: An absolute time, or a relative one such as ``-30min``.
_ABSOLUTE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
_RELATIVE = re.compile(r"^-\d{1,4}(s|min|h|d|w)$")
#: Epoch seconds, ``@1759400000``: an interval picked on a chart is exact and has
#: no time zone to get wrong.
_EPOCH = re.compile(r"^@\d{1,11}$")

_BOOT_LINE = re.compile(
    r"^\s*(?P<index>-?\d+)\s+(?P<boot_id>[0-9a-f]{32})\s+"
    r"(?P<first>\w{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \w+)\s+"
    r"(?P<last>\w{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \w+)\s*$"
)


@dataclass(frozen=True)
class JournalEntry:
    """
    One line of the journal.

    Attributes:
        timestamp: When it was logged, ISO 8601 UTC.
        priority: 0 (emergency) to 7 (debug).
        unit: The unit that logged it, or the program's identifier.
        message: The text, cut at :data:`MAX_MESSAGE`.
        pid: The process id, when it has one.
        cursor: The journal position, to continue after it.
    """

    timestamp: str
    priority: int
    unit: str
    message: str
    pid: int | None
    cursor: str

    def to_dict(self) -> dict[str, Any]:
        """
        Render the entry as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass(frozen=True)
class JournalPage:
    """
    A read of the journal.

    Attributes:
        entries: The entries, oldest first.
        next_cursor: Pass it as ``after_cursor`` to read what came after.
        truncated: There were more entries than were asked for.
    """

    entries: list[JournalEntry]
    next_cursor: str | None
    truncated: bool


@dataclass(frozen=True)
class SearchPage:
    """
    The answer to a query.

    Attributes:
        entries: The newest matching entries, oldest first.
        matched: How many entries matched among those examined.
        scanned: How many entries were examined.
        truncated: There were more matches than were asked for.
        scan_limit_reached: The range holds older entries that were not examined: narrow it
            (``since``, ``@unit:``) or raise the scan.
        oldest: When the oldest examined entry was logged, or None when none was.
        normalised: The query written back in full, as it was understood.
    """

    entries: list[JournalEntry]
    matched: int
    scanned: int
    truncated: bool
    scan_limit_reached: bool
    oldest: str | None
    normalised: str


@dataclass(frozen=True)
class ContextPage:
    """
    The entries around one entry.

    Attributes:
        entries: The entries before it, itself and the entries after it, oldest first.
        target: The position of the entry itself in ``entries``, or None when it is no longer in the journal.
    """

    entries: list[JournalEntry]
    target: int | None


@dataclass(frozen=True)
class Boot:
    """
    One boot the journal remembers.

    Attributes:
        index: 0 for this boot, -1 for the one before.
        boot_id: The kernel's identifier for it.
        first: When its first entry was logged, as journalctl prints it.
        last: When its last was.
    """

    index: int
    boot_id: str
    first: str
    last: str


def decode_message(value: object) -> str:
    """
    Turn a journal ``MESSAGE`` into text.

    Args:
        value: A string, or the list of byte values journald uses for a message
            that is not valid UTF-8.

    Returns:
        The text, cut at :data:`MAX_MESSAGE`.
    """
    if isinstance(value, str):
        text = value
    elif isinstance(value, list) and all(isinstance(item, int) for item in value):
        text = bytes(item % 256 for item in value).decode("utf-8", errors="replace")
    else:
        text = ""
    return text[:MAX_MESSAGE]


def parse_entries(text: str) -> list[JournalEntry]:
    """
    Read ``journalctl -o json``, one object per line.

    Args:
        text: The output.

    Returns:
        The entries that carry a cursor. A line that is not JSON is skipped: the
        journal is read while it is written to.
    """
    entries = []
    for line in text.splitlines():
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict) or not isinstance(raw.get("__CURSOR"), str):
            continue
        micros = str(raw.get("__REALTIME_TIMESTAMP", ""))
        stamp = (
            datetime.fromtimestamp(int(micros) / 1_000_000, tz=timezone.utc).isoformat()
            if micros.isdigit()
            else ""
        )
        priority = str(raw.get("PRIORITY", "6"))
        pid = str(raw.get("_PID", ""))
        entries.append(
            JournalEntry(
                timestamp=stamp,
                priority=int(priority) if priority.isdigit() else 6,
                unit=str(raw.get("_SYSTEMD_UNIT") or raw.get("SYSLOG_IDENTIFIER") or ""),
                message=decode_message(raw.get("MESSAGE")),
                pid=int(pid) if pid.isdigit() else None,
                cursor=raw["__CURSOR"],
            )
        )
    return entries


def parse_boots(text: str) -> list[Boot]:
    """
    Read ``journalctl --list-boots``.

    Args:
        text: The output.

    Returns:
        The boots, oldest first as journalctl prints them.
    """
    return [
        Boot(int(m["index"]), m["boot_id"], m["first"], m["last"])
        for line in text.splitlines()
        if (m := _BOOT_LINE.match(line))
    ]


def _time(value: str, name: str) -> str:
    """
    Check a time filter.

    Args:
        value: What the caller gave.
        name: The filter's name, for the message.

    Returns:
        The value.

    Raises:
        ValidationError: It is not a date, a date and time, ``-30min`` or ``@<epoch>``.
    """
    if not (_ABSOLUTE.fullmatch(value) or _RELATIVE.fullmatch(value) or _EPOCH.fullmatch(value)):
        raise ValidationError(
            f"'{name}' is not a time Noust reads: {value!r}",
            "Use 2026-09-29, 2026-09-29 10:30 or a relative time such as -30min, -2h or -7d.",
        )
    return value


def _unit_flag(unit: object) -> str:
    """
    Check a unit name and glue it to its option.

    Args:
        unit: What the caller gave.

    Returns:
        ``--unit=<name>``: glued, so a name that starts with a dash cannot become an option.

    Raises:
        ValidationError: It is not a unit name.
    """
    if not isinstance(unit, str) or not _UNIT.fullmatch(unit):
        raise ValidationError(
            f"Not a unit name: {unit!r}",
            "Use the name systemd shows, such as nginx or nginx.service.",
        )
    return f"--unit={unit}"


def _cursor(cursor: object) -> str:
    """
    Check a journal cursor.

    Args:
        cursor: What the caller gave.

    Returns:
        The cursor.

    Raises:
        ValidationError: It is not one a previous read could have returned.
    """
    if not isinstance(cursor, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9=;_.\-]{0,255}", cursor
    ):
        raise ValidationError("That is not a journal cursor", "Use one from a previous read.")
    return cursor


class JournalReader:
    """Reads the journal of any unit, with filters."""

    def __init__(self, *, runner: CommandRunner | None = None) -> None:
        """
        Args:
            runner: Command runner; the process-wide one when omitted.
        """
        self._runner = runner

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    def command(
        self,
        *,
        unit: str | None = None,
        priority: str | int | None = None,
        since: str | None = None,
        until: str | None = None,
        lines: int = 200,
        boot: int | None = None,
        kernel: bool = False,
        after_cursor: str | None = None,
        units: Sequence[str] | None = None,
        max_lines: int = MAX_LINES,
    ) -> list[str]:
        """
        Build the journalctl command for a set of filters.

        Args:
            unit: A unit name; ``.service`` is optional.
            priority: A level name (``err``) or number (0 to 7); it and everything
                more serious is shown.
            since: Earliest time.
            until: Latest time.
            lines: How many entries, at most :data:`MAX_LINES`.
            boot: 0 for this boot, -1 for the one before, and so on.
            kernel: Only the kernel's messages.
            after_cursor: Continue after this position.
            units: Several unit names, any of which is read (what ``--unit`` repeated means).
            max_lines: The most ``lines`` may be: the entries a person is shown, 1000; a query
                that examines more raises it.

        Returns:
            The argv.

        Raises:
            ValidationError: A filter is not valid.
        """
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= max_lines:
            raise ValidationError(f"lines must be between 1 and {max_lines}", f"Got {lines!r}.")
        # One more than asked for: whether it comes back is how truncation is known.
        argv = ["journalctl", "--no-pager", "--output=json", f"--lines={lines + 1}"]
        for name in [*([unit] if unit is not None else []), *(units or ())]:
            argv.append(_unit_flag(name))
        if priority is not None:
            level = PRIORITIES.get(str(priority)) if not str(priority).isdigit() else int(priority)
            if level is None or not 0 <= level <= 7:
                raise ValidationError(
                    f"Not a priority: {priority!r}",
                    f"Use a level ({', '.join(PRIORITIES)}) or a number from 0 to 7.",
                )
            argv.append(f"--priority={level}")
        if since is not None:
            argv.append(f"--since={_time(since, 'since')}")
        if until is not None:
            argv.append(f"--until={_time(until, 'until')}")
        if boot is not None:
            if isinstance(boot, bool) or not isinstance(boot, int) or boot > 0:
                raise ValidationError(
                    "boot is 0 for this boot, -1 for the one before", f"Got {boot!r}."
                )
            argv.append(f"--boot={boot}")
        if kernel:
            argv.append("--dmesg")
        if after_cursor is not None:
            argv.append(f"--after-cursor={_cursor(after_cursor)}")
        return argv

    def read(self, *, text: str | None = None, **filters: Any) -> JournalPage:
        """
        Read the journal.

        Args:
            text: Keep only entries whose message contains this, ignoring case.
                Filtered here: ``journalctl --grep`` needs PCRE2, which not
                every build has.
            **filters: The filters of :meth:`command`.

        Returns:
            The entries, oldest first.

        Raises:
            ValidationError: A filter is not valid.
            ServerError: journalctl failed, carrying its words.
        """
        argv = self.command(**filters)
        lines = int(argv[3].removeprefix("--lines=")) - 1
        result = self.runner.run(argv, timeout=READ_TIMEOUT)
        if not result.success:
            raise ServerError(
                "Could not read the journal",
                "journalctl failed. The output below is its own.",
                output="\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p),
            )
        entries = parse_entries(result.stdout)
        truncated = len(entries) > lines
        # Without a cursor journalctl prints the newest entries, so the extra one is the oldest;
        # after a cursor it prints the ones that follow it, so the extra one is the newest.
        if truncated:
            entries = (
                entries[:lines] if filters.get("after_cursor") is not None else entries[-lines:]
            )
        if text:
            needle = text.lower()
            entries = [entry for entry in entries if needle in entry.message.lower()]
        return JournalPage(
            entries=entries,
            next_cursor=entries[-1].cursor if entries else None,
            truncated=truncated,
        )

    def search(
        self,
        text: str,
        *,
        since: str | None = None,
        until: str | None = None,
        lines: int = 200,
        scan: int = DEFAULT_SCAN,
        boot: int | None = None,
        kernel: bool = False,
        app_units: logquery.AppUnits = lambda domain: (),
    ) -> SearchPage:
        """
        Answer a query (:mod:`noust.managers.server.logquery`).

        Reads the newest ``scan`` entries of the range, narrowed by whatever ``journalctl`` can do
        itself (a unit, an application's units, a level), and judges each against the whole query.

        Args:
            text: The query; empty matches everything.
            since: Earliest time.
            until: Latest time.
            lines: How many matching entries to return, at most :data:`MAX_LINES`.
            scan: How many entries to examine, at most :data:`MAX_SCAN`.
            boot: 0 for this boot, -1 for the one before, and so on.
            kernel: Only the kernel's messages.
            app_units: Resolves ``@app:`` to the units an application runs as.

        Returns:
            The newest matches, oldest first, with how much was examined.

        Raises:
            ValidationError: The query cannot be read, or a filter is not valid.
            ServerError: journalctl failed, carrying its words.
        """
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= MAX_LINES:
            raise ValidationError(f"lines must be between 1 and {MAX_LINES}", f"Got {lines!r}.")
        if isinstance(scan, bool) or not isinstance(scan, int) or not 1 <= scan <= MAX_SCAN:
            raise ValidationError(f"scan must be between 1 and {MAX_SCAN}", f"Got {scan!r}.")
        tree = logquery.parse(text)
        narrowed = logquery.pushdown(tree, app_units)
        argv = self.command(
            units=narrowed.units,
            priority=narrowed.priority,
            since=since,
            until=until,
            lines=scan,
            boot=boot,
            kernel=kernel,
            max_lines=MAX_SCAN,
        )
        result = self.runner.run(argv, timeout=READ_TIMEOUT)
        if not result.success:
            raise ServerError(
                "Could not read the journal",
                "journalctl failed. The output below is its own.",
                output="\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p),
            )
        entries = parse_entries(result.stdout)
        # One more than asked for: whether it comes back is how an unread past is known.
        reached = len(entries) > scan
        entries = entries[-scan:] if reached else entries
        found = [
            entry
            for entry in entries
            if logquery.matches(
                tree,
                logquery.Entry(
                    unit=entry.unit, priority=entry.priority, pid=entry.pid, message=entry.message
                ),
                app_units,
            )
        ]
        return SearchPage(
            entries=found[-lines:],
            matched=len(found),
            scanned=len(entries),
            truncated=len(found) > lines,
            scan_limit_reached=reached,
            oldest=entries[0].timestamp if entries else None,
            normalised=logquery.render(tree),
        )

    def context(
        self, cursor: str, *, before: int = 25, after: int = 25, unit: str | None = None
    ) -> ContextPage:
        """
        Read the entries around one entry, to see what led to it and what came of it.

        Args:
            cursor: The entry's position, from a previous read.
            before: How many entries before it, at most :data:`MAX_CONTEXT`.
            after: How many after it, at most :data:`MAX_CONTEXT`.
            unit: Only this unit's entries around it; every unit's when omitted.

        Returns:
            The entries, oldest first, and where the entry itself is among them.

        Raises:
            ValidationError: The cursor, a count or the unit is not valid.
            ServerError: journalctl failed, carrying its words.
        """
        _cursor(cursor)
        for name, count in (("before", before), ("after", after)):
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or not 0 <= count <= MAX_CONTEXT
            ):
                raise ValidationError(
                    f"{name} must be between 0 and {MAX_CONTEXT}", f"Got {count!r}."
                )
        narrow = [_unit_flag(unit)] if unit is not None else []
        # --cursor starts at the entry itself; with --reverse it then walks back through what
        # came before it. --after-cursor starts after it and walks forward.
        earlier = self._run(
            [
                "journalctl",
                "--no-pager",
                "--output=json",
                "--reverse",
                f"--lines={before + 1}",
                *narrow,
                f"--cursor={cursor}",
            ]
        )
        later = (
            self._run(
                [
                    "journalctl",
                    "--no-pager",
                    "--output=json",
                    f"--lines={after}",
                    *narrow,
                    f"--after-cursor={cursor}",
                ]
            )
            if after
            else []
        )
        entries = [*reversed(earlier), *later]
        found = next((index for index, entry in enumerate(entries) if entry.cursor == cursor), None)
        return ContextPage(entries=entries, target=found)

    def _run(self, argv: list[str]) -> list[JournalEntry]:
        """
        Run a journalctl read and parse what it printed.

        Args:
            argv: The command, already checked.

        Returns:
            The entries in the order journalctl printed them.

        Raises:
            ServerError: journalctl failed, carrying its words.
        """
        result = self.runner.run(argv, timeout=READ_TIMEOUT)
        if not result.success:
            raise ServerError(
                "Could not read the journal",
                "journalctl failed. The output below is its own.",
                output="\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p),
            )
        return parse_entries(result.stdout)

    def follow(
        self, on_line: Callable[[str], None], *, timeout: int = FOLLOW_TIMEOUT, **filters: Any
    ) -> None:
        """
        Deliver the journal line by line as it is written.

        The filters are checked exactly as for :meth:`read`; the output is the
        journal's own one-line-per-entry text, since there is nobody to parse it.

        Args:
            on_line: Receives each line, without the newline.
            timeout: When to stop following, in seconds.
            **filters: The filters of :meth:`command`.

        Raises:
            ValidationError: A filter is not valid.
            ServerError: journalctl failed to start, carrying its output.
        """
        argv = self.command(**filters)
        shown = int(argv[3].removeprefix("--lines="))
        argv = ["--output=short-iso" if part == "--output=json" else part for part in argv]
        argv[3] = f"--lines={shown - 1}"
        result = self.runner.stream([*argv, "--follow"], on_line=on_line, timeout=timeout)
        # A follow ends at the deadline (exit -1) or when the reader stops; only a
        # command that could not run at all is a failure.
        if not result.success and not result.timed_out:
            raise ServerError(
                "Could not follow the journal",
                "journalctl failed. The output below is its own.",
                output=result.stdout or result.stderr,
            )

    def boots(self) -> list[Boot]:
        """
        List the boots the journal remembers.

        Returns:
            The boots. Empty when the journal is volatile and has only this one.
        """
        result = self.runner.run(["journalctl", "--list-boots", "--no-pager"], timeout=READ_TIMEOUT)
        return parse_boots(result.stdout) if result.success else []
