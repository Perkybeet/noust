# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup priority``: how hard the backups lean on the server (audit gap: priority and CPU).

The command parses and prints; the settings are checked where every front end checks them
(``noust config set``, the console, a hand-edited file all end in the same ranges), the units
already on the server are brought along, and ``show`` is a read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.commands import backup_priority
from noust.core.config import Config
from noust.core.exceptions import ConfigError
from noust.managers.backup_priority import BackupPriority


class FakeConfig:
    """The configuration, in memory."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.saved = 0

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def save(self) -> bool:
        self.saved += 1
        return True


class FakeScheduler:
    def __init__(self, verbose: bool = False) -> None:
        self.applied = 0

    def apply_priority(self) -> list[str]:
        self.applied += 1
        return ["noust-backup-db-postgresql-shop.service", "noust-backup-self.service"]


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> FakeConfig:
    fake = FakeConfig()
    monkeypatch.setattr(backup_priority, "Config", lambda: fake)
    monkeypatch.setattr(
        backup_priority,
        "current_priority",
        lambda: BackupPriority(
            nice=int(fake.values.get("backup.nice", 10)),
            io_class=str(fake.values.get("backup.io_class", "best-effort")),
            io_priority=7,
            cpu_quota=None,
        ),
    )
    monkeypatch.setattr(backup_priority, "BackupScheduler", FakeScheduler)
    return fake


def invoke(*args: str) -> Any:
    return CliRunner().invoke(cli_app.cli, ["backup", "priority", *args])


def test_show_prints_the_defaults(config: FakeConfig) -> None:
    result = invoke("show")

    assert result.exit_code == 0, result.output
    assert "Nice:         10" in result.output and "best-effort" in result.output
    assert "CPU ceiling:  none" in result.output


def test_set_saves_what_was_named_and_brings_the_units_along(config: FakeConfig) -> None:
    result = invoke("set", "--nice", "15", "--io-class", "idle", "--cpu-quota", "50")

    assert result.exit_code == 0, result.output
    assert config.values == {
        "backup.nice": 15,
        "backup.io_class": "idle",
        "backup.cpu_quota": "50",
    }
    assert config.saved == 1
    assert "2 backup service(s)" in result.output


def test_set_with_nothing_to_set_says_what_to_name(config: FakeConfig) -> None:
    result = invoke("set")

    assert result.exit_code == 1 and "--nice" in result.output
    assert config.saved == 0


def test_a_value_out_of_range_never_reaches_the_configuration(config: FakeConfig) -> None:
    result = invoke("set", "--nice", "25")

    assert result.exit_code != 0 and config.values == {}


def test_apply_says_which_units_it_rewrote(config: FakeConfig) -> None:
    result = invoke("apply", "--json")

    assert json.loads(result.output) == {
        "units_changed": ["noust-backup-db-postgresql-shop.service", "noust-backup-self.service"]
    }


class TestTheSameRangesEverywhere:
    """``noust config set`` and a PUT of the whole configuration refuse what this does."""

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("backup.nice", 20),
            ("backup.io_class", "realtime"),
            ("backup.io_priority", 9),
            ("backup.cpu_quota", 0),
            ("backup.cpu_quota", "half"),
        ],
    )
    def test_the_configuration_refuses_them(self, key: str, value: Any, tmp_path: Path) -> None:
        config = Config(tmp_path / "config.yaml")

        with pytest.raises(ConfigError):
            config.set(key, value)

    def test_it_accepts_a_good_one_and_turns_off_a_ceiling(self, tmp_path: Path) -> None:
        config = Config(tmp_path / "config.yaml")

        config.set("backup.nice", "12")
        config.set("backup.cpu_quota", "none")

        assert config.get("backup.nice") == 12
        assert config.get("backup.cpu_quota") is None
