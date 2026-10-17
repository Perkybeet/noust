# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How long the journal keeps the logs (checklist G-13: a retention you can set).

What is defended: the drop-in says what was asked and nothing else, a file somebody
else wrote is never overwritten or removed, a number outside what is sensible is
refused before anything is written, journald is told to reread its configuration, and
clearing the retention removes exactly the file Noust wrote.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.exceptions import ServiceError, ValidationError
from noust.core.fs import RealFileSystem
from noust.core.runner import FakeRunner
from noust.managers.log_retention import MARKER, LogRetention, _days_of, _mb_of
from noust.web.api import log_search as log_search_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated


@pytest.fixture
def runner() -> FakeRunner:
    fake = FakeRunner()
    fake.script(
        ["journalctl", "--disk-usage"],
        stdout="Archived and active journals take up 1.2G in the file system.\n",
    )
    return fake


@pytest.fixture
def retention(tmp_path: Path, runner: FakeRunner) -> LogRetention:
    return LogRetention(
        runner,
        RealFileSystem(),
        dropin=tmp_path / "journald.conf.d" / "50-noust-retention.conf",
        journal_dir=tmp_path / "journal",
    )


class TestSetting:
    def test_the_dropin_says_what_was_asked_and_journald_rereads_it(
        self, retention: LogRetention, runner: FakeRunner, tmp_path: Path
    ) -> None:
        status = retention.set(days=30, max_use_mb=2048)

        text = (tmp_path / "journald.conf.d" / "50-noust-retention.conf").read_text()
        assert text.startswith(MARKER)
        assert "MaxRetentionSec=30day" in text and "SystemMaxUse=2048M" in text
        assert "Storage" not in text
        assert ("systemctl", "restart", "systemd-journald") in runner.calls
        assert (status.days, status.max_use_mb, status.configured) == (30, 2048, True)
        assert status.disk_usage.startswith("Archived and active journals")

    def test_what_is_not_named_is_kept(self, retention: LogRetention) -> None:
        retention.set(days=30, max_use_mb=2048)

        status = retention.set(days=7)

        assert (status.days, status.max_use_mb) == (7, 2048)

    def test_persistent_keeps_the_journal_across_reboots(
        self, retention: LogRetention, tmp_path: Path
    ) -> None:
        retention.set(days=30, persistent=True)

        assert (
            "Storage=persistent"
            in (tmp_path / "journald.conf.d" / "50-noust-retention.conf").read_text()
        )
        assert retention.status().persistent is True

    def test_a_volatile_journal_is_said_so(self, retention: LogRetention, tmp_path: Path) -> None:
        assert any("lost at every reboot" in note for note in retention.status().notes)

        (tmp_path / "journal").mkdir()

        assert not any("lost at every reboot" in note for note in retention.status().notes)
        assert retention.status().journal_on_disk is True

    @pytest.mark.parametrize(
        ("kwargs", "field"),
        [
            ({"days": 0}, "days"),
            ({"days": 4000}, "days"),
            ({"max_use_mb": 1}, "max_use_mb"),
            ({"max_use_mb": 10**9}, "max_use_mb"),
            ({}, "days"),
        ],
    )
    def test_what_is_out_of_range_is_refused_before_anything_is_written(
        self, retention: LogRetention, runner: FakeRunner, tmp_path: Path, kwargs: dict, field: str
    ) -> None:
        with pytest.raises(ValidationError) as raised:
            retention.set(**kwargs)

        assert raised.value.field == field
        assert not (tmp_path / "journald.conf.d").exists()
        assert ("systemctl", "restart", "systemd-journald") not in runner.calls

    def test_a_file_somebody_else_wrote_is_never_touched(
        self, retention: LogRetention, tmp_path: Path
    ) -> None:
        dropin = tmp_path / "journald.conf.d" / "50-noust-retention.conf"
        dropin.parent.mkdir()
        dropin.write_text("[Journal]\nMaxRetentionSec=1day\n")

        status = retention.status()
        assert status.foreign and not status.configured
        with pytest.raises(ServiceError, match="not written by Noust"):
            retention.set(days=30)
        with pytest.raises(ServiceError, match="not written by Noust"):
            retention.clear()

        assert dropin.read_text() == "[Journal]\nMaxRetentionSec=1day\n"

    def test_a_journald_that_will_not_restart_is_an_error_that_says_why(
        self, retention: LogRetention, runner: FakeRunner
    ) -> None:
        runner.script(
            ["systemctl", "restart"], stderr="Unit systemd-journald.service not found", exit_code=5
        )

        with pytest.raises(ServiceError) as raised:
            retention.set(days=30)

        assert "not found" in raised.value.details


class TestClearing:
    def test_it_removes_the_file_noust_wrote_and_nothing_else(
        self, retention: LogRetention, tmp_path: Path
    ) -> None:
        other = tmp_path / "journald.conf.d" / "10-mine.conf"
        retention.set(days=30)
        other.write_text("[Journal]\nCompress=yes\n")

        status = retention.clear()

        assert not status.configured and status.days is None
        assert not (tmp_path / "journald.conf.d" / "50-noust-retention.conf").exists()
        assert other.exists()

    def test_clearing_what_is_not_set_restarts_nothing(
        self, retention: LogRetention, runner: FakeRunner
    ) -> None:
        retention.clear()

        assert ("systemctl", "restart", "systemd-journald") not in runner.calls


class TestReadingWhatJournaldWouldRead:
    @pytest.mark.parametrize(
        ("text", "days"),
        [
            ("30day", 30),
            ("2592000", 30),
            ("1week", 7),
            ("1month", 30),
            ("12h", 1),
            ("1y", 365),
            ("nonsense", None),
        ],
    )
    def test_a_duration(self, text: str, days: int | None) -> None:
        assert _days_of(text) == days

    @pytest.mark.parametrize(
        ("text", "mb"), [("2G", 2048), ("500M", 500), ("1024K", 1), ("1T", 1048576), ("x", None)]
    )
    def test_a_size(self, text: str, mb: int | None) -> None:
        assert _mb_of(text) == mb


class TestTheApiAndTheCommand:
    @pytest.fixture
    def patched(self, retention: LogRetention, monkeypatch: pytest.MonkeyPatch) -> LogRetention:
        monkeypatch.setattr(log_search_api, "LogRetention", lambda: retention)
        monkeypatch.setattr("noust.cli.commands.log.LogRetention", lambda: retention)
        return retention

    @pytest.fixture
    def client(self, patched: LogRetention) -> TestClient:
        app = FastAPI()
        app.include_router(log_search_api.router, prefix="/api/logs")
        install_error_handlers(app)
        session = {"session_id": "t", "type": "master"}
        app.dependency_overrides[get_current_session] = lambda: session
        app.dependency_overrides[require_elevated] = lambda: session
        return TestClient(app, raise_server_exceptions=True)

    def test_the_api_reads_sets_and_clears_it(self, client: TestClient) -> None:
        assert client.get("/api/logs/retention").json()["configured"] is False

        saved = client.put("/api/logs/retention", json={"days": 14, "max_use_mb": 512}).json()
        assert (saved["days"], saved["max_use_mb"], saved["configured"]) == (14, 512, True)

        cleared = client.delete("/api/logs/retention").json()
        assert cleared["configured"] is False

    def test_the_api_refuses_what_is_out_of_range(self, client: TestClient) -> None:
        assert client.put("/api/logs/retention", json={"days": 0}).status_code == 422
        assert client.put("/api/logs/retention", json={}).status_code == 400

    def test_the_command_sets_shows_and_clears_it(self, patched: LogRetention) -> None:
        run = CliRunner()

        assert run.invoke(root_cli, ["log", "retention", "set", "--days", "30"]).exit_code == 0
        shown = run.invoke(root_cli, ["log", "retention", "show", "--json"])
        assert '"days": 30' in shown.output
        assert run.invoke(root_cli, ["log", "retention", "clear"]).exit_code == 0
        assert (
            '"configured": false'
            in run.invoke(root_cli, ["log", "retention", "show", "--json"]).output
        )
