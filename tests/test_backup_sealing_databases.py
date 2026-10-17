# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database dumps made in clear are sealed after the fact (checklist H-08).

The fixtures are those of the sealed database-backup tests (the engine that really writes dump
files, the shared fake rclone, openssl real). What is pinned: a dump taken before sealing was turned
on becomes an envelope, the record of the dump holds the stored file's size and digest (so the next
check does not say the file changed on disk), a restore still loads the plain dump, a dump that does
not match its record is left in clear and said, an imported dump nobody recorded is sealed without a
record to update, and a run that stopped before the record is repaired by running again.
"""

# ruff: noqa: F811

from __future__ import annotations

import pytest

from noust.core.backup_files import BackupKeys, is_sealed, seal_in_place
from noust.core.runner import SubprocessRunner
from noust.managers.backup_destination_files import file_sha256
from noust.managers.backup_sealing import SealExisting
from noust.managers.database.backups import DatabaseBackups, DumpView
from tests.database_backup_support import PG_ARCHIVE
from tests.test_database_sealed_backups import (  # noqa: F401 - fixtures
    OpensslRunner,
    _reset_store,
    backups,
    cheap_scrypt,
    dumps_dir,
    engine,
    keys,
    loaded,
    runner,
    service,
)

pytestmark = pytest.mark.allow_subprocess


def sealer(backups: DatabaseBackups, keys: BackupKeys) -> SealExisting:
    return SealExisting(keys=keys, apps=None, volumes=None, databases=backups, pitr=None)


def dump_in_clear(backups: DatabaseBackups, keys: BackupKeys) -> DumpView:
    """A dump taken while sealing was off, with sealing turned on again afterwards."""
    keys.stop_sealing()
    taken = backups.dump("postgresql", "shop")
    keys.make_key()
    return taken


def test_a_dump_in_clear_is_sealed_and_its_record_is_the_stored_files(
    backups: DatabaseBackups, keys: BackupKeys
) -> None:
    taken = dump_in_clear(backups, keys)
    path = taken.info.path
    assert not is_sealed(path)

    report = sealer(backups, keys).run()

    assert [(item.kind, item.state) for item in report.items] == [("database", "sealed")]
    assert is_sealed(path) and PG_ARCHIVE not in path.read_bytes()
    (view,) = backups.list_dumps("postgresql")
    assert view.record is not None
    assert view.record.size == path.stat().st_size
    assert view.record.sha256 == file_sha256(path)


def test_the_next_check_does_not_say_the_file_changed(
    backups: DatabaseBackups, keys: BackupKeys
) -> None:
    taken = dump_in_clear(backups, keys)
    sealer(backups, keys).run()

    checked = backups.verify("postgresql", taken.name)

    assert checked.record is not None and checked.record.verify_status == "ok", (
        checked.record.verify_detail
    )


def test_a_restore_loads_the_plain_dump_afterwards(
    backups: DatabaseBackups, keys: BackupKeys, loaded: list[bytes]
) -> None:
    taken = dump_in_clear(backups, keys)
    sealer(backups, keys).run()

    backups.restore("postgresql", "shop", taken.name, safety_backup=False)

    assert loaded == [PG_ARCHIVE]


def test_a_dump_that_does_not_match_its_record_is_left_in_clear_and_said(
    backups: DatabaseBackups, keys: BackupKeys
) -> None:
    taken = dump_in_clear(backups, keys)
    path = taken.info.path
    with path.open("ab") as handle:
        handle.write(b"tampered")
    before = path.read_bytes()

    report = sealer(backups, keys).run()

    (item,) = report.items
    assert item.state == "damaged"
    assert path.read_bytes() == before


def test_a_dump_nobody_recorded_is_sealed_with_no_record_to_update(
    backups: DatabaseBackups, keys: BackupKeys
) -> None:
    taken = dump_in_clear(backups, keys)
    assert backups.records.delete_dump("postgresql", taken.name) is True

    report = sealer(backups, keys).run()

    assert [item.state for item in report.items] == ["sealed"]
    assert is_sealed(taken.info.path)
    (view,) = backups.list_dumps("postgresql")
    assert view.record is None


def test_a_run_that_stopped_before_the_record_is_repaired_by_running_again(
    backups: DatabaseBackups, keys: BackupKeys
) -> None:
    taken = dump_in_clear(backups, keys)
    seal_in_place(taken.info.path, keys=keys, runner=SubprocessRunner())

    report = sealer(backups, keys).run()

    (item,) = report.items
    assert item.state == "repaired"
    (view,) = backups.list_dumps("postgresql")
    assert view.record is not None and view.record.sha256 == file_sha256(taken.info.path)


def test_a_dump_sealed_when_it_was_made_is_already_done(
    backups: DatabaseBackups, keys: BackupKeys
) -> None:
    taken = backups.dump("postgresql", "shop")

    report = sealer(backups, keys).run()

    assert [item.state for item in report.items] == ["already"]
    assert is_sealed(taken.info.path)
