# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the ``wasm backup destination``, ``push``, ``remote-list``,
``restore --from`` and ``run-schedule`` commands (2.2).

The one rule that matters most here: a secret field never appears in the
argument vector Click parses. ``--field`` is for the fields a backend does
not consider secret; the one secret field every backend has is read from
standard input or a hidden prompt, and these tests assert on exactly that -
not on behaviour the CLI happens to have today.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from wasm.cli.app import Context
from wasm.cli.commands.backup import cli
from wasm.core.runner import FakeRunner
from wasm.core.store import BackupScheduleRecord, WASMStore, get_store
from wasm.managers.backup_scheduler import BackupScheduler


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    WASMStore.reset_instance()
    yield
    WASMStore.reset_instance()


@pytest.fixture(autouse=True)
def _rclone(runner: FakeRunner) -> None:
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


def invoke(argv: list[str], **kwargs: object):
    return CliRunner().invoke(cli, argv, obj=Context(), **kwargs)


# ---------------------------------------------------------------------------
# destination add / update
# ---------------------------------------------------------------------------


def test_add_reads_the_secret_field_from_stdin() -> None:
    result = invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--stdin",
        ],
        input="s3cr3t-password-value\n",
    )

    assert result.exit_code == 0, result.output
    destination = get_store().get_backup_destination("nas")
    assert destination is not None
    assert destination.settings["host"] == "nas.example.com"
    assert "s3cr3t-password-value" not in result.output


def test_add_reads_the_secret_field_from_a_hidden_prompt() -> None:
    result = invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--prompt",
        ],
        # click.prompt with confirmation_prompt asks twice.
        input="a-secret-password\na-secret-password\n",
    )

    assert result.exit_code == 0, result.output
    assert "a-secret-password" not in result.output


def test_add_field_cannot_carry_the_secret(runner: FakeRunner) -> None:
    """A backend's secret field is refused as a plain --field, on purpose."""
    result = invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "pass=typed-on-the-command-line",
        ]
    )

    assert result.exit_code != 0
    for call in runner.calls:
        assert "typed-on-the-command-line" not in " ".join(call)


def test_add_rejects_stdin_and_prompt_together() -> None:
    result = invoke(
        ["backup", "destination", "add", "nas", "--type", "sftp", "--stdin", "--prompt"],
        input="x\n",
    )
    assert result.exit_code != 0


def test_add_with_encrypt_warns_to_save_the_key() -> None:
    result = invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--stdin",
            "--encrypt",
        ],
        input="s3cr3t\n",
    )

    assert result.exit_code == 0, result.output
    assert "show-key" in result.output


def test_update_changes_a_field() -> None:
    invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--stdin",
        ],
        input="s3cr3t\n",
    )

    result = invoke(
        ["backup", "destination", "update", "nas", "--field", "host=new-host.example.com"]
    )

    assert result.exit_code == 0, result.output
    assert get_store().get_backup_destination("nas").settings["host"] == "new-host.example.com"


# ---------------------------------------------------------------------------
# list / test / remove / show-key
# ---------------------------------------------------------------------------


def test_list_json_never_prints_a_secret_value() -> None:
    invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--stdin",
        ],
        input="s3cr3t-password-value\n",
    )

    result = invoke(["backup", "destination", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert "s3cr3t-password-value" not in result.output
    entries = json.loads(result.output)
    assert entries[0]["configured_secret_fields"] == ["pass"]


def test_test_command_reports_entries(runner: FakeRunner) -> None:
    invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--stdin",
        ],
        input="s3cr3t\n",
    )
    runner.script(("rclone", "lsf", "nas:wasm-backups", "--max-depth", "1"), stdout="shop-com/\n")

    result = invoke(["backup", "destination", "test", "nas"])

    assert result.exit_code == 0, result.output
    assert "shop-com/" in result.output


def test_remove_asks_for_confirmation_unless_forced() -> None:
    invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--stdin",
        ],
        input="s3cr3t\n",
    )

    declined = invoke(["backup", "destination", "remove", "nas"], input="n\n")
    assert get_store().get_backup_destination("nas") is not None
    assert declined.exit_code == 0

    forced = invoke(["backup", "destination", "remove", "nas", "-f"])
    assert forced.exit_code == 0, forced.output
    assert get_store().get_backup_destination("nas") is None


def test_show_key_prints_both_passphrases() -> None:
    invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--stdin",
            "--encrypt",
        ],
        input="s3cr3t\n",
    )

    result = invoke(["backup", "destination", "show-key", "nas"])

    assert result.exit_code == 0, result.output
    assert "password" in result.output


# ---------------------------------------------------------------------------
# push / remote-list
# ---------------------------------------------------------------------------


def test_push_reports_backup_not_found() -> None:
    result = invoke(["backup", "push", "no-such-backup", "nas"])
    assert result.exit_code != 0


def test_remote_list_without_app_lists_directories(runner: FakeRunner) -> None:
    invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--stdin",
        ],
        input="s3cr3t\n",
    )
    runner.script(
        ("rclone", "lsjson", "--hash", "nas:wasm-backups"),
        stdout=json.dumps([{"Name": "shop-example-com", "IsDir": True}]),
    )

    result = invoke(["backup", "remote-list", "nas"])

    assert result.exit_code == 0, result.output
    assert "shop-example-com" in result.output


# ---------------------------------------------------------------------------
# run-schedule (hidden) and schedule --destination
# ---------------------------------------------------------------------------


def test_run_schedule_is_hidden_from_help() -> None:
    result = invoke(["backup", "--help"])
    assert "run-schedule" not in result.output


def test_run_schedule_without_a_schedule_still_tries_the_backup() -> None:
    # A missing store row falls back to a 2.1 backup instead of stopping every
    # backup silently; here the application itself does not exist.
    result = invoke(["backup", "run-schedule", "nowhere.example.com"])
    assert result.exit_code != 0
    assert "Application not found" in result.output


def test_run_schedule_creates_a_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    from wasm.managers.backup_manager import BackupManager, BackupMetadata

    get_store().save_backup_schedule(
        BackupScheduleRecord(app_domain="shop.example.com", schedule="daily")
    )

    def fake_create(self: BackupManager, **kwargs: object) -> BackupMetadata:
        return BackupMetadata(
            id="shop-example-com_20260101_000000",
            domain="shop.example.com",
            app_name="shop-example-com",
            created_at="2026-01-01T00:00:00",
            size_bytes=1,
            app_type="static",
            version="2.0.0",
            description="",
            includes_env=True,
            includes_node_modules=False,
        )

    monkeypatch.setattr(BackupManager, "create", fake_create)

    result = invoke(["backup", "run-schedule", "shop.example.com"])

    assert result.exit_code == 0, result.output
    assert "shop-example-com_20260101_000000" in result.output


def test_schedule_create_with_destination_option(systemd_dir: Path) -> None:
    invoke(
        [
            "backup",
            "destination",
            "add",
            "nas",
            "--type",
            "sftp",
            "--field",
            "host=nas.example.com",
            "--field",
            "user=wasm",
            "--stdin",
        ],
        input="s3cr3t\n",
    )

    result = invoke(
        [
            "backup",
            "schedule",
            "create",
            "shop.example.com",
            "--destination",
            "nas:3:30",
        ]
    )

    assert result.exit_code == 0, result.output
    record = get_store().get_backup_schedule("shop.example.com")
    assert record is not None
    assert record.destinations == [{"name": "nas", "retention_count": 3, "retention_days": 30}]


def test_schedule_update_replaces_destinations(systemd_dir: Path) -> None:
    invoke(["backup", "schedule", "create", "shop.example.com", "--destination", "nas:3:30"])

    result = invoke(["backup", "schedule", "update", "shop.example.com"])

    assert result.exit_code == 0, result.output
    record = get_store().get_backup_schedule("shop.example.com")
    assert record is not None
    assert record.destinations == []
