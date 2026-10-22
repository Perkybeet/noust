# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sealing what is already on the disk, as a whole (checklist H-08).

The kinds of backup each have their own test with the real manager; here the coordinator is held to
what it promises over any of them, with small doubles that keep real files: it refuses to run while
sealing is off and says what to do, one kind that cannot be listed does not stop the others, a
rehearsal changes nothing, the plan counts without touching, the audit trail records the run once
and only when something was sealed, and the report says what is still in clear that no record covers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit, backup_files
from noust.core.backup_files import BackupKeys, is_sealed
from noust.core.ens import envelope
from noust.core.exceptions import BackupError
from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.runner import SubprocessRunner
from noust.core.secrets import SecretStore
from noust.managers.backup_sealing import SealExisting

pytestmark = pytest.mark.allow_subprocess

PLAIN = b"customer rows\n" * 100


@dataclass
class Meta:
    id: str
    domain: str
    checksum: str | None
    size_bytes: int = 0


class Apps:
    """Just enough of the application backup manager."""

    def __init__(self, root: Path, entries: list[Meta]) -> None:
        self.root = root
        self.entries = entries
        self.recorded: list[tuple[str, int, str]] = []

    def list_backups(self) -> list[Meta]:
        return self.entries

    def local_files(self, meta: Meta) -> tuple[Path, Path]:
        return self.root / f"{meta.id}.tar.gz", self.root / f"{meta.id}.json"

    def record_resealed(self, meta: Meta, *, size_bytes: int, checksum: str) -> None:
        meta.size_bytes, meta.checksum = size_bytes, checksum
        self.recorded.append((meta.id, size_bytes, checksum))


class Broken:
    """A manager whose listing fails."""

    def list_backups(self) -> list[Any]:
        raise BackupError("the volume backups cannot be listed", details="/var/backups is gone")


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BackupKeys:
    (tmp_path / "backups").mkdir()
    monkeypatch.setattr(backup_files.paths, "backup_dir", lambda: tmp_path / "backups")
    return BackupKeys(SecretStore(root=tmp_path / "secrets"))


@pytest.fixture
def apps(tmp_path: Path) -> Apps:
    import hashlib

    entries = []
    for name in ("shop.a", "shop.b"):
        archive = tmp_path / "backups" / f"{name}.tar.gz"
        archive.write_bytes(PLAIN + name.encode())
        entries.append(
            Meta(name, "shop.example.com", hashlib.sha256(archive.read_bytes()).hexdigest())
        )
    return Apps(tmp_path / "backups", entries)


def make(keys: BackupKeys, **managers: Any) -> SealExisting:
    base: dict[str, Any] = {"apps": None, "volumes": None, "databases": None, "pitr": None}
    base.update(managers)
    return SealExisting(keys=keys, runner=SubprocessRunner(), **base)


def test_it_refuses_while_sealing_is_off_and_says_what_to_do(keys: BackupKeys, apps: Apps) -> None:
    with pytest.raises(BackupError) as refusal:
        make(keys, apps=apps).run()

    assert "not sealed" in refusal.value.message
    assert "noust backup encryption enable" in (refusal.value.details or "")
    assert not any(is_sealed(apps.local_files(meta)[0]) for meta in apps.entries)


def test_every_backup_is_sealed_and_recorded(keys: BackupKeys, apps: Apps) -> None:
    keys.make_key()

    report = make(keys, apps=apps).run()

    assert [item.state for item in report.items] == ["sealed", "sealed"]
    assert report.counts()["sealed"] == 2 and report.ok is True
    assert [entry[0] for entry in apps.recorded] == ["shop.a", "shop.b"]
    assert all(is_sealed(apps.local_files(meta)[0]) for meta in apps.entries)


def test_a_second_run_finds_nothing_to_do(keys: BackupKeys, apps: Apps) -> None:
    keys.make_key()
    make(keys, apps=apps).run()
    recorded_before = list(apps.recorded)

    report = make(keys, apps=apps).run()

    assert {item.state for item in report.items} == {"already"}
    assert apps.recorded == recorded_before


def test_a_kind_that_cannot_be_listed_does_not_stop_the_others(
    keys: BackupKeys, apps: Apps
) -> None:
    keys.make_key()

    report = make(keys, apps=apps, volumes=Broken()).run()

    states = {(item.kind, item.state) for item in report.items}
    assert ("application", "sealed") in states
    failed = [item for item in report.items if item.state == "failed"]
    assert len(failed) == 1 and failed[0].kind == "data"
    assert "cannot be listed" in failed[0].detail and "/var/backups is gone" in failed[0].detail
    assert report.ok is False


def test_a_record_that_cannot_be_written_is_a_failure_that_running_again_repairs(
    keys: BackupKeys, apps: Apps, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys.make_key()
    original = apps.record_resealed
    calls = {"n": 0}

    def flaky(meta: Meta, *, size_bytes: int, checksum: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise BackupError("Could not write the metadata", details="disk full")
        original(meta, size_bytes=size_bytes, checksum=checksum)

    monkeypatch.setattr(apps, "record_resealed", flaky)

    first = make(keys, apps=apps).run()
    second = make(keys, apps=apps).run()

    assert [item.state for item in first.items] == ["failed", "sealed"]
    assert "disk full" in first.items[0].detail
    assert [item.state for item in second.items] == ["repaired", "already"]


def test_a_rehearsal_changes_nothing_and_says_what_it_would_do(
    keys: BackupKeys, apps: Apps
) -> None:
    keys.make_key()
    before = [apps.local_files(meta)[0].read_bytes() for meta in apps.entries]
    set_fs(DryRunFileSystem())
    try:
        report = make(keys, apps=apps).run()
    finally:
        set_fs(None)

    assert [item.state for item in report.items] == ["would_seal", "would_seal"]
    assert [apps.local_files(meta)[0].read_bytes() for meta in apps.entries] == before
    assert apps.recorded == []


def test_the_plan_counts_what_is_in_clear_without_touching_it(keys: BackupKeys, apps: Apps) -> None:
    keys.make_key()
    make(keys, apps=apps).run()
    extra = apps.root / "shop.c.tar.gz"
    extra.write_bytes(PLAIN)
    import hashlib

    apps.entries.append(Meta("shop.c", "shop.example.com", hashlib.sha256(PLAIN).hexdigest()))

    plan = make(keys, apps=apps).plan()

    assert plan["applications"] == 1 and plan["total"] == 1
    assert plan["sealing"] is True
    assert not is_sealed(extra)


def test_the_plan_says_when_sealing_is_off(keys: BackupKeys, apps: Apps) -> None:
    assert make(keys, apps=apps).plan()["sealing"] is False


def test_the_run_is_audited_once_and_only_when_something_was_sealed(
    keys: BackupKeys, apps: Apps, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys.make_key()
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        audit, "record", lambda event, **kwargs: events.append((event, kwargs.get("details", {})))
    )

    make(keys, apps=apps).run()
    make(keys, apps=apps).run()

    seals = [details for event, details in events if event == "backup.encryption.seal"]
    assert len(seals) == 1 and seals[0]["sealed"] == 2


def test_the_report_counts_what_no_record_covers(
    keys: BackupKeys, apps: Apps, tmp_path: Path
) -> None:
    keys.make_key()
    stray = tmp_path / "backups" / "databases" / "mysql" / "old.sql.gz"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(PLAIN)

    report = make(keys, apps=apps).run()

    assert report.clear_left == 1
    assert report.to_dict()["clear_left"] == 1
    assert not is_sealed(stray)


def test_the_report_is_plain_data(keys: BackupKeys, apps: Apps) -> None:
    keys.make_key()

    data = make(keys, apps=apps).run().to_dict()

    assert data["ok"] is True
    assert data["counts"]["sealed"] == 2
    assert data["items"][0] == {
        "kind": "application",
        "name": "shop.a",
        "state": "sealed",
        "detail": "",
    }


def test_the_real_managers_are_built_when_none_is_given(keys: BackupKeys) -> None:
    """The product path: ``SealExisting()`` with no doubles reads every kind from its own store."""
    keys.make_key()

    plan = SealExisting(keys=keys).plan()

    assert plan["sealing"] is True
    assert plan["total"] == 0
