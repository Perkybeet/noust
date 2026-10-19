# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The checks before Noust updates itself (checklist N-26).

An update replaces the program that runs the server as root, so what stops it is judged here:
room to unpack it, a store that opens and is whole, the package manager's own rehearsal, and a
copy to go back to. A ``fail`` stops the update; a ``warn`` is said and it goes on.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.exceptions import NoustError
from noust.core.runner import FakeRunner
from noust.managers.upgrade_preflight import (
    MIN_FREE_BYTES,
    WARN_FREE_BYTES,
    PreflightCheck,
    PreflightReport,
    UpgradePreflight,
    simulation_argv,
)

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
MB = 1024 * 1024


def preflight(tmp_path: Path, runner: FakeRunner | None = None, **options) -> UpgradePreflight:
    options.setdefault("method", "apt")
    options.setdefault("free_bytes", lambda path: 5000 * MB)
    options.setdefault("backup_age", lambda: timedelta(days=1))
    return UpgradePreflight(
        runner=runner or FakeRunner(), state_dir=tmp_path, now=lambda: NOW, **options
    )


def check(report: PreflightReport, name: str) -> PreflightCheck:
    return next(c for c in report.checks if c.name == name)


# -- the rehearsal each package manager offers


@pytest.mark.parametrize(
    ("method", "argv"),
    [
        (
            "apt",
            [
                "apt-get",
                "-s",
                "-q",
                "-o",
                "Dpkg::Use-Pty=0",
                "install",
                "--only-upgrade",
                "noust",
            ],
        ),
        (
            "dnf",
            ["dnf", "-y", "upgrade", "--refresh", "--setopt=tsflags=test", "noust"],
        ),
        ("yum", ["yum", "-y", "update", "--setopt=tsflags=test", "noust"]),
        ("zypper", ["zypper", "--non-interactive", "update", "--dry-run", "noust"]),
    ],
)
def test_each_package_manager_rehearses_the_update_without_changing_anything(
    method: str, argv: list[str]
) -> None:
    assert simulation_argv(method, ("noust",)) == argv


@pytest.mark.parametrize("method", ["pip", "pipx", "source", "unknown"])
def test_an_installation_without_a_resolver_to_rehearse_has_none(method: str) -> None:
    assert simulation_argv(method, ("noust",)) is None


def test_a_rehearsal_the_package_manager_accepts_is_ok(tmp_path: Path) -> None:
    runner = FakeRunner()

    report = preflight(tmp_path, runner).run()

    assert check(report, "packages").status == "ok"
    assert report.ready
    assert runner.calls[0][:3] == ("apt-get", "-s", "-q")


def test_a_conflict_the_package_manager_finds_stops_the_update_in_its_own_words(
    tmp_path: Path,
) -> None:
    runner = FakeRunner().script(
        ["apt-get", "-s"],
        exit_code=100,
        stdout="Reading package lists...\n",
        stderr="E: Unable to correct problems, you have held broken packages.",
    )

    report = preflight(tmp_path, runner).run()

    packages = check(report, "packages")
    assert packages.status == "fail"
    assert "apt-get refuses the update" in packages.text
    assert "held broken packages" in packages.evidence
    assert not report.ready
    assert report.blocking == (packages,)


def test_only_the_end_of_a_long_rehearsal_is_kept_as_evidence(tmp_path: Path) -> None:
    runner = FakeRunner().script(
        ["apt-get", "-s"], exit_code=100, stdout="\n".join(f"line {i}" for i in range(200))
    )

    packages = check(preflight(tmp_path, runner).run(), "packages")

    assert packages.evidence.splitlines()[-1] == "line 199"
    assert len(packages.evidence.splitlines()) == 20


def test_pip_has_nothing_to_rehearse_and_runs_no_command(tmp_path: Path) -> None:
    runner = FakeRunner()

    report = preflight(tmp_path, runner, method="pip").run()

    assert check(report, "packages").status == "ok"
    assert runner.calls == []


# -- room


def test_too_little_room_stops_the_update_and_says_where(tmp_path: Path) -> None:
    report = preflight(tmp_path, free_bytes=lambda path: 10 * MB).run()

    disk = check(report, "disk")
    assert disk.status == "fail"
    assert "Only 10 MB are free" in disk.text
    assert disk.fix is not None
    assert not report.ready


def test_little_room_is_a_warning_and_the_smallest_place_is_the_one_named(tmp_path: Path) -> None:
    room = {"/usr": 4000 * MB, str(tmp_path): 100 * MB}

    report = preflight(tmp_path, free_bytes=lambda path: room[str(path)]).run()

    disk = check(report, "disk")
    assert disk.status == "warn"
    assert str(tmp_path) in disk.text
    assert report.ready


def test_the_thresholds_are_the_ones_the_messages_use() -> None:
    assert MIN_FREE_BYTES < WARN_FREE_BYTES


def test_free_space_that_cannot_be_read_is_a_warning_not_a_stop(tmp_path: Path) -> None:
    def broken(path: Path) -> int:
        raise OSError("stale file handle")

    disk = check(preflight(tmp_path, free_bytes=broken).run(), "disk")

    assert (disk.status, "stale file handle" in disk.text) == ("warn", True)


# -- the store


def make_store(path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE apps (domain TEXT)")
    connection.execute("INSERT INTO apps VALUES ('shop.example.com')")
    connection.commit()
    connection.close()
    return path


def test_a_whole_store_passes(tmp_path: Path) -> None:
    store = make_store(tmp_path / "noust.db")

    assert check(preflight(tmp_path, store_path=store).run(), "store").status == "ok"


def test_no_store_yet_has_nothing_to_migrate(tmp_path: Path) -> None:
    store = check(preflight(tmp_path, store_path=tmp_path / "missing.db").run(), "store")

    assert (store.status, store.text) == ("ok", "No store yet: nothing to migrate")


def test_a_store_that_is_not_a_database_stops_the_update(tmp_path: Path) -> None:
    broken = tmp_path / "noust.db"
    broken.write_bytes(b"this was a database once" * 100)

    report = preflight(tmp_path, store_path=broken).run()

    store = check(report, "store")
    assert store.status == "fail"
    assert "cannot be read" in store.text
    assert "not a database" in store.evidence
    assert "self-backup" in (store.fix or "")
    assert not report.ready


def test_the_check_reads_the_store_and_never_writes_it(tmp_path: Path) -> None:
    store = make_store(tmp_path / "noust.db")
    before = store.read_bytes()

    preflight(tmp_path, store_path=store).run()

    assert store.read_bytes() == before


# -- a copy to go back to


def test_no_self_backup_at_all_is_a_warning_with_the_command_that_takes_one(tmp_path: Path) -> None:
    report = preflight(tmp_path, backup_age=lambda: None).run()

    backup = check(report, "self_backup")
    assert backup.status == "warn"
    assert backup.fix == "Take one first: noust self-backup create"
    assert report.ready


def test_an_old_self_backup_says_how_old(tmp_path: Path) -> None:
    backup = check(preflight(tmp_path, backup_age=lambda: timedelta(days=12)).run(), "self_backup")

    assert (backup.status, backup.text) == ("warn", "The newest self-backup is 12 days old")


def test_a_recent_self_backup_is_enough(tmp_path: Path) -> None:
    assert check(preflight(tmp_path).run(), "self_backup").status == "ok"


def test_backups_that_cannot_be_listed_are_a_warning_that_says_so(tmp_path: Path) -> None:
    def broken() -> timedelta | None:
        raise NoustError("the backup directory is not readable")

    backup = check(preflight(tmp_path, backup_age=broken).run(), "self_backup")

    assert backup.status == "warn"
    assert "not readable" in backup.text


# -- the report


def test_the_report_is_json_ready_and_says_whether_the_update_may_go_on(tmp_path: Path) -> None:
    data = preflight(tmp_path, free_bytes=lambda path: 1 * MB).run().to_dict()

    assert data["ready"] is False
    assert [c["name"] for c in data["checks"]] == ["disk", "store", "packages", "self_backup"]
    assert data["checks"][0]["status"] == "fail"
