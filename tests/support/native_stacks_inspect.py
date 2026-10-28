# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_native_stacks_inspect.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from noust.core.runner import set_runner
from noust.core.store import NoustStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """
    Provide an isolated store, installed as the process-wide singleton.

    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()
    set_runner(None)


def project(root: Path, files: Mapping[str, str]) -> str:
    """
    Args:
        root: Where to make the project.
        files: Path to content.

    Returns:
        The project's path, as the source of an inspection.
    """
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return str(root)


GO = {"go.mod": "module example.com/app\n\ngo 1.22\n", "main.go": "package main\n"}
