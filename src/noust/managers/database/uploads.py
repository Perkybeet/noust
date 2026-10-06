# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Dumps uploaded from the browser: resumable, size-capped, owner-only, swept.

A dump from another provider can be many gigabytes, and a request that carries
it whole is cut by the first proxy that has an opinion (a central's tunnel
gives a request 300 seconds). So an upload is a session the client fills in
chunks, which it can resume from wherever the server says it got to:

1. ``create`` declares the file name and its size, and gets an identifier. The
   name's extension must be one of :data:`ALLOWED_EXTENSIONS` and the size at
   most ``databases.import_max_bytes`` (10 GiB), checked before a byte moves,
   with the free space of the disk it lands on.
2. ``append`` sends the next chunk at the offset the server expects (the
   ``received`` it last reported); a chunk at any other offset is refused with
   the offset to resume from, so a retried request can never write a chunk
   twice or leave a hole.
3. ``complete`` checks the size and, when the client declared one, the SHA-256,
   and hands over the file.

Each session is a directory owned by root, mode 0700, under the backup
directory (the disk sized for dumps), holding the data, mode 0600, and a
``meta.json``. The uploader is recorded and only they can continue it. An
upload nobody finished is swept after :data:`UPLOAD_TTL` (a day), whenever
another is started, and an upload an import has consumed is removed with the
import: a dump is the whole database, and it does not stay on a disk because
a browser tab was closed.

The client's file name is never used as a path: the data is always ``data``
inside a directory named by a random identifier, and the name is only read for
its extension.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core.audit import Actor, record
from noust.core.config import Config
from noust.core.exceptions import ValidationError
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs

__all__ = [
    "ALLOWED_EXTENSIONS",
    "DEFAULT_MAX_BYTES",
    "MAX_CHUNK_BYTES",
    "UPLOAD_TTL",
    "Upload",
    "UploadOffsetError",
    "UploadStore",
    "UploadTooLargeError",
    "check_file_name",
    "max_upload_bytes",
]

_log = logging.getLogger(__name__)

#: What a dump file may end in. ``.sql.gz`` and ``.tar.bz2`` end in two of them.
ALLOWED_EXTENSIONS = ("sql", "gz", "zip", "tar", "bz2", "xz", "dump", "archive")

#: The largest upload when ``databases.import_max_bytes`` says nothing.
DEFAULT_MAX_BYTES = 10 * 1024**3

#: The most one chunk request may carry. Bigger chunks are refused rather than
#: buffered: the client splits.
MAX_CHUNK_BYTES = 16 * 1024**2

#: How long an unfinished upload lives after its last chunk.
UPLOAD_TTL = 24 * 3600

#: Disk kept free beyond an upload's size, for the import that follows.
_HEADROOM = 64 * 1024**2

_ID = re.compile(r"[0-9a-f]{32}")
_NAME = re.compile(r"[^\x00-\x1f/\\]{1,255}")
_DATA = "data"
_META = "meta.json"
_READ = 1024 * 1024


class UploadOffsetError(ValidationError):
    """A chunk was sent at an offset the server did not expect."""

    def __init__(self, message: str, *, received: int) -> None:
        """
        Args:
            message: What was wrong.
            received: The bytes the server holds, which is where to resume.
        """
        super().__init__(message, "Send the next chunk from the offset the server reports.")
        self.received = received


class UploadTooLargeError(ValidationError):
    """An upload or a chunk is past a limit."""


@dataclass
class Upload:
    """
    One upload session.

    Attributes:
        id: Its identifier.
        file_name: The name the client gave, kept for the extension and the log.
        size: Bytes the client said the file has.
        received: Bytes the server holds.
        sha256: The digest the client declared, if any.
        owner: Who started it.
        created_at: When.
        updated_at: When the last chunk arrived.
        complete: Whether every byte is in and checked.
    """

    id: str
    file_name: str
    size: int
    received: int
    sha256: str | None
    owner: str
    created_at: str
    updated_at: str
    complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The session as plain data, JSON-ready.
        """
        return asdict(self)


def max_upload_bytes(config: Config | None = None) -> int:
    """
    Read the largest dump an upload may be.

    Args:
        config: The configuration; the server's when None.

    Returns:
        ``databases.import_max_bytes``, or :data:`DEFAULT_MAX_BYTES` when it
        is missing or not a positive whole number.
    """
    value = (config or Config()).get("databases.import_max_bytes", DEFAULT_MAX_BYTES)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_BYTES
    return number if number > 0 else DEFAULT_MAX_BYTES


def check_file_name(name: str) -> str:
    """
    Check that a dump's name ends in an allowed extension.

    Args:
        name: The file name, as a client or an operator gave it.

    Returns:
        The name.

    Raises:
        ValidationError: When it is not a plain file name, or its last
            extension is not one of :data:`ALLOWED_EXTENSIONS`.
    """
    if not _NAME.fullmatch(name) or name in (".", ".."):
        raise ValidationError(
            "The file name is not usable",
            details="Name the dump with a plain file name, without a path.",
            field="file_name",
        )
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"{name!r} does not look like a database dump",
            details="A dump's name ends in one of: "
            + ", ".join(f".{extension}" for extension in ALLOWED_EXTENSIONS)
            + ". Rename it if it is one (a plain SQL dump is .sql, a compressed one .sql.gz).",
            field="file_name",
        )
    return name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class UploadStore:
    """
    The upload sessions under one directory.

    The directory is :meth:`default_root` unless a caller gives one; it is
    created on first use and sweeps itself.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        fs: FileSystem | None = None,
        config: Config | None = None,
    ) -> None:
        """
        Args:
            root: The sessions' parent directory; under the backup directory
                when None.
            fs: The filesystem sessions are made and removed through.
            config: The configuration the size limit is read from.
        """
        self._root = root
        self._fs = fs
        self._config = config

    @property
    def fs(self) -> FileSystem:
        """The filesystem sessions are created and removed through."""
        return self._fs or get_fs()

    @property
    def root(self) -> Path:
        """The directory holding every session."""
        if self._root is not None:
            return self._root
        return self.default_root(self._config)

    @staticmethod
    def default_root(config: Config | None = None) -> Path:
        """
        Name where uploads live.

        Args:
            config: The configuration; the server's when None.

        Returns:
            ``.uploads`` under the backup directory, hidden from the listings
            that read that directory as one folder per application.
        """
        return (config or Config()).backup_directory / ".uploads"

    # ------------------------------------------------------------- reading

    def _directory(self, upload_id: str) -> Path:
        """
        Locate a session by its identifier.

        Args:
            upload_id: The identifier.

        Returns:
            The session's directory.

        Raises:
            ValidationError: When it is not an identifier this store issues
                (so no request can name a path).
        """
        if not isinstance(upload_id, str) or not _ID.fullmatch(upload_id):
            raise ValidationError("That is not an upload", field="upload_id")
        return self.root / upload_id

    def _read(self, directory: Path) -> Upload:
        """
        Read a session's record.

        Args:
            directory: The session's directory.

        Returns:
            The record, with ``received`` read from the data file.

        Raises:
            ValidationError: When there is no such session.
        """
        try:
            data = json.loads((directory / _META).read_text(encoding="utf-8"))
            received = (directory / _DATA).stat().st_size
            return Upload(**{**data, "received": received})
        except (OSError, ValueError, TypeError) as exc:
            raise ValidationError(
                "There is no such upload",
                details="It may have been finished, removed or swept after a day of silence. "
                "Start it again.",
                field="upload_id",
            ) from exc

    def _write(self, directory: Path, upload: Upload) -> None:
        """
        Write a session's record.

        Args:
            directory: The session's directory.
            upload: The record.
        """
        self.fs.write_text(
            directory / _META, json.dumps(upload.to_dict(), sort_keys=True), mode=SECRET_MODE
        )

    def get(self, upload_id: str, *, owner: str | None = None) -> Upload:
        """
        Read a session, which only its owner may.

        Args:
            upload_id: The identifier.
            owner: Who is asking; None skips the check (a job holding the id).

        Returns:
            The session.

        Raises:
            ValidationError: When there is no such upload, or it is another's.
        """
        upload = self._read(self._directory(upload_id))
        if owner is not None and upload.owner != owner:
            raise ValidationError(
                "There is no such upload", details="It belongs to someone else.", field="upload_id"
            )
        return upload

    def path(self, upload_id: str) -> Path:
        """
        Name the file of a finished upload.

        Args:
            upload_id: The identifier.

        Returns:
            The data file.

        Raises:
            ValidationError: When there is no such upload, or it is not finished.
        """
        upload = self._read(self._directory(upload_id))
        if not upload.complete:
            raise ValidationError(
                "The upload is not finished",
                details=f"{upload.received} of {upload.size} bytes arrived. Send the rest, "
                "then complete it.",
                field="upload_id",
            )
        return self._directory(upload_id) / _DATA

    # ------------------------------------------------------------ the verbs

    def create(self, file_name: str, size: int, *, owner: str, sha256: str | None = None) -> Upload:
        """
        Start an upload.

        Args:
            file_name: The dump's name; only its extension is read.
            size: Its size in bytes.
            owner: Who is uploading, as the audit trail names them.
            sha256: The digest the finished file must have, if the client has
                one.

        Returns:
            The session, with nothing received yet.

        Raises:
            ValidationError: When the name or the digest is not usable.
            UploadTooLargeError: When the size is past the limit or the disk
                cannot hold it.
        """
        check_file_name(file_name)
        limit = max_upload_bytes(self._config)
        if size <= 0:
            raise ValidationError("The upload is empty", field="size")
        if size > limit:
            raise UploadTooLargeError(
                f"The upload is {size} bytes; the limit is {limit}",
                details="Raise databases.import_max_bytes with 'noust config set', or import "
                "the file by its path on the server: noust db import.",
                field="size",
            )
        if sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise ValidationError("sha256 must be 64 hex digits", field="sha256")
        self.sweep()
        self.fs.make_dir(self.root, mode=SECRET_DIR_MODE, parents=True)
        free = shutil.disk_usage(self.root).free
        if free < size + _HEADROOM:
            raise UploadTooLargeError(
                f"Not enough space for the upload: it is {size} bytes and {self.root} has "
                f"{free} bytes free",
                details="Free space on that disk first; nothing was received.",
                field="size",
            )
        upload_id = secrets.token_hex(16)
        directory = self.root / upload_id
        self.fs.make_dir(directory, mode=SECRET_DIR_MODE)
        # Created here, exclusively and 0600, so the file is never briefly
        # readable and never a link someone left at that name.
        descriptor = os.open(
            directory / _DATA, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, SECRET_MODE
        )
        os.close(descriptor)
        stamp = _now()
        upload = Upload(
            id=upload_id,
            file_name=file_name,
            size=size,
            received=0,
            sha256=sha256.lower() if sha256 else None,
            owner=owner,
            created_at=stamp,
            updated_at=stamp,
        )
        self._write(directory, upload)
        return upload

    def append(self, upload_id: str, offset: int, data: bytes, *, owner: str) -> Upload:
        """
        Add the next chunk.

        Args:
            upload_id: The identifier.
            offset: Where this chunk starts in the file; it must be what the
                server holds.
            data: The chunk, at most :data:`MAX_CHUNK_BYTES`.
            owner: Who is uploading.

        Returns:
            The session, with ``received`` updated.

        Raises:
            ValidationError: When there is no such upload, or it is finished.
            UploadOffsetError: When ``offset`` is not where the file stands;
                its ``received`` is where to resume.
            UploadTooLargeError: When the chunk is too big or would pass the
                declared size.
        """
        directory = self._directory(upload_id)
        upload = self.get(upload_id, owner=owner)
        if upload.complete:
            raise ValidationError("The upload is already finished", field="upload_id")
        if len(data) > MAX_CHUNK_BYTES:
            raise UploadTooLargeError(
                f"A chunk may be at most {MAX_CHUNK_BYTES} bytes",
                details="Split the file in smaller pieces.",
            )
        if upload.received + len(data) > upload.size:
            raise UploadTooLargeError(
                "The chunk goes past the size the upload declared",
                details=f"{upload.received} of {upload.size} bytes are in.",
            )
        descriptor = os.open(directory / _DATA, os.O_WRONLY | os.O_NOFOLLOW)
        try:
            # Two requests for the same upload must not interleave: the lock
            # makes the size check and the write one step.
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            held = os.fstat(descriptor).st_size
            if offset != held:
                raise UploadOffsetError(
                    f"The chunk starts at {offset} but the server holds {held} bytes",
                    received=held,
                )
            os.lseek(descriptor, 0, os.SEEK_END)
            view = memoryview(data)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        upload.received = offset + len(data)
        upload.updated_at = _now()
        self._write(directory, upload)
        return upload

    def complete(self, upload_id: str, *, owner: str) -> Upload:
        """
        Close an upload: every byte is in, and the digest matches.

        Args:
            upload_id: The identifier.
            owner: Who is uploading.

        Returns:
            The finished session.

        Raises:
            ValidationError: When bytes are missing, or the digest does not
                match (the data is discarded: a corrupted dump is never kept).
        """
        directory = self._directory(upload_id)
        upload = self.get(upload_id, owner=owner)
        if upload.received != upload.size:
            raise UploadOffsetError(
                f"{upload.received} of {upload.size} bytes arrived",
                received=upload.received,
            )
        if upload.sha256 is not None:
            digest = hashlib.sha256()
            with open(directory / _DATA, "rb") as handle:
                while chunk := handle.read(_READ):
                    digest.update(chunk)
            if digest.hexdigest() != upload.sha256:
                self.discard(upload_id)
                raise ValidationError(
                    "The upload does not match the SHA-256 given for it",
                    details=f"It was {digest.hexdigest()}; the file was damaged on the way. "
                    "It was removed: upload it again.",
                    field="sha256",
                )
        upload.complete = True
        upload.updated_at = _now()
        self._write(directory, upload)
        record(
            "db.import.upload",
            actor=Actor.from_label(owner),
            target=f"db:upload/{upload.id}",
            details={"file": upload.file_name, "size": upload.size, "sha256": upload.sha256},
        )
        return upload

    def discard(self, upload_id: str) -> None:
        """
        Remove an upload and its data.

        Args:
            upload_id: The identifier.
        """
        directory = self._directory(upload_id)
        try:
            self.fs.remove_tree(directory)
        except OSError as exc:
            _log.warning("Could not remove the upload %s: %s", directory, exc)

    def sweep(self, *, now: float | None = None) -> list[str]:
        """
        Remove the uploads nobody touched for :data:`UPLOAD_TTL`.

        Args:
            now: The moment to judge at, as ``time.time()``; the clock by default.

        Returns:
            The identifiers removed.
        """
        removed: list[str] = []
        try:
            entries = [entry for entry in self.root.iterdir() if entry.is_dir()]
        except OSError:
            return removed
        horizon = (now if now is not None else time.time()) - UPLOAD_TTL
        for entry in entries:
            if not _ID.fullmatch(entry.name):
                continue
            try:
                # The newest thing in the session: a chunk writes the data,
                # the record follows it.
                newest = max(
                    (entry / name).stat().st_mtime
                    for name in (_DATA, _META)
                    if (entry / name).exists()
                )
            except (OSError, ValueError):
                newest = 0.0
            if newest < horizon:
                self.discard(entry.name)
                removed.append(entry.name)
        return removed
