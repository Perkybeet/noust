# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the 2.3 integration harness found: archive sources and reused backup ids."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from wasm.deployers.helpers.preflight import repository_unreachable
from wasm.managers.backup_manager import BackupManager


class _NoGitRunner:
    """Fails the test if git is asked anything."""

    def run(self, argv: list[str], **kwargs: Any) -> Any:
        raise AssertionError(f"git must not be probed for an archive: {argv}")


def test_an_archive_source_is_never_probed_as_a_git_repository() -> None:
    """A recipe's tarball (with its checksum) is downloaded, not cloned."""
    source = "https://wordpress.org/latest.tar.gz#checksum=https://wordpress.org/latest.tar.gz.sha1"

    assert repository_unreachable(_NoGitRunner(), source) == []  # type: ignore[arg-type]


def test_a_new_backup_id_is_never_behind_the_newest_one(tmp_path: Path, monkeypatch: Any) -> None:
    """Retention deleted _000044 locally; a copy with that name may live on a destination."""
    manager = BackupManager.__new__(BackupManager)
    directory = tmp_path / "bk-test"
    directory.mkdir()
    (directory / "bk-test_20260929_000045.tar.gz").write_bytes(b"x")
    monkeypatch.setattr(manager, "_get_app_backup_dir", lambda app_name: directory)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> _Clock:
            return cls(2026, 9, 29, 0, 0, 44)

    monkeypatch.setattr("wasm.managers.backup_manager.datetime", _Clock)

    assert manager._generate_backup_id("bk.test") == "bk-test_20260929_000046"
