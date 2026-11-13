# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A rehearsal that belongs to one request, not to the process.

``--dry-run`` installs a rehearsing runner and filesystem for the whole process
(:func:`noust.core.runner.set_runner`, :func:`noust.core.fs.set_fs`), which is what a command wants.
A console that serves other requests while one of them asks "what would this update do" cannot use
that: for as long as it lasted, every other request would rehearse too and report success for
changes that were never made.

This is the same engine with a narrower reach. The two seams, ``get_runner()`` and ``get_fs()``,
look here before they look at the process-wide object, so whatever runs inside :func:`scoped`, in
the thread that entered it or in a worker handed a copy of its context, is rehearsed, and nothing
else is. The store needs nothing of its own: it already rolls back its writes while
:func:`noust.core.fs.is_rehearsal` is true, and that asks the filesystem.

This module imports nothing of Noust at run time: the runner and the filesystem import it.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.core.fs import FileSystem
    from noust.core.runner import CommandRunner

_runner: contextvars.ContextVar[CommandRunner | None] = contextvars.ContextVar(
    "noust_rehearsal_runner", default=None
)
_fs: contextvars.ContextVar[FileSystem | None] = contextvars.ContextVar(
    "noust_rehearsal_fs", default=None
)
_on_check: contextvars.ContextVar[Callable[[Sequence[str], float | None], None] | None] = (
    contextvars.ContextVar("noust_rehearsal_on_check", default=None)
)


def current_runner() -> CommandRunner | None:
    """
    Returns:
        The runner of the rehearsal this context is inside, or None when it is inside none.
    """
    return _runner.get()


def current_filesystem() -> FileSystem | None:
    """
    Returns:
        The filesystem of the rehearsal this context is inside, or None when it is inside none.
    """
    return _fs.get()


def active() -> bool:
    """
    Returns:
        Whether this context is inside a rehearsal of its own. ``--dry-run`` is not one: it
        replaces the process-wide objects and this scope never sees it.
    """
    return _runner.get() is not None or _fs.get() is not None


def note_check(argv: Sequence[str], *, seconds: float | None = None) -> bool:
    """
    Tell the rehearsal that the code would now ask a check of the application.

    A health check is not a command and not a file: Noust asks it itself, over HTTP or by running
    the application's own command, and a rehearsal has no application to ask (the release it would
    ask about was never built, and the unit was never restarted). The check is kept in the order it
    would happen, and the code goes on as if it had passed.

    Args:
        argv: What would be asked (``("GET", "http://127.0.0.1:3000/")``).
        seconds: The longest it would wait for an answer.

    Returns:
        True when a rehearsal took the note, and the caller must not ask; False when this context
        is inside none (or inside one that does not keep checks), and the caller asks for real.
    """
    sink = _on_check.get()
    if sink is None:
        return False
    sink(argv, seconds)
    return True


@contextmanager
def scoped(
    *,
    runner: CommandRunner,
    fs: FileSystem,
    on_check: Callable[[Sequence[str], float | None], None] | None = None,
) -> Iterator[None]:
    """
    Rehearse what runs inside the block, in this context only.

    Args:
        runner: The runner ``get_runner()`` answers with inside the block.
        fs: The filesystem ``get_fs()`` answers with inside the block. It decides
            :func:`~noust.core.fs.is_rehearsal`, so it must be a rehearsing one.
        on_check: Told of each check of the application the code would ask (see :func:`note_check`).

    Yields:
        Nothing; the previous scope, or none, is put back however the block ends.
    """
    tokens = (_runner.set(runner), _fs.set(fs), _on_check.set(on_check))
    try:
        yield
    finally:
        _on_check.reset(tokens[2])
        _fs.reset(tokens[1])
        _runner.reset(tokens[0])
