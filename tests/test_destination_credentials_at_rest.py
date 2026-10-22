# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A backup destination's credentials are encrypted on the server's disk (checklist H-14).

The properties that matter: nothing readable is left in the secrets directory
(the password, and the key that encrypts what is sent there), a copy of that
directory discloses nothing without the key file that lives apart from it, a
file moved over another destination's or changed by one bit is refused, a
destination made before this existed is encrypted the first time it is used
and by one command, and a key that is gone is said so with what to restore.
"""

from __future__ import annotations

import base64
import json
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import Context
from noust.cli.commands.backup import cli
from noust.core import at_rest, paths, secrets, totp
from noust.core.exceptions import ConfigError
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore, at_rest_key_path
from noust.core.store import NoustStore, get_store
from noust.managers import security_posture
from noust.managers.backup_destinations import BackupDestinationManager

PASSWORD = "s3cr3t-password-value"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def manager(runner: FakeRunner) -> BackupDestinationManager:
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    return BackupDestinationManager(runner=runner)


def sftp(**overrides: str) -> dict[str, str]:
    fields = {"host": "nas.example.com", "user": "noust", "pass": PASSWORD}
    fields.update(overrides)
    return fields


def on_disk(name: str) -> str:
    return SecretStore().path(f"backup-destinations/{name}").read_text(encoding="utf-8")


class TestTheConstruction:
    DOMAIN = at_rest.Domain(prefix="noust-test:v1:", label="noust-test", noun="test secret")
    KEY = bytes(range(32))

    def test_a_value_reads_back_and_hides_its_text(self) -> None:
        sealed = self.DOMAIN.seal("a password: zażółć", self.KEY, "ctx")
        assert sealed.startswith("noust-test:v1:")
        assert "password" not in sealed
        assert self.DOMAIN.open(sealed, self.KEY, "ctx") == "a password: zażółć"

    def test_the_same_value_is_never_sealed_the_same_way_twice(self) -> None:
        assert self.DOMAIN.seal("x", self.KEY, "ctx") != self.DOMAIN.seal("x", self.KEY, "ctx")

    def test_another_owner_context_is_refused(self) -> None:
        sealed = self.DOMAIN.seal("x", self.KEY, "backup-destinations/nas")
        with pytest.raises(at_rest.AtRestError, match="integrity"):
            self.DOMAIN.open(sealed, self.KEY, "backup-destinations/other")

    def test_a_flipped_bit_is_refused(self) -> None:
        sealed = self.DOMAIN.seal("x" * 40, self.KEY, "ctx")
        raw = bytearray(base64.urlsafe_b64decode(sealed[len(self.DOMAIN.prefix) :]))
        # One bit of the ciphertext, one of the tag: either is refused.
        for position in (20, len(raw) - 1):
            changed = bytearray(raw)
            changed[position] ^= 1
            flipped = self.DOMAIN.prefix + base64.urlsafe_b64encode(bytes(changed)).decode()
            with pytest.raises(at_rest.AtRestError):
                self.DOMAIN.open(flipped, self.KEY, "ctx")

    def test_another_key_is_refused(self) -> None:
        sealed = self.DOMAIN.seal("x", self.KEY, "ctx")
        with pytest.raises(at_rest.AtRestError):
            self.DOMAIN.open(sealed, bytes(32), "ctx")

    def test_a_value_of_another_domain_does_not_open_here(self) -> None:
        other = at_rest.Domain(prefix="noust-test:v1:", label="noust-other", noun="x")
        sealed = other.seal("x", self.KEY, "ctx")
        with pytest.raises(at_rest.AtRestError):
            self.DOMAIN.open(sealed, self.KEY, "ctx")

    def test_a_missing_key_says_what_to_restore(self) -> None:
        sealed = self.DOMAIN.seal("x", self.KEY, "ctx")
        with pytest.raises(at_rest.AtRestError, match="restore the file"):
            self.DOMAIN.open(sealed, None, "ctx", missing_key="Please restore the file.")

    def test_a_value_in_clear_is_read_as_it_is(self) -> None:
        assert self.DOMAIN.open("plain", None, "ctx") == "plain"


class TestKeyFile:
    def test_it_is_made_private_and_reused(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.key"
        first = at_rest.load_key(path, create=True)
        assert first is not None and len(first) == 32
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert at_rest.load_key(path, create=True) == first
        assert at_rest.load_key(path, create=False) == first

    def test_without_create_a_missing_one_is_none(self, tmp_path: Path) -> None:
        assert at_rest.load_key(tmp_path / "nothing", create=False) is None

    def test_a_symbolic_link_is_not_followed(self, tmp_path: Path) -> None:
        target = tmp_path / "elsewhere"
        target.write_text("00" * 32)
        link = tmp_path / "secrets.key"
        link.symlink_to(target)
        with pytest.raises(OSError):
            at_rest.load_key(link, create=True)

    def test_a_file_that_is_not_a_key_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets.key"
        path.write_text("not a key")
        with pytest.raises(OSError):
            at_rest.load_key(path, create=False)


class TestTheSecondFactorKeepsItsBytes:
    #: Produced by the construction as it was before it moved to ``at_rest``, with the
    #: nonce 00..0f, the key 07 * 32 and the context ``account:1``.
    STORED = "noust-totp:v1:AAECAwQFBgcICQoLDA0OD-muceqsVWVNCDz5uRVTl4HRVdEI6d7VlHe-f07vAhKiJ2m3p3w4PFg4yaGS6d0agA=="

    def test_a_secret_stored_before_still_opens(self) -> None:
        assert totp.open_secret(self.STORED, bytes([7]) * 32, "account:1") == "JBSWY3DPEHPK3PXP"

    def test_the_error_is_the_totp_one(self) -> None:
        with pytest.raises(totp.TotpSealError):
            totp.open_secret(self.STORED, bytes([7]) * 32, "account:2")


class TestWhereTheKeyLives:
    def test_on_a_server_it_is_in_the_configuration_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = tmp_path / "var-lib-noust"
        config = tmp_path / "etc-noust"
        monkeypatch.setattr(paths, "state_dir", lambda: state)
        monkeypatch.setattr(paths, "config_dir", lambda: config)
        assert at_rest_key_path(state / "secrets") == config / "secrets.key"

    def test_in_any_other_store_it_is_beside_the_secrets(self, tmp_path: Path) -> None:
        assert (
            at_rest_key_path(tmp_path / "store" / "secrets") == tmp_path / "store" / "secrets.key"
        )


class TestProtectedSecrets:
    def test_a_mapping_reads_back_and_its_file_holds_no_value(self, tmp_path: Path) -> None:
        store = SecretStore(tmp_path / "s" / "secrets")
        store.write_protected_json("backup-destinations/nas", {"pass": PASSWORD, "user": "u"})
        text = store.path("backup-destinations/nas").read_text(encoding="utf-8")
        assert PASSWORD not in text and "pass" not in text
        assert text.startswith(secrets.PROTECTED_DOMAIN.prefix)
        assert store.read_protected_json("backup-destinations/nas") == {
            "pass": PASSWORD,
            "user": "u",
        }
        assert stat.S_IMODE(store.path("backup-destinations/nas").stat().st_mode) == 0o600

    def test_the_key_is_not_in_the_secrets_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "s" / "secrets"
        SecretStore(root).write_protected_json("a/b", {"k": "v"})
        assert (tmp_path / "s" / "secrets.key").is_file()
        assert not any(path.name == "secrets.key" for path in root.rglob("*"))

    def test_a_mapping_stored_in_clear_is_read_and_then_protected(self, tmp_path: Path) -> None:
        store = SecretStore(tmp_path / "s" / "secrets")
        store.write_json("backup-destinations/nas", {"pass": PASSWORD})
        assert store.is_protected("backup-destinations/nas") is False
        assert store.read_protected_json("backup-destinations/nas") == {"pass": PASSWORD}

        assert store.protect("backup-destinations/nas") is True
        assert store.is_protected("backup-destinations/nas") is True
        assert PASSWORD not in store.path("backup-destinations/nas").read_text(encoding="utf-8")
        assert store.read_protected_json("backup-destinations/nas") == {"pass": PASSWORD}
        assert store.protect("backup-destinations/nas") is False

    def test_an_absent_secret_is_neither(self, tmp_path: Path) -> None:
        store = SecretStore(tmp_path / "s" / "secrets")
        assert store.is_protected("backup-destinations/none") is None
        assert store.protect("backup-destinations/none") is False
        assert store.read_protected_json("backup-destinations/none") == {}

    def test_a_copy_moved_over_another_name_is_refused(self, tmp_path: Path) -> None:
        store = SecretStore(tmp_path / "s" / "secrets")
        store.write_protected_json("backup-destinations/nas", {"pass": "a"})
        store.write_protected_json("backup-destinations/offsite", {"pass": "b"})
        store.path("backup-destinations/offsite").write_text(
            store.path("backup-destinations/nas").read_text(encoding="utf-8"), encoding="utf-8"
        )
        with pytest.raises(ConfigError, match="integrity"):
            store.read_protected_json("backup-destinations/offsite")

    def test_a_lost_key_says_what_to_restore(self, tmp_path: Path) -> None:
        store = SecretStore(tmp_path / "s" / "secrets")
        store.write_protected_json("backup-destinations/nas", {"pass": "a"})
        (tmp_path / "s" / "secrets.key").unlink()
        with pytest.raises(ConfigError) as caught:
            store.read_protected_json("backup-destinations/nas")
        assert "secrets.key" in (caught.value.details or "")
        assert "Noust backup" in (caught.value.details or "")

    def test_a_rehearsal_creates_no_key(self, tmp_path: Path) -> None:
        store = SecretStore(tmp_path / "s" / "secrets", fs=DryRunFileSystem())
        set_fs(DryRunFileSystem())
        try:
            store.write_protected_json("backup-destinations/nas", {"pass": "a"})
        finally:
            set_fs(None)
        assert not (tmp_path / "s" / "secrets.key").exists()

    def test_the_plain_reader_does_not_pretend_to_read_one(self, tmp_path: Path) -> None:
        store = SecretStore(tmp_path / "s" / "secrets")
        store.write_protected_json("backup-destinations/nas", {"pass": "a"})
        with pytest.raises(ConfigError, match="damaged"):
            store.read_json("backup-destinations/nas")


class TestDestinations:
    def test_a_new_destination_keeps_nothing_readable_on_disk(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", sftp(), encrypted=True)
        text = on_disk("nas")
        assert PASSWORD not in text
        assert "crypt_password" not in text
        assert manager.credentials_protected("nas") is True

    def test_what_rclone_is_given_is_unchanged(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", sftp(), encrypted=True)
        env = manager.remote_env("nas")
        assert env["RCLONE_CONFIG_NAS_PASS"] == "OBSCURED"
        assert env["RCLONE_CONFIG_NASCRYPT_PASSWORD"] == "OBSCURED"

    def test_the_encryption_key_can_still_be_shown_for_safekeeping(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", sftp(), encrypted=True)
        keys = manager.show_key("nas")
        assert keys["password"] and keys["password2"]
        assert keys["password"] not in on_disk("nas")

    def test_an_update_keeps_it_protected_and_keeps_the_blank_fields(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", sftp())
        manager.update("nas", {"pass": "", "host": "other.example.com"})
        assert manager.credentials_protected("nas") is True
        assert manager.configured_secret_fields("nas") == ["pass"]
        manager.update("nas", {"pass": "a-new-password"})
        assert PASSWORD not in on_disk("nas") and "a-new-password" not in on_disk("nas")
        assert manager.secrets.read_protected_json("backup-destinations/nas")["pass"] == (
            "a-new-password"
        )

    def test_a_destination_from_before_is_protected_the_first_time_it_is_used(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", sftp())
        manager.secrets.write_json("backup-destinations/nas", {"pass": PASSWORD})
        assert manager.credentials_protected("nas") is False

        env = manager.remote_env("nas")

        assert env["RCLONE_CONFIG_NAS_PASS"] == "OBSCURED"
        assert manager.credentials_protected("nas") is True
        assert PASSWORD not in on_disk("nas")

    def test_a_disk_that_cannot_be_written_does_not_stop_an_upload(
        self, manager: BackupDestinationManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager.add("nas", "sftp", sftp())
        manager.secrets.write_json("backup-destinations/nas", {"pass": PASSWORD})

        def refuse(*args: object, **kwargs: object) -> None:
            raise ConfigError("read-only file system")

        monkeypatch.setattr(SecretStore, "write_protected_json", refuse)
        assert manager.remote_env("nas")["RCLONE_CONFIG_NAS_PASS"] == "OBSCURED"
        assert manager.credentials_protected("nas") is False

    def test_the_command_protects_every_one_from_before_and_says_which(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", sftp())
        manager.add("offsite", "sftp", sftp(), encrypted=True)
        manager.secrets.write_json("backup-destinations/nas", {"pass": PASSWORD})

        assert manager.protect_credentials() == ["nas"]
        assert manager.protect_credentials() == []
        assert PASSWORD not in on_disk("nas")

    def test_a_lost_key_is_said_when_the_destination_is_used(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", sftp())
        at_rest_key_path(SecretStore().root).unlink()
        with pytest.raises(ConfigError) as caught:
            manager.remote_env("nas")
        assert "backup destination update" in (caught.value.details or "") or (
            "enter the credentials again" in (caught.value.details or "")
        )

    def test_removing_a_destination_removes_its_credentials(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", sftp())
        manager.remove("nas")
        assert not SecretStore().path("backup-destinations/nas").exists()


class TestTheCommand:
    @pytest.fixture(autouse=True)
    def _rclone(self, runner: FakeRunner) -> None:
        runner.only_knows("rclone")
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")

    def invoke(self, argv: list[str]):
        return CliRunner().invoke(cli, argv, obj=Context())

    def test_it_protects_the_destinations_that_are_not_and_lists_them(self) -> None:
        manager = BackupDestinationManager()
        manager.add("nas", "sftp", sftp())
        manager.secrets.write_json("backup-destinations/nas", {"pass": PASSWORD})

        result = self.invoke(["backup", "destination", "protect"])

        assert result.exit_code == 0, result.output
        assert "nas" in result.output
        assert PASSWORD not in result.output
        assert manager.credentials_protected("nas") is True

    def test_with_nothing_to_do_it_says_so(self) -> None:
        BackupDestinationManager().add("nas", "sftp", sftp())
        result = self.invoke(["backup", "destination", "protect"])
        assert result.exit_code == 0, result.output
        assert "already" in result.output.lower()

    def test_the_listing_says_whether_the_credentials_are_encrypted(self) -> None:
        manager = BackupDestinationManager()
        manager.add("nas", "sftp", sftp())
        manager.add("old", "sftp", sftp())
        manager.secrets.write_json("backup-destinations/old", {"pass": PASSWORD})

        result = self.invoke(["backup", "destination", "list", "--json"])

        rows = {row["name"]: row for row in json.loads(result.output)}
        assert rows["nas"]["credentials_protected"] is True
        assert rows["old"]["credentials_protected"] is False


class TestThePosture:
    def test_a_destination_with_clear_credentials_is_a_finding_with_its_fix(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", sftp())
        manager.secrets.write_json("backup-destinations/nas", {"pass": PASSWORD})

        findings = [
            f for f in security_posture.collect() if f.code == "destination_credentials_clear"
        ]

        assert len(findings) == 1
        assert "nas" in findings[0].summary
        assert findings[0].fix == "noust backup destination protect"

    def test_protected_ones_are_not(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", sftp())
        assert all(f.code != "destination_credentials_clear" for f in security_posture.collect())

    def test_the_store_is_the_one_that_lists_them(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", sftp())
        assert [d.name for d in get_store().list_backup_destinations()] == ["nas"]
