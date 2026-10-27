# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A value in a log line cannot start another line.

Noust logs names, domains and sessions that a request supplied. A name that holds a line break
writes a second, forged line into the journal (CWE-117), and one that holds ``session=`` writes
a forged actor into the audit trail (the bridge in :mod:`noust.core.audit.bridge` reads its
lines). Escaping at each of the sixty call sites is how one gets forgotten, so it is done where
every line is made: the record. :func:`install` makes every record of the process report its
message with line breaks and control characters written out (``\\n``, ``\\x1b``), whoever logged
it and whichever handler writes it. A traceback is the formatter's, not the message's, and
keeps its lines.

:mod:`noust.core.logger` calls :func:`install` when it is imported, which every Noust process
does; the handlers and the arguments of a record are left as they were.
"""

from __future__ import annotations

import logging
import re

__all__ = ["install", "is_installed", "neutralize"]

#: Every control character but the tab, and the Unicode line and paragraph separators.
_UNSAFE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f  ]")

_NAMED = {"\n": "\\n", "\r": "\\r"}


def _written_out(match: re.Match[str]) -> str:
    """
    Spell one character the way a person reading the log can see it.

    Args:
        match: A character :data:`_UNSAFE` found.

    Returns:
        ``\\n``, ``\\r``, ``\\xNN`` or ``\\uNNNN``.
    """
    char = match.group()
    if char in _NAMED:
        return _NAMED[char]
    code = ord(char)
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}"


def neutralize(text: str) -> str:
    """
    Write out the characters that end or forge a log line.

    Args:
        text: A message, or a value that goes into one.

    Returns:
        The text with line breaks and other control characters written out as escapes. Applying
        it again changes nothing.
    """
    return _UNSAFE.sub(_written_out, text)


class _NeutralRecord(logging.LogRecord):
    """A record whose message is safe to write as one line."""

    def getMessage(self) -> str:  # noqa: N802 - the name logging calls
        """
        Return the message, formatted with its arguments and neutralized.

        Returns:
            The single-line message.
        """
        return neutralize(super().getMessage())


def is_installed() -> bool:
    """
    Tell whether the process's records are neutralized.

    Returns:
        True once :func:`install` has run and nobody replaced the factory since.
    """
    return bool(getattr(logging.getLogRecordFactory(), "_noust_neutral", False))


def install() -> None:
    """
    Make every record of this process neutralize its message. Idempotent.

    A factory somebody else installed first keeps making the records; this one only changes the
    kind of record an ordinary one becomes.
    """
    if is_installed():
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)  # type: ignore[arg-type]
        if type(record) is logging.LogRecord:
            record.__class__ = _NeutralRecord
        return record

    factory._noust_neutral = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)
