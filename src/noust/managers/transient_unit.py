# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A transient systemd unit, asked from outside: is it running, how did it end, what did it write.

Work that has to outlive the console runs in a ``systemd-run`` unit of its own:
operating system updates (:mod:`noust.managers.server.updates_unit`) and Noust
updating itself (:mod:`noust.managers.self_update`). The ``noust`` package's
post-install script restarts ``noust-web``, so the console that started such a
unit is routinely not the one that sees it end. This module is the one reading
of such a unit, shared by the code that follows one while it runs and by the
reconciliation that finds one again after a restart
(:mod:`noust.web.job_reconcile`), so the two cannot disagree on what "ended
well" means.

How it ended is read from two places, because the units are started with
``--collect`` and systemd forgets them the moment they end: ``systemctl show``
while it still knows the unit (``Result``, ``ExecMainStatus``), and otherwise
the line systemd itself wrote to the unit's journal when it ended, found by its
catalogued ``MESSAGE_ID`` and, for a journal read as text, by its words.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from noust.core.runner import CommandRunner

if TYPE_CHECKING:
    from noust.core.store import JobRecord

#: What systemd calls a unit that is still doing something.
ACTIVE_STATES = frozenset({"active", "activating", "deactivating", "reloading", "refreshing"})

#: The properties :func:`unit_state` asks for.
SHOW_PROPERTIES = "LoadState,ActiveState,SubState,Result,ExecMainStatus"

#: Seconds ``systemctl show`` may take.
SHOW_TIMEOUT = 20

#: Seconds reading a unit's journal may take.
JOURNAL_TIMEOUT = 30

#: systemd's catalogued message "Deactivated successfully" (``SD_MESSAGE_UNIT_SUCCESS``).
UNIT_SUCCESS_ID = "7ad2d189f7e94e70a38c781354912448"

#: systemd's catalogued message "Failed with result ..." (``SD_MESSAGE_UNIT_FAILURE_RESULT``).
UNIT_FAILURE_ID = "d9b373ed55a64feb8242e02dbe79a49c"

#: Lines of a unit's output a failure carries in its error.
ERROR_TAIL_LINES = 40

#: How long a job whose kind sets no deadline waits for a unit still running.
DEFAULT_MAX_SECONDS = 3600.0

Status = Literal["running", "succeeded", "failed", "unknown"]


@dataclass(frozen=True)
class UnitState:
    """
    What systemd says about a transient unit.

    Attributes:
        loaded: systemd still knows the unit. A unit started with ``--collect``
            is forgotten the moment it ends, so False means "it ended".
        active: The unit is running or starting.
        sub_state: systemd's finer state (``running``, ``dead``, ``failed``).
        result: systemd's verdict on how it ended (``success``, ``exit-code``,
            ``timeout``, ``signal``).
        exit_status: The main process's exit status, when it has one.
        known: False when systemd did not answer (``systemctl show`` failed or
            timed out): nothing above is then true or false, and a caller must
            not read "not active" as "it ended".
    """

    loaded: bool
    active: bool
    sub_state: str = ""
    result: str = ""
    exit_status: int | None = None
    known: bool = True


@dataclass(frozen=True)
class JournalEntry:
    """
    One line a unit's journal holds.

    Attributes:
        cursor: The journal's position after it; empty for a journal read as text.
        message: The line, verbatim.
        message_id: systemd's catalogue identifier, for systemd's own lines.
        unit_result: The ``UNIT_RESULT`` systemd attached to a failure line.
    """

    cursor: str
    message: str
    message_id: str = ""
    unit_result: str = ""


@dataclass(frozen=True)
class UnitEnding:
    """
    How a unit ended, or that it has not.

    Attributes:
        status: ``running``; ``succeeded`` or ``failed``; ``unknown`` when it
            ended and neither systemd nor its journal says how any more.
        result: systemd's ``Result`` (``success``, ``exit-code``, ``timeout``...).
        exit_status: Its main process's exit status, when known.
    """

    status: Status
    result: str = ""
    exit_status: int | None = None

    @property
    def ended(self) -> bool:
        """Whether the unit is over, whatever the outcome."""
        return self.status != "running"


@dataclass(frozen=True)
class JobVerdict:
    """
    What a console job that ran in a unit comes to, once the unit ended.

    Attributes:
        succeeded: The job completed.
        result: What the job returns, as its function would have.
        error: Why it failed: the sentence, then the unit's own words.
    """

    succeeded: bool
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class UnitJobKind:
    """
    How one type of console job whose work runs in a unit is reconciled after a restart.

    Attributes:
        find_unit: Finds the unit of a job that did not record one (started by
            an older console), in the kind's own record.
        verdict: Decides the job from the unit's ending and everything it
            wrote; systemd's verdict alone when omitted.
        max_seconds: How long a unit still running is waited for.
        audit_event: The audit event the job's own function records when it
            ends (``server.update`` for an update), so a job finished from its
            unit leaves the same line in the trail; None records nothing.
        audit_target: What that event is about (``packages``).
        audit_details: The event's details beside how it ended, from the job
            and its unit, as the function gives them.
    """

    find_unit: Callable[[JobRecord], str | None] | None = None
    verdict: Callable[[JobRecord, str, UnitEnding, list[str]], JobVerdict] | None = None
    max_seconds: float = DEFAULT_MAX_SECONDS
    audit_event: str | None = None
    audit_target: str | None = None
    audit_details: Callable[[JobRecord, str], dict[str, Any]] | None = None


def parse_show(text: str) -> UnitState:
    """
    Read the output of ``systemctl show`` for the properties this asks for.

    Args:
        text: ``Key=Value`` lines.

    Returns:
        The state; a unit systemd has forgotten reads as not loaded.
    """
    fields = dict(line.partition("=")[::2] for line in text.splitlines() if "=" in line)
    status = fields.get("ExecMainStatus", "")
    return UnitState(
        loaded=fields.get("LoadState", "not-found") != "not-found",
        active=fields.get("ActiveState", "inactive") in ACTIVE_STATES,
        sub_state=fields.get("SubState", ""),
        result=fields.get("Result", ""),
        exit_status=int(status) if status.lstrip("-").isdigit() else None,
    )


def decode_message(value: object) -> str | None:
    """
    Turn a journal entry's ``MESSAGE`` into text.

    Args:
        value: What ``journalctl -o json`` put there: a string, or a list of
            byte values when the message was not valid UTF-8.

    Returns:
        The text, or None when the entry has no message.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, int) for item in value):
        return bytes(item % 256 for item in value).decode("utf-8", errors="replace")
    return None


def parse_journal_entries(text: str) -> list[JournalEntry]:
    """
    Read ``journalctl -o json`` output, one JSON object per line.

    Args:
        text: The output.

    Returns:
        Every entry that has a cursor and a message. A line that is not JSON is
        skipped, not fatal: it is the journal being read while it is written to.
    """
    entries: list[JournalEntry] = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        message = decode_message(entry.get("MESSAGE"))
        cursor = entry.get("__CURSOR")
        if message is None or not isinstance(cursor, str):
            continue
        message_id = entry.get("MESSAGE_ID")
        result = entry.get("UNIT_RESULT")
        entries.append(
            JournalEntry(
                cursor=cursor,
                message=message,
                message_id=message_id if isinstance(message_id, str) else "",
                unit_result=result if isinstance(result, str) else "",
            )
        )
    return entries


def parse_journal(text: str) -> list[tuple[str, str]]:
    """
    Read ``journalctl -o json`` output as ``(cursor, message)`` pairs.

    Args:
        text: The output.

    Returns:
        ``(cursor, message)`` for every entry that has both.
    """
    return [(entry.cursor, entry.message) for entry in parse_journal_entries(text)]


def entries_from_lines(lines: Sequence[str]) -> list[JournalEntry]:
    """
    Wrap lines of a journal read as text, so :func:`ending_of` can read them.

    Args:
        lines: ``journalctl --output=cat`` lines.

    Returns:
        One entry per line, without cursors or identifiers.
    """
    return [JournalEntry(cursor="", message=line) for line in lines]


def unit_state(runner: CommandRunner, unit: str) -> UnitState:
    """
    Ask systemd about a unit.

    Args:
        runner: The command runner.
        unit: The unit's name, without ``.service``.

    Returns:
        Its state. When systemd did not answer the state is not ``known``: an
        empty or failed ``show`` parsed as "inactive" once declared running
        updates over.
    """
    result = runner.run(
        ["systemctl", "show", f"{unit}.service", f"--property={SHOW_PROPERTIES}"],
        timeout=SHOW_TIMEOUT,
    )
    if not result.success or "LoadState=" not in result.stdout:
        return UnitState(loaded=True, active=True, known=False)
    return parse_show(result.stdout)


def read_journal(
    runner: CommandRunner, unit: str, *, after_cursor: str | None = None
) -> list[JournalEntry]:
    """
    Read what a unit wrote, systemd's own lines about it included.

    Args:
        runner: The command runner.
        unit: The unit's name, without ``.service``.
        after_cursor: Only what came after this position.

    Returns:
        The entries, oldest first.
    """
    argv = ["journalctl", f"--unit={unit}.service", "--output=json", "--no-pager", "--all"]
    if after_cursor:
        argv.append(f"--after-cursor={after_cursor}")
    result = runner.run(argv, timeout=JOURNAL_TIMEOUT)
    return parse_journal_entries(result.stdout)


def _result_in(message: str) -> str:
    """
    Pull systemd's result out of its "Failed with result" line.

    Args:
        message: The line.

    Returns:
        The result (``exit-code``), or empty when the line does not name one.
    """
    _, _, rest = message.partition("Failed with result '")
    return rest.partition("'")[0]


def ending_of(unit: str, state: UnitState, entries: Sequence[JournalEntry]) -> UnitEnding:
    """
    Decide how a unit ended, from what systemd and its journal say.

    Args:
        unit: The unit's name, without ``.service``.
        state: What ``systemctl show`` said.
        entries: The unit's journal; its last ending line is the one that counts.

    Returns:
        ``running`` while it runs, or while systemd does not answer; the
        verdict once it ended; ``unknown`` for a unit that ended and left no
        word of how (its journal rotated away).
    """
    if not state.known or state.active:
        return UnitEnding("running")
    if state.loaded and state.result:
        status: Status = "succeeded" if state.result == "success" else "failed"
        return UnitEnding(status, state.result, state.exit_status)
    # systemd's own lines start with the unit's name; a line the program
    # printed that happens to read "Succeeded." is not systemd's verdict.
    own = f"{unit}.service: "
    for entry in reversed(entries):
        text = entry.message
        if entry.message_id == UNIT_SUCCESS_ID or (
            text.startswith(own)
            and text.rstrip(".").endswith(("Deactivated successfully", "Succeeded"))
        ):
            return UnitEnding("succeeded", "success", 0)
        if entry.message_id == UNIT_FAILURE_ID or (
            text.startswith(own) and "Failed with result" in text
        ):
            return UnitEnding("failed", entry.unit_result or _result_in(text))
    return UnitEnding("unknown")


def default_verdict(unit: str, ending: UnitEnding, lines: Sequence[str]) -> JobVerdict:
    """
    Turn a unit's ending into its job's, for a job with nothing better to read.

    Args:
        unit: The unit's name.
        ending: How it ended.
        lines: Everything it wrote.

    Returns:
        Success for a unit systemd says succeeded; a failure carrying the
        unit's last lines otherwise.
    """
    if ending.status == "succeeded":
        return JobVerdict(True, result={"unit": unit, "result": "success", "exit_status": 0})
    if ending.status == "failed":
        status = f", exit status {ending.exit_status}" if ending.exit_status is not None else ""
        message = f"{unit} failed (systemd's result: {ending.result or 'unknown'}{status})"
    else:
        message = (
            f"{unit} ended and systemd no longer says how: its journal holds no ending "
            "line (rotated away, or the machine restarted)"
        )
    return JobVerdict(False, error=with_output(message, lines))


def with_output(message: str, lines: Sequence[str]) -> str:
    """
    Put a failure's sentence above the unit's own last words, as a job error is shown.

    Args:
        message: The sentence.
        lines: The unit's output.

    Returns:
        The sentence, then the last :data:`ERROR_TAIL_LINES` lines verbatim.
    """
    tail = "\n".join(lines[-ERROR_TAIL_LINES:]).rstrip()
    return f"{message}\n\n{tail}" if tail else message
