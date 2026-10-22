# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A new backup destination is encrypted unless someone says otherwise (checklist H-07)."""

from __future__ import annotations

import json

import pytest

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import get_store
from noust.managers.backup_destinations import (
    BackupDestinationManager,
    encrypt_new_destinations,
    resolve_encryption,
)
from tests.test_cli_backup_destinations import (  # noqa: F401
    _rclone,
    _reset_store,
    invoke,
)

ADD = [
    "backup",
    "destination",
    "add",
    "nas",
    "--type",
    "sftp",
    "--field",
    "host=nas.example.com",
    "--field",
    "user=noust",
    "--stdin",
]


class TestTheDecision:
    def test_what_the_operator_said_wins(self) -> None:
        assert resolve_encryption(True) is True
        assert resolve_encryption(False) is False

    def test_a_key_given_means_encryption(self) -> None:
        assert resolve_encryption(None, key_given=True) is True

    def test_nothing_said_is_encrypted(self) -> None:
        assert resolve_encryption(None) is True and encrypt_new_destinations() is True

    def test_a_disk_this_server_mounts_is_plain_unless_asked(self) -> None:
        assert resolve_encryption(None, backend="local") is False
        assert resolve_encryption(True, backend="local") is True
        assert resolve_encryption(None, backend="local", key_given=True) is True
        assert resolve_encryption(None, backend="sftp") is True

    def test_the_default_can_be_turned_off_in_the_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            Config,
            "get",
            lambda self, key, default=None: (
                False if key == "backup.encrypt_new_destinations" else default
            ),
        )

        assert resolve_encryption(None) is False
        assert resolve_encryption(True) is True


class TestTheCommandLine:
    def test_a_destination_added_with_no_word_about_it_is_encrypted_and_says_to_keep_the_key(
        self, runner: FakeRunner
    ) -> None:
        result = invoke(ADD, input="s3cr3t\n")

        assert result.exit_code == 0, result.output
        destination = get_store().get_backup_destination("nas")
        assert destination is not None and destination.encrypted is True
        assert "show-key nas" in result.output and "unrecoverable" in result.output

    def test_no_encrypt_makes_a_plain_one(self, runner: FakeRunner) -> None:
        result = invoke([*ADD, "--no-encrypt"], input="s3cr3t\n")

        assert result.exit_code == 0, result.output
        destination = get_store().get_backup_destination("nas")
        assert destination is not None and destination.encrypted is False

    def test_a_key_read_from_stdin_is_encryption_too(self, runner: FakeRunner) -> None:
        manager = BackupDestinationManager()
        manager.add("old", "sftp", {"host": "h", "user": "u", "pass": "p"}, encrypted=True)
        key = json.dumps(manager.show_key("old"))
        invoke(["backup", "destination", "remove", "old", "--force", "--key-saved"])

        result = invoke(
            [
                "backup",
                "destination",
                "add",
                "nas",
                "--type",
                "sftp",
                "--field",
                "host=h",
                "--field",
                "user=u",
                "--key-stdin",
            ],
            input=key,
        )

        assert result.exit_code == 0, result.output
        destination = get_store().get_backup_destination("nas")
        assert destination is not None and destination.encrypted is True

    def test_help_says_which_way_the_default_goes(self) -> None:
        result = invoke(["backup", "destination", "add", "--help"])

        assert "--encrypt / --no-encrypt" in result.output or "--no-encrypt" in result.output
        text = " ".join(result.output.split())
        assert "encrypted unless you pass --no-encrypt" in text
        assert "A 'local' destination" in text
