# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Base backups of point-in-time recovery made in clear are sealed after the fact (checklist H-08).

The fixtures are those of the PITR tests (a simulated engine over a real directory layout) and
openssl is real. What is pinned: a base backup taken before sealing was turned on becomes an
envelope, its sidecar holds the stored file's size and digest (the digest is checked before every
recovery, so a stale one would refuse a recovery that is sound), a recovery still extracts the plain
archive, and a base that does not match its sidecar is left in clear and said. The archived log is
not covered and is not touched: it is written by the engine's own ``archive_command``, one segment
at a time, outside the backups directory.
"""

# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core import backup_files
from noust.core.backup_files import BackupKeys, is_sealed
from noust.core.ens import envelope
from noust.core.secrets import SecretStore
from noust.managers.backup_destination_files import file_sha256
from noust.managers.backup_sealing import SealExisting
from noust.managers.database.pitr import PitrManager
from tests.test_database_pitr import (  # noqa: F401 - fixtures
    SYSTEM_ID,
    Clock,
    SimulatedPostgres,
    _reset_store,
    clock,
    enabled,
    engine,
    host,
    pitr,
    timer,
)

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BackupKeys:
    """Keys in a store of the test's own; sealing is off until the test makes a key."""
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)
    store = SecretStore(root=tmp_path / "sealing-secrets")
    monkeypatch.setattr(backup_files, "SecretStore", lambda: store)
    monkeypatch.setattr(backup_files.paths, "backup_dir", lambda: tmp_path / "scratch-root")
    return BackupKeys(store)


def sealer(pitr: PitrManager, keys: BackupKeys) -> SealExisting:
    return SealExisting(keys=keys, apps=None, volumes=None, databases=None, pitr=pitr)


def test_a_base_backup_in_clear_is_sealed_and_its_sidecar_is_the_stored_files(
    enabled: PitrManager, engine: SimulatedPostgres, clock: Clock, keys: BackupKeys
) -> None:
    clock.advance(hours=1)
    base = enabled.take_base()
    archive = enabled.base_path(SYSTEM_ID, base)
    assert not is_sealed(archive)
    keys.make_key()

    report = sealer(enabled, keys).run()

    assert {item.state for item in report.items} == {"sealed"}
    assert is_sealed(archive)
    (stored,) = [b for b in enabled.list_bases(SYSTEM_ID) if b.id == base.id]
    assert stored.size == archive.stat().st_size and stored.sha256 == file_sha256(archive)
    assert enabled.plan_restore(None).base.id == base.id, "the digest checked is the stored file's"


def test_a_recovery_extracts_the_plain_archive_of_a_base_sealed_afterwards(
    enabled: PitrManager, engine: SimulatedPostgres, clock: Clock, keys: BackupKeys
) -> None:
    clock.advance(hours=1)
    base = enabled.take_base()
    keys.make_key()
    sealer(enabled, keys).run()

    enabled.restore(None, confirmed=True, base_id=base.id)

    (given,) = engine.extracted
    assert not given.startswith(envelope.MAGIC)
    assert given[:2] == b"\x1f\x8b"


def test_a_base_that_does_not_match_its_sidecar_is_left_in_clear_and_said(
    enabled: PitrManager, engine: SimulatedPostgres, clock: Clock, keys: BackupKeys
) -> None:
    clock.advance(hours=1)
    base = enabled.take_base()
    archive = enabled.base_path(SYSTEM_ID, base)
    with archive.open("ab") as handle:
        handle.write(b"tampered")
    before = archive.read_bytes()
    keys.make_key()

    report = sealer(enabled, keys).run()

    assert any(item.state == "damaged" and item.name.endswith(base.id) for item in report.items)
    assert archive.read_bytes() == before


def test_the_archived_log_is_not_touched(
    enabled: PitrManager, engine: SimulatedPostgres, clock: Clock, keys: BackupKeys
) -> None:
    clock.advance(hours=1)
    enabled.take_base()
    before: dict[str, bytes] = {
        path.name: path.read_bytes() for path in enabled.archived_files(SYSTEM_ID)
    }
    keys.make_key()

    sealer(enabled, keys).run()

    after: dict[str, bytes] = {
        path.name: path.read_bytes() for path in enabled.archived_files(SYSTEM_ID)
    }
    assert after == before


def test_without_a_policy_there_is_nothing_to_do(
    pitr: PitrManager, engine: SimulatedPostgres, keys: BackupKeys
) -> None:
    keys.make_key()

    report = sealer(pitr, keys).run()

    assert report.items == []
