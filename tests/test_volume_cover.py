# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether a named volume of an application's stack is in a copy, and when the last was (item 189).

The Docker zone lists volumes and draws, for each, whether it is protected. That is the backup manager's
to say and it says it from what it keeps (the application's policy for its data, the copies of its volumes
and the application backups that carry them), never from Docker: the volumes are given by name, and nothing
here asks the engine for anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.store import App, get_store
from noust.managers.backup_manager import BackupManager
from noust.managers.volume_backup_records import VolumePolicy, VolumeRecords
from noust.managers.volume_backups import VolumeBackupManager, VolumeBackupMetadata
from tests.support.volume_backups import (  # noqa: F401
    FakeServices,
    _reset_store,
    app,
    app_dir,
    manager,
    runner,
    services,
)

# ruff: noqa: F811

STACK = "stack.example.com"


@pytest.fixture
def stack(tmp_path: Path, manager: VolumeBackupManager) -> App:
    return get_store().create_app(
        App(
            domain=STACK,
            app_type="docker-compose",
            app_path=str(tmp_path / "apps" / "stack-example-com"),
            layout="inplace",
        )
    )


def copy_of(manager: VolumeBackupManager, volume: str, taken: datetime) -> VolumeBackupMetadata:
    """A copy of a named volume as the manager keeps one: a sidecar and its archive."""
    identifier = f"stack-example-com.vol-{volume}_{taken:%Y%m%d_%H%M%S}"
    metadata = VolumeBackupMetadata(
        id=identifier,
        domain=STACK,
        app_name="stack-example-com",
        target=f"volume:{volume}",
        kind="docker",
        source=volume,
        slug=f"vol-{volume}",
        created_at=taken.isoformat(timespec="seconds"),
        size_bytes=7,
        checksum=None,
    )
    manager._write_sidecar(metadata)
    manager.archive_path(metadata).write_bytes(b"archive")
    return metadata


def policy(targets: list[str] | None = None, *, enabled: bool = True) -> None:
    VolumeRecords(get_store()).save_policy(
        VolumePolicy(app_domain=STACK, schedule="daily", targets=targets or [], enabled=enabled)
    )


NOW = datetime(2026, 11, 14, 3, 0, 0, tzinfo=timezone.utc)


class TestWhatIsDoneForAVolume:
    def test_a_volume_nobody_copies_says_so_and_how_to_copy_it(
        self, manager: VolumeBackupManager, stack: App
    ) -> None:
        found = manager.cover(STACK, ["stack_pgdata"])["stack_pgdata"]

        assert (found.state, found.last_at) == ("none", None)
        assert found.via == f"noust backup volumes create {STACK} --target volume:stack_pgdata"

    def test_a_volume_that_has_a_copy_and_no_schedule_says_when_the_last_was(
        self, manager: VolumeBackupManager, stack: App
    ) -> None:
        copy_of(manager, "stack_pgdata", NOW - timedelta(days=9))
        newest = copy_of(manager, "stack_pgdata", NOW - timedelta(days=2))

        found = manager.cover(STACK, ["stack_pgdata"])["stack_pgdata"]

        assert (found.state, found.last_at) == ("copied", newest.created_at)

    def test_a_volume_a_schedule_covers_says_so_with_or_without_a_copy_yet(
        self, manager: VolumeBackupManager, stack: App
    ) -> None:
        policy()

        found = manager.cover(STACK, ["stack_pgdata"])["stack_pgdata"]

        assert (found.state, found.last_at) == ("scheduled", None)

    def test_a_schedule_that_names_other_data_does_not_cover_this_volume(
        self, manager: VolumeBackupManager, stack: App
    ) -> None:
        policy(["volume:stack_uploads", "path:uploads"])

        found = manager.cover(STACK, ["stack_pgdata", "stack_uploads"])

        assert found["stack_pgdata"].state == "none"
        assert found["stack_uploads"].state == "scheduled"

    def test_a_schedule_that_is_off_covers_nothing(
        self, manager: VolumeBackupManager, stack: App
    ) -> None:
        policy(enabled=False)

        assert manager.cover(STACK, ["stack_pgdata"])["stack_pgdata"].state == "none"

    def test_the_last_copy_is_of_the_volume_and_not_of_another_one(
        self, manager: VolumeBackupManager, stack: App
    ) -> None:
        copy_of(manager, "stack_uploads", NOW)

        found = manager.cover(STACK, ["stack_pgdata", "stack_uploads"])

        assert found["stack_pgdata"].last_at is None
        assert found["stack_uploads"].last_at is not None

    def test_an_application_backup_that_carried_its_volumes_is_a_copy_of_each(
        self,
        manager: VolumeBackupManager,
        stack: App,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        carried = type(
            "Backup",
            (),
            {
                "created_at": (NOW - timedelta(days=1)).isoformat(timespec="seconds"),
                "docker_volume_backups": [{"volume": "stack_pgdata"}],
            },
        )()
        monkeypatch.setattr(BackupManager, "list_backups", lambda self, **kw: [carried])

        found = manager.cover(STACK, ["stack_pgdata", "stack_uploads"])

        assert (found["stack_pgdata"].state, found["stack_pgdata"].last_at) == (
            "copied",
            carried.created_at,
        )
        assert found["stack_uploads"].state == "none"

    def test_the_newest_of_the_two_kinds_of_copy_is_the_last(
        self,
        manager: VolumeBackupManager,
        stack: App,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        carried = type(
            "Backup",
            (),
            {
                "created_at": (NOW - timedelta(days=1)).isoformat(timespec="seconds"),
                "docker_volume_backups": [{"volume": "stack_pgdata"}],
            },
        )()
        monkeypatch.setattr(BackupManager, "list_backups", lambda self, **kw: [carried])
        copy_of(manager, "stack_pgdata", NOW - timedelta(days=5))

        found = manager.cover(STACK, ["stack_pgdata"])["stack_pgdata"]

        assert found.last_at == carried.created_at

    def test_nothing_is_asked_of_docker(
        self, manager: VolumeBackupManager, stack: App, runner: object
    ) -> None:
        policy()
        copy_of(manager, "stack_pgdata", NOW)

        manager.cover(STACK, ["stack_pgdata", "stack_uploads"])

        assert [c for c in runner.calls if c[:1] == ("docker",)] == []  # type: ignore[attr-defined]

    def test_an_application_that_is_gone_has_nothing_covered(
        self, manager: VolumeBackupManager
    ) -> None:
        found = manager.cover("gone.example.com", ["stack_pgdata"])["stack_pgdata"]

        assert found.state == "none"


def test_the_command_a_volume_is_told_to_use_is_one_the_command_line_has() -> None:
    from click.testing import CliRunner

    from noust.cli.app import cli

    result = CliRunner().invoke(cli, ["backup", "volumes", "create", "--help"])

    assert result.exit_code == 0
    assert "--target" in result.output
