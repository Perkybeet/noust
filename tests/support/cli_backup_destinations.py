# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_cli_backup_destinations.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from click.testing import CliRunner

from noust.cli.app import Context
from noust.cli.commands.backup import cli
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def _rclone(runner: FakeRunner) -> None:
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")


def invoke(argv: list[str], **kwargs: object):
    return CliRunner().invoke(cli, argv, obj=Context(), **kwargs)
