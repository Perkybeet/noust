# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Adding a database an engine holds to Noust (4.1, item 103).

A database Noust finds on a server and does not manage (one created by hand, one that was there
before Noust) is not a different kind of database. It is a database whose state is "not added",
and what a person does with it is **add it to Noust**: from then on it is listed as Noust's,
linkable to an application, and backed up with that application. The state is Noust's; where the
database runs (the system's engine, a container) is a separate fact the database's server says,
so the two are never one word.

:func:`record_database` is the one place the store row is written, for the one database the
console adds and for every database ``noust db adopt`` adds (rule 3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.exceptions import DatabaseExistsError
from noust.core.server_text import say
from noust.core.store import Database

if TYPE_CHECKING:
    from noust.managers.database.service import DatabaseService, DatabaseView

__all__ = ["NOUST_STATES", "adopt_database", "noust_state", "record_database"]

#: What Noust knows of a database: it manages it, does not yet, it is gone from its engine, or
#: its engine could not be read this time.
NOUST_STATES = ("added", "not_added", "missing", "unverified")


def noust_state(view: DatabaseView) -> str:
    """
    Say whether Noust manages a database, apart from where it runs.

    Args:
        view: The database, as the service describes it.

    Returns:
        ``not_added`` (the engine holds it and Noust does not manage it: the action is to add
        it), ``added``, ``missing`` (Noust records it and the engine no longer has it) or
        ``unverified`` (Noust records it and its engine could not be read).
    """
    if not view.tracked:
        return "not_added"
    if view.missing:
        return "missing"
    if view.unverified:
        return "unverified"
    return "added"


def record_database(service: DatabaseService, view: DatabaseView) -> str:
    """
    Write the row that makes an engine's database Noust's.

    Args:
        service: The database service, whose store is written.
        view: The database an engine holds and the store does not record.

    Returns:
        ``engine/name``.
    """
    manager = service.manager(view.engine)
    service.store.create_database(
        Database(
            name=view.name,
            engine=view.engine,
            host="localhost",
            port=manager.server_port(),
            encoding=view.encoding,
        )
    )
    return f"{view.engine}/{view.name}"


def adopt_database(service: DatabaseService, engine: str, name: str) -> str:
    """
    Add one database an engine holds to Noust.

    Args:
        service: The database service.
        engine: The engine, or the key of the server it runs on (``mysql@legacy``).
        name: The database.

    Returns:
        ``engine/name``, as ``noust db adopt`` reports them.

    Raises:
        DatabaseNotFoundError: The engine holds no such database.
        DatabaseExistsError: Noust already manages it (``databases.already_added``).
    """
    view = service.get(engine, name)
    if view.tracked:
        raise DatabaseExistsError.from_text(
            say("databases.already_added", name=view.name, engine=view.engine)
        )
    added = record_database(service, view)
    service.audit("db.adopt", view.engine, databases=added)
    return added
