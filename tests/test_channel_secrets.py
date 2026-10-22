# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The secrets of the notification channels are encrypted in ``config.yaml`` (checklist K-12).

The file was 0600 and its secrets redacted on the way out, but the webhook URL of a Slack
channel, a Telegram bot token or the SMTP password sat in it as text, so a copy of the file in a
ticket, a backup or a support bundle disclosed every channel. They are encrypted now with the
mechanism the backup destinations' credentials use (:mod:`noust.core.at_rest`): readable in memory,
never on disk, bound to the key they belong to, and legible by a server that has lost its key only
as the opaque text they became, never as an empty string that a save would then write back.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
import yaml

from noust.core import channel_secrets
from noust.core.config import Config
from noust.core.fs import DryRunFileSystem, RealFileSystem, set_fs

SLACK = "https://hooks.slack.com/services/T000/B000/abcdef0123456789"


@pytest.fixture
def path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "etc" / "config.yaml"
    target.parent.mkdir()
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", target)
    set_fs(RealFileSystem())
    Config.reset_instance()
    yield target
    Config.reset_instance()


def raw(path: Path) -> str:
    return path.read_text()


class TestTheTree:
    def tree(self) -> dict:
        return {
            "notifications": {
                "channels": {
                    "slack": {"webhook_url": SLACK},
                    "telegram": {"bot_token": "123:ABC", "chat_id": "-100123"},
                    "webhook": {"webhook_url": "", "secret": "s3cr3t"},
                    "email": {"enabled": True},
                }
            },
            "monitor": {
                "smtp": {"host": "smtp.example.com", "port": 465, "password": "hunter2"},
                "email_api": {"provider": "resend", "api_key": "re_123"},
            },
            "web": {"port": 8080},
        }

    def test_the_secrets_of_the_channels_are_sealed_and_nothing_else(self, tmp_path: Path) -> None:
        key = tmp_path / "secrets.key"

        sealed = channel_secrets.seal(self.tree(), key)

        channels = sealed["notifications"]["channels"]
        assert channel_secrets.is_sealed(channels["slack"]["webhook_url"])
        assert channel_secrets.is_sealed(channels["telegram"]["bot_token"])
        assert channel_secrets.is_sealed(channels["webhook"]["secret"])
        assert channel_secrets.is_sealed(sealed["monitor"]["smtp"]["password"])
        assert channel_secrets.is_sealed(sealed["monitor"]["email_api"]["api_key"])
        # What is not a secret stays readable, and so does what is empty.
        assert channels["telegram"]["chat_id"] == "-100123"
        assert channels["webhook"]["webhook_url"] == ""
        assert channels["email"] == {"enabled": True}
        assert sealed["monitor"]["smtp"]["host"] == "smtp.example.com"
        assert sealed["web"] == {"port": 8080}
        assert SLACK not in str(sealed)

    def test_the_tree_it_was_given_is_not_changed(self, tmp_path: Path) -> None:
        tree = self.tree()

        channel_secrets.seal(tree, tmp_path / "secrets.key")

        assert tree == self.tree()

    def test_what_was_sealed_opens_to_what_it_was(self, tmp_path: Path) -> None:
        key = tmp_path / "secrets.key"
        sealed = channel_secrets.seal(self.tree(), key)

        opened, problems = channel_secrets.open_sealed(sealed, key)

        assert opened == self.tree()
        assert problems == []

    def test_a_value_that_is_already_sealed_is_not_sealed_again(self, tmp_path: Path) -> None:
        key = tmp_path / "secrets.key"
        once = channel_secrets.seal(self.tree(), key)

        twice = channel_secrets.seal(once, key)

        assert twice == once

    def test_a_value_moved_to_another_setting_is_refused_not_read(self, tmp_path: Path) -> None:
        key = tmp_path / "secrets.key"
        sealed = channel_secrets.seal(self.tree(), key)
        stolen = sealed["notifications"]["channels"]["slack"]["webhook_url"]
        sealed["notifications"]["channels"]["webhook"]["secret"] = stolen

        opened, problems = channel_secrets.open_sealed(sealed, key)

        assert opened["notifications"]["channels"]["webhook"]["secret"] == stolen
        assert any("notifications.channels.webhook.secret" in problem for problem in problems)

    def test_a_lost_key_leaves_the_text_sealed_and_says_so(self, tmp_path: Path) -> None:
        sealed = channel_secrets.seal(self.tree(), tmp_path / "secrets.key")

        opened, problems = channel_secrets.open_sealed(sealed, tmp_path / "another.key")

        assert channel_secrets.is_sealed(
            opened["notifications"]["channels"]["slack"]["webhook_url"]
        )
        assert len(problems) == 5
        assert "key is missing" in problems[0]

    def test_a_tree_with_nothing_sealed_needs_no_key_to_be_read(self, tmp_path: Path) -> None:
        key = tmp_path / "never-made.key"

        opened, problems = channel_secrets.open_sealed(self.tree(), key)

        assert opened == self.tree()
        assert problems == []
        assert not key.exists()

    def test_what_is_in_clear_is_counted(self) -> None:
        assert channel_secrets.plain_secrets(self.tree()) == 5
        assert channel_secrets.plain_secrets({"web": {"port": 1}}) == 0

    def test_the_key_is_private(self, tmp_path: Path) -> None:
        key = tmp_path / "secrets.key"

        channel_secrets.seal(self.tree(), key)

        assert stat.S_IMODE(key.stat().st_mode) == 0o600


class TestTheConfigFile:
    def test_a_saved_secret_is_never_in_the_file_and_is_read_back(self, path: Path) -> None:
        config = Config()
        config.set("notifications.channels.slack.webhook_url", SLACK)
        config.set("notifications.channels.telegram.chat_id", "-100123")

        config.write()

        text = raw(path)
        assert SLACK not in text
        assert "-100123" in text
        stored = yaml.safe_load(text)["notifications"]["channels"]["slack"]["webhook_url"]
        assert channel_secrets.is_sealed(stored)
        Config.reset_instance()
        assert Config().get("notifications.channels.slack.webhook_url") == SLACK

    def test_the_key_sits_beside_the_file_and_is_private(self, path: Path) -> None:
        config = Config()
        config.set("notifications.channels.slack.webhook_url", SLACK)

        config.write()

        key = path.parent / "secrets.key"
        assert stat.S_IMODE(key.stat().st_mode) == 0o600

    def test_a_file_written_before_encryption_is_read_and_sealed_by_the_next_save(
        self, path: Path
    ) -> None:
        path.write_text(
            yaml.dump({"notifications": {"channels": {"slack": {"webhook_url": SLACK}}}})
        )
        Config.reset_instance()
        config = Config()
        assert config.get("notifications.channels.slack.webhook_url") == SLACK

        config.write()

        assert SLACK not in raw(path)
        Config.reset_instance()
        assert Config().get("notifications.channels.slack.webhook_url") == SLACK

    def test_protecting_seals_what_is_in_clear_once(self, path: Path) -> None:
        path.write_text(
            yaml.dump(
                {
                    "notifications": {"channels": {"slack": {"webhook_url": SLACK}}},
                    "monitor": {"smtp": {"password": "hunter2"}},
                }
            )
        )
        Config.reset_instance()

        assert channel_secrets.protect(Config()) == 2
        assert channel_secrets.protect(Config()) == 0
        assert "hunter2" not in raw(path)

    def test_a_server_without_the_key_keeps_the_ciphertext_when_it_saves(self, path: Path) -> None:
        config = Config()
        config.set("notifications.channels.slack.webhook_url", SLACK)
        config.write()
        (path.parent / "secrets.key").unlink()
        Config.reset_instance()

        lost = Config()
        lost.set("web.port", 9090)
        lost.write()

        stored = yaml.safe_load(raw(path))["notifications"]["channels"]["slack"]["webhook_url"]
        assert channel_secrets.is_sealed(stored)
        assert lost.get("notifications.channels.slack.webhook_url") == stored

    def test_the_value_a_console_shows_is_redacted_whatever_it_holds(self, path: Path) -> None:
        from noust.core.config import redact_secrets

        config = Config()
        config.set("notifications.channels.slack.webhook_url", SLACK)
        config.write()
        Config.reset_instance()

        shown = redact_secrets(Config().to_dict())

        assert shown["notifications"]["channels"]["slack"]["webhook_url"] == "***"

    def test_a_rehearsal_makes_no_key(self, path: Path) -> None:
        config = Config()
        config.set("notifications.channels.slack.webhook_url", SLACK)
        set_fs(DryRunFileSystem())
        try:
            config.write()
        finally:
            set_fs(RealFileSystem())

        assert not (path.parent / "secrets.key").exists()
        assert not path.exists()

    def test_a_key_that_cannot_be_made_stops_the_save_instead_of_writing_clear_text(
        self, path: Path
    ) -> None:
        config = Config()
        config.set("notifications.channels.slack.webhook_url", SLACK)
        (path.parent / "secrets.key").mkdir()

        with pytest.raises(OSError):
            config.write()

        assert not path.exists() or SLACK not in raw(path)

    def test_the_key_file_is_the_one_the_backup_destinations_use(self, path: Path) -> None:
        from noust.core import secrets

        assert channel_secrets.key_path(path) == path.parent / secrets.AT_REST_KEY_FILE


class TestTheCommand:
    def invoke(self, *args: str):
        from click.testing import CliRunner

        from noust.cli.app import cli as root_cli

        return CliRunner().invoke(root_cli, ["config", *args])

    def test_it_encrypts_what_the_file_holds_as_text_and_says_when_there_is_nothing_left(
        self, path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.commands import config as config_cmd

        monkeypatch.setattr(config_cmd, "DEFAULT_CONFIG_PATH", path)
        path.write_text(
            yaml.dump({"notifications": {"channels": {"slack": {"webhook_url": SLACK}}}})
        )
        Config.reset_instance()

        first = self.invoke("protect")
        second = self.invoke("protect")

        assert first.exit_code == 0, first.output
        assert "Encrypted 1 secret(s)" in first.output
        assert second.exit_code == 0, second.output
        assert "already encrypted" in second.output
        assert SLACK not in raw(path)
