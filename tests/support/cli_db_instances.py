# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_cli_db_instances.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.cli.commands import db as db_cli
from noust.core.config import Config
from noust.core.logger import Logger
from noust.managers.database.service import DatabaseService
from tests.support.database_instance_support import World, build_world, close_world


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    yield build_world(tmp_path, monkeypatch)
    close_world()


@pytest.fixture
def cli_world(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """The server of the managed-instances tests, with the CLI pointed at it."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    monkeypatch.setattr(
        db_cli,
        "_service",
        lambda logger: DatabaseService(
            store=world.store, secrets=world.service.secrets, logger=logger
        ),
    )
    monkeypatch.setattr(
        "noust.managers.database.managed.server_resources",
        world.managed._resources,
    )
    try:
        yield world
    finally:
        Config.reset_instance()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines
