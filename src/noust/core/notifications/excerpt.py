# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turning another program's output into the short piece a notification shows.

A failed deploy used to put its whole evidence into the message: forty lines
of journal, four restart cycles of the same five lines, four kilobytes that
Telegram and Discord then cut at the *end*, which is where the console link
was. The rules here are the opposite ones:

- **Whole lines, verbatim.** A line is either shown as it came or, when it is
  absurdly long, cut with an ellipsis. Never reworded, never merged: what
  Noust does not say in its own words it does not paraphrase either.
- **The end of the output.** A process says why it died last - except when
  it does not: a Node service that throws prints its error, a dozen stack
  frames and its version, and systemd then adds five lines of its own, so the
  last twelve lines never say what broke. When asked (``pin_error``), the
  first line that states an error is kept above the end, and the renderers
  mark it; it is the program's own line, moved, never reworded.
- **Nothing twice.** Blank lines go, consecutive identical lines fold into
  one, and a line Noust has already said in its own words is not repeated.
- **A budget.** :func:`make_excerpt` bounds lines and characters;
  :func:`trim_excerpt` lets a channel with less room take fewer, always from
  the top.

Two parsers live here because they read formats other modules own:
:func:`split_evidence` reads what the health gate writes
(``deployers/helpers/health_gate.py``) and :func:`split_error_text` what
``str(NoustError)`` prints; a test builds each with the real code, so the two
cannot drift apart unseen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from noust.core.notifications.model import Excerpt, clean_line

#: Lines and characters of an excerpt in a channel with room (email, webhook).
#: A chat renderer trims further with :func:`trim_excerpt`.
DEFAULT_MAX_LINES = 12
DEFAULT_MAX_CHARS = 1200

#: A line longer than this is cut with an ellipsis: a minified stack trace or a
#: base64 blob says nothing more in its four-thousandth character.
DEFAULT_MAX_LINE = 200

_ELLIPSIS = "\u2026"

_NON_WORD = re.compile(r"\W+")


def normalize(text: str) -> str:
    """
    Reduce text to what a reader would call "the same sentence".

    Args:
        text: Any text.

    Returns:
        Lower case, punctuation and runs of space collapsed.
    """
    return _NON_WORD.sub(" ", text).strip().lower()


def _clip(line: str, limit: int) -> str:
    """
    Cut one line to a length.

    Args:
        line: The line.
        limit: Its longest length, ellipsis included.

    Returns:
        The line, or its start and an ellipsis.
    """
    return line if len(line) <= limit else line[: max(limit - 1, 0)] + _ELLIPSIS


def _fold(lines: Iterable[str]) -> list[str]:
    """
    Drop blank lines and fold consecutive identical ones.

    Args:
        lines: Cleaned lines.

    Returns:
        The lines that carry something.
    """
    kept: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        if kept and kept[-1] == line:
            continue
        kept.append(line)
    return kept


# What a program writes when it states an error, in its own words. Deliberately
# narrow: a line matched here is shown above everything else, so a pattern
# that also matches ordinary chatter ("0 errors", systemd's "Failed with
# result") would pin noise. Matched against the message, after the journal's
# date, host and process.
_ERROR_LINE = re.compile(
    # Error: ..., TypeError [ERR_X]: ..., java.io.IOException: ...
    r"\b\w*(?:Error|Exception)(?: \[[\w.-]+\])?: \S"
    r"|^Exception in thread "
    r"|\bpanic: "
    r"|\bFATAL\b"
    r"|\bFatal error\b"
    r"|\bERR! "
    r"|\bCannot find module\b"
    r"|\bEADDRINUSE\b"
)

# Python states the exception after its traceback, and not every exception is
# called *Error: the first unindented line after the heading is the one.
_TRACEBACK = re.compile(r"^Traceback \(most recent call last\):")

# The lines after an error that are its stack, not its message: Node and Java
# (at ...), Python (File "..."), PHP (#0), Go (goroutine), a caret.
_STACK_FRAME = re.compile(r"^\s*(?:at\s|File \"|#\d|goroutine\s|\.\.\.\s|\^)")

# "node[73120]: " after the journal's date and host.
_PROCESS = re.compile(r"[\w.@/-]+(?:\[\d+\])?: ?")


def _message(line: str) -> tuple[str, str]:
    """
    Split a line into who wrote it and what it says.

    Args:
        line: A cleaned line.

    Returns:
        ``(source, message)``: the process after the journal's date and host,
        and the rest. A line without a journal prefix is all message.
    """
    stamp = _JOURNAL_PREFIX.match(line)
    if stamp is None:
        return "", line
    process = _PROCESS.match(line, stamp.end())
    end = process.end() if process is not None else stamp.end()
    return line[stamp.end() : end].strip(), line[end:]


def _first_error(lines: list[str]) -> tuple[int, int] | None:
    """
    Find the first line that states an error, and whether the next continues it.

    Args:
        lines: Cleaned, folded lines, oldest first.

    Returns:
        ``(index, count)``: the line, and how many lines its message takes
        (one, or two when the next line of the same process continues it
        rather than starting its stack), or None when no line states an
        error.
    """
    for index, line in enumerate(lines):
        source, text = _message(line)
        if _TRACEBACK.match(text):
            for after in range(index + 1, len(lines)):
                later_source, later = _message(lines[after])
                if later_source == source and later.strip() and not later[:1].isspace():
                    return after, 1
            return index, 1
        if not _ERROR_LINE.search(text):
            continue
        if index + 1 < len(lines):
            next_source, following = _message(lines[index + 1])
            continues = text.rstrip().endswith(":") or following[:1].isspace()
            if (
                continues
                and next_source == source
                and following.strip()
                and not _STACK_FRAME.match(following)
                and not _ERROR_LINE.search(following)
            ):
                return index, 2
        return index, 1
    return None


def _chars(lines: list[str]) -> int:
    """
    Measure lines as an excerpt shows them.

    Args:
        lines: Lines.

    Returns:
        Their length, one newline each.
    """
    return sum(len(line) + 1 for line in lines)


def _tail(lines: list[str], max_lines: int, max_chars: int, *, may_be_empty: bool) -> list[str]:
    """
    Take the end of some lines within a budget.

    Args:
        lines: The lines, oldest first.
        max_lines: The most lines to take.
        max_chars: The most characters to take.
        may_be_empty: Whether nothing is an answer, because something else
            is shown; otherwise the last line is kept even over the budget.

    Returns:
        The last lines that fit.
    """
    picked = lines[-max_lines:] if max_lines > 0 else []
    while len(picked) > 1 and _chars(picked) > max_chars:
        picked = picked[1:]
    if picked and _chars(picked) > max_chars and may_be_empty:
        picked = []
    return picked


def make_excerpt(
    source: str | Iterable[str],
    *,
    label: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_line: int = DEFAULT_MAX_LINE,
    avoid: Iterable[str] = (),
    lead: str | None = None,
    pin_error: bool = False,
) -> Excerpt | None:
    """
    Take the end of some output as an :class:`Excerpt`.

    Args:
        source: The output, as text or as lines.
        label: What it is, already translated.
        max_lines: The most lines to keep (the ``lead`` counts as one).
        max_chars: The most characters to keep across the kept lines.
        max_line: The longest a line may be before it is cut.
        avoid: Sentences Noust already said in its own words; a line that
            reads the same (:func:`normalize`) is left out.
        lead: A line to put first whatever the budget, such as the failing
            step's own one-line message when it says something the summary
            does not.
        pin_error: Keep the first line that states an error (and the next,
            when it continues the message) right after ``lead``, when the end
            of the output does not already say it. The rest of the budget is
            the end of the output, as without it.

    Returns:
        The excerpt, or None when nothing is left to show.
    """
    raw = source.splitlines() if isinstance(source, str) else list(source)
    lines = [clean_line(part) for chunk in raw for part in str(chunk).splitlines() or [""]]
    skipped = {normalize(text) for text in avoid} - {""}
    lines = [line for line in _fold(lines) if normalize(line) not in skipped]
    lines = [_clip(line, max_line) for line in lines]

    lead_line = _clip(clean_line(lead), max_line) if lead and lead.strip() else None
    if lead_line is not None:
        max_lines -= 1
        max_chars -= len(lead_line) + 1
    if not lines and lead_line is None:
        return None

    picked = _tail(lines, max_lines, max_chars, may_be_empty=lead_line is not None)
    cause: list[str] = []
    if pin_error:
        cause, picked = _pin_first_error(lines, picked, max_lines, max_chars)

    head = [lead_line] if lead_line is not None else []
    return Excerpt(
        label=label,
        lines=(*head, *cause, *picked),
        omitted=len(lines) - len(cause) - len(picked),
        pinned=len(head) + len(cause) if cause else 0,
    )


def _pin_first_error(
    lines: list[str], picked: list[str], max_lines: int, max_chars: int
) -> tuple[list[str], list[str]]:
    """
    Bring the output's first error above its end, when the end does not say it.

    Args:
        lines: Every line, oldest first.
        picked: The end of them that fits the budget without the error.
        max_lines: The budget in lines, the lead already taken out.
        max_chars: The budget in characters, the lead already taken out.

    Returns:
        ``(cause, tail)``: the error's lines (empty when nothing is brought
        up) and the end of the output that still fits beside them.
    """
    start = len(lines) - len(picked)
    found = _first_error(lines[:start])
    if found is None:
        return [], picked
    index, count = found
    cause = lines[index : min(index + count, start)]
    # The same error said again at the end, as a restart loop does every
    # cycle, is already shown: only the process id and the time differ.
    said = {normalize(_message(line)[1]) for line in picked}
    if normalize(_message(cause[0])[1]) in said or len(cause) > max_lines:
        return [], picked
    tail = _tail(lines, max_lines - len(cause), max_chars - _chars(cause), may_be_empty=True)
    if index + len(cause) == len(lines) - len(tail):
        # Nothing between the error and the end: that is the end, as it came.
        return [], [*cause, *tail]
    return cause, tail


def trim_excerpt(
    excerpt: Excerpt | None,
    *,
    max_lines: int,
    max_chars: int,
    max_line: int = DEFAULT_MAX_LINE,
) -> Excerpt | None:
    """
    Fit an excerpt into a channel's smaller budget, from the top.

    Args:
        excerpt: The excerpt, or None.
        max_lines: The most lines the channel shows.
        max_chars: The most characters across them.
        max_line: The longest a line may be; a longer one is cut with an
            ellipsis, so an excerpt built by hand with one enormous line is
            shortened rather than lost.

    Returns:
        The same excerpt when it fits, the end of it with the count of what
        was dropped added otherwise, or None when nothing fits. Its pinned
        lines (:attr:`Excerpt.pinned`) are dropped last, the first of them
        first.
    """
    if excerpt is None:
        return None
    lines = [_clip(line, max_line) for line in excerpt.lines]
    pinned = min(excerpt.pinned, len(lines))
    while lines and (len(lines) > max_lines or _chars(lines) > max_chars):
        if len(lines) > pinned:
            lines.pop(pinned)
        else:
            lines.pop(0)
            pinned -= 1
    if not lines:
        return None
    dropped = len(excerpt.lines) - len(lines)
    if dropped == 0 and tuple(lines) == excerpt.lines:
        return excerpt
    return replace(excerpt, lines=tuple(lines), omitted=excerpt.omitted + dropped, pinned=pinned)


# "Sep 29 10:45:30 web-1 " (journalctl's default) and
# "2026-09-29T10:45:30+0000 web-1 " (short-iso).
_JOURNAL_PREFIX = re.compile(
    r"^(?:[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d"
    r"|\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[+-]\d\d:?\d\d|Z)?) \S+ "
)


def strip_journal_prefix(lines: tuple[str, ...]) -> tuple[str, ...]:
    """
    Drop the date and host from journal lines, when every line has them.

    For a channel that does not wrap code (Telegram's ``pre``): on a phone a
    line is cut at the edge of the screen, and the date and host - which the
    message's own header already gives - are what pushes the part that
    matters off it. All or nothing: a stack trace among the lines has no
    prefix, and half a column is worse than none.

    Args:
        lines: Excerpt lines.

    Returns:
        The lines without their prefix, or unchanged.
    """
    if lines and all(_JOURNAL_PREFIX.match(line) for line in lines):
        return tuple(_JOURNAL_PREFIX.sub("", line, count=1) for line in lines)
    return lines


@dataclass(frozen=True)
class EvidenceParts:
    """
    A failed health gate's evidence, taken apart.

    Attributes:
        summary: What came before the journal: the failing step's own words
            and the probes.
        unit: The unit whose journal follows, or None when there is none.
        journal: The journal lines.
        trailing: What came after the journal (a deployer's own notes).
    """

    summary: str
    unit: str | None
    journal: tuple[str, ...]
    trailing: str


# HealthGate.evidence() writes this heading, then the journal, then (for the
# deployers that add notes) a blank line and more text.
_JOURNAL_HEADING = re.compile(r"^Last lines of the journal of (?P<unit>\S+):[ \t]*$", re.MULTILINE)


def split_evidence(text: str) -> EvidenceParts:
    """
    Separate a health gate's journal from the rest of its evidence.

    Args:
        text: ``NoustError.details`` (or ``output``) of a failed gate.

    Returns:
        The parts; without a journal heading everything is the summary.
    """
    match = _JOURNAL_HEADING.search(text)
    if match is None:
        return EvidenceParts(text.strip(), None, (), "")
    head = text[: match.start()].strip()
    rest = text[match.end() :].lstrip("\n")
    journal, _, trailing = rest.partition("\n\n")
    lines = tuple(line for line in journal.splitlines() if line.strip())
    return EvidenceParts(head, match.group("unit"), lines, trailing.strip())


_DETAILS_MARKER = "\n  Details: "


def split_error_text(text: str | None) -> tuple[str, str]:
    """
    Take a failure's text apart into its message and the rest.

    ``str(NoustError)`` is ``message`` or ``message\\n  Details: details``, and
    a console job appends the tool's ``output`` after a blank line. The
    message is Noust's own sentence; everything after it is somebody else's
    words or a fix.

    Args:
        text: A failure's text, or None.

    Returns:
        ``(message, output)``; ``output`` is empty when there is only a
        message.
    """
    if not text or not text.strip():
        return "", ""
    body = text.strip()
    message, marker, rest = body.partition(_DETAILS_MARKER)
    if marker:
        return message.strip(), rest.strip()
    first, _, remainder = body.partition("\n")
    return first.strip(), remainder.strip()
