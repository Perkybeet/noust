# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_server_power.py`` that other tests build on."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.server.power import PowerManager
from noust.managers.server.power_records import PowerRecords
from tests.support.server_support import make_host

NOW = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "power.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def host(tmp_path: Path):
    root = make_host(tmp_path / "root")
    (root.boot_id).write_text("d875e599-869f-4722-96c9-6f9b3e1b5356\n")
    root.proc_uptime.write_text("12345.67 45678.90\n")
    return root


def _manager(store, host, runner: FakeRunner | None = None, **kwargs) -> PowerManager:
    runner = runner or FakeRunner()
    kwargs.setdefault("apps_check", lambda r: [])
    return PowerManager(
        runner=runner,
        fs=RecordingFileSystem(),
        host=host,
        records=PowerRecords(store),
        clock=lambda: NOW,
        **kwargs,
    )


def _healthy(runner: FakeRunner) -> FakeRunner:
    runner.script(["systemctl", "is-enabled"], stdout="enabled\n")
    runner.script(["findmnt", "--verify"], stdout="Success, no errors or warnings detected\n")
    return runner
