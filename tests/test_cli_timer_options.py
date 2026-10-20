# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``--max-runtime`` and ``--timezone`` on the commands that set a backup schedule (checklist H-01).

The flags are one definition shared by the four commands. What is pinned: an application's schedule
writes the limit and the zone into its units, an update that names neither keeps what the timer had
(like retention), one that names them replaces them, a bad value is a usage error that writes
nothing, and the lines that show a schedule say the limit and the zone in words.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import Context, cli
from noust.cli.commands.timer_options import describe_timer_options
from noust.core.runner import FakeRunner
from tests.test_backup_safety_22 import DOMAIN, _reset_store, systemd_dir  # noqa: F401

UNIT = "noust-backup-shop-example-com"


@pytest.fixture
def zoneinfo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "zoneinfo"
    for name in ("UTC", "Europe/Madrid"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TZif2")
    monkeypatch.setattr("noust.managers.backup_timer_options.ZONEINFO_DIRS", (root,))


def invoke(*argv: str) -> Any:
    return CliRunner().invoke(cli, list(argv), obj=Context())


class TestAnApplicationsSchedule:
    def test_create_writes_the_limit_and_the_zone_into_the_units(
        self, systemd_dir: Path, runner: FakeRunner, zoneinfo: None
    ) -> None:
        result = invoke(
            "backup",
            "schedule",
            "create",
            DOMAIN,
            "--max-runtime",
            "2h",
            "--timezone",
            "Europe/Madrid",
        )

        assert result.exit_code == 0, result.output
        assert "TimeoutStartSec=7200\n" in (systemd_dir / f"{UNIT}.service").read_text()
        assert "Europe/Madrid" in (systemd_dir / f"{UNIT}.timer").read_text()

    def test_an_update_that_names_neither_keeps_both(
        self, systemd_dir: Path, runner: FakeRunner, zoneinfo: None
    ) -> None:
        invoke("backup", "schedule", "create", DOMAIN, "--max-runtime", "2h", "--timezone", "UTC")

        result = invoke("backup", "schedule", "update", DOMAIN, "--schedule", "weekly")

        assert result.exit_code == 0, result.output
        assert "TimeoutStartSec=7200\n" in (systemd_dir / f"{UNIT}.service").read_text()
        assert "UTC" in (systemd_dir / f"{UNIT}.timer").read_text()

    def test_an_update_that_names_one_replaces_the_pair(
        self, systemd_dir: Path, runner: FakeRunner, zoneinfo: None
    ) -> None:
        invoke("backup", "schedule", "create", DOMAIN, "--max-runtime", "2h", "--timezone", "UTC")

        result = invoke("backup", "schedule", "update", DOMAIN, "--max-runtime", "30m")

        assert result.exit_code == 0, result.output
        assert "TimeoutStartSec=1800\n" in (systemd_dir / f"{UNIT}.service").read_text()
        assert " UTC" not in (systemd_dir / f"{UNIT}.timer").read_text()

    def test_the_listing_says_them(
        self, systemd_dir: Path, runner: FakeRunner, zoneinfo: None
    ) -> None:
        invoke("backup", "schedule", "create", DOMAIN, "--max-runtime", "2h", "--timezone", "UTC")
        runner.script(
            ["systemctl", "list-timers"], stdout=f"Sat 2026-10-18 02:00:00 UTC 3h {UNIT}.timer x\n"
        )
        runner.script(
            ["systemctl", "show", f"{UNIT}.timer"],
            stdout="Description=Noust backup timer for shop.example.com\n",
        )

        result = invoke("backup", "schedule", "list")

        assert "time-limit=7200s" in result.output and "zone=UTC" in result.output

    @pytest.mark.parametrize(
        "flags", [["--max-runtime", "10s"], ["--timezone", "Mars/Olympus"], ["--max-runtime", "x"]]
    )
    def test_a_bad_value_is_refused_and_writes_nothing(
        self, flags: list[str], systemd_dir: Path, runner: FakeRunner, zoneinfo: None
    ) -> None:
        result = invoke("backup", "schedule", "create", DOMAIN, *flags)

        assert result.exit_code == 1
        assert list(systemd_dir.iterdir()) == []


class TestTheOtherCommandsTakeTheSameFlags:
    @pytest.mark.parametrize(
        "argv",
        [
            ["db", "backup-schedule", "set", "--help"],
            ["backup", "volumes", "schedule", "set", "--help"],
            ["self-backup", "schedule", "set", "--help"],
            ["backup", "schedule", "create", "--help"],
            ["backup", "schedule", "update", "--help"],
        ],
    )
    def test_they_show_in_the_help(self, argv: list[str]) -> None:
        result = invoke(*argv)

        assert result.exit_code == 0, result.output
        assert "--max-runtime" in result.output and "--timezone" in result.output


class TestTheWords:
    def test_a_limit_is_said_in_hours_minutes_and_seconds(self) -> None:
        assert describe_timer_options({"max_runtime_seconds": 5400}) == [
            "  Time limit:   1h 30m (a run past it is stopped)"
        ]

    def test_a_zone_is_said_and_nothing_is_said_when_nothing_is_set(self) -> None:
        assert describe_timer_options({"timezone": "UTC"}) == ["  Time zone:    UTC"]
        assert describe_timer_options({"max_runtime_seconds": None, "timezone": None}) == []
