# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_cli_server.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.cli.commands import server as server_cli
from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from tests.support.server_support import make_machine


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "cli.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store):
    """The server made of fixtures, root, and the CLI pointed at it."""
    made = make_machine(tmp_path, monkeypatch, store)
    made.ctx.power._apps_check = lambda runner: []
    monkeypatch.setattr(server_cli, "build_context", lambda: made.ctx)
    monkeypatch.setattr(server_cli, "check_root", lambda: True)
    monkeypatch.setattr(server_cli, "RecordStore", lambda: made.ctx.records)
    return made
