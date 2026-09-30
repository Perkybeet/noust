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
- **The end of the output.** A process says why it died last.
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


def make_excerpt(
    source: str | Iterable[str],
    *,
    label: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_line: int = DEFAULT_MAX_LINE,
    avoid: Iterable[str] = (),
    lead: str | None = None,
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

    total = len(lines)
    picked = lines[-max_lines:] if max_lines > 0 else []
    while len(picked) > 1 and sum(len(line) + 1 for line in picked) > max_chars:
        picked = picked[1:]
    if picked and sum(len(line) + 1 for line in picked) > max_chars and lead_line is not None:
        picked = []
    if lead_line is not None:
        picked = [lead_line, *picked]
    return Excerpt(
        label=label, lines=tuple(picked), omitted=total - (len(picked) - bool(lead_line))
    )


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
        was dropped added otherwise, or None when nothing fits.
    """
    if excerpt is None:
        return None
    lines = [_clip(line, max_line) for line in excerpt.lines]
    while lines and (len(lines) > max_lines or sum(len(line) + 1 for line in lines) > max_chars):
        lines.pop(0)
    if not lines:
        return None
    dropped = len(excerpt.lines) - len(lines)
    if dropped == 0 and tuple(lines) == excerpt.lines:
        return excerpt
    return replace(excerpt, lines=tuple(lines), omitted=excerpt.omitted + dropped)


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
