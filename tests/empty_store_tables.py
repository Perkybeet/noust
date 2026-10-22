# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tables a store double does not model, as tables that hold nothing.

Modules that keep their own rows (the extra copies of an application, the
processes that run beside it) read them through ``store._transaction()``. A test
double of the store that only answers ``get_app`` and ``list_services`` would fail
on the first of them; giving it ``_transaction`` from here says that nothing else
is recorded, which is what those tests are about.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class NoRows:
    """A cursor over tables that hold nothing."""

    def execute(self, *_args: Any, **_kwargs: Any) -> NoRows:
        """Accept any statement and stay empty."""
        return self

    def fetchone(self) -> None:
        """Return no row."""
        return None

    def fetchall(self) -> list[Any]:
        """Return no rows."""
        return []


@contextmanager
def empty_transaction() -> Iterator[NoRows]:
    """
    Stand in for ``store._transaction()``.

    Yields:
        A cursor that finds nothing.
    """
    yield NoRows()
