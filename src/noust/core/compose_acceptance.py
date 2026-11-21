# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a Compose stack was accepted with, and why it is not brought up to date or was refused.

The guard of a Compose stack judges when the stack is activated, and its unit runs
``docker compose up`` again at every start with the files and the variables that are there then.
"What the stack already had" therefore cannot be read from the tree it runs from (the stack's own
code writes it, ``.git`` included) or from the variables of today: it is written down here when a
stack is activated having passed the guard, and the unit's start and every restart Noust makes are
compared with it.

Each entry is the set of the guard's findings (the identity the guard gives each:
``(service or job, kind, thing)``, see :data:`noust.deployers.helpers.compose_guard.FindingKey`) that
the stack was given and that something accepted: a new stack's exception, an update that did not
add them, what 4.2 had started when 4.3 took the stack over (``inherited``). A stack with no entry
has never been through a deployment of this version nor been taken over: it starts as it always did,
and ``noust health`` lists it.

This module is the only one that reads or writes ``compose_accepted`` and ``compose_gate_state``
(``schema_v18.COMPOSE_SQL``). Both are keyed by the domain, like the compose exception, and
:func:`forget` is what the deletion of an application runs, so what an application was accepted with
never belongs to the next one that takes its name.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.timeutil import utc_now_iso

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = [
    "GATE_STATES",
    "SOURCES",
    "Accepted",
    "GateNote",
    "Key",
    "clear_note",
    "forget",
    "forget_record",
    "get",
    "inherited_domains",
    "note",
    "note_of",
    "notes",
    "record",
]

#: What identifies one finding of the guard: where, what, which.
Key = tuple[str, str, str]

#: How an entry came to be.
SOURCES = frozenset(
    {"deploy", "update", "redeploy", "adopt", "edit", "exception", "release", "upgrade"}
)

#: What the gate can say of a stack it did not let through, or could not bring up to date.
GATE_STATES = frozenset({"pending", "refused"})

#: What deleting an application forgets of its domain, which is keyed by the name and not by the row: the
#: pin of a branch an operator chose, the compose exception, what the stack was accepted with and what
#: the gate said of it. Written out, one statement each, so that no name is ever pasted into one.
_FORGET = (
    "DELETE FROM app_branch_pins WHERE domain = ?",
    "DELETE FROM compose_exceptions WHERE domain = ?",
    "DELETE FROM compose_accepted WHERE domain = ?",
    "DELETE FROM compose_gate_state WHERE domain = ?",
)


@dataclass(frozen=True)
class Accepted:
    """
    What a stack was accepted with.

    Attributes:
        domain: The application.
        keys: The findings of the guard the stack was given.
        source: How the entry came to be (:data:`SOURCES`).
        actor: Who accepted it; empty for something Noust took over by itself.
        accepted_at: When, ISO 8601, UTC.
        inherited: Taken over from what 4.2 had started, not decided: ``noust health`` lists it to be
            looked at.
    """

    domain: str
    keys: frozenset[Key]
    source: str
    actor: str
    accepted_at: str
    inherited: bool


@dataclass(frozen=True)
class GateNote:
    """
    What the gate has to say of a stack.

    Attributes:
        domain: The application.
        state: ``pending`` (not brought up to date, and why) or ``refused`` (its last start was held
            back, with the line that is new).
        detail: The sentence, as the operator read it.
        noted_at: When, ISO 8601, UTC.
    """

    domain: str
    state: str
    detail: str
    noted_at: str


def _store(store: NoustStore | None) -> NoustStore:
    """Return the store given, or the process-wide one (imported late: the store imports this)."""
    if store is not None:
        return store
    from noust.core.store import get_store

    return get_store()


def _keys_of(text: str) -> frozenset[Key]:
    """
    Read a stored list of findings back.

    Args:
        text: The JSON the entry holds.

    Returns:
        The keys; none for text that is not a list of triples (an entry nobody could have written).
    """
    try:
        loaded = json.loads(text)
    except ValueError:
        return frozenset()
    if not isinstance(loaded, list):
        return frozenset()
    return frozenset(
        (str(item[0]), str(item[1]), str(item[2]))
        for item in loaded
        if isinstance(item, list) and len(item) == 3
    )


def get(domain: str, *, store: NoustStore | None = None) -> Accepted | None:
    """
    Read what a stack was accepted with.

    Args:
        domain: The application.
        store: The store; the process-wide one by default.

    Returns:
        The entry, or None when the stack has none.
    """
    row = (
        _store(store)
        ._get_connection()
        .execute(
            "SELECT findings, source, accepted_by, accepted_at, inherited "
            "FROM compose_accepted WHERE domain = ?",
            (domain,),
        )
        .fetchone()
    )
    if row is None:
        return None
    return Accepted(domain, _keys_of(row[0]), row[1], row[2], row[3], bool(row[4]))


def record(
    domain: str,
    keys: Iterable[Key],
    *,
    source: str,
    actor: str = "",
    inherited: bool = False,
    replace: bool = False,
    store: NoustStore | None = None,
) -> Accepted:
    """
    Write down what a stack was given of the server and is accepted with.

    An activation that passed the guard adds what it was given to what the stack already had
    accepted: if it then fails and the stack goes back to what it replaced, that is still accepted. The
    activation that serves settles the entry with ``replace``, so what the stack stopped having is not
    accepted any more.

    Args:
        domain: The application.
        keys: The findings of the guard the stack is given.
        source: How the entry comes to be (:data:`SOURCES`).
        actor: Who accepted it.
        inherited: Whether Noust took it over from what 4.2 had started.
        replace: Keep only ``keys``, not what was there before.
        store: The store; the process-wide one by default.

    Returns:
        The entry as written.

    Raises:
        ValueError: ``source`` is not a known one, or a key is not three texts (a defect of the
            caller, never something an operator typed).
    """
    if source not in SOURCES:
        raise ValueError(f"Unknown source of what a stack was accepted with: {source!r}")
    wanted: set[Key] = set()
    for key in keys:
        if len(key) != 3 or not all(isinstance(part, str) for part in key):
            raise ValueError(f"A finding is named by three texts, not {key!r}")
        wanted.add((key[0], key[1], key[2]))
    target = _store(store)
    if not replace:
        before = get(domain, store=target)
        if before is not None:
            wanted |= before.keys
    now = utc_now_iso("seconds")
    with target._transaction() as cursor:
        cursor.execute(
            "INSERT INTO compose_accepted (domain, findings, source, accepted_by, accepted_at, "
            "inherited) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(domain) DO UPDATE SET "
            "findings = excluded.findings, source = excluded.source, "
            "accepted_by = excluded.accepted_by, accepted_at = excluded.accepted_at, "
            "inherited = excluded.inherited",
            (
                domain,
                json.dumps(sorted(wanted)),
                source,
                actor,
                now,
                1 if inherited else 0,
            ),
        )
    return Accepted(domain, frozenset(wanted), source, actor, now, inherited)


def inherited_domains(*, store: NoustStore | None = None) -> list[str]:
    """
    List the stacks whose entry was taken over from 4.2 and nobody decided since.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        Their domains, in order.
    """
    rows = (
        _store(store)
        ._get_connection()
        .execute("SELECT domain FROM compose_accepted WHERE inherited = 1 ORDER BY domain")
        .fetchall()
    )
    return [str(row[0]) for row in rows]


def note(domain: str, state: str, detail: str, *, store: NoustStore | None = None) -> GateNote:
    """
    Say why a stack is not brought up to date, or why its start was held back.

    Args:
        domain: The application.
        state: One of :data:`GATE_STATES`.
        detail: The sentence.
        store: The store; the process-wide one by default.

    Returns:
        The note as written.

    Raises:
        ValueError: ``state`` is not a known one (a defect of the caller).
    """
    if state not in GATE_STATES:
        raise ValueError(f"Unknown state of a stack's gate: {state!r}")
    now = utc_now_iso("seconds")
    with _store(store)._transaction() as cursor:
        cursor.execute(
            "INSERT INTO compose_gate_state (domain, state, detail, noted_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(domain) DO UPDATE SET state = excluded.state, detail = excluded.detail, "
            "noted_at = excluded.noted_at",
            (domain, state, detail, now),
        )
    return GateNote(domain, state, detail, now)


def note_of(domain: str, *, store: NoustStore | None = None) -> GateNote | None:
    """
    Read what the gate said of a stack.

    Args:
        domain: The application.
        store: The store; the process-wide one by default.

    Returns:
        The note, or None.
    """
    row = (
        _store(store)
        ._get_connection()
        .execute(
            "SELECT state, detail, noted_at FROM compose_gate_state WHERE domain = ?", (domain,)
        )
        .fetchone()
    )
    return None if row is None else GateNote(domain, row[0], row[1], row[2])


def notes(*, store: NoustStore | None = None) -> dict[str, GateNote]:
    """
    Read what the gate said of every stack it said something of.

    Args:
        store: The store; the process-wide one by default.

    Returns:
        Domain to note.
    """
    rows = (
        _store(store)
        ._get_connection()
        .execute("SELECT domain, state, detail, noted_at FROM compose_gate_state ORDER BY domain")
        .fetchall()
    )
    return {str(row[0]): GateNote(str(row[0]), row[1], row[2], row[3]) for row in rows}


def clear_note(domain: str, *, store: NoustStore | None = None) -> None:
    """
    Forget what the gate said of a stack.

    Args:
        domain: The application.
        store: The store; the process-wide one by default.
    """
    with _store(store)._transaction() as cursor:
        cursor.execute("DELETE FROM compose_gate_state WHERE domain = ?", (domain,))


def forget(cursor: sqlite3.Cursor, domain: str) -> None:
    """
    Forget everything a domain's stack had in the store, inside the transaction that deletes it.

    Keyed by domain, not by row: the application may never have had one (an exception can be
    recorded before the first deployment), and the next application on the name must not inherit any
    of it.

    Args:
        cursor: The cursor of the deletion's transaction.
        domain: The application.
    """
    for statement in _FORGET:
        cursor.execute(statement, (domain,))


def forget_record(domain: str, *, store: NoustStore | None = None) -> None:
    """
    Forget what a stack was accepted with and what the gate said of it, and nothing else.

    What a first deployment that failed before it made the application leaves: the exception an
    operator recorded ahead of it is theirs to keep for the next attempt.

    Args:
        domain: The application.
        store: The store; the process-wide one by default.
    """
    with _store(store)._transaction() as cursor:
        for statement in _FORGET[2:]:
            cursor.execute(statement, (domain,))
