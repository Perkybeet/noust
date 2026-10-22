# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The SQLite databases an application keeps: find them, look at them, check them, copy them.

Most of the applications Noust runs keep their data in a SQLite file (Vaultwarden, Memos,
PocketBase, Uptime Kuma, a Django project left on its default). Until now that file was only a
path in ``persistent_paths``: backed up consistently
(:mod:`noust.managers.sqlite_snapshot`) but invisible. This module is what makes it a
resource of the application: ``noust db sqlite list`` and the application's Database tab say
which files an application holds, how big each is, whether it is in WAL mode, and whether
SQLite itself finds it sound.

Everything here reads. The listing reads the first 100 bytes of each regular file (the
header), a check opens the database read-only, and a copy is SQLite's backup API; none of
them takes a lock for longer than the work needs, and none writes into the application's
tree. A repository is untrusted input, so a link is never followed: neither while looking for
files nor when a client names one.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from noust.core.exceptions import DatabaseError, ValidationError
from noust.core.fs import get_fs
from noust.core.store import App
from noust.deployers.helpers.layout import RELEASES, app_root
from noust.deployers.releases import SHARED_DIR
from noust.managers.sqlite_snapshot import (
    COMPANION_SUFFIXES,
    SQLITE_MAGIC,
    is_sqlite,
    open_readonly,
)
from noust.managers.sqlite_snapshot import snapshot as take_snapshot

#: Directories that hold a project's dependencies or its history, never its data.
SKIP_DIRECTORIES = frozenset(
    {"node_modules", ".git", "vendor", "venv", ".venv", "__pycache__", "site-packages", ".cache"}
)

#: The most files a search looks at before it says it stopped: a tree of that size is not
#: where an application keeps a database.
MAX_VISITED = 200_000

#: The most databases a listing returns.
MAX_FOUND = 200

#: The most databases a restore drill checks in one backup.
MAX_TREE_CHECKS = 50

#: Seconds a search may take before it stops and says so. The console asks for the listing every
#: time its Database tab opens, so a tree of a hundred thousand files must not hold it for long.
SEARCH_SECONDS = 8.0

#: How many problems a check reports; SQLite stops counting there.
MAX_PROBLEMS = 20

#: Seconds a check may run before it is stopped and says so. SQLite counts them between the
#: steps of a statement, and its scan of the pages is a single step: the budget bounds the
#: checks of rows and indexes, so whoever offers a check to a client also bounds the size.
DEFAULT_BUDGET = 30.0

#: Seconds a check waits for a writer to let go of a database in the rollback journal mode.
LOCK_WAIT = 5.0

#: The largest database a request to the console's API is asked to check; the terminal has no limit.
MAX_REQUEST_BYTES = 1024 * 1024 * 1024

#: Bytes of the header SQLite writes at the start of every database.
HEADER_SIZE = 100

#: Journal modes the header can tell apart: byte 18 is 2 for WAL and 1 for the rollback journal.
WAL = "wal"
ROLLBACK = "rollback"


@dataclass(frozen=True)
class SqliteFile:
    """
    One SQLite database inside an application's directory.

    Attributes:
        path: Where it is, relative to the application's directory, with ``/`` separators.
        size: Bytes in the file itself.
        wal_bytes: Bytes waiting in its ``-wal`` file: committed, and not yet in ``size``.
        modified: When the file last changed, seconds since the epoch.
        journal_mode: ``wal`` or ``rollback``, as the header says.
        page_size: Bytes in a page; 0 when the header does not say.
        pages: Pages in the file.
    """

    path: str
    size: int
    wal_bytes: int
    modified: float
    journal_mode: str
    page_size: int
    pages: int

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The fields as JSON-serialisable values.
        """
        return {
            "path": self.path,
            "size": self.size,
            "wal_bytes": self.wal_bytes,
            "modified": self.modified,
            "journal_mode": self.journal_mode,
            "page_size": self.page_size,
            "pages": self.pages,
        }


@dataclass(frozen=True)
class SqliteListing:
    """
    What a search for an application's SQLite databases found.

    Attributes:
        domain: The application.
        files: The databases, by path.
        searched: The directories searched, relative to the application's directory.
        truncated: True when the search stopped at a limit and more may exist.
    """

    domain: str
    files: list[SqliteFile]
    searched: list[str]
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The listing as JSON-serialisable values.
        """
        return {
            "domain": self.domain,
            "files": [item.to_dict() for item in self.files],
            "searched": self.searched,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class SqliteCheck:
    """
    What SQLite said about one database.

    Attributes:
        path: The database, relative to the application's directory.
        ok: True when SQLite found nothing wrong.
        complete: False when the budget ran out first: ``ok`` then says only that nothing
            had been found up to there.
        full: True for ``integrity_check`` (every row and index), False for ``quick_check``.
        problems: What SQLite reported, at most :data:`MAX_PROBLEMS`.
        seconds: How long it took.
    """

    path: str
    ok: bool
    complete: bool
    full: bool
    problems: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result as JSON-serialisable values.
        """
        return {
            "path": self.path,
            "ok": self.ok,
            "complete": self.complete,
            "full": self.full,
            "problems": self.problems,
            "seconds": self.seconds,
        }


def _describe(path: Path, relative: str) -> SqliteFile | None:
    """
    Read a database's header.

    Args:
        path: A file that starts with SQLite's magic.
        relative: Its path relative to the application's directory.

    Returns:
        The description, or None when the file vanished or cannot be read.
    """
    try:
        info = path.lstat()
        with path.open("rb") as handle:
            header = handle.read(HEADER_SIZE)
    except OSError:
        return None
    if len(header) < HEADER_SIZE or not header.startswith(SQLITE_MAGIC):
        return None
    raw_page = int.from_bytes(header[16:18], "big")
    page_size = 65536 if raw_page == 1 else raw_page
    # A header that does not hold a power of two is damaged: say nothing rather than divide by it.
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        page_size = 0
    wal = path.with_name(path.name + "-wal")
    try:
        wal_bytes = wal.lstat().st_size if stat.S_ISREG(wal.lstat().st_mode) else 0
    except OSError:
        wal_bytes = 0
    return SqliteFile(
        path=relative,
        size=info.st_size,
        wal_bytes=wal_bytes,
        modified=info.st_mtime,
        journal_mode=WAL if header[18] == 2 else ROLLBACK,
        page_size=page_size,
        pages=info.st_size // page_size if page_size else 0,
    )


def search_roots(app: App) -> list[Path]:
    """
    Name where an application keeps data that survives a deploy.

    Args:
        app: The application's store row.

    Returns:
        ``shared/`` on the releases layout, where every release links its persistent paths
        from (the releases themselves hold code); the application's directory in place.
    """
    root = app_root(app)
    return [root / SHARED_DIR] if app.layout == RELEASES else [root]


def find(app: App) -> SqliteListing:
    """
    Find the SQLite databases an application keeps.

    Args:
        app: The application's store row.

    Returns:
        The databases, smallest path first. Links are not followed and a directory that holds
        dependencies is not entered.
    """
    files, searched, truncated = _search(search_roots(app), app_root(app))
    return SqliteListing(domain=app.domain, files=files, searched=searched, truncated=truncated)


def _search(starts: Sequence[Path], root: Path) -> tuple[list[SqliteFile], list[str], bool]:
    """
    Walk some directories for SQLite databases.

    Args:
        starts: Where to look, each inside ``root`` (or ``root`` itself).
        root: What the paths found are relative to.

    Returns:
        The databases by path, the places searched (relative to ``root``), and whether a limit
        stopped the search.
    """
    files: list[SqliteFile] = []
    visited = 0
    truncated = False
    searched: list[str] = []
    deadline = time.monotonic() + SEARCH_SECONDS
    for start in starts:
        searched.append(start.relative_to(root).as_posix() if start != root else ".")
        if not start.is_dir() or start.is_symlink():
            continue
        for directory, dirnames, filenames in os.walk(start, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRECTORIES)
            current = Path(directory)
            if time.monotonic() > deadline:
                truncated = True
                break
            for name in sorted(filenames):
                if name.endswith(COMPANION_SUFFIXES):
                    continue
                visited += 1
                if visited > MAX_VISITED or (visited % 512 == 0 and time.monotonic() > deadline):
                    truncated = True
                    break
                path = current / name
                if not is_sqlite(path):
                    continue
                described = _describe(path, path.relative_to(root).as_posix())
                if described is None:
                    continue
                if len(files) >= MAX_FOUND:
                    truncated = True
                    break
                files.append(described)
            if truncated:
                break
    files.sort(key=lambda item: item.path)
    return files, searched, truncated


def locate(app: App, relative: str) -> Path:
    """
    Turn a path a client named into a database inside the application's directory.

    The path is relative to the application's directory, as :func:`find` reports it. No part
    of it may be a link, so a repository cannot point a check or a copy at a file it does not
    own.

    Args:
        app: The application's store row.
        relative: The path as the client sent it.

    Returns:
        The absolute path of a regular SQLite database.

    Raises:
        ValidationError: When the path is absolute, climbs out, goes through a link, is not
            there, or is not a SQLite database.
    """
    posix = PurePosixPath(relative)
    parts = posix.parts
    if (
        not relative
        or "\x00" in relative
        or "\\" in relative
        or posix.is_absolute()
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValidationError(
            f"{relative!r} is not a path inside {app.domain}'s directory",
            details="Name it as 'noust db sqlite list' shows it, for example data/app.db.",
        )
    current = app_root(app)
    for part in parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise ValidationError(
                f"{relative} is not in {app.domain}'s directory",
                details="List what it holds with 'noust db sqlite list'.",
            ) from exc
        if stat.S_ISLNK(mode):
            raise ValidationError(
                f"{relative} goes through a link",
                details="Noust does not follow links out of an application's directory. "
                "Name the path the link points to, inside it.",
            )
    if not is_sqlite(current):
        raise ValidationError(
            f"{relative} is not a SQLite database",
            details="Only a regular file that starts with SQLite's header can be checked or copied.",
        )
    return current


def check(
    app: App, relative: str, *, full: bool = False, budget: float = DEFAULT_BUDGET
) -> SqliteCheck:
    """
    Ask SQLite whether a database is sound, without writing to it.

    ``quick_check`` reads every page and skips the cross-checks of indexes against their
    tables; ``integrity_check`` (``full``) does both and takes longer. Either one reads inside
    a transaction, so a database in the rollback journal mode does not accept a write while it
    runs: the budget stops it.

    Args:
        app: The application's store row.
        relative: The database, as :func:`find` reports it.
        full: Run ``integrity_check`` instead of ``quick_check``.
        budget: Seconds after which the check stops where it is (see :data:`DEFAULT_BUDGET`).

    Returns:
        What SQLite said.

    Raises:
        ValidationError: When the path is not a database of this application.
        DatabaseError: When the database is locked for longer than the wait, or cannot be
            opened.
    """
    return check_path(locate(app, relative), relative, full=full, budget=budget)


def check_path(
    path: Path, relative: str, *, full: bool = False, budget: float = DEFAULT_BUDGET
) -> SqliteCheck:
    """
    Ask SQLite whether the database at a path is sound, without writing to it.

    The one check (rule 3): :func:`check` finds the path inside an application, a restore drill
    finds it inside the copy it unpacked.

    Args:
        path: A database already known to be one, as :func:`locate` or :func:`find` return it.
        relative: How to call it in the answer.
        full: Run ``integrity_check`` instead of ``quick_check``.
        budget: Seconds after which the check stops where it is.

    Returns:
        What SQLite said.

    Raises:
        DatabaseError: When the database is locked for longer than the wait, or cannot be
            opened.
    """
    started = time.monotonic()
    deadline = started + budget
    pragma = "integrity_check" if full else "quick_check"
    try:
        connection = open_readonly(path, timeout=LOCK_WAIT)
    except sqlite3.Error as exc:
        raise DatabaseError(
            f"{relative} could not be opened", details=_first_line(str(exc))
        ) from exc
    complete = True
    problems: list[str] = []
    try:
        connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        try:
            rows = connection.execute(f"PRAGMA {pragma}({MAX_PROBLEMS})").fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "interrupted" in message:
                complete = False
                rows = []
            else:
                raise DatabaseError(
                    f"{relative} could not be read",
                    details="It is locked by a writer, or the disk refused: "
                    + _first_line(message),
                ) from exc
        except sqlite3.DatabaseError as exc:
            # "database disk image is malformed" and "file is not a database" are the answer.
            rows = []
            problems.append(_first_line(str(exc)))
    finally:
        connection.close()
    for row in rows:
        text = str(row[0])
        if text != "ok":
            problems.append(text)
    return SqliteCheck(
        path=relative,
        ok=not problems,
        complete=complete,
        full=full,
        problems=problems[:MAX_PROBLEMS],
        seconds=round(time.monotonic() - started, 3),
    )


def check_tree(
    root: Path, *, full: bool = False, limit: int = MAX_TREE_CHECKS
) -> list[SqliteCheck]:
    """
    Check every SQLite database found under a directory.

    For a restore drill: the directory is the copy of an application's backup, unpacked, and a
    database that does not pass SQLite's check there would not come back sound either. A
    database that cannot be opened is a failed check, not an exception, so one bad file does
    not hide the rest.

    Args:
        root: A directory, searched whole (links are not followed).
        full: Run ``integrity_check`` instead of ``quick_check``.
        limit: The most databases checked.

    Returns:
        One result per database, by path; empty when there is none.
    """
    files, _searched, _truncated = _search([root], root)
    results: list[SqliteCheck] = []
    for item in files[:limit]:
        try:
            results.append(check_path(root / item.path, item.path, full=full))
        except DatabaseError as exc:
            results.append(
                SqliteCheck(
                    path=item.path,
                    ok=False,
                    complete=True,
                    full=full,
                    problems=[f"{exc.message}: {exc.details}" if exc.details else exc.message],
                )
            )
    return results


def copy_to(app: App, relative: str, destination: Path) -> Path:
    """
    Copy a database, in use or not, to a file of its own, consistently.

    The copy is SQLite's backup API (one implementation, the one the application backups use),
    so it holds every committed transaction, the ones still in the ``-wal`` file included, and
    none half-written. The file is created private (0600) and an existing one is never
    replaced.

    Args:
        app: The application's store row.
        relative: The database, as :func:`find` reports it.
        destination: A file that does not exist yet, in a directory that does.

    Returns:
        The destination.

    Raises:
        ValidationError: When the path is not a database of this application, or the
            destination exists or has no directory.
        DatabaseError: When SQLite cannot read the database.
    """
    source = locate(app, relative)
    if not destination.parent.is_dir():
        raise ValidationError(
            f"{destination.parent} does not exist",
            details="Create the directory first, or name a file in one that exists.",
        )
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValidationError(
            f"{destination} already exists", details="A copy never replaces a file."
        ) from exc
    os.close(descriptor)
    try:
        take_snapshot(source, destination)
    except sqlite3.Error as exc:
        get_fs().remove(destination)
        raise DatabaseError(
            f"{relative} could not be copied", details=_first_line(str(exc))
        ) from exc
    return destination


def _first_line(text: str) -> str:
    """
    Args:
        text: A message from SQLite.

    Returns:
        Its first line, bounded: a message is shown to people and kept in logs.
    """
    return (text.splitlines() or [""])[0][:300]
