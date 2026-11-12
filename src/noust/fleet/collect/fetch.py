# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Taking one file from a server, in pieces that resume, and checking what arrived (item 195).

What is promised, because each was once a way a backup was lost:

- **Never half a file under the name of a good copy.** A file is written as
  ``.<name>.<size>.<digest>.part`` beside where it will be and renamed into place only when every
  byte has arrived and matched, so a transfer that stops (a tunnel that drops, a console that
  restarts, the disk filling) leaves a part that nothing offers, and an interrupted copy is never
  one that restores wrong.
- **A cut transfer resumes.** The next pass finds the part, hashes what is in it and asks the server
  for the rest from where it ended; a part that is not the same file any more (the name carries the
  size and the digest the server listed) is not resumed but removed.
- **What arrives is checked against the digest the server recorded.** A mismatch discards the part
  and says so (:class:`DigestMismatch`): the server lied, or the transfer was damaged, and a copy
  nobody can trust is worse than none. A file the server recorded no digest for is checked by its
  length, and the copy says so.
- **A server that sends more than was asked, or less than it listed, is not believed.** A piece is
  never longer than the request, and the file is as long as the list said.
- **Reads are paced** (:class:`~noust.fleet.collect.throttle.Throttle`) and the disk is asked
  before every piece (:class:`~noust.fleet.collect.space.Guard`), so a transfer neither takes the
  office line nor the last of the disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import NodeError, NodeRefusedError
from noust.core.fs import FileSystem, get_fs
from noust.fleet.collect.api import NodeApi
from noust.fleet.collect.names import RemoteFile, RemoteItem
from noust.fleet.collect.space import Guard
from noust.fleet.collect.throttle import Throttle
from noust.managers.backup_catalogue import MAX_SIDECAR_BYTES

__all__ = [
    "RETRY_PAUSE",
    "TRANSFER_RETRIES",
    "DigestMismatch",
    "Fetcher",
    "Received",
    "SourceEndedEarly",
    "TooManyFailures",
    "TransferError",
]

_log = logging.getLogger(__name__)

#: Consecutive failures of a piece before a file is given up on (it resumes next pass).
TRANSFER_RETRIES = 5

#: Seconds waited between two attempts of a piece.
RETRY_PAUSE = 2.0

#: Bytes hashed at a time when a part is resumed.
_HASH_BLOCK = 1024 * 1024


class TransferError(NodeError):
    """
    A file could not be taken from a server.

    Attributes:
        code: A stable word for it: ``mismatch``, ``ended_early``, ``transfer_failed``,
            ``protocol`` or ``stopped``.
    """

    code = "transfer_failed"


class DigestMismatch(TransferError):
    """What arrived is not what the server said the file was."""

    code = "mismatch"


class SourceEndedEarly(TransferError):
    """The server has fewer bytes of a file than it listed."""

    code = "ended_early"


class TooManyFailures(TransferError):
    """A piece failed too many times in a row; what arrived is kept to resume from."""

    code = "transfer_failed"


@dataclass(frozen=True)
class Received:
    """
    A file that arrived whole and matched, and is not yet in its place.

    Attributes:
        name: Its name.
        role: ``archive`` or ``sidecar``.
        size: Bytes.
        sha256: The digest of what arrived.
        verified: ``digest`` when the server's digest matched, ``size`` when it gave none.
        part: Where it waits to be put in place.
    """

    name: str
    role: str
    size: int
    sha256: str
    verified: str
    part: Path


def part_name(file: RemoteFile) -> str:
    """
    Name the part of a file, with what makes it the same file.

    Args:
        file: The file as the server listed it.

    Returns:
        ``.<name>.<size>.<digest>.part``: a part is resumed only when all of it is the same.
    """
    return f".{file.name}.{file.size}.{(file.sha256 or 'nosum')[:12]}.part"


class Fetcher:
    """
    Take files from a server.

    Args:
        api: The server.
        throttle: Paces the reads.
        guard: Asked for room before each piece; none asks nothing.
        piece: Bytes asked for at a time.
        retries: Consecutive failed pieces before a file is given up on.
        pause: Seconds between attempts.
        sleep: Waits (tests record it).
        should_stop: Whether the console is stopping: checked between pieces.
        progress: Told ``(bytes of this file so far, its size)`` after each piece.
        fs: The filesystem changes go through.
    """

    def __init__(
        self,
        api: NodeApi,
        *,
        throttle: Throttle | None = None,
        guard: Guard | None = None,
        piece: int = 4 * 1024**2,
        retries: int = TRANSFER_RETRIES,
        pause: float = RETRY_PAUSE,
        sleep: Callable[[float], None] = time.sleep,
        should_stop: Callable[[], bool] = lambda: False,
        progress: Callable[[int, int], None] | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        self.api = api
        self.throttle = throttle or Throttle(None)
        self.guard = guard
        self.piece = max(1, piece)
        self.retries = retries
        self.pause = pause
        self._sleep = sleep
        self._should_stop = should_stop
        self._progress = progress
        self._fs = fs

    @property
    def fs(self) -> FileSystem:
        """The filesystem changes go through."""
        return self._fs or get_fs()

    def _prefix(self, part: Path, hasher: hashlib._Hash) -> int:
        """
        Read what a part already holds into the digest.

        Args:
            part: The part.
            hasher: The running digest.

        Returns:
            How many bytes it holds.
        """
        held = 0
        with part.open("rb") as handle:
            while block := handle.read(_HASH_BLOCK):
                hasher.update(block)
                held += len(block)
        return held

    def receive(self, item: RemoteItem, file: RemoteFile, directory: Path) -> Received | None:
        """
        Take one file of a copy.

        A sidecar is small and changes under its own server (it is written again when the copy is
        verified), so it is taken as it is read, at most :data:`~noust.managers.backup_catalogue.MAX_SIDECAR_BYTES`,
        and a sidecar that is not a JSON object is left out rather than failing the copy.

        Args:
            item: The copy.
            file: The file as the server listed it.
            directory: The folder the copy's files are in.

        Returns:
            What arrived; None for a sidecar that could not be taken.

        Raises:
            DigestMismatch: The digest of what arrived is not the one the server recorded. The part
                is removed.
            SourceEndedEarly: The server ran out of bytes before the size it listed.
            TooManyFailures: Pieces kept failing; the part is kept to resume from.
            DiskFullError: The disk has no room for the next piece; the part is kept.
            NodeRefusedError: The server refused the central's token.
        """
        self.fs.make_dir(directory, mode=0o700, parents=True)
        part = directory / part_name(file)
        for stale in directory.glob(f".{file.name}.*.part"):
            if stale != part:
                self.fs.remove(stale)
        sidecar = file.role == "sidecar"
        hasher = hashlib.sha256()
        offset = 0
        if part.is_file():
            if sidecar or part.stat().st_size > file.size:
                self.fs.remove(part)
            else:
                offset = self._prefix(part, hasher)
        failures = 0
        # Private from the moment it exists: what is in it is every application's secrets.
        descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            while sidecar or offset < file.size:
                if self._should_stop():
                    raise TransferError("The console is stopping; the transfer resumes later")
                want = self.piece if sidecar else min(self.piece, file.size - offset)
                if self.guard is not None:
                    self.guard.require(want)
                try:
                    data = self.api.read(item, file, offset, want)
                except NodeRefusedError:
                    raise
                except NodeError as exc:
                    failures += 1
                    if failures > self.retries:
                        raise TooManyFailures(
                            f"Taking {file.name} failed {failures} times in a row: {exc.message}",
                            details="What arrived is kept; the next pass resumes from there.",
                            output=exc.output,
                        ) from exc
                    _log.info("A piece of %s failed (%s); asking again", file.name, exc.message)
                    self._sleep(self.pause)
                    continue
                if len(data) > want:
                    raise TransferError(
                        f"The server sent more of {file.name} than was asked for",
                        details="It is not believed: nothing of this file was kept.",
                    )
                if not data:
                    if sidecar:
                        break
                    failures += 1
                    if failures > self.retries:
                        raise SourceEndedEarly(
                            f"The server has only {offset} of the {file.size} bytes it listed "
                            f"of {file.name}",
                            details="The copy changed or went away on the server while it was "
                            "being taken. What arrived is kept; the next pass asks again.",
                        )
                    self._sleep(self.pause)
                    continue
                failures = 0
                handle.write(data)
                hasher.update(data)
                offset += len(data)
                self.throttle.take(len(data))
                if self._progress is not None:
                    self._progress(offset, file.size)
                if sidecar and offset > MAX_SIDECAR_BYTES:
                    break
            handle.flush()
            os.fsync(handle.fileno())
        if sidecar:
            return self._sidecar(file, part, offset, hasher.hexdigest())
        digest = hasher.hexdigest()
        if file.sha256 is not None and digest != file.sha256:
            self.fs.remove(part)
            raise DigestMismatch(
                f"{file.name} did not arrive as the server recorded it",
                details=f"It listed SHA-256 {file.sha256} and what arrived is {digest}. "
                "The copy was discarded: the server's own file is damaged, or it is not telling "
                "the truth, or the transfer was. Verify the copy on the server "
                "(noust backup verify) before taking it again.",
            )
        return Received(
            file.name, file.role, offset, digest, "digest" if file.sha256 else "size", part
        )

    def _sidecar(self, file: RemoteFile, part: Path, size: int, digest: str) -> Received | None:
        """
        Judge a sidecar that arrived.

        Args:
            file: The file.
            part: Where it is.
            size: Its length.
            digest: Its digest.

        Returns:
            It, or None when it is too big or is not a JSON object (it is removed).
        """
        try:
            ok = 0 < size <= MAX_SIDECAR_BYTES and isinstance(json.loads(part.read_bytes()), dict)
        except (OSError, ValueError):
            ok = False
        if not ok:
            self.fs.remove(part)
            return None
        return Received(file.name, file.role, size, digest, "size", part)
