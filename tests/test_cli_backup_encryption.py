# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup encryption`` (checklist H-08): the keys backups on this server are sealed with.

The command parses and prints; :mod:`noust.core.backup_files` decides. What is pinned: nothing is
changed without being asked, the key is shown when it is made and again on request and with the
warning that it must live elsewhere, a rotation keeps the old key, stopping keeps every key, and the
status counts the backups on the disk by whether they are sealed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.audit_policy import is_read_only
from noust.cli.commands import backup_encryption
from noust.core import backup_files
from noust.core.backup_files import BackupKeys, inventory
from noust.core.ens import envelope
from noust.core.secrets import SecretStore


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def keys(tmp_path: Path, runner: Any, monkeypatch: pytest.MonkeyPatch) -> BackupKeys:
    """A secret store of the test's own, used by every command here."""
    store = SecretStore(root=tmp_path / "secrets")
    monkeypatch.setattr(backup_files, "SecretStore", lambda: store)
    monkeypatch.setattr(backup_files.paths, "backup_dir", lambda: tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    return BackupKeys(store)


def invoke(cli_runner: CliRunner, *args: str, input: str | None = None) -> Any:
    """Run ``noust backup encryption ...``."""
    return cli_runner.invoke(cli_app.cli, ["backup", "encryption", *args], input=input)


def test_nothing_is_sealed_until_asked_and_asking_is_confirmed(
    cli_runner: CliRunner, keys: BackupKeys
) -> None:
    refused = invoke(cli_runner, "enable", input="n\n")

    assert refused.exit_code == 0 and "Nothing was changed." in refused.output
    assert keys.enabled() is False

    accepted = invoke(cli_runner, "enable", "--yes")

    assert accepted.exit_code == 0, accepted.output
    assert keys.enabled() is True


def test_the_key_is_shown_when_it_is_made_with_the_warning_to_keep_it_elsewhere(
    cli_runner: CliRunner, keys: BackupKeys
) -> None:
    result = invoke(cli_runner, "enable", "--yes")

    key_id = keys.current_id()
    assert key_id is not None
    assert f"Key {key_id}:" in result.output
    assert (keys.passphrase(key_id) or "x") in result.output
    assert "somewhere that is not this server" in result.output


def test_enabling_twice_changes_nothing(cli_runner: CliRunner, keys: BackupKeys) -> None:
    invoke(cli_runner, "enable", "--yes")
    first = keys.current_id()

    again = invoke(cli_runner, "enable", "--yes")

    assert "already sealed" in again.output and keys.current_id() == first


def test_show_key_prints_the_current_key_or_the_one_asked_for(
    cli_runner: CliRunner, keys: BackupKeys
) -> None:
    invoke(cli_runner, "enable", "--yes")
    old = keys.current_id()
    invoke(cli_runner, "rotate", "--yes")
    new = keys.current_id()

    current = invoke(cli_runner, "show-key")
    older = invoke(cli_runner, "show-key", old or "")

    assert old != new
    assert (keys.passphrase(new or "") or "x") in current.output
    assert (keys.passphrase(old or "") or "x") in older.output


def test_show_key_says_so_when_there_is_none(cli_runner: CliRunner, keys: BackupKeys) -> None:
    none = invoke(cli_runner, "show-key")
    unknown = invoke(cli_runner, "show-key", "deadbeef")

    assert none.exit_code == 1 and "enable" in none.output
    assert unknown.exit_code == 1 and "no such key" in unknown.output


def test_a_rotation_keeps_the_old_key_and_asks(cli_runner: CliRunner, keys: BackupKeys) -> None:
    invoke(cli_runner, "enable", "--yes")
    old = keys.current_id()

    declined = invoke(cli_runner, "rotate", input="n\n")
    assert keys.current_id() == old and "Nothing was changed." in declined.output
    invoke(cli_runner, "rotate", "--yes")

    assert keys.current_id() != old
    assert {key.id for key in keys.keys()} >= {old, keys.current_id()}


def test_rotating_what_is_not_sealed_points_at_enable(
    cli_runner: CliRunner, keys: BackupKeys
) -> None:
    assert "not sealed" in invoke(cli_runner, "rotate", "--yes").output


def test_stopping_keeps_every_key_and_asks(cli_runner: CliRunner, keys: BackupKeys) -> None:
    invoke(cli_runner, "enable", "--yes")
    made = keys.current_id()

    declined = invoke(cli_runner, "disable", input="n\n")
    assert keys.enabled() and "Nothing was changed." in declined.output
    invoke(cli_runner, "disable", "--yes")

    assert not keys.enabled() and made in {key.id for key in keys.keys()}
    assert "not sealed" in invoke(cli_runner, "disable", "--yes").output


@pytest.mark.allow_subprocess
def test_the_status_counts_what_is_sealed_and_what_is_in_clear(
    cli_runner: CliRunner, keys: BackupKeys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.runner import SubprocessRunner

    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)
    root = tmp_path / "backups"
    (root / "shop").mkdir()
    (root / "databases").mkdir()
    (root / "shop" / "a.tar.gz").write_bytes(b"clear archive")
    (root / "shop" / "a.json").write_text("{}")
    (root / "databases" / "pg-shop.dump").write_bytes(b"to be sealed")
    (root / ".staging").mkdir()
    (root / ".staging" / "ignored.dump").write_bytes(b"staging")
    (root / "self").mkdir()
    (root / "self" / "bundle.tar.gz").write_bytes(b"own state")
    keys.make_key()
    backup_files.seal_in_place(
        root / "databases" / "pg-shop.dump", keys=keys, runner=SubprocessRunner()
    )

    assert inventory(root) == {"sealed": 1, "clear": 1}
    result = invoke(cli_runner, "status", "--json")

    view = json.loads(result.output)
    assert view["enabled"] is True and view["sealed"] == 1 and view["clear"] == 1
    assert [key["id"] for key in view["keys"]] == [keys.current_id()]
    text = invoke(cli_runner, "status").output
    assert "1 sealed, 1 in clear" in text and "stay as they are" in text


def test_the_status_of_a_server_that_seals_nothing_says_how_to_start(
    cli_runner: CliRunner, keys: BackupKeys
) -> None:
    text = invoke(cli_runner, "status").output

    assert "kept in clear" in text and "noust backup encryption enable" in text


def test_reading_is_not_recorded_and_changing_is() -> None:
    group = backup_encryption.encryption

    assert is_read_only(group.commands["status"], "backup encryption status", {})
    for name in ("enable", "disable", "rotate", "show-key"):
        assert not is_read_only(group.commands[name], f"backup encryption {name}", {})


def test_a_lost_key_is_imported_from_a_file_never_from_an_argument(
    cli_runner: CliRunner, keys: BackupKeys, tmp_path: Path
) -> None:
    key_id = keys.make_key()
    kept = keys.passphrase(key_id) or ""
    keys.store.delete(f"{backup_files.KEY_PREFIX}/{key_id}")
    file = tmp_path / "kept-elsewhere"
    file.write_text(kept + "\n")

    result = invoke(cli_runner, "import-key", key_id, "--passphrase-file", str(file))

    assert result.exit_code == 0, result.output
    assert keys.passphrase(key_id) == kept
    assert kept not in result.output


def test_a_lost_key_is_imported_from_a_prompt_that_does_not_echo(
    cli_runner: CliRunner, keys: BackupKeys
) -> None:
    key_id = keys.make_key()
    kept = keys.passphrase(key_id) or ""
    keys.store.delete(f"{backup_files.KEY_PREFIX}/{key_id}")

    result = invoke(cli_runner, "import-key", key_id, input=kept + "\n")

    assert result.exit_code == 0, result.output
    assert keys.passphrase(key_id) == kept
    assert kept not in result.output


def test_importing_a_key_that_is_not_one_says_why(cli_runner: CliRunner, keys: BackupKeys) -> None:
    result = invoke(cli_runner, "import-key", "current", input="x" * 48 + "\n")

    assert result.exit_code == 1 and "is not the id of a key" in result.output


# ------------------------------------------------------------------ sealing what is already there


class TestSealExisting:
    """``seal-existing`` over doubles that keep real files; the managers have their own tests."""

    @pytest.fixture
    def sealer(self, keys: BackupKeys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        import hashlib

        from noust.core.runner import SubprocessRunner
        from noust.managers.backup_sealing import SealExisting
        from tests.test_backup_sealing import Apps, Meta

        monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)
        entries = []
        for name in ("shop.a", "shop.b"):
            archive = tmp_path / "backups" / f"{name}.tar.gz"
            archive.write_bytes(b"clear archive " + name.encode())
            entries.append(
                Meta(name, "shop.example.com", hashlib.sha256(archive.read_bytes()).hexdigest())
            )
        apps = Apps(tmp_path / "backups", entries)
        monkeypatch.setattr(
            backup_encryption,
            "SealExisting",
            lambda: SealExisting(
                keys=keys,
                apps=apps,
                volumes=None,
                databases=None,
                pitr=None,
                runner=SubprocessRunner(),
            ),
        )
        return apps

    def test_it_refuses_while_sealing_is_off_and_points_at_enable(
        self, cli_runner: CliRunner, keys: BackupKeys, sealer: Any
    ) -> None:
        result = invoke(cli_runner, "seal-existing", "--yes")

        assert result.exit_code == 1
        assert "not sealed" in result.output and "noust backup encryption enable" in result.output

    @pytest.mark.allow_subprocess
    def test_it_asks_and_a_no_changes_nothing(
        self, cli_runner: CliRunner, keys: BackupKeys, sealer: Any
    ) -> None:
        keys.make_key()

        result = invoke(cli_runner, "seal-existing", input="n\n")

        assert result.exit_code == 0 and "Nothing was changed." in result.output
        assert "2 application archive(s)" in result.output
        assert not any(backup_files.is_sealed(sealer.local_files(m)[0]) for m in sealer.entries)

    @pytest.mark.allow_subprocess
    def test_yes_seals_every_one_and_says_what_it_did(
        self, cli_runner: CliRunner, keys: BackupKeys, sealer: Any
    ) -> None:
        keys.make_key()

        result = invoke(cli_runner, "seal-existing", "--yes")

        assert result.exit_code == 0, result.output
        assert "application shop.a: sealed" in result.output
        assert "Sealed 2; already sealed 0; left as they were 0" in result.output
        assert all(backup_files.is_sealed(sealer.local_files(m)[0]) for m in sealer.entries)

    @pytest.mark.allow_subprocess
    def test_json_is_the_report_and_asks_nothing(
        self, cli_runner: CliRunner, keys: BackupKeys, sealer: Any
    ) -> None:
        keys.make_key()

        result = invoke(cli_runner, "seal-existing", "--yes", "--json")

        report = json.loads(result.output)
        assert report["ok"] is True and report["counts"] == {"sealed": 2}

    def test_json_without_yes_is_a_usage_error(
        self, cli_runner: CliRunner, keys: BackupKeys, sealer: Any
    ) -> None:
        keys.make_key()

        assert invoke(cli_runner, "seal-existing", "--json").exit_code == 2

    @pytest.mark.allow_subprocess
    def test_a_backup_that_does_not_match_its_record_is_said_and_the_exit_is_1(
        self, cli_runner: CliRunner, keys: BackupKeys, sealer: Any
    ) -> None:
        keys.make_key()
        sealer.entries[0].checksum = "0" * 64

        result = invoke(cli_runner, "seal-existing", "--yes")

        assert result.exit_code == 1
        assert "shop.a: damaged" in result.output and "does not match" in result.output
        assert "Sealed 1" in result.output

    @pytest.mark.allow_subprocess
    def test_nothing_in_clear_says_so(
        self, cli_runner: CliRunner, keys: BackupKeys, sealer: Any
    ) -> None:
        keys.make_key()
        invoke(cli_runner, "seal-existing", "--yes")

        result = invoke(cli_runner, "seal-existing", "--yes")

        assert result.exit_code == 0 and "Nothing is in clear" in result.output

    def test_it_changes_things_so_it_is_recorded(self) -> None:
        command = backup_encryption.encryption.commands["seal-existing"]

        assert not is_read_only(command, "backup encryption seal-existing", {})

    def test_enabling_points_at_it_for_what_is_already_there(
        self, cli_runner: CliRunner, keys: BackupKeys
    ) -> None:
        result = invoke(cli_runner, "enable", "--yes")

        assert "noust backup encryption seal-existing" in result.output
