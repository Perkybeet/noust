# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Copying a SQLite database that is in use.

Copying a SQLite file while the application writes to it can capture half a
transaction, and in WAL mode it leaves the committed data in a ``-wal`` file the
copy does not hold. SQLite's own backup API reads a consistent snapshot while
the database stays open, so every backup in Noust that archives a data
directory (an application's, a volume's) asks it for one instead of copying
the file. Coolify only warns that a live copy of a database may be
inconsistent; here the common case, a SQLite file, is made consistent.

One implementation for both backups (rule 3). Only a regular file is opened:
opening a FIFO waits for a writer for ever.
"""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

#: The first bytes of every SQLite database.
SQLITE_MAGIC = b"SQLite format 3\x00"

#: Files SQLite keeps beside a database: already in the snapshot, so not archived.
COMPANION_SUFFIXES = ("-wal", "-shm", "-journal")


def is_sqlite(path: Path) -> bool:
    """
    Tell whether something found in a data directory is a SQLite database.

    Args:
        path: Anything found in a data directory.

    Returns:
        Whether it is a regular file that starts with SQLite's header.
    """
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return False
        with path.open("rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def companions_of(names: set[str]) -> set[str]:
    """
    Name the files SQLite keeps beside the given databases.

    Args:
        names: The databases' file names.

    Returns:
        Each name with ``-wal``, ``-shm`` and ``-journal`` appended.
    """
    return {f"{name}{suffix}" for name in names for suffix in COMPANION_SUFFIXES}


def snapshot(source: Path, destination: Path) -> None:
    """
    Copy a live SQLite database consistently, with SQLite's backup API.

    Args:
        source: The database, possibly in use in WAL mode.
        destination: Where the snapshot goes.

    Raises:
        sqlite3.Error: When the database cannot be read as one.
    """
    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    writer = sqlite3.connect(destination)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()
