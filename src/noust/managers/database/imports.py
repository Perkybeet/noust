# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Importing a dump that did not come from Noust: from a browser, or from a path on the server.

Whoever arrives from another provider has a file, not a Noust backup: a
``pg_dump`` of any format, a ``mysqldump`` in any compression, a
``mongodump --archive``. Importing it is a restore with three extra problems,
and this module is where each is solved once, for the console, the API and
``noust db import``:

- **What is this file?** :func:`detect_format` reads it, never its name: the
  magic bytes of gzip, bzip2, xz and zip, then of what is inside (a PostgreSQL
  custom archive, a tar of one, a mongodump archive, SQL text). The name only
  has to end in an allowed extension (:data:`~.uploads.ALLOWED_EXTENSIONS`),
  because that is the one thing a user can mistake. A file that is none of
  these is refused before anything is touched, without echoing its contents.
- **Can the engine load it?** The engines read plain SQL, a PostgreSQL archive,
  a mongodump archive or gzipped tree, optionally gzipped. :func:`prepare`
  turns bzip2, xz and zip into the plain file the engine reads, in a private
  scratch directory removed afterwards, and names the file for what it is.
- **What if it fails?** The restore underneath
  (:meth:`~noust.managers.database.backups.DatabaseBackups.restore_file`) is
  the one every restore uses: the target is dumped first (a safety copy, kept)
  and put back when the load fails. An import into a database that did not
  exist leaves no database behind when it fails.

The options are the ones an operator moving between providers needs, and are
spelled by what they do:

- ``replace``: drop and recreate the target before loading (the safety copy is
  taken first). Without it the dump is loaded over what the database holds.
- ``keep_owners`` (PostgreSQL): keep the owners and privileges the dump names.
  Off by default, because they are the roles of the server it came from:
  ``pg_restore`` is told ``--no-owner --no-acl`` and a plain dump has those
  statements taken out, and everything is created as the database's owner.

What is not here: a dump of *all* databases of an engine, and replacing the
engine's own accounts from one. Both change more than the database an operator
chose, and neither can be taken back with one safety copy.
"""

from __future__ import annotations

import bz2
import logging
import lzma
import os
import re
import secrets
import shutil
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from noust.core.config import Config
from noust.core.exceptions import (
    DatabaseBackupError,
    DatabaseError,
    ValidationError,
)
from noust.core.fs import SECRET_DIR_MODE, SECRET_MODE, FileSystem, get_fs
from noust.managers.database.backup_verify import check_dump
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.base import BaseDatabaseManager, RestoreOutcome, format_size
from noust.managers.database.mongo_archive import ARCHIVE_MAGIC
from noust.managers.database.service import DatabaseService
from noust.managers.database.uploads import (
    UploadStore,
    check_file_name,
)

__all__ = [
    "ACCEPTS",
    "DatabaseImports",
    "DumpFormat",
    "ImportOutcome",
    "Prepared",
    "allowed_directories",
    "detect_format",
    "prepare",
    "resolve_server_path",
]

_log = logging.getLogger(__name__)

#: How much of a file is read to tell what it is. A tar header's ``ustar``
#: sits at 257; a dump's first statements follow within a page.
_HEAD = 8192

#: The most a decompressed dump may be, when the disk would allow more.
MAX_EXPANDED_BYTES = 256 * 1024**3

#: Disk kept free beyond what a decompression writes.
_HEADROOM = 64 * 1024**2

#: Tar members read to tell what a tar holds: a PostgreSQL archive starts with
#: ``toc.dat``, a dump tree with its first collection.
_TAR_PEEK = 5

_COPY_BUFFER = 1024 * 1024

#: What plain SQL starts with, after blanks and a byte-order mark. Anything
#: else that is text is not taken for a dump: a file Noust does not recognise
#: is refused, not fed to a database client.
_SQL_START = re.compile(
    rb"\A(?:\s|\xef\xbb\xbf)*(?:--|/\*|SET\s|CREATE\s|INSERT\s|BEGIN\b|START\s+TRANSACTION|"
    rb"USE\s|DROP\s|COPY\s|ALTER\s|SELECT\s|PRAGMA\s|LOCK\s|UNLOCK\s|DELIMITER\s|"
    rb"\\restrict\b|\\connect\b)",
    re.IGNORECASE,
)

#: PostgreSQL's custom-format signature.
_PG_CUSTOM = b"PGDMP"

#: What each engine reads, by kind.
ACCEPTS: dict[str, frozenset[str]] = {
    "postgresql": frozenset({"pg-custom", "pg-tar", "sql"}),
    "mysql": frozenset({"sql", "tar-sql"}),
    "mongodb": frozenset({"mongo-archive", "mongo-tree"}),
}

#: What a kind is called to an operator.
_KIND_NAMES = {
    "pg-custom": "a PostgreSQL custom-format archive (pg_dump -Fc)",
    "pg-tar": "a PostgreSQL tar-format archive (pg_dump -Ft)",
    "sql": "plain SQL",
    "tar-sql": "a tar holding one SQL dump",
    "mongo-archive": "a mongodump archive (--archive)",
    "mongo-tree": "a tar of a mongodump directory",
    "unknown": "something that is not a database dump",
}


@dataclass(frozen=True)
class DumpFormat:
    """
    What a file is, read from its bytes.

    Attributes:
        kind: ``pg-custom``, ``pg-tar``, ``sql``, ``tar-sql``,
            ``mongo-archive``, ``mongo-tree`` or ``unknown``.
        compression: ``none``, ``gzip``, ``bz2``, ``xz`` or ``zip`` around it.
        description: One sentence an operator reads.
        engines: The engines that can load it.
    """

    kind: str
    compression: str
    description: str
    engines: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The format as plain data, JSON-ready.
        """
        return {
            "kind": self.kind,
            "compression": self.compression,
            "description": self.description,
            "engines": list(self.engines),
        }


def _classify(head: bytes) -> str:
    """
    Name what the first bytes of a (decompressed) stream are.

    Args:
        head: The first bytes.

    Returns:
        ``pg-custom``, ``tar`` (a tar, to be looked into), ``mongo-archive``,
        ``sql`` or ``unknown``.
    """
    if head.startswith(_PG_CUSTOM):
        return "pg-custom"
    if head.startswith(ARCHIVE_MAGIC):
        return "mongo-archive"
    if len(head) > 262 and head[257:262] == b"ustar":
        return "tar"
    if b"\x00" not in head and _SQL_START.match(head):
        return "sql"
    return "unknown"


def _open_decompressed(path: Path, compression: str) -> IO[bytes]:
    """
    Open a file the way its compression asks.

    Args:
        path: The file.
        compression: ``none``, ``gzip``, ``bz2`` or ``xz``.

    Returns:
        A binary stream of the plain bytes.
    """
    import gzip

    if compression == "gzip":
        return gzip.open(path, "rb")  # type: ignore[return-value]
    if compression == "bz2":
        return bz2.open(path, "rb")
    if compression == "xz":
        return lzma.open(path, "rb")
    return path.open("rb")


def _compression_of(prefix: bytes) -> str:
    """
    Args:
        prefix: A file's first bytes.

    Returns:
        ``gzip``, ``bz2``, ``xz``, ``zip`` or ``none``, by magic number.
    """
    if prefix.startswith(b"\x1f\x8b"):
        return "gzip"
    if prefix.startswith(b"BZh"):
        return "bz2"
    if prefix.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if prefix.startswith(b"PK\x03\x04"):
        return "zip"
    return "none"


def _tar_kind(path: Path) -> str:
    """
    Look into a tar, at its first members only.

    Args:
        path: The tar, plain or compressed.

    Returns:
        ``pg-tar`` when it starts with PostgreSQL's table of contents,
        ``tar-sql`` when it holds one ``.sql`` file, ``mongo-tree`` when it
        holds ``.bson`` files, else ``unknown``.
    """
    names: list[str] = []
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive:
                names.append(member.name)
                if len(names) >= _TAR_PEEK:
                    break
    except (tarfile.TarError, OSError, EOFError):
        return "unknown"
    if not names:
        return "unknown"
    if names[0] == "toc.dat":
        return "pg-tar"
    files = [name for name in names if not name.endswith("/")]
    if any(
        name.endswith((".bson", ".bson.gz", ".metadata.json", ".metadata.json.gz"))
        for name in files
    ):
        return "mongo-tree"
    if len(files) == 1 and files[0].endswith(".sql"):
        return "tar-sql"
    return "unknown"


def detect_format(path: Path) -> DumpFormat:
    """
    Say what a file is by reading its first bytes, and what is inside a wrapper.

    Nothing is executed and nothing but a prefix is decompressed. The file's
    contents are never put in an error message.

    Args:
        path: The file.

    Returns:
        What it is. ``kind`` is ``unknown`` for anything that is not a dump
        Noust can load; ``engines`` lists who can load it.

    Raises:
        DatabaseBackupError: When the file cannot be read, or its compression
            is damaged or cannot be read (an encrypted zip, a zip of several
            files).
    """
    try:
        with path.open("rb") as handle:
            prefix = handle.read(16)
        compression = _compression_of(prefix)
        if compression == "zip":
            with zipfile.ZipFile(path) as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
                if len(members) != 1:
                    raise DatabaseBackupError(
                        f"{path.name} is a zip of {len(members)} files",
                        details="Import one dump at a time: zip a single dump file.",
                    )
                if members[0].flag_bits & 0x1:
                    raise DatabaseBackupError(
                        f"{path.name} is an encrypted zip",
                        details="Noust cannot ask for its password. Decrypt it first.",
                    )
                with archive.open(members[0]) as inner:
                    head = inner.read(_HEAD)
            kind = _classify(head)
            if kind == "tar":
                kind = "unknown"  # a tar inside a zip is not looked into
        else:
            with _open_decompressed(path, compression) as stream:
                head = stream.read(_HEAD + 512)
            kind = _classify(head)
            if kind == "tar":
                kind = _tar_kind(path)
    except (OSError, EOFError, zipfile.BadZipFile, lzma.LZMAError) as exc:
        raise DatabaseBackupError(
            f"Could not read {path.name}",
            details=f"{exc}. Check that the file is complete and not damaged.",
        ) from exc
    engines = tuple(engine for engine, kinds in ACCEPTS.items() if kind in kinds)
    return DumpFormat(
        kind=kind,
        compression=compression,
        description=_KIND_NAMES[kind]
        + ("" if compression == "none" else f", {compression}-compressed"),
        engines=engines,
    )


@dataclass
class Prepared:
    """
    A dump made loadable.

    Attributes:
        path: The file or directory the engine's restore is given.
        format: What it was.
        converted: Whether a file was written for it (bzip2, xz, zip).
    """

    path: Path
    format: DumpFormat
    converted: bool = False
    notes: list[str] = field(default_factory=list)


def _free_after(directory: Path) -> int:
    return shutil.disk_usage(directory).free - _HEADROOM


def _stream_into(
    source: IO[bytes], destination: Path, *, limit: int, what: str, check: Callable[[], None]
) -> int:
    """
    Copy a decompressing stream into a new owner-only file, within a limit.

    Args:
        source: The decompressed stream.
        destination: The file to create; it must not exist.
        limit: Bytes after which the copy is refused.
        what: The source's name, for the error.
        check: Called now and then; raises when the disk has run low.

    Returns:
        The bytes written.

    Raises:
        DatabaseBackupError: When the data is larger than ``limit``, or damaged.
    """
    written = 0
    descriptor = os.open(
        destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, SECRET_MODE
    )
    try:
        with os.fdopen(descriptor, "wb") as out:
            while chunk := source.read(_COPY_BUFFER):
                written += len(chunk)
                if written > limit:
                    raise DatabaseBackupError(
                        f"{what} expands to more than {format_size(limit)}",
                        details="That is past what the disk can hold, or past the cap on a "
                        "decompressed dump. Decompress it yourself on a bigger disk and "
                        "import the plain file by its path.",
                    )
                out.write(chunk)
                if written % (64 * _COPY_BUFFER) < _COPY_BUFFER:
                    check()
    except (OSError, EOFError, lzma.LZMAError, zipfile.BadZipFile) as exc:
        raise DatabaseBackupError(
            f"{what} could not be decompressed",
            details=f"{exc}. The file is damaged or not complete.",
        ) from exc
    return written


def prepare(
    source: Path,
    fmt: DumpFormat,
    engine_type: str,
    scratch: Path,
    *,
    fs: FileSystem | None = None,
) -> Prepared:
    """
    Make a detected dump into what the engine's restore reads.

    gzip is left to the engine (it decompresses into its own staging, as it
    does for its own dumps); bzip2, xz and zip are decompressed here, into
    ``scratch``, whose contents the caller removes. The file the engine is
    given is named for what it is, because the engines read ``.sql.gz`` and
    ``.dump`` differently: the name is a link in ``scratch`` to the dump, never
    a copy of it when the dump can be read where it is.

    Args:
        source: The dump (an upload's data file, or a path on the server).
        fmt: What :func:`detect_format` said.
        engine_type: ``postgresql``, ``mysql`` or ``mongodb``.
        scratch: A private directory to put what is written in.
        fs: The filesystem links are made through.

    Returns:
        What to restore from.

    Raises:
        DatabaseBackupError: When the engine cannot load this kind of dump, or
            it cannot be decompressed.
    """
    fs = fs or get_fs()
    accepted = ACCEPTS.get(engine_type, frozenset())
    if fmt.kind not in accepted:
        offered = ", ".join(sorted(_KIND_NAMES[kind] for kind in accepted)) or "no dump"
        raise DatabaseBackupError(
            f"{source.name} is {_KIND_NAMES[fmt.kind]}, and {engine_type} cannot load it",
            details=(
                f"{engine_type} imports {offered}."
                if accepted
                else f"{engine_type} has no dump to import."
            ),
        )
    notes: list[str] = []
    suffix = {
        "pg-custom": ".dump",
        "pg-tar": ".tar",
        "sql": ".sql",
        "tar-sql": ".sql",
        "mongo-archive": ".archive",
        "mongo-tree": ".tar",
    }[fmt.kind]

    if fmt.kind == "mongo-tree" and fmt.compression != "gzip":
        raise DatabaseBackupError(
            f"{source.name} is a mongodump tree in a {fmt.compression} tar",
            details="MongoDB imports a dump tree as a gzipped tar (.tar.gz). Repack it, or "
            "take the dump with 'mongodump --archive'.",
        )

    needs_plain = fmt.compression in ("bz2", "xz", "zip") or fmt.kind == "tar-sql"
    if not needs_plain:
        # gzip or nothing around it: the engine reads it as it is.
        name = f"import{suffix}" + (".gz" if fmt.compression == "gzip" else "")
        if fmt.kind == "mongo-tree":
            name = "import.tar.gz"
        link = scratch / name
        fs.symlink(source.resolve(), link)
        return Prepared(path=link, format=fmt, notes=notes)

    plain = scratch / f"import{suffix}"
    limit = min(MAX_EXPANDED_BYTES, _free_after(scratch))
    if limit <= 0:
        raise DatabaseBackupError(
            f"There is no room to decompress {source.name}",
            details=f"{scratch} has no free space left. Free some, or import a plain file.",
        )

    def room() -> None:
        if _free_after(scratch) <= 0:
            raise DatabaseBackupError(
                f"The disk filled up decompressing {source.name}",
                details=f"Free space under {scratch} and import it again.",
            )

    if fmt.kind == "tar-sql":
        with tarfile.open(source, "r:*") as tar:
            sql_members = [m for m in tar if m.isfile() and m.name.endswith(".sql")]
            if len(sql_members) != 1:
                raise DatabaseBackupError(
                    f"{source.name} holds {len(sql_members)} SQL files",
                    details="A tar is imported when it holds exactly one dump.",
                )
            from_tar = tar.extractfile(sql_members[0])
            if from_tar is None:  # pragma: no cover - a regular file always has one
                raise DatabaseBackupError(f"{source.name} cannot be read")
            _stream_into(from_tar, plain, limit=limit, what=source.name, check=room)
        notes.append("Took the one SQL dump out of the tar")
    elif fmt.compression == "zip":
        with zipfile.ZipFile(source) as bundle:
            (entry,) = [info for info in bundle.infolist() if not info.is_dir()]
            if entry.file_size > limit:
                raise DatabaseBackupError(
                    f"{source.name} expands to {format_size(entry.file_size)}",
                    details=f"{format_size(max(limit, 0))} is the most there is room for.",
                )
            with bundle.open(entry) as from_zip:
                _stream_into(from_zip, plain, limit=limit, what=source.name, check=room)
        notes.append("Decompressed the zip")
    else:
        with _open_decompressed(source, fmt.compression) as stream:
            _stream_into(stream, plain, limit=limit, what=source.name, check=room)
        notes.append(f"Decompressed the {fmt.compression} file")
    return Prepared(path=plain, format=fmt, converted=True, notes=notes)


# ------------------------------------------------------------------ the paths


def allowed_directories(config: Config | None = None) -> list[Path]:
    """
    List where the API may be told to read a dump from.

    The CLI is root on the machine and may name any file; the console is a
    client of the API, which must not become a way to point the database
    superuser at any file on the disk. So a path from the API must be inside
    the ``imports`` folder of the backup directory (drop a file there with
    ``scp``) or a directory the operator listed in ``databases.import_directories``.

    Args:
        config: The configuration; the server's when None.

    Returns:
        Absolute directories.
    """
    config = config or Config()
    directories = [config.backup_directory / "imports"]
    configured = config.get("databases.import_directories", [])
    if isinstance(configured, list):
        directories.extend(Path(item) for item in configured if isinstance(item, str))
    return [directory for directory in directories if directory.is_absolute()]


def resolve_server_path(raw: str, *, restrict: bool, config: Config | None = None) -> Path:
    """
    Check a dump's path on the server.

    Args:
        raw: The path as given.
        restrict: Hold it to :func:`allowed_directories` (what the API does).
        config: The configuration; the server's when None.

    Returns:
        The file, resolved (links followed, so the check is of the real file).

    Raises:
        ValidationError: When the path is not absolute, is not a regular file,
            has no allowed extension, or (``restrict``) is outside the
            allowed directories.
    """
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValidationError(
            "The path must be absolute", details="Give the whole path, from /.", field="path"
        )
    check_file_name(candidate.name)
    try:
        real = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValidationError(f"There is no file at {raw}", details=str(exc), field="path") from exc
    if not real.is_file():
        raise ValidationError(f"{raw} is not a regular file", field="path")
    if restrict:
        inside = any(
            real.is_relative_to(directory.resolve()) for directory in allowed_directories(config)
        )
        if not inside:
            folders = ", ".join(str(d) for d in allowed_directories(config))
            raise ValidationError(
                "That path is not in a folder dumps may be imported from",
                details=f"Put the file in one of: {folders} (or list its folder in "
                "databases.import_directories), or upload it.",
                field="path",
            )
    return real


# ----------------------------------------------------------------- the import


@dataclass
class ImportOutcome:
    """
    What an import did.

    Attributes:
        engine: The engine.
        database: The database loaded into.
        format: What the file was.
        created: The database did not exist and was created.
        replaced: It existed and was dropped and recreated first.
        safety_copy: The dump of what it held, kept; None when it did not
            exist.
        notes: What was done to the file on the way.
    """

    engine: str
    database: str
    format: DumpFormat
    created: bool
    replaced: bool
    safety_copy: str | None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The outcome as plain data, JSON-ready.
        """
        return {
            "engine": self.engine,
            "database": self.database,
            "format": self.format.to_dict(),
            "created": self.created,
            "replaced": self.replaced,
            "safety_copy": self.safety_copy,
            "notes": list(self.notes),
        }


class DatabaseImports:
    """
    Import a dump into a database, whichever way it arrived.

    Construct one per request or command.
    """

    def __init__(
        self,
        service: DatabaseService,
        *,
        backups: DatabaseBackups | None = None,
        uploads: UploadStore | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        """
        Args:
            service: The database service: engines, store, audit, logger.
            backups: The class every restore goes through. A default one over
                ``service`` when None.
            uploads: Where uploaded dumps are. A default store when None.
            fs: The filesystem scratch directories are made and removed through.
        """
        self.service = service
        self._backups = backups
        self._uploads = uploads
        self._fs = fs

    @property
    def backups(self) -> DatabaseBackups:
        """The one class every restore goes through."""
        if self._backups is None:
            self._backups = DatabaseBackups(self.service)
        return self._backups

    @property
    def uploads(self) -> UploadStore:
        """The upload sessions."""
        if self._uploads is None:
            self._uploads = UploadStore(fs=self._fs)
        return self._uploads

    @property
    def fs(self) -> FileSystem:
        """The filesystem scratch directories go through."""
        return self._fs or get_fs()

    # ------------------------------------------------------------- inspect

    def inspect(self, source: Path, *, engine: str | None = None) -> dict[str, Any]:
        """
        Say what a file is, and whether an engine can load it. Changes nothing.

        Args:
            source: The file.
            engine: An engine to judge it against.

        Returns:
            ``format`` (see :meth:`DumpFormat.to_dict`), ``size`` and, with an
            engine, ``loadable`` and ``reason``.
        """
        fmt = detect_format(source)
        answer: dict[str, Any] = {
            "format": fmt.to_dict(),
            "size": source.stat().st_size,
            "size_human": format_size(source.stat().st_size),
        }
        if engine is not None:
            manager = self.service.manager(engine)
            loadable = manager.engine_type in fmt.engines
            answer["loadable"] = loadable
            answer["reason"] = (
                None if loadable else f"{manager.DISPLAY_NAME} cannot load {_KIND_NAMES[fmt.kind]}"
            )
        return answer

    # -------------------------------------------------------------- import

    @contextmanager
    def _scratch(self) -> Iterator[Path]:
        """
        A private directory for what an import writes, removed afterwards.

        Yields:
            The directory, 0700, beside the uploads (the disk sized for dumps).
        """
        root = self.uploads.root.parent / ".imports"
        self.fs.make_dir(root, mode=SECRET_DIR_MODE, parents=True)
        directory = root / secrets.token_hex(8)
        self.fs.make_dir(directory, mode=SECRET_DIR_MODE)
        try:
            yield directory
        finally:
            try:
                self.fs.remove_tree(directory)
            except OSError as exc:
                _log.warning(
                    "Could not remove the import's scratch directory %s: %s", directory, exc
                )

    def run(
        self,
        engine: str,
        database: str,
        source: Path,
        *,
        replace: bool = False,
        keep_owners: bool = False,
        safety_backup: bool = True,
        file_name: str | None = None,
        on_step: Callable[[str], None] | None = None,
    ) -> ImportOutcome:
        """
        Import a dump into a database, creating it when it does not exist.

        Args:
            engine: The engine.
            database: The database to load into.
            source: The dump: a file on this server.
            replace: Drop and recreate the database first (a safety copy is
                taken before, and put back when the load fails).
            keep_owners: PostgreSQL: keep the owners and privileges the dump
                names instead of giving everything to the database's owner.
            safety_backup: Dump the database first when it is loaded over
                rather than replaced.
            file_name: The name the dump had where it came from, when
                ``source`` is a stored upload; checked for its extension.
            on_step: Told what is happening, for a job's progress.

        Returns:
            What was done.

        Raises:
            ValidationError: When the name or the options are not usable.
            DatabaseBackupError: When the file is not a dump the engine can
                load, or the load fails (what the database held is put back
                when the error says so).
            DatabaseEngineError: When the engine is not running.
        """
        manager = self.service.running(engine)
        name = manager.validate_database_name(database)
        check_file_name(file_name or source.name)
        if manager.engine_type == "redis":
            raise ValidationError(
                "Redis has no dump to import here",
                details="Restore a Redis snapshot with 'noust db restore'.",
                field="engine",
            )

        def step(text: str) -> None:
            self.service.logger.info(text)
            if on_step:
                on_step(text)

        step(f"Reading {file_name or source.name}")
        fmt = detect_format(source)
        exists = manager.database_exists(name)
        options: dict[str, Any] = {}
        if manager.engine_type == "postgresql":
            options["keep_owners"] = keep_owners
            if not keep_owners and exists:
                owner = self._owner_of(manager, name)
                if owner:
                    options["owner_role"] = owner
        with self._scratch() as scratch:
            prepared = prepare(source, fmt, manager.engine_type, scratch, fs=self.fs)
            for note in prepared.notes:
                step(note)
            step("Checking the dump")
            verdict = check_dump(manager, prepared.path)
            if not verdict.ok:
                raise DatabaseBackupError(
                    f"{file_name or source.name} failed its check; nothing was loaded",
                    details=verdict.detail,
                )
            step(f"Loading into {name}" + (" (replacing it)" if exists and replace else ""))
            try:
                outcome = self._load(
                    manager, name, prepared.path, exists, replace, safety_backup, options
                )
            except DatabaseError:
                if not exists:
                    self._forget_new(manager, name)
                raise
        self.service.audit(
            "db.import",
            f"{manager.ENGINE_NAME}/{name}",
            file=file_name or source.name,
            format=fmt.kind,
            compression=fmt.compression,
            created=not exists,
            replaced=exists and replace,
            keep_owners=keep_owners,
            safety_copy=outcome.safety_copy.name if outcome.safety_copy else None,
        )
        return ImportOutcome(
            engine=manager.ENGINE_NAME,
            database=name,
            format=fmt,
            created=not exists,
            replaced=exists and replace,
            safety_copy=outcome.safety_copy.name if outcome.safety_copy else None,
            notes=prepared.notes,
        )

    def _owner_of(self, manager: BaseDatabaseManager, database: str) -> str | None:
        """
        Name who owns a database, so a dump's objects can be given to them.

        Args:
            manager: The engine.
            database: The database.

        Returns:
            The owning role, or None when the engine cannot say.
        """
        try:
            return manager.get_database_info(database).owner
        except DatabaseError as exc:
            self.service.logger.warning(f"Could not read the owner of '{database}': {exc}")
            return None

    def _load(
        self,
        manager: BaseDatabaseManager,
        name: str,
        path: Path,
        exists: bool,
        replace: bool,
        safety_backup: bool,
        options: dict[str, Any],
    ) -> RestoreOutcome:
        """
        Hand the prepared dump to the one restore.

        Args:
            manager: The engine.
            name: The target database.
            path: The prepared dump.
            exists: Whether the target is already there.
            replace: Drop and recreate it first.
            safety_backup: Take the safety copy when nothing is dropped.
            options: The engine's restore options.

        Returns:
            What the restore did.
        """
        engine = manager.ENGINE_NAME
        if exists:
            return self.backups.restore_file(
                engine, name, path, replace, safety_backup, None, options=options
            )
        # A database that is not there is made through the restore-as-new path:
        # isolated, so a dump that switches database reaches nothing else, and
        # recorded in the store once it loads.
        return self.backups.restore_file(engine, name, path, False, False, name, options=options)

    def _forget_new(self, manager: BaseDatabaseManager, name: str) -> None:
        """
        Remove a database an import created and could not fill.

        Args:
            manager: The engine.
            name: The database.
        """
        try:
            if manager.database_exists(name):
                manager.drop_database(name, force=True)
                self.service.logger.warning(
                    f"Dropped '{name}': the import created it and did not complete"
                )
        except DatabaseError as exc:
            self.service.logger.warning(
                f"The import of '{name}' failed and the half-loaded database could not be "
                f"dropped: {exc}"
            )

    def run_upload(
        self,
        engine: str,
        database: str,
        upload_id: str,
        *,
        replace: bool = False,
        keep_owners: bool = False,
        safety_backup: bool = True,
        on_step: Callable[[str], None] | None = None,
    ) -> ImportOutcome:
        """
        Import a finished upload, and remove it once it has been loaded.

        The upload stays when the import fails: a retry should not mean
        sending ten gigabytes again. It is swept after a day.

        Args:
            engine: The engine.
            database: The database to load into.
            upload_id: The finished upload.
            replace: See :meth:`run`.
            keep_owners: See :meth:`run`.
            safety_backup: See :meth:`run`.
            on_step: Told what is happening.

        Returns:
            What was done.

        Raises:
            ValidationError: When there is no such finished upload.
            DatabaseBackupError: As :meth:`run`.
        """
        upload = self.uploads.get(upload_id)
        path = self.uploads.path(upload_id)
        outcome = self.run(
            engine,
            database,
            path,
            replace=replace,
            keep_owners=keep_owners,
            safety_backup=safety_backup,
            file_name=upload.file_name,
            on_step=on_step,
        )
        self.uploads.discard(upload_id)
        return outcome
