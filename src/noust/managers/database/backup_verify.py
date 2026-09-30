# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Is this dump one Noust could restore?

Two checks, from cheap to expensive:

- :func:`check_dump` reads the file the way the engine's own tool would and
  says whether it is whole: ``pg_restore --list`` for a PostgreSQL archive, the
  ``PostgreSQL database dump complete`` trailer of a plain one, mysqldump's
  ``-- Dump completed`` line, ``tar -tzf`` for a mongodump archive,
  ``redis-check-rdb`` for a snapshot. A dump that is 0 bytes, truncated or
  corrupt fails here, with the tool's own words, before anything depends on it.
- :func:`restore_test` loads the dump into a temporary database and drops it
  again: the only proof a dump restores, and the one an operator otherwise
  learns about on the worst day. The temporary name is generated here and
  checked again before the drop, so the drop can only ever reach it.

Every process goes through the manager's runner (rule 1); reading the head and
the tail of a file is plain file access.
"""

from __future__ import annotations

import re
import secrets
import tempfile
import time
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import DatabaseBackupError, DatabaseError
from noust.managers.database.base import BaseDatabaseManager, backup_format
from noust.managers.database.postgres import CUSTOM_FORMAT_SIGNATURE
from noust.managers.database.psql_script import check_plain_dump

#: Deadline for a verification tool. A listing of an archive is fast; this is
#: for a very large one on a slow disk.
_CHECK_TIMEOUT = 1800

#: Deadline for decompressing a dump into the verification directory.
_DECOMPRESS_TIMEOUT = 3600

#: How much of the end of a text dump is read to find its trailer.
_TAIL_BYTES = 16 * 1024

#: What a plain PostgreSQL dump starts and ends with.
_PG_HEAD = "PostgreSQL database dump"
_PG_TAIL = "PostgreSQL database dump complete"

#: What mysqldump writes last, with or without the date.
_MYSQL_TAIL = re.compile(r"^-- Dump completed", re.MULTILINE)

#: Where a tar archive says so: "ustar" at offset 257 of its first header.
_TAR_MAGIC_OFFSET = 257
_TAR_MAGIC = b"ustar"

#: The first bytes of a Redis snapshot.
_RDB_MAGIC = b"REDIS"

#: A temporary database's name: generated here, nowhere else.
_TEMP_NAME = re.compile(r"noust_verify_[0-9a-f]{8}")


@dataclass(frozen=True)
class DumpCheck:
    """
    What a look at a dump found.

    Attributes:
        ok: Whether it is whole.
        method: What looked (``pg_restore --list``, ``tail``, ``size``...).
        detail: What was found; on a failure, the tool's own words.
    """

    ok: bool
    method: str
    detail: str


@dataclass(frozen=True)
class RestoreTest:
    """
    What loading a dump into a temporary database showed.

    Attributes:
        ok: Whether it loaded (and the temporary database is gone).
        database: The temporary database's name.
        detail: The evidence: tables found and how long it took, or the
            loader's own words when it failed.
        seconds: How long the load took.
    """

    ok: bool
    database: str
    detail: str
    seconds: float


def _fail(method: str, detail: str) -> DumpCheck:
    """
    Args:
        method: What looked.
        detail: What it said.

    Returns:
        A failed check.
    """
    return DumpCheck(
        False, method, detail.strip() or f"{method} reported a problem and said nothing."
    )


@contextmanager
def _plain_copy(manager: BaseDatabaseManager, path: Path) -> Iterator[Path]:
    """
    Give a plain (not gzipped) file to read, decompressing when the dump is gzipped.

    The copy is made beside the backups, on their own filesystem, in a
    directory only root can enter, and removed afterwards whatever happens.

    Args:
        manager: The engine's manager, for its runner and backup directory.
        path: The dump.

    Yields:
        The dump itself when it is not gzipped, else a decompressed copy.

    Raises:
        DatabaseBackupError: When gzip cannot decompress it (it is corrupt or
            truncated): the error carries gzip's own words.
    """
    if path.suffix != ".gz":
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="noust-verify-", dir=manager.BACKUP_DIR) as work:
        target = Path(work) / path.name.removesuffix(".gz")
        result = manager.runner.capture_to_file(
            ["gzip", "-dc", str(path)], target, timeout=_DECOMPRESS_TIMEOUT
        )
        if not result.success:
            raise DatabaseBackupError(
                f"{path.name} is not a valid gzip file",
                details=result.stderr.strip() or "gzip -dc failed and said nothing.",
            )
        if not target.is_file():
            raise DatabaseBackupError(
                f"{path.name} could not be decompressed",
                details="gzip -dc reported success but wrote no file.",
            )
        yield target


def _tail(path: Path) -> str:
    """
    Args:
        path: A text file.

    Returns:
        Its last :data:`_TAIL_BYTES` bytes, decoded loosely.
    """
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - _TAIL_BYTES))
        return handle.read().decode("utf-8", errors="replace")


def _head(path: Path, count: int = 2048) -> str:
    """
    Args:
        path: A text file.
        count: Bytes to read.

    Returns:
        Its first bytes, decoded loosely.
    """
    with open(path, "rb") as handle:
        return handle.read(count).decode("utf-8", errors="replace")


def _postgres_format(path: Path) -> str:
    """
    Tell a PostgreSQL archive from a plain dump by the file's first bytes.

    The same signatures :meth:`PostgresManager._dump_format` reads when it
    restores, so the check and the restore agree on what a file is; the name
    is only a hint, since an application backup made before 3.1 calls a custom
    dump ``.sql.gz`` as readily as a plain one.

    Args:
        path: The dump, not gzipped.

    Returns:
        ``custom``, ``tar`` or ``plain``.
    """
    with open(path, "rb") as handle:
        head = handle.read(_TAR_MAGIC_OFFSET + len(_TAR_MAGIC))
    if head.startswith(CUSTOM_FORMAT_SIGNATURE):
        return "custom"
    if head[_TAR_MAGIC_OFFSET:].startswith(_TAR_MAGIC):
        return "tar"
    return "plain"


def _check_postgresql(manager: BaseDatabaseManager, path: Path, size: int) -> DumpCheck:
    """
    Check a PostgreSQL dump: an archive by listing it, a plain one by its trailer.

    Args:
        manager: The PostgreSQL manager.
        path: The dump.
        size: Its size.

    Returns:
        The check.
    """
    try:
        with _plain_copy(manager, path) as readable:
            fmt = _postgres_format(readable)
            if fmt in ("custom", "tar"):
                result = manager.runner.run(
                    ["pg_restore", "--list", str(readable)], timeout=_CHECK_TIMEOUT
                )
                if not result.success:
                    return _fail("pg_restore --list", result.stderr or result.stdout)
                entries = sum(
                    1
                    for line in result.stdout.splitlines()
                    if line.strip() and not line.startswith(";")
                )
                if entries == 0:
                    return _fail("pg_restore --list", "The archive lists no objects.")
                return DumpCheck(True, "pg_restore --list", f"{entries} objects listed.")
            try:
                check_plain_dump(readable)
            except DatabaseBackupError as exc:
                return _fail("psql script check", exc.details or str(exc))
            if _PG_HEAD not in _head(readable):
                return _fail("header", "The file does not start like a pg_dump dump.")
            if _PG_TAIL not in _tail(readable):
                return _fail(
                    "trailer",
                    f"The dump does not end with '{_PG_TAIL}': it was cut short.",
                )
            return DumpCheck(True, "trailer", f"pg_dump trailer found in {size} bytes.")
    except DatabaseBackupError as exc:
        return _fail("gzip -dc", exc.details or str(exc))
    except (OSError, EOFError, zlib.error) as exc:
        return _fail("read", f"{exc}")


def _check_mysql(manager: BaseDatabaseManager, path: Path, size: int) -> DumpCheck:
    """
    Check a MySQL or MariaDB dump by mysqldump's last line.

    Args:
        manager: The MySQL manager.
        path: The dump.
        size: Its size.

    Returns:
        The check.
    """
    try:
        with _plain_copy(manager, path) as readable:
            if not _MYSQL_TAIL.search(_tail(readable)):
                return _fail(
                    "trailer",
                    "The dump does not end with '-- Dump completed': it was cut short.",
                )
            return DumpCheck(True, "trailer", f"mysqldump trailer found in {size} bytes.")
    except DatabaseBackupError as exc:
        return _fail("gzip -dc", exc.details or str(exc))
    except (OSError, EOFError, zlib.error) as exc:
        return _fail("read", f"{exc}")


def _check_mongodb(manager: BaseDatabaseManager, path: Path, size: int) -> DumpCheck:
    """
    Check a mongodump archive by listing it.

    Args:
        manager: The MongoDB manager.
        path: The archive.
        size: Its size.

    Returns:
        The check.
    """
    result = manager.runner.run(["tar", "-tzf", str(path)], timeout=_CHECK_TIMEOUT)
    if not result.success:
        return _fail("tar -tzf", result.stderr or result.stdout)
    entries = [line for line in result.stdout.splitlines() if line.strip()]
    if not entries:
        return _fail("tar -tzf", "The archive holds nothing.")
    return DumpCheck(True, "tar -tzf", f"{len(entries)} entries listed.")


def _check_redis(manager: BaseDatabaseManager, path: Path, size: int) -> DumpCheck:
    """
    Check a Redis snapshot with ``redis-check-rdb``, or its magic bytes without it.

    Args:
        manager: The Redis manager.
        path: The snapshot (or an append-only file).
        size: Its size.

    Returns:
        The check.
    """
    kind = backup_format(path)
    tool = "redis-check-aof" if kind == "aof" else "redis-check-rdb"
    try:
        with _plain_copy(manager, path) as readable:
            if manager.runner.exists(tool):
                result = manager.runner.run([tool, str(readable)], timeout=_CHECK_TIMEOUT)
                text = (result.stdout + "\n" + result.stderr).strip()
                if not result.success:
                    return _fail(tool, text)
                return DumpCheck(True, tool, text.splitlines()[-1] if text else "Checked.")
            if kind == "rdb" and not _head(readable, len(_RDB_MAGIC)).startswith("REDIS"):
                return _fail("header", "The file does not start with the Redis snapshot magic.")
            return DumpCheck(
                True, "header", f"{tool} is not installed; the snapshot's header was checked."
            )
    except DatabaseBackupError as exc:
        return _fail("gzip -dc", exc.details or str(exc))
    except (OSError, EOFError, zlib.error) as exc:
        return _fail("read", f"{exc}")


_CHECKERS: dict[str, Callable[[BaseDatabaseManager, Path, int], DumpCheck]] = {
    "postgresql": _check_postgresql,
    "mysql": _check_mysql,
    "mongodb": _check_mongodb,
    "redis": _check_redis,
}


def check_dump(manager: BaseDatabaseManager, path: Path) -> DumpCheck:
    """
    Say whether a dump is whole, as the engine's own tool reads it.

    Args:
        manager: The engine's manager.
        path: The dump.

    Returns:
        The check; never raises for a dump that is bad, since a bad dump is
        the answer.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return _fail("size", f"The dump cannot be read: {exc}")
    if size == 0:
        return _fail("size", "The dump is empty (0 bytes).")
    checker = _CHECKERS.get(manager.ENGINE_NAME)
    if checker is None:
        return DumpCheck(True, "size", f"{size} bytes; no integrity check exists for this engine.")
    return checker(manager, path, size)


def temporary_name() -> str:
    """
    Returns:
        A fresh name for a restore test's database.
    """
    return f"noust_verify_{secrets.token_hex(4)}"


def restore_test(manager: BaseDatabaseManager, path: Path) -> RestoreTest:
    """
    Load a dump into a temporary database, look, and drop it.

    Nothing that exists is touched: the database is new, its name is
    generated, and the drop refuses any other name. When the drop fails the
    test says so and names the database to remove by hand, because a leftover
    database is a cost the operator must hear about.

    Args:
        manager: The engine's manager. A Redis snapshot replaces the whole
            instance and cannot be tested this way, so the caller must not
            ask.
        path: The dump.

    Returns:
        What happened. A failed load is a result, not an exception.
    """
    for _ in range(5):
        name = temporary_name()
        if not manager.database_exists(name):
            break
    else:
        # Five collisions in a row on eight random hex digits is not luck: do not
        # load into, or drop, a database Noust did not make.
        return RestoreTest(False, name, f"Every temporary name tried ({name}) already exists.", 0.0)
    started = time.monotonic()
    loaded = False
    failure = ""
    tables: int | None = None
    try:
        # Isolated: a dump taken with --databases names the database it came
        # from, and loading it into the temporary one would write there.
        manager.restore(name, path, drop_existing=False, safety_backup=False, isolated=True)
        loaded = True
        try:
            tables = manager.get_database_info(name).tables
        except DatabaseError:
            tables = None
    except DatabaseError as exc:
        failure = exc.details or str(exc)
    seconds = time.monotonic() - started

    leftover = ""
    if _TEMP_NAME.fullmatch(name) and (loaded or manager.database_exists(name)):
        try:
            manager.drop_database(name, force=True)
        except DatabaseError as exc:
            leftover = (
                f"\nThe temporary database {name} could not be dropped and is still there: "
                f"{exc.details or exc}. Drop it by hand."
            )
    if not loaded:
        return RestoreTest(False, name, (failure + leftover).strip(), seconds)
    found = f"{tables} tables" if tables is not None else "loaded"
    return RestoreTest(
        not leftover,
        name,
        f"Loaded into {name} in {seconds:.1f} s ({found}); dropped afterwards.{leftover}",
        seconds,
    )
