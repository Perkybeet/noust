# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Starting a database as a copy of another: what a preview's data is seeded with (checklist B-32).

A preview of an application with a database gets one of its own so that a pull request
cannot write into production's. Empty, it proves little: the migrations a branch adds
are never run against the data they will meet. With ``--data clone`` the preview's database
starts as a copy of the application's, made before the preview's first build, so the
branch's migrations (its ``pre_deploy`` hooks) run over a copy of real data.

The copy is a dump of the source loaded into the target through the one import every
upload and restore goes through (:class:`~noust.managers.database.imports.DatabaseImports`),
which hands what it loads to the target's own owner, so nothing the source's account owns
is left readable by the target's. The dump is written where an operator's backups are not,
read once and deleted: a second copy of production's data is not something to leave on disk.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from noust.core.fs import get_fs
from noust.managers.database.imports import DatabaseImports
from noust.managers.database.service import DatabaseService

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedOutcome:
    """
    What copying a database did.

    Attributes:
        source: ``engine/database`` it was copied from.
        target: ``engine/database`` it was copied into.
        size: Bytes of the dump that was loaded.
    """

    source: str
    target: str
    size: int


def copy_database(
    service: DatabaseService,
    *,
    source_engine: str,
    source_database: str,
    target_engine: str,
    target_database: str,
    on_step: Callable[[str], None] | None = None,
) -> SeedOutcome:
    """
    Load a copy of one database into another that exists and is empty.

    Args:
        service: The database service: engines, store, audit, logger.
        source_engine: The engine the source is on (``postgresql``, or ``postgresql@pg16``
            for an instance Noust runs).
        source_database: The database to copy.
        target_engine: The engine the target is on.
        target_database: The database to fill; it must exist.
        on_step: Told what is happening, for a job's progress.

    Returns:
        What was copied.

    Raises:
        DatabaseError: The dump or the load failed (the engine says whether the target is as
            it was found); the dump is deleted either way.
    """

    def step(text: str) -> None:
        service.logger.info(text)
        if on_step is not None:
            on_step(text)

    step(f"Copying {source_engine}/{source_database} into {target_database}")
    info = service.dump(source_engine, source_database)
    try:
        importer = DatabaseImports(service)
        importer.run(
            target_engine,
            target_database,
            info.path,
            safety_backup=False,
            on_step=on_step,
        )
    finally:
        # The copy of production's data is deleted whether or not it loaded.
        try:
            get_fs().remove(info.path)
        except OSError as exc:
            _log.warning("Could not delete the dump %s made to seed a copy: %s", info.path, exc)
    return SeedOutcome(
        source=f"{source_engine}/{source_database}",
        target=f"{target_engine}/{target_database}",
        size=info.size,
    )


__all__ = ["SeedOutcome", "copy_database"]
