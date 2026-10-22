# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fixtures of the recipe tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.store import NoustStore


@pytest.fixture
def recipe_store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    A store of this test's own, installed as the process-wide one.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()
