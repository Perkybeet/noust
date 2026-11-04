# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One line of a job's log, and the sentence a step or a line may say with a code.

A job reports what it is doing as text (``JobContext.update("Creating backup", 20)``), which a
console in another language could only show as it came. A step or a line that is one of the
server's own sentences carries its code and its values beside the English text
(:mod:`noust.core.server_text`), so the console says it in its own language; what a tool printed
travels as it came, with no code.

Kept beside :mod:`noust.web.jobs`, whose module is past the size a module should have.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from noust.core.redact import Scrubber
from noust.core.server_text import Param, ServerText, say


@dataclass
class JobLogEntry:
    """
    A single log entry for a job.

    Attributes:
        timestamp: When the entry was recorded.
        level: One of ``info``, ``warning``, ``error`` or ``success``.
        message: The message itself.
        step: Progress value at the time of the entry.
        coded: ``message`` as a sentence with a code, for the console to translate; None for a
            line nobody gave a code (a tool's own output, a name somebody typed).
    """

    timestamp: datetime
    level: str
    message: str
    step: int | None = None
    coded: ServerText | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the entry as JSON-serialisable data.

        Returns:
            The entry as a dictionary.
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "message": self.message,
            "step": self.step,
            "coded": self.coded.to_dict() if self.coded is not None else None,
        }


def scrub_sentence(sentence: ServerText, scrubber: Scrubber) -> ServerText:
    """
    Take a job's secrets out of a sentence, in its text and in its values.

    A log line is scrubbed before it is kept anywhere, and the values of a sentence are part of
    what is kept: a console that puts them in its own words must not be handed one that the
    English text no longer says.

    Args:
        sentence: What the job says.
        scrubber: The job's, which knows what is secret in what it works on.

    Returns:
        The same sentence, with every text value scrubbed.
    """
    values: dict[str, Param] = {
        name: scrubber.scrub(value) if isinstance(value, str) else value
        for name, value in sentence.params.items()
    }
    return say(sentence.code, **values)
