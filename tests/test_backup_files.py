# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Backups encrypted on the server's disk, and the one door they are opened through (checklist H-08).

openssl is real here (the envelope is only as good as the program that writes it), with a cheap key
derivation. What is pinned: a sealed backup keeps its name and its owner-only mode and is an
envelope that does not contain its plaintext; the door yields a file that is not sealed as it is
and a sealed one decrypted, once for nested readers, removed afterwards however the block ends;
nothing is sealed when sealing is off or suspended; a key rotated away still opens what it sealed;
and a backup whose key is gone, or that was altered, is refused with the fix.
"""

from __future__ import annotations

import stat
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import backup_files
from noust.core.backup_files import (
    CURRENT_KEY,
    KEY_PREFIX,
    BackupKeys,
    is_sealed,
    open_backup,
    seal_in_place,
    sealed_with,
    sealing_suspended,
)
from noust.core.ens import envelope
from noust.core.exceptions import BackupError
from noust.core.runner import SubprocessRunner
from noust.core.secrets import SecretStore

pytestmark = pytest.mark.allow_subprocess

PLAINTEXT = b"PGDMP-secret-customer-rows\n" * 200


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fast key derivation: the construction is under test, not its cost."""
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture
def keys(tmp_path: Path) -> BackupKeys:
    """The keys, in a secret store of the test's own."""
    return BackupKeys(SecretStore(root=tmp_path / "secrets"))


@pytest.fixture
def backups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The backups directory, and so the scratch directory beside it."""
    directory = tmp_path / "backups"
    directory.mkdir()
    monkeypatch.setattr(backup_files.paths, "backup_dir", lambda: directory)
    return directory


@pytest.fixture
def dump(backups: Path) -> Path:
    """A backup in clear."""
    path = backups / "postgresql-shop-20261017.dump.gz"
    path.write_bytes(PLAINTEXT)
    path.chmod(0o600)
    return path


def seal(path: Path, keys: BackupKeys) -> bool:
    """Seal a backup with the real openssl."""
    return seal_in_place(path, keys=keys, runner=SubprocessRunner())


def read(path: Path, keys: BackupKeys) -> bytes:
    """Read a backup through the door."""
    with open_backup(path, keys=keys, runner=SubprocessRunner()) as readable:
        return readable.read_bytes()


# ------------------------------------------------------------------ the keys


def test_nothing_is_sealed_until_a_key_is_made(keys: BackupKeys, dump: Path) -> None:
    assert keys.enabled() is False and keys.current_id() is None

    assert seal(dump, keys) is False
    assert dump.read_bytes() == PLAINTEXT and not is_sealed(dump)


def test_a_key_is_made_current_and_kept_in_the_secret_store(keys: BackupKeys) -> None:
    key_id = keys.make_key()

    assert keys.current_id() == key_id and keys.enabled() is True
    passphrase = keys.passphrase(key_id)
    assert passphrase is not None and len(passphrase) == 48 and passphrase.isalnum()
    assert keys.store.read(CURRENT_KEY) == key_id
    assert [(view.id, view.current) for view in keys.keys()] == [(key_id, True)]


def test_stopping_keeps_every_key_so_what_was_sealed_still_opens(
    keys: BackupKeys, dump: Path
) -> None:
    key_id = keys.make_key()
    seal(dump, keys)

    assert keys.stop_sealing() is True
    assert keys.enabled() is False and keys.stop_sealing() is False

    assert read(dump, keys) == PLAINTEXT
    assert [view.id for view in keys.keys()] == [key_id]
    assert [view.current for view in keys.keys()] == [False]


def test_a_new_key_seals_what_comes_next_and_the_old_one_still_opens_the_old(
    keys: BackupKeys, backups: Path
) -> None:
    first = backups / "first.dump"
    second = backups / "second.dump"
    first.write_bytes(PLAINTEXT)
    second.write_bytes(PLAINTEXT + b"more")
    old = keys.make_key()
    seal(first, keys)

    new = keys.make_key()
    seal(second, keys)

    assert old != new
    assert sealed_with(first) == old and sealed_with(second) == new
    assert read(first, keys) == PLAINTEXT and read(second, keys) == PLAINTEXT + b"more"
    assert {view.id: view.current for view in keys.keys()} == {old: False, new: True}


def test_enabling_makes_a_key_once(keys: BackupKeys) -> None:
    first, created = keys.enable()
    again, created_again = keys.enable()

    assert created is True and created_again is False
    assert first == again == keys.current_id()


def test_rotating_needs_sealing_to_be_on_and_then_keeps_the_old_key(keys: BackupKeys) -> None:
    with pytest.raises(BackupError, match="not sealed"):
        keys.rotate()
    old, _ = keys.enable()

    new = keys.rotate()

    assert new != old and keys.current_id() == new
    assert {view.id: view.current for view in keys.keys()} == {old: False, new: True}


def test_revealing_a_key_gives_its_value_and_leaves_a_record_that_names_no_value(
    keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_id, _ = keys.enable()
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        backup_files.audit,
        "record",
        lambda event, **kwargs: events.append((event, kwargs.get("details", {}))),
    )

    shown_id, value = keys.reveal()

    assert shown_id == key_id and value == keys.passphrase(key_id)
    assert events == [("backup.encryption.reveal", {"key": key_id})]
    assert value not in repr(events)


def test_revealing_says_what_is_wrong_when_there_is_nothing_to_show(keys: BackupKeys) -> None:
    with pytest.raises(BackupError, match="No key is current"):
        keys.reveal()
    keys.enable()

    with pytest.raises(BackupError, match="holds no such key"):
        keys.reveal("0123abcd")
    with pytest.raises(BackupError, match="holds no such key"):
        keys.reveal("../../etc")


@pytest.mark.parametrize(
    "identifier", ["", "../x", "a/b", "a b", "current", "ABCDEF12", "0123456", "0123456789"]
)
def test_a_key_id_that_could_name_another_secret_is_not_looked_up(
    keys: BackupKeys, identifier: str
) -> None:
    keys.make_key()

    assert keys.passphrase(identifier) is None


# ------------------------------------------------------------------ sealing


def test_a_sealed_backup_keeps_its_name_its_mode_and_hides_its_content(
    keys: BackupKeys, dump: Path
) -> None:
    keys.make_key()

    assert seal(dump, keys) is True

    assert is_sealed(dump) and dump.name == "postgresql-shop-20261017.dump.gz"
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600
    assert b"customer" not in dump.read_bytes()
    assert not [p for p in dump.parent.iterdir() if p.name.endswith(".sealing")]
    assert dump.read_bytes().startswith(envelope.MAGIC)


def test_sealing_twice_changes_nothing(keys: BackupKeys, dump: Path) -> None:
    keys.make_key()
    seal(dump, keys)
    once = dump.read_bytes()

    assert seal(dump, keys) is False
    assert dump.read_bytes() == once


def test_a_file_that_is_not_there_or_a_rehearsal_is_not_sealed(
    keys: BackupKeys, dump: Path, backups: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys.make_key()
    assert seal(backups / "absent.dump", keys) is False

    monkeypatch.setattr(backup_files, "is_rehearsal", lambda: True)
    assert seal(dump, keys) is False and dump.read_bytes() == PLAINTEXT


def test_sealing_is_suspended_for_what_a_sealed_archive_will_contain(
    keys: BackupKeys, dump: Path
) -> None:
    keys.make_key()

    with sealing_suspended():
        assert seal(dump, keys) is False
        with sealing_suspended():
            assert seal(dump, keys) is False
        assert seal(dump, keys) is False
    assert dump.read_bytes() == PLAINTEXT

    assert seal(dump, keys) is True, "and sealed again once the block is over"


def test_suspending_sealing_in_one_thread_does_not_leave_another_in_clear(
    keys: BackupKeys, dump: Path
) -> None:
    keys.make_key()
    held = threading.Event()
    release = threading.Event()

    def other() -> None:
        with sealing_suspended():
            held.set()
            release.wait(10)

    thread = threading.Thread(target=other)
    thread.start()
    assert held.wait(10)
    try:
        assert seal(dump, keys) is True
    finally:
        release.set()
        thread.join(10)


def test_a_key_that_is_gone_refuses_to_seal_and_leaves_the_backup_alone(
    keys: BackupKeys, dump: Path
) -> None:
    key_id = keys.make_key()
    keys.store.delete(f"{KEY_PREFIX}/{key_id}")

    with pytest.raises(BackupError, match="the key is missing") as caught:
        seal(dump, keys)

    assert "noust backup encryption" in str(caught.value.details)
    assert dump.read_bytes() == PLAINTEXT


# ------------------------------------------------------------------ the door


def test_a_backup_that_is_not_sealed_is_read_where_it_is(keys: BackupKeys, dump: Path) -> None:
    with open_backup(dump, keys=keys) as readable:
        assert readable == dump


def test_a_sealed_backup_is_read_decrypted_from_a_private_copy_removed_afterwards(
    keys: BackupKeys, dump: Path, backups: Path
) -> None:
    keys.make_key()
    seal(dump, keys)

    with open_backup(dump, keys=keys, runner=SubprocessRunner()) as readable:
        assert readable != dump and readable.name == dump.name
        assert readable.read_bytes() == PLAINTEXT
        assert stat.S_IMODE(readable.stat().st_mode) == 0o600
        assert stat.S_IMODE(readable.parent.stat().st_mode) == 0o700
        scratch = readable.parent

    assert not scratch.exists()
    assert dump.read_bytes().startswith(envelope.MAGIC), "the stored file is untouched"
    assert list((backups / ".open").iterdir()) == []


def test_the_copy_is_removed_however_the_block_ends(
    keys: BackupKeys, dump: Path, backups: Path
) -> None:
    keys.make_key()
    seal(dump, keys)

    with pytest.raises(RuntimeError, match="boom"):
        with open_backup(dump, keys=keys, runner=SubprocessRunner()):
            raise RuntimeError("boom")

    assert list((backups / ".open").iterdir()) == []


def test_readers_nested_in_one_another_share_one_decrypted_copy(
    keys: BackupKeys, dump: Path, backups: Path
) -> None:
    keys.make_key()
    seal(dump, keys)
    runner = SubprocessRunner()

    with open_backup(dump, keys=keys, runner=runner) as outer:
        with open_backup(dump, keys=keys, runner=runner) as inner:
            assert inner == outer
            with open_backup(dump, keys=keys, runner=runner) as innermost:
                assert innermost == outer
        assert outer.read_bytes() == PLAINTEXT, "the outer one's copy outlives the inner ones"

    assert list((backups / ".open").iterdir()) == []
    with open_backup(dump, keys=keys, runner=runner) as again:
        assert again != outer, "and a later reader gets a copy of its own"


def test_a_sealed_backup_whose_key_this_server_lacks_says_how_to_get_it_back(
    keys: BackupKeys, dump: Path, tmp_path: Path
) -> None:
    keys.make_key()
    seal(dump, keys)
    elsewhere = BackupKeys(SecretStore(root=tmp_path / "another-server"))

    with pytest.raises(BackupError, match="does not hold its key") as caught:
        read(dump, elsewhere)

    assert "show-key" in str(caught.value.details)
    assert sealed_with(dump) in str(caught.value.details)


def test_an_altered_backup_is_refused_before_a_byte_of_it_is_used(
    keys: BackupKeys, dump: Path
) -> None:
    keys.make_key()
    seal(dump, keys)
    blob = bytearray(dump.read_bytes())
    blob[-10] ^= 0xFF
    dump.write_bytes(bytes(blob))

    with pytest.raises(BackupError, match="does not open") as caught:
        read(dump, keys)

    assert "altered or damaged" in str(caught.value.details)


def test_a_file_that_only_looks_sealed_is_refused_not_passed_on(
    keys: BackupKeys, dump: Path
) -> None:
    keys.make_key()
    dump.write_bytes(envelope.MAGIC + b"not a header\n")

    with pytest.raises(BackupError):
        read(dump, keys)


def test_a_rehearsal_reads_the_file_as_it_is(
    keys: BackupKeys, dump: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys.make_key()
    seal(dump, keys)
    monkeypatch.setattr(backup_files, "is_rehearsal", lambda: True)

    with open_backup(dump, keys=keys) as readable:
        assert readable == dump


def test_the_door_is_the_only_place_that_opens_a_sealed_backup() -> None:
    """Rule 3: nothing outside the door calls the envelope's opener on a backup."""
    root = Path(backup_files.__file__).parents[1]
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "open_envelope(" in path.read_text(encoding="utf-8")
        and path.name not in {"envelope.py", "backup_files.py", "self_backup.py", "backup.py"}
    ]

    assert offenders == [], f"These open an envelope on their own: {offenders}"


@pytest.fixture(autouse=True)
def _no_leftover_open_state() -> Iterator[None]:
    yield
    backup_files._local.__dict__.pop("open", None)


# ------------------------------------------------------------------ a key that was lost


def test_a_lost_key_is_put_back_and_the_backup_it_sealed_opens_again(
    keys: BackupKeys, dump: Path, tmp_path: Path
) -> None:
    key_id = keys.make_key()
    kept = keys.passphrase(key_id) or ""
    seal(dump, keys)
    keys.store.delete(f"{KEY_PREFIX}/{key_id}")
    with pytest.raises(BackupError, match="does not hold its key"):
        read(dump, keys)

    keys.import_key(key_id, kept)

    assert read(dump, keys) == PLAINTEXT


def test_a_key_is_imported_on_another_server_to_read_what_the_first_sealed(
    keys: BackupKeys, dump: Path, tmp_path: Path
) -> None:
    key_id = keys.make_key()
    seal(dump, keys)
    elsewhere = BackupKeys(SecretStore(root=tmp_path / "another-server"))

    elsewhere.import_key(key_id, keys.passphrase(key_id) or "")

    assert read(dump, elsewhere) == PLAINTEXT
    assert elsewhere.enabled() is False, "importing a key does not make this server seal"


@pytest.mark.parametrize("identifier", ["", "current", "../x", "ABCDEF12", "abc"])
def test_only_the_id_of_a_key_can_be_imported_as_one(keys: BackupKeys, identifier: str) -> None:
    with pytest.raises(BackupError, match="is not the id of a key"):
        keys.import_key(identifier, "a" * 48)


def test_a_value_too_short_to_be_a_key_is_refused(keys: BackupKeys) -> None:
    with pytest.raises(BackupError, match="too short"):
        keys.import_key("0123abcd", "short")


def test_a_wrong_id_cannot_replace_a_good_key(keys: BackupKeys) -> None:
    key_id = keys.make_key()
    before = keys.passphrase(key_id)

    with pytest.raises(BackupError, match="already holds a key"):
        keys.import_key(key_id, "b" * 48)

    assert keys.passphrase(key_id) == before
