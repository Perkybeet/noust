# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What was typed and what was printed, kept for the audit.

A shell in the browser is an open door to the application's secrets, so it must
not be one that leaves no trace. A session here leaves two: the audit trail records who opened it,
on what and for how long (``terminal.start`` and ``terminal.end``), and a
recording keeps the session itself, so "what happened in that shell" has an
answer a year later.

**Format.** An asciicast v2 file (``.cast``): a JSON header line and one
``[seconds, "o"|"i"|"r"|"m", data]`` line per event, so any asciinema player
plays it and nothing here is needed to read it. The header carries a
``noust`` object (session, who, what). Beside it, a small ``.json`` says how
the session ended and the SHA-256 of the cast, which the audit event repeats:
a recording that no longer matches the trail has been changed.

**Bounded, and fairly.** A recording takes at most ``terminal.recording.max_mb``
and input and output have a share each (:data:`OUTPUT_SHARE`): a flood of
output (``yes``, ``cat`` of a huge file) fills its share and is replaced by one
marker, while what was *typed* keeps being recorded. A recording that can be
filled by the person it records is a recording of nothing.

**Scrubbed.** Every known secret value of the application (and Noust's own
credentials) is replaced by ``***`` before anything is written, including a
value split across two reads (:class:`StreamScrubber`). What is typed at a
password prompt (echo off, still in line mode) is not recorded at all.
Anything Noust does not know is a secret is recorded as it was: this is an
audit trail, not a vault, and it is readable only with ``audit.read``.

**Kept for** ``retention.terminal_days`` (at least the audit's under the ENS
profile) and then deleted by :func:`prune`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core.exceptions import NoustError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.core.redact import Scrubber

logger = logging.getLogger(__name__)

#: Name of the directory recordings live in, next to the store like job logs.
DIRECTORY_NAME = "terminal-recordings"

#: A recording's id: when it started and the session that made it.
RECORDING_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$")

#: The share of a recording's budget that output may take. The rest is for
#: what was typed, which an output flood must not be able to push out.
OUTPUT_SHARE = 0.8

#: What a recording says where it stopped keeping a direction.
TRUNCATED_NOTE = "recording limit reached: later {direction} is not kept"

#: The text that replaces what was typed at a password prompt.
HIDDEN_INPUT = "<hidden: the program asked for a secret>"

#: Largest recording the API returns whole; a bigger one is read from disk.
MAX_READ_BYTES = 16 * 1024 * 1024

#: ESC sequences and the control characters a transcript must not carry to
#: whoever reads it in their own terminal.
_CONTROL_SEQUENCES = re.compile(
    r"""
    \x1b\][^\x07\x1b]*(?:\x07|\x1b\\)   # OSC ... BEL or ST
    | \x1b\[[0-?]*[ -/]*[@-~]            # CSI
    | \x1b[PX^_][^\x1b]*\x1b\\           # DCS, SOS, PM, APC
    | \x1b[@-Z\\-_]                      # two-character escapes
    """,
    re.VERBOSE,
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class RecordingNotFound(NoustError):
    """There is no recording with that id."""


def recordings_dir() -> Path:
    """
    Where recordings are kept.

    Returns:
        A directory next to the store's database, like the jobs' logs: it
        follows the store wherever the data directory was put.
    """
    from noust.core.store import get_store

    return get_store().db_path.parent / DIRECTORY_NAME


def plain_text(text: str) -> str:
    """
    Make recorded output safe to print in the reader's own terminal.

    A recording holds the escape sequences a program sent. Printed as they are,
    they would be run by the terminal of whoever reads the recording: move the
    cursor, set the title, answer queries. This keeps the words and the line
    breaks and drops the rest.

    Args:
        text: Recorded output.

    Returns:
        The text with every escape sequence and control character removed,
        except the newline and the tab; a carriage return before a newline is
        dropped with it.
    """
    cleaned = _CONTROL_SEQUENCES.sub("", text).replace("\r\n", "\n")
    cleaned = cleaned.replace("\r", "\n")
    return _CONTROL_CHARACTERS.sub("", cleaned)


class StreamScrubber:
    """
    Scrub secrets from text that arrives in pieces.

    A read from a terminal ends wherever the kernel's buffer did, so a secret
    can be split across two reads, and a scrubber that only looks at each
    piece would write both halves. This holds back the last ``longest - 1``
    characters until the next piece (or the end) tells whether they begin a
    secret.
    """

    def __init__(self, scrubber: Scrubber) -> None:
        """
        Args:
            scrubber: What knows the secrets.
        """
        self._scrubber = scrubber
        self._pending = ""

    def push(self, text: str) -> str:
        """
        Take a piece of the stream.

        Args:
            text: The next piece.

        Returns:
            What is safe to write now, scrubbed; the rest waits.
        """
        self._pending += text
        hold = max(self._scrubber.longest() - 1, 0)
        cut = len(self._pending) - hold
        if cut <= 0:
            return ""
        for start, end in self._scrubber.spans(self._pending):
            if start >= cut:
                break
            if end > cut:
                cut = start  # never cut through a secret
                break
        emit, self._pending = self._pending[:cut], self._pending[cut:]
        return self._scrubber.scrub(emit)

    def flush(self) -> str:
        """
        Returns:
            What was held back, scrubbed; the stream has ended.
        """
        emit, self._pending = self._pending, ""
        return self._scrubber.scrub(emit)


@dataclass
class RecordingSummary:
    """
    What a finished recording says about itself.

    Attributes:
        id: ``<start>-<session>``.
        bytes: The size of the cast.
        sha256: Its digest, which ``terminal.end`` repeats in the audit trail.
        events: How many events it holds.
        truncated_output: Output was cut at the budget.
        truncated_input: Input was cut at the budget.
        dropped_output_bytes: Output not kept.
        dropped_input_bytes: Input not kept.
        failed: The disk refused a write: what follows the failure is not kept.
    """

    id: str
    bytes: int = 0
    sha256: str = ""
    events: int = 0
    truncated_output: bool = False
    truncated_input: bool = False
    dropped_output_bytes: int = 0
    dropped_input_bytes: int = 0
    failed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return asdict(self)


class SessionRecorder:
    """
    The recording of one session, written as it happens.

    Thread-safe: output comes from the session's reader thread and input from
    the socket's.
    """

    def __init__(
        self,
        directory: Path,
        *,
        session_id: str,
        meta: dict[str, Any],
        rows: int,
        cols: int,
        max_bytes: int,
        scrubber: Scrubber | None = None,
        clock: Callable[[], float] = time.time,
        fs: FileSystem | None = None,
    ) -> None:
        """
        Args:
            directory: Where recordings live; created when missing.
            session_id: The session's identifier (16 hexadecimal digits).
            meta: What the header says about the session: who, what, where.
            rows: The terminal's height at the start.
            cols: The terminal's width at the start.
            max_bytes: The most the recording may take.
            scrubber: What knows the secrets to hide.
            clock: ``time.time``; a test passes its own.
            fs: The filesystem to create the directory and the sidecar through.

        Raises:
            ValidationError: The session id is not what a ticket makes.
            OSError: The recording cannot be created.
        """
        if not re.fullmatch(r"[0-9a-f]{16}", session_id):
            raise ValidationError(f"Not a session id: {session_id!r}")
        self._fs = fs or get_fs()
        self._clock = clock
        self._lock = threading.Lock()
        self._started = clock()
        stamp = datetime.fromtimestamp(self._started, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.id = f"{stamp}-{session_id}"
        self.directory = directory
        self._fs.make_dir(directory, mode=SECRET_DIR_MODE)
        self.path = directory / f"{self.id}.cast"
        self._meta_path = directory / f"{self.id}.json"
        self._meta = {**meta, "id": self.id, "session": session_id}
        self._output_budget = int(max_bytes * OUTPUT_SHARE)
        self._input_budget = max_bytes - self._output_budget
        self._used = {"o": 0, "i": 0}
        self._dropped = {"o": 0, "i": 0}
        self._truncated = {"o": False, "i": False}
        self._events = 0
        self._bytes = 0
        self._digest = hashlib.sha256()
        scrubber = scrubber or Scrubber()
        self._streams = {"o": StreamScrubber(scrubber), "i": StreamScrubber(scrubber)}
        self._closed = False
        self._failed = False
        self._hidden_noted = False
        # Created exclusively and 0600: a recording holds what an application's
        # shell printed, and a name that exists is not ours to write.
        self._descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SECRET_MODE)
        header = {
            "version": 2,
            "width": cols,
            "height": rows,
            "timestamp": int(self._started),
            "title": str(meta.get("label") or meta.get("target") or "terminal"),
            "env": {"TERM": "xterm-256color"},
            "noust": {key: value for key, value in self._meta.items() if value is not None},
        }
        self._write(json.dumps(header, ensure_ascii=False) + "\n")
        self._write_meta("recording")

    def _write(self, line: str) -> None:
        """
        Append one line to the cast, whole, at once.

        Args:
            line: The line, with its newline.
        """
        if self._failed:
            return
        data = line.encode("utf-8")
        try:
            os.write(self._descriptor, data)
        except OSError as exc:
            # A full disk must not end the session it records: the audit event
            # for the end says the recording failed, which is what it can do.
            self._failed = True
            logger.error("Recording %s stopped, the disk refused a write: %s", self.id, exc)
            return
        self._digest.update(data)
        self._bytes += len(data)

    def _write_meta(self, status: str, **fields: Any) -> None:
        """
        Write the sidecar.

        Args:
            status: ``recording``, ``complete`` or ``interrupted``.
            **fields: What the end adds.
        """
        document = {"status": status, "started_at": self._started, **self._meta, **fields}
        try:
            self._fs.write_text(
                self._meta_path, json.dumps(document, ensure_ascii=False), mode=SECRET_MODE
            )
        except OSError as exc:
            # The cast is what matters; the audit event carries the rest.
            logger.warning("Could not write the sidecar of recording %s: %s", self.id, exc)

    def _event(self, direction: str, text: str) -> None:
        """
        Append one event of ``o`` or ``i``, within that direction's budget.

        Args:
            direction: ``o`` or ``i``.
            text: What was printed or typed, already scrubbed.
        """
        if not text:
            return
        budget = self._output_budget if direction == "o" else self._input_budget
        line = json.dumps([round(self._clock() - self._started, 6), direction, text]) + "\n"
        size = len(line.encode("utf-8"))
        if self._used[direction] + size > budget:
            self._dropped[direction] += size
            if not self._truncated[direction]:
                self._truncated[direction] = True
                name = "output" if direction == "o" else "input"
                note = TRUNCATED_NOTE.format(direction=name)
                self._write(json.dumps([round(self._clock() - self._started, 6), "m", note]) + "\n")
                self._events += 1
            return
        self._used[direction] += size
        self._events += 1
        self._write(line)

    def output(self, text: str) -> None:
        """
        Record what the program printed.

        Args:
            text: Decoded output.
        """
        with self._lock:
            if not self._closed:
                self._event("o", self._streams["o"].push(text))

    def input(self, text: str, *, hidden: bool = False) -> None:
        """
        Record what the person typed.

        Args:
            text: What was typed.
            hidden: The program asked for a secret and the terminal did not
                echo it: only that is recorded.
        """
        with self._lock:
            if self._closed:
                return
            if hidden:
                # Said once when the prompt starts, not for every key.
                if not self._hidden_noted:
                    self._hidden_noted = True
                    self._event("i", HIDDEN_INPUT)
            else:
                self._hidden_noted = False
                self._event("i", self._streams["i"].push(text))

    def resize(self, rows: int, cols: int) -> None:
        """
        Record that the terminal changed size.

        Args:
            rows: The new height.
            cols: The new width.
        """
        with self._lock:
            if not self._closed:
                line = json.dumps([round(self._clock() - self._started, 6), "r", f"{cols}x{rows}"])
                self._events += 1
                self._write(line + "\n")

    def close(
        self, *, reason: str, exit_code: int | None, interrupted: bool = False
    ) -> RecordingSummary:
        """
        Finish the recording.

        Args:
            reason: Why the session ended.
            exit_code: How the program ended, if it did.
            interrupted: The recorder is being closed without the session having
                ended properly (the console is stopping).

        Returns:
            What the recording says about itself.
        """
        with self._lock:
            if self._closed:
                return self._summary()
            for direction in ("o", "i"):
                self._event(direction, self._streams[direction].flush())
            self._closed = True
            try:
                os.fsync(self._descriptor)
            except OSError as exc:
                logger.warning("Could not sync recording %s: %s", self.id, exc)
            finally:
                os.close(self._descriptor)
            summary = self._summary()
            ended = self._clock()
            self._write_meta(
                "interrupted" if interrupted else "complete",
                ended_at=ended,
                duration=round(ended - self._started, 3),
                end_reason=reason,
                exit_code=exit_code,
                **{key: value for key, value in summary.to_dict().items() if key != "id"},
            )
            return summary

    def _summary(self) -> RecordingSummary:
        return RecordingSummary(
            id=self.id,
            bytes=self._bytes,
            sha256=self._digest.hexdigest(),
            events=self._events,
            truncated_output=self._truncated["o"],
            truncated_input=self._truncated["i"],
            dropped_output_bytes=self._dropped["o"],
            dropped_input_bytes=self._dropped["i"],
            failed=self._failed,
        )


@dataclass
class RecordingInfo:
    """
    One recording, as the list shows it.

    Attributes:
        id: ``<start>-<session>``.
        status: ``recording``, ``complete`` or ``interrupted``.
        meta: What the sidecar holds: who, what, how it ended.
    """

    id: str
    status: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The recording as JSON-ready data.
        """
        return {"id": self.id, "status": self.status, **self.meta}


def _sidecars(directory: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """
    Read every sidecar, newest first.

    Args:
        directory: Where recordings live.

    Yields:
        Each sidecar's path and content; one that cannot be read is skipped
        and logged.
    """
    try:
        names = sorted(
            (entry for entry in directory.iterdir() if entry.suffix == ".json"),
            key=lambda entry: entry.name,
            reverse=True,
        )
    except FileNotFoundError:
        return
    for path in names:
        if path.is_symlink() or not RECORDING_ID.fullmatch(path.stem):
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable recording sidecar %s: %s", path, exc)
            continue
        if isinstance(document, dict):
            yield path, document


def list_recordings(
    directory: Path | None = None,
    *,
    domain: str | None = None,
    actor: str | None = None,
    kind: str | None = None,
    active: frozenset[str] = frozenset(),
    limit: int = 100,
) -> list[RecordingInfo]:
    """
    List recordings, newest first.

    Args:
        directory: Where recordings live; the usual place by default.
        domain: Only the sessions on this application.
        actor: Only the sessions of this person, as the audit trail names them.
        kind: Only ``app`` or ``server`` sessions.
        active: The ids of recordings whose session is still open here; a
            sidecar that says ``recording`` and is not among them belongs to a
            session the console lost (a crash), and is listed as interrupted.
        limit: The most to return.

    Returns:
        The recordings.
    """
    found: list[RecordingInfo] = []
    for path, document in _sidecars(directory or recordings_dir()):
        if domain is not None and document.get("domain") != domain:
            continue
        if actor is not None and document.get("actor") != actor:
            continue
        if kind is not None and document.get("kind") != kind:
            continue
        status = str(document.get("status") or "complete")
        if status == "recording" and path.stem not in active:
            status = "interrupted"
        found.append(
            RecordingInfo(
                id=path.stem,
                status=status,
                meta={key: value for key, value in document.items() if key not in ("id", "status")},
            )
        )
        if len(found) >= limit:
            break
    return found


@dataclass
class Recording:
    """
    One recording, read back.

    Attributes:
        info: What the list shows.
        header: The cast's first line.
        events: Every event after it, as ``(seconds, type, data)``.
        intact: Whether the cast still matches the SHA-256 its sidecar and the
            audit trail recorded; None while the session is still recording.
    """

    info: RecordingInfo
    header: dict[str, Any]
    events: list[tuple[float, str, str]]
    intact: bool | None


def read_recording(recording_id: str, directory: Path | None = None) -> Recording:
    """
    Read a recording.

    Args:
        recording_id: Its id.
        directory: Where recordings live; the usual place by default.

    Returns:
        The recording.

    Raises:
        ValidationError: The id is not one a recording has.
        RecordingNotFound: There is no such recording.
        NoustError: It is too large to return whole, or damaged.
    """
    if not RECORDING_ID.fullmatch(recording_id):
        raise ValidationError(
            f"Not a recording id: {recording_id!r}",
            details="An id is a date, a T, a time, a Z, a dash and 16 hexadecimal digits.",
            field="recording",
        )
    base = directory or recordings_dir()
    cast = base / f"{recording_id}.cast"
    sidecar = base / f"{recording_id}.json"
    if cast.is_symlink() or not cast.is_file():
        raise RecordingNotFound(
            f"No recording {recording_id}",
            details="List them with: noust audit recordings",
        )
    if cast.stat().st_size > MAX_READ_BYTES:
        raise NoustError(
            f"The recording {recording_id} is larger than {MAX_READ_BYTES // (1024 * 1024)} MiB",
            details=f"Read the file itself: {cast}",
        )
    raw = cast.read_bytes()
    lines = raw.decode("utf-8", errors="replace").splitlines()
    try:
        header = json.loads(lines[0])
        events = [
            (float(entry[0]), str(entry[1]), str(entry[2]))
            for entry in (json.loads(line) for line in lines[1:] if line.strip())
        ]
    except (IndexError, ValueError, TypeError) as exc:
        raise NoustError(
            f"The recording {recording_id} is damaged: {exc}", details=f"The file is {cast}"
        ) from exc
    meta: dict[str, Any] = {}
    try:
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
        meta = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        meta = {}
    status = str(meta.get("status") or "complete")
    recorded = meta.get("sha256")
    intact = None if status == "recording" else (hashlib.sha256(raw).hexdigest() == recorded)
    return Recording(
        info=RecordingInfo(
            id=recording_id,
            status=status,
            meta={key: value for key, value in meta.items() if key not in ("id", "status")},
        ),
        header=header if isinstance(header, dict) else {},
        events=events,
        intact=intact,
    )


def transcript(recording: Recording, *, with_input: bool = True) -> str:
    """
    Render a recording as plain text, safe to print.

    Args:
        recording: The recording.
        with_input: Show what was typed, each keystroke group on its own line
            marked ``>``; off, only the output.

    Returns:
        The output, in order, with the escape sequences of the programs
        removed (:func:`plain_text`).
    """
    pieces: list[str] = []
    for _seconds, kind, data in recording.events:
        if kind == "o":
            pieces.append(plain_text(data))
        elif kind == "i" and with_input:
            typed = plain_text(data).replace("\n", "<enter>")
            pieces.append(f"\n> {typed}\n")
        elif kind == "m":
            pieces.append(f"\n[{plain_text(data)}]\n")
    return "".join(pieces)


def prune(cutoff: datetime, directory: Path | None = None, *, fs: FileSystem | None = None) -> int:
    """
    Delete the recordings that ended before a moment.

    Only regular files that are named like a recording and sit directly in the
    directory are deleted, never a link: a sidecar edited to name another file
    deletes nothing else.

    Args:
        cutoff: A local naive datetime, as the retention pass gives it.
        directory: Where recordings live; the usual place by default.
        fs: The filesystem to delete through.

    Returns:
        How many recordings were deleted.
    """
    base = directory or recordings_dir()
    filesystem = fs or get_fs()
    limit = cutoff.timestamp()
    deleted = 0
    for path, document in _sidecars(base):
        if document.get("status") == "recording":
            continue
        finished = document.get("ended_at") or document.get("started_at")
        if not isinstance(finished, (int, float)) or finished >= limit:
            continue
        cast = base / f"{path.stem}.cast"
        for target in (cast, path):
            if target.is_symlink() or (target.exists() and not target.is_file()):
                logger.warning("Not deleting %s: it is not a plain file", target)
                continue
            filesystem.remove(target)
        deleted += 1
    return deleted
