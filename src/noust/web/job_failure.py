# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a job's failure is read from the exception that ended it (item 151).

The error a job keeps is the one line ``str(exc)`` makes. The sentence behind it, when the exception
carried one (:attr:`noust.core.exceptions.NoustError.coded`), travels beside it as a code and its
values, scrubbed of the job's secrets like everything else the job keeps, with the failing
program's own words apart: the console says the sentence in its language and shows those as they
came.
"""

from __future__ import annotations

from noust.core.exceptions import NoustError
from noust.core.redact import Scrubber
from noust.core.server_text import ServerText
from noust.web.job_log import scrub_sentence

__all__ = ["error_text", "failure_sentence"]


def error_text(exc: BaseException) -> str:
    """
    Render a failure for a job's ``error``, keeping a tool's own output.

    ``str()`` of a :class:`NoustError` carries its message and fix but not its
    ``output``: certbot's or nginx's own words, which the job page shows
    verbatim under the diagnosis and which a job otherwise lost.

    Args:
        exc: The failure.

    Returns:
        The message, then the fix, then the tool's output, as present.
    """
    if isinstance(exc, NoustError) and exc.output:
        return f"{exc}\n\n{exc.output.rstrip()}"
    return str(exc)


def failure_sentence(
    exc: BaseException, scrubber: Scrubber
) -> tuple[ServerText | None, str | None]:
    """
    Args:
        exc: What ended the job.
        scrubber: The job's, which knows what is secret in what it works on.

    Returns:
        The sentence of the catalog the failure was and the failing program's own words; both None
        for an exception that carries no sentence (a defect, or an error nobody gave a code), whose
        only words are the job's ``error``.
    """
    if not isinstance(exc, NoustError) or exc.coded is None:
        return None, None
    output = scrubber.scrub(exc.output.rstrip()) if exc.output else None
    return scrub_sentence(exc.coded, scrubber), output
