# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A copy of an application that must not share its database.

A preview of a pull request and a clone of an application are the same
situation: running code that was copied from an application whose variables
point at that application's database. Left as they are, the copy writes into
the original's data. This module is the one answer to the two questions that
follow, for both:

- **Which database does the copy get?** :func:`own_database` reads the
  application's recorded databases and the variable that connects to each, and
  says what to make for the copy: an engine, a name that tells the copy's apart
  from the original's, the variable to write its connection into, and the
  instance when the original runs on one.
- **Which variables must the copy not be given?** :func:`connection_variables`
  names every variable that connects to a database (a URL, or the
  ``DB_HOST``... that a database link writes beside it): a copy with a database
  of its own is given none of the original's.

The database itself is made by the deploy that creates the copy
(:meth:`~noust.managers.database.service.DatabaseService.prepare_for_new_app`),
and dropped with the copy by whoever owns it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from noust.core.store import App, get_store

#: Longest database name a copy's own may have (MySQL's limit is 64).
MAX_NAME = 56


@dataclass(frozen=True)
class DatabaseWish:
    """
    The database a copy of an application gets of its own.

    Attributes:
        engine: The engine of the application's database it copies the kind of.
        name: What it is called.
        env_var: The variable the application reads its connection from.
        extra_vars: Also write ``DB_HOST``, ``DB_PORT``, ``DB_NAME``, ``DB_USER``
            and ``DB_PASSWORD``, because the application has them.
        source: The application's database a clone of the data starts from.
        instance: The instance Noust runs the application's database on, when it
            is not the host's engine.
    """

    engine: str
    name: str
    env_var: str
    extra_vars: bool
    source: str
    instance: str | None = None


def connection_variables(env: Mapping[str, str]) -> list[str]:
    """
    Name the variables that connect to a database.

    Args:
        env: An application's variables.

    Returns:
        Them: every one whose value is a connection URL, and the variables a
        database link writes beside the URL (``DB_HOST``, ``DB_PASSWORD``...).
    """
    from noust.managers.database.detect_links import references_in
    from noust.managers.database.linking import EXTRA_VARIABLES

    names = {name for name, value in env.items() if references_in({name: value})}
    names |= set(EXTRA_VARIABLES) & set(env)
    return sorted(names)


def own_database(app: App, env: Mapping[str, str], suffix: str) -> DatabaseWish | None:
    """
    Decide the database a copy of an application gets of its own.

    Args:
        app: The application copied.
        env: Its variables, which say which of its databases it connects to.
        suffix: What tells the copy's database apart (``pr42``, ``clone``).

    Returns:
        What to make, from the first of the application's recorded databases its
        variables connect to; None when it connects to none (a copy of it needs
        no database of its own).
    """
    from noust.managers.database.detect_links import references_in

    if app.id is None:
        return None
    recorded = {
        (db.engine.partition("@")[0], db.name): db.engine.partition("@")[2] or None
        for db in get_store().list_databases(app_id=app.id)
    }
    if not recorded:
        return None
    references = [
        reference
        for reference in references_in(dict(env))
        if (reference.engine, reference.database) in recorded
    ]
    if not references:
        return None
    first = references[0]
    base = (first.database or app.domain.split(".")[0]).strip("_") or "copy"
    name = f"{base[: MAX_NAME - len(suffix) - 1]}_{suffix}"
    extra = any(var in env for var in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"))
    return DatabaseWish(
        first.engine,
        name,
        first.variable,
        extra,
        first.database or "",
        recorded[(first.engine, first.database)],
    )
