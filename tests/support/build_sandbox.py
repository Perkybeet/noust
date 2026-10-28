# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_build_sandbox.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core import paths
from noust.core.store import NoustStore
from noust.deployers.helpers import sandbox as build_sandbox


@pytest.fixture
def as_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """
    Pretend to be root, with a self-test that passes and caches in the test's directory.

    Returns:
        Where the cache root and the protected configuration directory are.
    """
    cache_root = tmp_path / "cache" / "build"
    config = tmp_path / "etc-noust"
    config.mkdir()
    monkeypatch.setattr(build_sandbox, "running_as_root", lambda: True)
    monkeypatch.setattr(paths, "BUILD_CACHE_DIR", cache_root)
    monkeypatch.setattr(paths, "config_dir", lambda: config)
    monkeypatch.setattr(
        build_sandbox,
        "self_test",
        lambda runner, fs=None: build_sandbox.SelfTest(passed=True, checks=(("ok", True, ""),)),
    )
    return SimpleNamespace(cache_root=cache_root, config=config)


def forget_regime(store: NoustStore) -> None:
    """Make the application look like one from before 3.1: no row."""
    with store._transaction() as cursor:
        cursor.execute("DELETE FROM build_sandbox")
