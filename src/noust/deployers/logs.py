# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deployment log reading: the one place a deployment's captured build log is
read back off disk.

:class:`~noust.deployers.recorder.DeploymentRecorder` writes the log and stores
its path on the history row; this module is the only reader, used by both the
server-rendered deployment detail page (:mod:`noust.web.views.deployments`) and
the JSON API (:mod:`noust.web.api.deployments`). A second implementation is how
one of the two surfaces would end up reading a path it should have refused.

The stored path is data, not an instruction: it is only followed when it
resolves inside the deployment log directory next to the store's own
database, which is where the recorder writes. A row pointing anywhere else is
refused and reported, never read.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.exceptions import SecurityError, ValidationError
from noust.core.secret_detection import mask_secret_shapes
from noust.validators.names import resolve_within

if TYPE_CHECKING:
    from noust.core.store import DeploymentRecord

log = logging.getLogger(__name__)

#: Bytes of a captured log returned when the caller does not ask for a
#: narrower tail, counted from the end of the file. Deployment logs are
#: rotated at twenty per application, so the whole file fits in memory well
#: under this cap for the overwhelming majority of builds; this only protects
#: against the rare build tool that will not stop talking.
DEFAULT_TAIL_BYTES = 512 * 1024


#: The levels a log can be read at, from everything to the least: ``warn`` keeps warnings and
#: errors, ``error`` only errors.
LEVELS = ("all", "warn", "error")

#: What a line's words say of it. The tools print no level, so the words do; this is the
#: classifier the console draws its colours with (``features/app/deployments/buildLog.ts``), kept
#: in step by the cases both are tested against (``logLevels.cases.json``). Narrow on purpose: a
#: line is a failure or a warning only when it reads like one to a person too.
_ERROR = re.compile(
    r"npm err!|\berr!|\berror\b|\u2717|failed to compile|\btype error\b|\btraceback\b|"
    r"\bexception\b|\bfatal\b|\bfailed with result\b|/failure\b",
    re.IGNORECASE,
)
_WARNING = re.compile(r"\bwarn(?:ing)?\b|\u26a0", re.IGNORECASE)

#: ``[2026-09-25 21:53:39] `` at the start of a captured line.
_STAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?\] ?")

#: The level word a job's own log writes after the stamp: ``[WARNING] text``.
_JOB_LEVEL = re.compile(r"^\[(DEBUG|INFO|SUCCESS|WARNING|WARN|ERROR)\] ?")

#: A terminal's control sequences, colours and cursor moves alike.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

#: How much of a log each level keeps, so a level can be compared with another.
_RANK = {"debug": 0, "info": 0, "warn": 1, "error": 2}


def level_of(text: str) -> str:
    """
    Say how serious a line of tool output reads.

    Args:
        text: A line without its stamp.

    Returns:
        ``error``, ``warn`` or ``info``.
    """
    if _ERROR.search(text):
        return "error"
    if _WARNING.search(text):
        return "warn"
    return "info"


def line_level(line: str) -> str:
    """
    Say the level of one line of a captured log, as the file holds it.

    Args:
        line: ``[stamp] text`` or ``[stamp] [LEVEL] text``.

    Returns:
        ``debug``, ``info``, ``warn`` or ``error``: the level word a job wrote when the
        line has one, otherwise what the words of the line say.
    """
    # Colour codes sit against the words they colour (``\x1b[31mError``), which a word
    # boundary does not see through.
    body = _ANSI.sub("", _STAMP.sub("", line, count=1))
    job = _JOB_LEVEL.match(body)
    if job is not None:
        word = job.group(1)
        if word == "ERROR":
            return "error"
        if word in ("WARNING", "WARN"):
            return "warn"
        if word == "DEBUG":
            return "debug"
        return level_of(body[job.end() :])
    return level_of(body)


def view_log(
    content: str,
    *,
    level: str = "all",
    search: str | None = None,
    timestamps: bool = True,
    sanitize: bool = False,
) -> str:
    """
    Shape a captured log for reading, copying or downloading.

    The file is stored with the secrets the build knew scrubbed already; ``sanitize``
    is the second layer for a log that leaves the console (a ticket, a chat): it masks
    what looks like a credential by its shape and takes out the terminal's control
    sequences. The same function serves the console and ``noust app deploy-log``.

    Args:
        content: The log, one stamped line per line.
        level: ``all``, ``warn`` (warnings and errors) or ``error``.
        search: Keep only the lines that hold this text, ignoring case.
        timestamps: Keep the stamp at the start of each line.
        sanitize: Mask credentials by shape and strip control sequences.

    Returns:
        The log as asked; empty when nothing matches.

    Raises:
        ValidationError: ``level`` is not one of :data:`LEVELS`.
    """
    if level not in LEVELS:
        raise ValidationError(
            f"{level!r} is not a level of a log",
            details="Use one of: " + ", ".join(LEVELS),
            field="level",
        )
    if not content:
        return content
    ends_with_newline = content.endswith("\n")
    lines = content.removesuffix("\n").split("\n")
    wanted = {"all": 0, "warn": 1, "error": 2}[level]
    needle = search.lower() if search else None
    kept = [
        line
        for line in lines
        if _RANK[line_level(line)] >= wanted and (needle is None or needle in line.lower())
    ]
    text = "\n".join(kept)
    if sanitize:
        text = mask_secret_shapes(_ANSI.sub("", text))
    if not timestamps:
        text = "\n".join(_STAMP.sub("", line, count=1) for line in text.split("\n"))
    if text and ends_with_newline:
        text += "\n"
    return text


@dataclass
class LogRead:
    """
    The result of reading a deployment's captured log.

    Attributes:
        content: The log text, or an empty string when nothing was read.
        truncated: True when ``content`` is the tail of a larger file.
        missing_reason: Why ``content`` is empty, in words fit to show an
            operator. ``None`` when ``content`` holds the log.
    """

    content: str
    truncated: bool
    missing_reason: str | None


def read_deployment_log(
    record: DeploymentRecord,
    *,
    tail: int | None = None,
    level: str = "all",
    search: str | None = None,
    timestamps: bool = True,
    sanitize: bool = False,
) -> LogRead:
    """
    Read a deployment's captured build log from disk.

    Args:
        record: The history row whose log is being read.
        tail: Maximum bytes of the file to return, counted from the end.
            Defaults to :data:`DEFAULT_TAIL_BYTES`.
        level: ``all``, ``warn`` or ``error``: see :func:`view_log`.
        search: Keep only the lines that hold this text.
        timestamps: Keep each line's stamp.
        sanitize: Mask credentials by shape and strip control sequences.

    Returns:
        The log, or the honest reason there is nothing to show.

    Raises:
        ValidationError: ``level`` is not one of :data:`LEVELS`.
    """
    result = _read_deployment_log(record, tail=tail)
    if result.missing_reason is not None:
        return result
    return LogRead(
        content=view_log(
            result.content, level=level, search=search, timestamps=timestamps, sanitize=sanitize
        ),
        truncated=result.truncated,
        missing_reason=None,
    )


def _read_deployment_log(record: DeploymentRecord, *, tail: int | None) -> LogRead:
    """
    Read a deployment's captured log exactly as the file holds it.

    Args:
        record: The history row whose log is being read.
        tail: Maximum bytes of the file to return, counted from the end.

    Returns:
        The log, or the honest reason there is nothing to show.
    """
    if not record.log_path:
        return LogRead(
            content="",
            truncated=False,
            missing_reason="No build log was captured for this deployment.",
        )

    from noust.core.store import get_store

    root = get_store().db_path.parent / "deploy-logs"
    try:
        path = resolve_within(root, record.log_path)
    except (SecurityError, ValidationError):
        log.warning(
            "Deployment %s records a log path outside %s; refusing to read it",
            record.id,
            root,
        )
        return LogRead(
            content="",
            truncated=False,
            missing_reason=(
                "The recorded log path is not under the deployment log directory, "
                "so the panel will not read it."
            ),
        )

    if not path.is_file():
        return LogRead(
            content="", truncated=False, missing_reason="The captured log is no longer on disk."
        )

    try:
        # read_bytes rather than an open() of our own: this module is audited
        # against touching the filesystem outside the seam for writes, and a
        # whole-name check cannot tell a read-only handle from a writable one.
        data = path.read_bytes()
    except OSError as exc:
        log.warning("Could not read the captured log for deployment %s: %s", record.id, exc)
        return LogRead(
            content="", truncated=False, missing_reason=f"The captured log could not be read: {exc}"
        )

    limit = tail if tail is not None else DEFAULT_TAIL_BYTES
    if len(data) > limit:
        text = data[-limit:].decode("utf-8", errors="replace")
        # Drop the partial line the cut landed inside.
        return LogRead(content=text.split("\n", 1)[-1], truncated=True, missing_reason=None)
    return LogRead(
        content=data.decode("utf-8", errors="replace"), truncated=False, missing_reason=None
    )
