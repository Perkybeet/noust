# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sealing a backup that was made in clear, and recording what the stored file now is (checklist H-08).

A backup made before sealing was turned on stays in clear until its retention removes it, which is
the copy someone who gets the disk reads. Sealing it in place changes the bytes the sidecar and the
records describe (size, checksum), so the act is one unit: check the clear file against what was
recorded (a damaged backup is not sealed into something that looks fine), seal it, and hand back the
new size and checksum for the owner of the record to write. What is pinned: a matching clear file is
sealed and described; one that does not match its record is left alone; a sealed file whose record
is stale because the run stopped between the two steps is repaired once it opens to exactly what was
recorded; anything else that is sealed and does not match is refused; the key's absence is said, not
raised past the caller; and a rehearsal changes nothing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from noust.core import backup_files
from noust.core.backup_files import BackupKeys, ResealOutcome, is_sealed, reseal
from noust.core.ens import envelope
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.runner import SubprocessRunner
from noust.core.secrets import SecretStore

pytestmark = pytest.mark.allow_subprocess

PLAINTEXT = b"PGDMP-secret-customer-rows\n" * 200
CLEAR_SHA = hashlib.sha256(PLAINTEXT).hexdigest()


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BackupKeys:
    """Keys in a store of the test's own; no key is made until the test makes one."""
    directory = tmp_path / "backups"
    directory.mkdir()
    monkeypatch.setattr(backup_files.paths, "backup_dir", lambda: directory)
    return BackupKeys(SecretStore(root=tmp_path / "secrets"))


@pytest.fixture
def dump(tmp_path: Path) -> Path:
    path = tmp_path / "backups" / "postgresql-shop-20261017.dump.gz"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(PLAINTEXT)
    path.chmod(0o600)
    return path


def run(path: Path, keys: BackupKeys, recorded: str | None) -> ResealOutcome:
    return reseal(path, recorded, keys=keys, runner=SubprocessRunner())


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestAClearFile:
    def test_it_is_sealed_and_described_as_it_now_is(self, keys: BackupKeys, dump: Path) -> None:
        keys.make_key()

        outcome = run(dump, keys, CLEAR_SHA)

        assert outcome.state == "sealed"
        assert is_sealed(dump) and PLAINTEXT[:20] not in dump.read_bytes()
        assert (outcome.size, outcome.sha256) == (dump.stat().st_size, sha_of(dump))
        assert outcome.sha256 != CLEAR_SHA
        assert (dump.stat().st_mode & 0o777) == 0o600

    def test_one_that_does_not_match_its_record_is_left_exactly_as_it_is(
        self, keys: BackupKeys, dump: Path
    ) -> None:
        keys.make_key()

        outcome = run(dump, keys, "0" * 64)

        assert outcome.state == "damaged"
        assert "does not match" in outcome.detail
        assert dump.read_bytes() == PLAINTEXT

    def test_one_with_no_record_is_sealed_and_described(self, keys: BackupKeys, dump: Path) -> None:
        keys.make_key()

        outcome = run(dump, keys, None)

        assert outcome.state == "sealed" and outcome.sha256 == sha_of(dump)

    def test_nothing_is_sealed_when_sealing_is_off(self, keys: BackupKeys, dump: Path) -> None:
        outcome = run(dump, keys, CLEAR_SHA)

        assert outcome.state == "off"
        assert dump.read_bytes() == PLAINTEXT

    def test_a_missing_file_is_said(self, keys: BackupKeys, tmp_path: Path) -> None:
        keys.make_key()

        outcome = run(tmp_path / "backups" / "gone.dump.gz", keys, CLEAR_SHA)

        assert outcome.state == "missing"

    def test_a_rehearsal_changes_nothing(self, keys: BackupKeys, dump: Path) -> None:
        keys.make_key()
        set_fs(DryRunFileSystem())
        try:
            outcome = run(dump, keys, CLEAR_SHA)
        finally:
            set_fs(None)

        assert outcome.state == "would_seal"
        assert dump.read_bytes() == PLAINTEXT


class TestASealedFile:
    def sealed(self, keys: BackupKeys, dump: Path) -> str:
        keys.make_key()
        assert run(dump, keys, CLEAR_SHA).state == "sealed"
        return sha_of(dump)

    def test_with_a_current_record_there_is_nothing_to_do(
        self, keys: BackupKeys, dump: Path
    ) -> None:
        stored = self.sealed(keys, dump)

        outcome = run(dump, keys, stored)

        assert outcome.state == "already" and outcome.sha256 == stored

    def test_a_run_that_stopped_before_its_record_is_repaired_once_it_opens_to_what_was_recorded(
        self, keys: BackupKeys, dump: Path
    ) -> None:
        stored = self.sealed(keys, dump)

        outcome = run(dump, keys, CLEAR_SHA)

        assert outcome.state == "repaired"
        assert outcome.sha256 == stored and outcome.size == dump.stat().st_size

    def test_one_that_opens_to_something_else_is_refused(
        self, keys: BackupKeys, dump: Path
    ) -> None:
        self.sealed(keys, dump)

        outcome = run(dump, keys, "f" * 64)

        assert outcome.state == "mismatch"
        assert "does not open to what was recorded" in outcome.detail

    def test_without_its_key_it_is_said_and_nothing_is_touched(
        self, keys: BackupKeys, dump: Path, tmp_path: Path
    ) -> None:
        self.sealed(keys, dump)
        before = dump.read_bytes()
        stranger = BackupKeys(SecretStore(root=tmp_path / "another-server"))

        outcome = run(dump, stranger, CLEAR_SHA)

        assert outcome.state == "unreadable"
        assert "does not hold its key" in outcome.detail
        assert dump.read_bytes() == before

    def test_an_altered_envelope_is_refused_not_repaired(
        self, keys: BackupKeys, dump: Path
    ) -> None:
        self.sealed(keys, dump)
        raw = bytearray(dump.read_bytes())
        raw[-5] ^= 0xFF
        dump.write_bytes(bytes(raw))

        outcome = run(dump, keys, CLEAR_SHA)

        assert outcome.state == "unreadable"
