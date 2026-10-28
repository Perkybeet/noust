# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fixtures that many test modules asked for and each defined the same way."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.core import audit
from noust.core.fs import RecordingFileSystem
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.backup_scheduler import BackupScheduler
from tests.support.console_patch_support import patch_console


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def cli_runner() -> CliRunner:
    """Returns: A runner that captures stdout and stderr together."""
    return CliRunner()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "state" / "secrets")


@pytest.fixture
def isolated_panel_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Point the layered configuration at a per-test file.

    ``panel_url`` reads ``web.*`` through the Config singleton, which would
    otherwise read the developer's real ``/etc/wasm/config.yaml`` and make
    these tests depend on the machine they run on. The self-signed TLS pair
    path is pinned the same way, so a machine that has actually run
    ``wasm web start --self-signed`` does not turn "http" into "https" under
    a test.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The configuration file the test may write.
    """
    from noust.core import config as config_module

    path = tmp_path / "config.yaml"
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", path)
    patch_console(monkeypatch, "PANEL_TLS_CERT", tmp_path / "panel-tls" / "panel.crt")
    patch_console(monkeypatch, "PANEL_TLS_KEY", tmp_path / "panel-tls" / "panel.key")
    config_module.Config.reset_instance()
    yield path
    config_module.Config.reset_instance()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record",
        lambda event, **kw: events.append({"event": event, **kw}),
    )
    return events


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kwargs: recorded.append((event, kwargs)))
    return recorded


@pytest.fixture
def fs() -> RecordingFileSystem:
    return RecordingFileSystem()


@pytest.fixture
def scheduler(runner: FakeRunner, systemd_dir: Path) -> BackupScheduler:
    return BackupScheduler(verbose=False, runner=runner)


@pytest.fixture
def zoneinfo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "zoneinfo"
    for name in ("UTC", "Europe/Madrid"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TZif2")
    monkeypatch.setattr("noust.managers.backup_timer_options.ZONEINFO_DIRS", (root,))
