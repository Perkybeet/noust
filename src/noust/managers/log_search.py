# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Searching the logs of every application at once (checklist G-13).

The logs of an application are in the journal (its unit says ``StandardOutput=journal``)
or, for a Compose stack, in Docker; reading one means choosing one application and
paging through it. This is the question an operator asks after something went wrong:
*where did this error appear, in which of my applications, around what time?*

A query is words and a few fields. ``timeout "connection refused" -healthcheck`` is
every line with ``timeout`` and the phrase and without ``healthcheck`` (case does not
matter); ``a OR b`` is a line that has all the words of one side or of the other;
``app:shop.example.com`` narrows to applications, ``unit:nginx.service`` adds a system
unit, ``level:err`` keeps lines at that level or worse (journal entries have one, a
container's lines do not, so they are left out and the answer says so).

It runs on the server, over ``journalctl`` and ``docker logs`` through the command
runner, as argv, and:

- **reads a bounded amount**: a window (``since``/``until``), a unit filter and the
  first positive word as ``--grep`` hand ``journalctl`` the narrowing, at most
  :data:`SCAN_LIMIT` entries are examined, and the answer says when that was reached;
- **masks what it shows**: ANSI is stripped and both the secrets the application holds and
  anything shaped like a credential (:func:`~noust.core.secret_detection.mask_secret_shapes`)
  are replaced before a line leaves, like every other log Noust shows;
- **shows a team its own applications and no others** (the caller narrows ``apps``).
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NoustError, ValidationError
from noust.core.redact import Scrubber, scrubber_for
from noust.core.runner import CommandRunner, get_runner
from noust.core.secret_detection import mask_secret_shapes
from noust.core.store import App, is_compose_backed
from noust.core.utils import domain_to_app_name
from noust.terminal.targets import find_stack_containers

logger = logging.getLogger(__name__)

#: Journal priority names, to the number journald uses.
LEVELS: dict[str, int] = {
    "emerg": 0,
    "alert": 1,
    "crit": 2,
    "err": 3,
    "error": 3,
    "warning": 4,
    "warn": 4,
    "notice": 5,
    "info": 6,
    "debug": 7,
}

#: The names an answer uses for a priority.
LEVEL_NAMES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")

#: Most lines an answer holds, and the default.
MAX_LIMIT = 1_000
DEFAULT_LIMIT = 200

#: Entries examined for one search: more than this and the answer is marked truncated.
SCAN_LIMIT = 50_000

#: Lines read from each container of a stack.
CONTAINER_TAIL = 5_000

#: Deadlines of the commands a search runs.
JOURNAL_TIMEOUT = 90
DOCKER_TIMEOUT = 30

#: Windows no wider than this: a search is about what happened lately, and the
#: journal is bounded by its own retention anyway.
MAX_WINDOW_SECONDS = 400 * 86_400

#: Words a query may have, and the longest one.
MAX_TERMS = 12
MAX_TERM_LENGTH = 200

#: A unit name a query may add.
_UNIT = re.compile(r"[A-Za-z0-9:_.@\\-]{1,128}")
_DURATION = re.compile(r"(\d+)\s*([smhdw])")
_UNITS_OF_TIME = {"s": 1, "m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]")
_CONTAINER_LINE = re.compile(r"(?P<ts>\d{4}-\d\d-\d\dT[\d:.]+Z) ?(?P<text>.*)", re.DOTALL)


@dataclass(frozen=True)
class Alternative:
    """
    One way a line can match: all of these words and none of those.

    Attributes:
        terms: Words the line must contain.
        excluded: Words it must not.
    """

    terms: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        """
        Args:
            text: A line, lower-cased.

        Returns:
            Whether it has every word and none of the excluded ones.
        """
        return all(t in text for t in self.terms) and not any(t in text for t in self.excluded)


@dataclass(frozen=True)
class Query:
    """
    What was asked.

    Attributes:
        alternatives: The ways a line can match (any one is enough). Empty
            matches every line.
        apps: Applications named with ``app:``.
        units: System units named with ``unit:``.
        level: The worst level kept, as its name, or None.
    """

    alternatives: tuple[Alternative, ...] = ()
    apps: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    level: str | None = None

    def matches(self, text: str) -> bool:
        """
        Args:
            text: A line.

        Returns:
            Whether the words of the query allow it.
        """
        if not self.alternatives:
            return True
        lowered = text.lower()
        return any(alternative.matches(lowered) for alternative in self.alternatives)

    @property
    def first_term(self) -> str | None:
        """Returns: A word every matching line has, to narrow the journal by; None when no word is needed everywhere."""
        if not self.alternatives:
            return None
        common = set(self.alternatives[0].terms)
        for alternative in self.alternatives[1:]:
            common &= set(alternative.terms)
        return max(common, key=len) if common else None


def parse_query(text: str) -> Query:
    """
    Read a query.

    Args:
        text: What the operator typed.

    Returns:
        The query.

    Raises:
        ValidationError: The text cannot be read (an open quote, too many
            words, a word too long, a level or a unit that is not one).
    """
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValidationError(
            "The search has a quote that is not closed",
            details="Close it, or use ' for a phrase that has a \" in it.",
            field="q",
        ) from exc
    apps: list[str] = []
    units: list[str] = []
    level: str | None = None
    alternatives: list[Alternative] = []
    terms: list[str] = []
    excluded: list[str] = []
    count = 0

    def close() -> None:
        if terms or excluded:
            alternatives.append(Alternative(tuple(terms), tuple(excluded)))
        terms.clear()
        excluded.clear()

    for piece in tokens:
        if piece == "OR":
            close()
            continue
        field_name, separator, value = piece.partition(":")
        if separator and field_name in ("app", "unit", "level") and value:
            if field_name == "app":
                apps.append(value.lower())
            elif field_name == "unit":
                if not _UNIT.fullmatch(value):
                    raise ValidationError(f"{value!r} is not a unit name", field="q")
                units.append(value)
            else:
                if value.lower() not in LEVELS:
                    raise ValidationError(
                        f"{value!r} is not a level",
                        details=f"The levels are: {', '.join(LEVEL_NAMES)}.",
                        field="q",
                    )
                level = LEVEL_NAMES[LEVELS[value.lower()]]
            continue
        negated = piece.startswith("-") and len(piece) > 1
        word = (piece[1:] if negated else piece).lower()
        if not word:
            continue
        if len(word) > MAX_TERM_LENGTH:
            raise ValidationError(
                f"A word of the search is over {MAX_TERM_LENGTH} characters", field="q"
            )
        count += 1
        if count > MAX_TERMS:
            raise ValidationError(f"The search has more than {MAX_TERMS} words", field="q")
        (excluded if negated else terms).append(word)
    close()
    return Query(
        tuple(alternatives), tuple(dict.fromkeys(apps)), tuple(dict.fromkeys(units)), level
    )


def parse_moment(text: str | float | None, now: float, default: float) -> float:
    """
    Read a moment: epoch seconds, a time ago, or a date.

    Args:
        text: ``1760600000``, ``15m``, ``2h``, ``7d`` (that long ago), or
            ``2026-10-16`` / ``2026-10-16T08:30:00`` (UTC).
        now: Epoch seconds now.
        default: What an empty value means.

    Returns:
        Epoch seconds.

    Raises:
        ValidationError: It is none of those.
    """
    if text is None or text == "":
        return default
    if isinstance(text, (int, float)):
        return float(text)
    raw = text.strip()
    match = _DURATION.fullmatch(raw)
    if match is not None:
        return now - int(match.group(1)) * _UNITS_OF_TIME[match.group(2)]
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(
            f"{text!r} is not a time",
            details="Use epoch seconds, a time ago such as 15m, 2h or 7d, or a date such as 2026-10-16.",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


@dataclass(frozen=True)
class LogLine:
    """
    One line of a log.

    Attributes:
        ts: Epoch seconds.
        app: The application, or empty for a system unit.
        source: ``journal`` or ``container``.
        unit: The unit, or the container's name.
        process: The application's process (``web``, ``worker``), if known.
        level: The journal's priority name, or None where there is none.
        message: The text, masked.
    """

    ts: float
    app: str
    source: str
    unit: str
    process: str
    level: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The line as the API shows it.
        """
        return {
            "ts": self.ts,
            "app": self.app,
            "source": self.source,
            "unit": self.unit,
            "process": self.process,
            "level": self.level,
            "message": self.message,
        }


@dataclass
class SearchResult:
    """
    What a search found.

    Attributes:
        lines: The matching lines, newest first, at most the limit.
        matched: How many lines matched in all (the first :data:`SCAN_LIMIT` examined).
        scanned: How many entries were examined.
        truncated: The scan stopped at its limit, so older matches may exist.
        since: Start of the window, epoch seconds.
        until: End of the window.
        notes: What was left out and why, in a sentence each.
    """

    lines: list[LogLine] = field(default_factory=list)
    matched: int = 0
    scanned: int = 0
    truncated: bool = False
    since: float = 0.0
    until: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result as the API shows it.
        """
        return {
            "lines": [line.to_dict() for line in self.lines],
            "matched": self.matched,
            "scanned": self.scanned,
            "truncated": self.truncated,
            "since": self.since,
            "until": self.until,
            "notes": list(self.notes),
        }


def process_names(apps: Sequence[App]) -> dict[str, set[str]]:
    """
    Name the side processes of some applications, to tell their units apart.

    Args:
        apps: Applications.

    Returns:
        Domain to the names of its declared processes.
    """
    from noust.core import app_process_store

    return {
        app.domain: {record.name for record in app_process_store.list_processes(app.domain)}
        for app in apps
        if not is_compose_backed(app.app_type)
    }


def _message(entry: dict[str, Any]) -> str | None:
    """
    Args:
        entry: One decoded journal entry.

    Returns:
        Its text; None when it has none.
    """
    value = entry.get("MESSAGE")
    if isinstance(value, list):
        try:
            value = bytes(value).decode("utf-8", errors="replace")
        except (TypeError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.rstrip("\n")
    return text if text.strip() else None


def _level(entry: dict[str, Any]) -> int:
    """
    Args:
        entry: One decoded journal entry.

    Returns:
        Its priority, 6 when it says nothing usable.
    """
    try:
        value = int(entry.get("PRIORITY", 6))
    except (TypeError, ValueError):
        return 6
    return value if 0 <= value <= 7 else 6


class LogSearch:
    """
    Searches the journal and the containers of some applications.

    Args:
        runner: Runs ``journalctl`` and ``docker``; the process-wide one by default.
        scrubber_for_app: Makes the scrubber of an application's secrets.
        clock: Epoch seconds.
    """

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        scrubber_for_app: Callable[[str], Scrubber] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._runner = runner
        self._scrubber_for = scrubber_for_app or scrubber_for
        self._clock = clock
        self._scrubbers: dict[str, Scrubber] = {}

    @property
    def runner(self) -> CommandRunner:
        """The command runner."""
        return self._runner or get_runner()

    def search(
        self,
        text: str,
        apps: Sequence[App],
        *,
        since: str | float | None = None,
        until: str | float | None = None,
        level: str | None = None,
        limit: int = DEFAULT_LIMIT,
        processes: dict[str, set[str]] | None = None,
        allow_system_units: bool = False,
    ) -> SearchResult:
        """
        Search.

        Args:
            text: The query.
            apps: The applications whose logs may be searched (the caller has
                already narrowed them to what the principal sees). ``app:`` in
                the query narrows further.
            since: Start of the window; an hour ago by default.
            until: End of the window; now by default.
            level: The worst level kept (also writable as ``level:`` in the query).
            limit: Most lines to return.
            processes: Domain to the names of its processes, to tell a worker's
                unit from another application's.
            allow_system_units: Whether ``unit:`` may name a system unit (only a
                caller that sees the whole server may).

        Returns:
            The lines, newest first, with how much was examined.

        Raises:
            ValidationError: The query, the window, the level or the limit is
                not acceptable, or ``unit:`` is used where it is not allowed.
        """
        now = self._clock()
        query = parse_query(text)
        chosen_level = level or query.level
        if chosen_level is not None and chosen_level.lower() not in LEVELS:
            raise ValidationError(
                f"{chosen_level!r} is not a level",
                details=f"The levels are: {', '.join(LEVEL_NAMES)}.",
                field="level",
            )
        if not 1 <= limit <= MAX_LIMIT:
            raise ValidationError(f"limit is a number from 1 to {MAX_LIMIT}", field="limit")
        start = parse_moment(since, now, now - 3_600)
        end = parse_moment(until, now, now)
        if end <= start:
            raise ValidationError("The window ends before it starts", field="until")
        if end - start > MAX_WINDOW_SECONDS:
            raise ValidationError(
                "The window is longer than the journal can hold",
                details=f"Ask for at most {MAX_WINDOW_SECONDS // 86_400} days at a time.",
                field="since",
            )
        if query.units and not allow_system_units:
            raise ValidationError(
                "Searching a system unit is for whoever sees the whole server",
                details="Search the applications you have, or ask an administrator.",
                field="q",
            )

        selected = [
            app
            for app in apps
            if not query.apps or any(wanted in app.domain.lower() for wanted in query.apps)
        ]
        result = SearchResult(since=start, until=end)
        if query.apps and not selected:
            result.notes.append("No application you can search matches " + ", ".join(query.apps))
        self._scrubbers = {}
        found: list[LogLine] = []
        journal_apps = [app for app in selected if not is_compose_backed(app.app_type)]
        if journal_apps or query.units:
            self._journal(
                query, journal_apps, processes or {}, start, end, chosen_level, result, found
            )
        stacks = [app for app in selected if is_compose_backed(app.app_type)]
        if stacks:
            if chosen_level is not None:
                result.notes.append(
                    "The containers of Docker stacks have no levels, so they are not searched "
                    f"for level:{chosen_level}."
                )
            else:
                for app in stacks:
                    self._containers(query, app, start, end, result, found)
        found.sort(key=lambda line: line.ts, reverse=True)
        result.matched = len(found)
        result.lines = found[:limit]
        return result

    # -- The journal ----------------------------------------------------------------------

    def _journal(
        self,
        query: Query,
        apps: Sequence[App],
        processes: dict[str, set[str]],
        start: float,
        end: float,
        level: str | None,
        result: SearchResult,
        found: list[LogLine],
    ) -> None:
        """
        Search the journal for the applications that run as units.

        Args:
            query: What was asked.
            apps: The applications to search.
            processes: Domain to its process names.
            start: Start of the window.
            end: End of the window.
            level: The worst level kept.
            result: Filled with what was examined and what went wrong.
            found: Filled with the lines.
        """
        from noust.managers.log_shipping import AppIndex

        index = AppIndex(list(apps), processes)
        argv = [
            "journalctl",
            "--no-pager",
            "--output=json",
            "--output-fields=MESSAGE,PRIORITY,_SYSTEMD_UNIT",
            "--reverse",
            f"--lines={SCAN_LIMIT + 1}",
            f"--since=@{int(start)}",
            f"--until=@{int(end)}",
        ]
        if level is not None:
            argv.append(f"--priority={LEVELS[level.lower()]}")
        term = query.first_term
        if term:
            argv += ["--case-sensitive=false", f"--grep={re.escape(term)}"]
        for app in apps:
            base = domain_to_app_name(app.domain)
            argv += ["-u", f"{base}*", "-u", f"wasm-{base}*"]
        for unit in query.units:
            argv += ["-u", unit]
        done = self.runner.run(argv, timeout=JOURNAL_TIMEOUT)
        if not done.success:
            reason = (done.stderr or done.stdout).strip().splitlines()
            # journalctl exits 1 for "no entries" on some versions; a real failure says something.
            if reason and "No entries" not in reason[0]:
                result.notes.append(f"The journal could not be searched: {reason[0][:200]}")
            return
        lines = done.stdout.splitlines()
        if len(lines) > SCAN_LIMIT:
            result.truncated = True
            lines = lines[:SCAN_LIMIT]
        for raw in lines:
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            result.scanned += 1
            text = _message(entry)
            if text is None:
                continue
            unit = str(entry.get("_SYSTEMD_UNIT") or "")
            located = index.lookup(unit)
            if located is None and unit not in query.units:
                continue
            domain, process = located if located is not None else ("", "")
            clean = self._clean(domain, text)
            if not query.matches(clean):
                continue
            try:
                ts = int(entry.get("__REALTIME_TIMESTAMP", 0)) / 1_000_000
            except (TypeError, ValueError):
                ts = 0.0
            found.append(
                LogLine(
                    ts=ts,
                    app=domain,
                    source="journal",
                    unit=unit,
                    process=process,
                    level=LEVEL_NAMES[_level(entry)],
                    message=clean,
                )
            )

    # -- A stack's containers -------------------------------------------------------------

    def _containers(
        self,
        query: Query,
        app: App,
        start: float,
        end: float,
        result: SearchResult,
        found: list[LogLine],
    ) -> None:
        """
        Search what the containers of a stack printed.

        Args:
            query: What was asked.
            app: A Compose or container application.
            start: Start of the window.
            end: End of the window.
            result: Filled with what was examined and what went wrong.
            found: Filled with the lines.
        """
        try:
            containers = find_stack_containers(app, self.runner)
        except NoustError as exc:
            # A stack that cannot be listed is a note on the answer, not a failure of it.
            result.notes.append(f"{app.domain}: its containers could not be listed ({exc.message})")
            return
        for container in containers:
            done = self.runner.run(
                [
                    "docker",
                    "logs",
                    "--timestamps",
                    f"--since={int(start)}",
                    f"--until={int(end)}",
                    f"--tail={CONTAINER_TAIL}",
                    container.id,
                ],
                timeout=DOCKER_TIMEOUT,
            )
            if not done.success:
                result.notes.append(
                    f"{app.domain}: {container.service} could not be read "
                    f"({(done.stderr or '').strip()[:120] or 'docker logs failed'})"
                )
                continue
            # docker writes a container's stderr to its own stderr.
            for raw in (done.stdout + "\n" + done.stderr).splitlines():
                match = _CONTAINER_LINE.fullmatch(raw)
                if match is None:
                    continue
                result.scanned += 1
                clean = self._clean(app.domain, match.group("text"))
                if not clean.strip() or not query.matches(clean):
                    continue
                found.append(
                    LogLine(
                        ts=_epoch(match.group("ts")),
                        app=app.domain,
                        source="container",
                        unit=container.service,
                        process=container.service,
                        level=None,
                        message=clean,
                    )
                )

    # -- Masking ---------------------------------------------------------------------------

    def _clean(self, domain: str, text: str) -> str:
        """
        Take the colour, the application's secrets and anything shaped like a
        credential out of a line.

        Args:
            domain: The application, or empty for a system unit.
            text: The line.

        Returns:
            The line that may be shown.
        """
        scrubber = self._scrubbers.get(domain)
        if scrubber is None:
            scrubber = self._scrubbers[domain] = self._scrubber_for(domain)
        return mask_secret_shapes(scrubber.scrub(_ANSI.sub("", text)))


def _epoch(stamp: str) -> float:
    """
    Args:
        stamp: ``2026-10-13T10:00:00.123456789Z``.

    Returns:
        Epoch seconds; 0 when it is not one.
    """
    whole, _, fraction = stamp.rstrip("Z").partition(".")
    try:
        moment = datetime.fromisoformat(whole).replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    return moment.timestamp() + (float("0." + fraction[:6]) if fraction.isdigit() else 0.0)
