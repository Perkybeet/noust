# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which Noust can open which store: the table that makes going back a plan (checklist N-27).

The store's schema only moves forward. A release opens a store written at its own schema or an
older one (migrating the older ones, after keeping a copy beside the file), and refuses a store
written by a newer release instead of guessing (``StoreError`` in
:meth:`~noust.core.store.NoustStore._ensure_schema`). So going back to an earlier Noust is a
question with a precise answer: is the store on disk at a schema that release understands? If it
is, install the older package. If it is not, the older release cannot read it, and the way back is
the copy the upgrade kept before migrating (``noust.db.v<schema>-<time>-<pid>-<id>.bak``), at the
price of whatever changed since.

This module is that answer: :data:`SCHEMA_RELEASES` says which release first wrote each schema
(a test fails when the store's schema has no entry, so a release cannot ship without declaring
its own), :func:`plan` says what to do to reach a release, and :func:`kept_copies` finds the
copies. ``noust store compat`` prints it; ``docs/DOWNGRADING.md`` explains it.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: The first release that writes each schema version. A release understands every schema up to
#: the highest one at or before it in this table. Releases before 2.0.0 are not listed.
SCHEMA_RELEASES: dict[int, str] = {
    8: "2.0.0",
    9: "2.1.0",
    10: "2.2.0",
    11: "3.0.0",
    12: "3.1.0",
    13: "3.2.0",
    14: "3.4.0",
}

#: ``noust.db.v13-20261003T101500Z-4242-ab12cd.bak``: the copy the upgrade kept, by schema.
_COPY_NAME = re.compile(r"\.v(?P<schema>\d+)-(?P<stamp>\d{8}T\d{6}Z)(?:-[A-Za-z0-9]+)*\.bak$")


def release_key(release: str) -> tuple[int, ...]:
    """
    Order a release string.

    Args:
        release: ``3.3.1``, with an optional leading ``v``.

    Returns:
        Its numeric parts.

    Raises:
        ValueError: It is not a dotted number.
    """
    parts = release.strip().removeprefix("v").split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"{release!r} is not a release such as 3.3.1")
    return tuple(int(part) for part in parts)


def schema_of_release(release: str) -> int | None:
    """
    The schema a release writes and the newest one it understands.

    Args:
        release: A release, such as ``3.3.1``.

    Returns:
        The highest schema in :data:`SCHEMA_RELEASES` first written at or before that release;
        None for a release older than the table starts.

    Raises:
        ValueError: ``release`` is not a release.
    """
    wanted = release_key(release)
    known = [schema for schema, first in SCHEMA_RELEASES.items() if release_key(first) <= wanted]
    return max(known) if known else None


def oldest_release_for(schema: int) -> str | None:
    """
    The oldest release that can open a store at a schema.

    Args:
        schema: The store's schema version.

    Returns:
        The first release whose schema is at least ``schema``; None when no release in the table
        writes one that new (a store from a build this table does not know).
    """
    candidates = [first for known, first in SCHEMA_RELEASES.items() if known >= schema]
    return min(candidates, key=release_key) if candidates else None


@dataclass(frozen=True)
class KeptCopy:
    """
    A copy of the store the upgrade kept before migrating it.

    Attributes:
        path: Where it is.
        schema: The schema it holds.
        taken_at: When, from its name (UTC).
        size: Bytes.
    """

    path: Path
    schema: int
    taken_at: datetime
    size: int

    def to_dict(self) -> dict[str, Any]:
        """Returns: The copy as plain data, JSON-ready."""
        return {
            "path": str(self.path),
            "schema": self.schema,
            "taken_at": self.taken_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": self.size,
        }


def kept_copies(db_path: Path) -> list[KeptCopy]:
    """
    Find the copies Noust kept beside the store, newest first.

    Args:
        db_path: The store file.

    Returns:
        Each ``<store name>.v<schema>-<time>-....bak`` regular file next to it. A link is not a
        copy.
    """
    found: list[KeptCopy] = []
    if not db_path.parent.is_dir():
        return found
    for path in db_path.parent.glob(f"{db_path.name}.v*.bak"):
        match = _COPY_NAME.search(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            continue
        taken = datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
        found.append(KeptCopy(path, int(match.group("schema")), taken, path.stat().st_size))
    return sorted(found, key=lambda copy: copy.taken_at, reverse=True)


@dataclass(frozen=True)
class DowngradePlan:
    """
    What it takes to run an earlier release on this store.

    Attributes:
        target: The release asked for.
        store_schema: The schema on disk now.
        target_schema: The newest schema the release understands (None: older than the table).
        compatible: Whether the release can open the store as it is.
        copy: When it cannot, the newest kept copy it can open; None if there is none.
        steps: What to do, in order, in the words the CLI prints.
    """

    target: str
    store_schema: int
    target_schema: int | None
    compatible: bool
    copy: KeptCopy | None = None
    steps: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Returns: The plan as plain data, JSON-ready."""
        return {
            "target": self.target,
            "store_schema": self.store_schema,
            "target_schema": self.target_schema,
            "compatible": self.compatible,
            "copy": self.copy.to_dict() if self.copy else None,
            "steps": list(self.steps),
        }


def plan(
    target: str,
    store_schema: int,
    copies: Iterable[KeptCopy],
    *,
    db_path: Path,
) -> DowngradePlan:
    """
    Say how to run ``target`` on a store at ``store_schema``.

    Args:
        target: The release to go back to.
        store_schema: The schema on disk now.
        copies: The kept copies, newest first (:func:`kept_copies`).
        db_path: The store file, for the commands in the steps.

    Returns:
        The plan. Compatible: install the older package. Not compatible: restore the newest copy
        whose schema the release understands, and say what that loses.

    Raises:
        ValueError: ``target`` is not a release.
    """
    release_key(target)
    understood = schema_of_release(target)
    if understood is not None and store_schema <= understood:
        return DowngradePlan(
            target,
            store_schema,
            understood,
            True,
            steps=(
                f"Noust {target} understands this store (schema {store_schema}): install it "
                "(for example apt install noust=<version>, dnf downgrade noust, pip install "
                f"noust=={target}) and restart the console.",
            ),
        )
    usable = None
    if understood is not None:
        usable = next((c for c in copies if c.schema <= understood), None)
    if usable is None:
        return DowngradePlan(
            target,
            store_schema,
            understood,
            False,
            steps=(
                f"Noust {target} cannot open this store (schema {store_schema}), and no copy it "
                "can open was kept beside it. Take the store from a self-backup made before the "
                "upgrade (noust self-backup list), or stay on this release.",
            ),
        )
    return DowngradePlan(
        target,
        store_schema,
        understood,
        False,
        copy=usable,
        steps=(
            f"Noust {target} cannot open this store (schema {store_schema}; it understands up to "
            f"{understood}). The copy kept before the upgrade can be opened: {usable.path.name}, "
            f"schema {usable.schema}, taken {usable.taken_at:%Y-%m-%d %H:%M} UTC.",
            "Stop the console and the monitor: systemctl stop noust-web noust-monitor",
            "Keep the current store aside, with its write-ahead log (a missing -wal or -shm is "
            f"fine): mkdir -p {db_path.parent}/before-downgrade && "
            f"mv {db_path} {db_path}-wal {db_path}-shm {db_path.parent}/before-downgrade/",
            f"Put the copy in its place: cp {usable.path} {db_path} && chmod 600 {db_path}",
            f"Install Noust {target}, then start the console again.",
            "Whatever changed in Noust since that copy was taken (applications, accounts, "
            "settings) is not in it.",
        ),
    )


def read_schema(db_path: Path) -> int | None:
    """
    Read the schema version a store file says it has, without opening it for writing.

    Args:
        db_path: The store file.

    Returns:
        The highest recorded version; None when the file is absent or has no version table.

    Raises:
        sqlite3.Error: The file is not a database.
    """
    if not db_path.exists():
        return None
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0] or 0)
    finally:
        connection.close()
