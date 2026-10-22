# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A schedule has a time limit and a time zone of its own (checklist H-01).

A dump that hangs held its timer's unit for as long as it liked, and every schedule ran on the
server's clock whatever the operator's day looked like. What is pinned here: the limit becomes
``TimeoutStartSec=`` in the one service a schedule runs in and the zone a suffix of ``OnCalendar=``,
both are validated before they reach a root-owned unit (a zone must be one the server's database
knows, because ``validate_calendar`` rightly refuses the underscore Buenos_Aires has), they are
kept apart from the policies so no full-row write can put an old value back, and they follow the
unit through every rewrite.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.backup_priority import BackupPriority, with_priority
from noust.managers.backup_scheduler import BackupScheduler
from noust.managers.backup_timer_options import (
    TimerOptions,
    TimerOptionsStore,
    parse_duration,
    validate_options,
    with_options,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    opened = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = opened
    yield opened
    opened.close()
    NoustStore.reset_instance()


@pytest.fixture
def zoneinfo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A zone database with the zones the tests name."""
    root = tmp_path / "zoneinfo"
    for name in ("UTC", "Europe/Madrid", "America/Argentina/Buenos_Aires", "Etc/GMT+1"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TZif2")
    monkeypatch.setattr("noust.managers.backup_timer_options.ZONEINFO_DIRS", (root,))
    return root


class TestADuration:
    @pytest.mark.parametrize(
        ("text", "seconds"),
        [("3600", 3600), ("90m", 5400), ("2h", 7200), ("90s", 90), ("1d", 86400), (" 30M ", 1800)],
    )
    def test_it_reads_seconds_minutes_hours_and_days(self, text: str, seconds: int) -> None:
        assert parse_duration(text) == seconds

    @pytest.mark.parametrize("text", ["", "0", "10", "59s", "2 hours", "-5m", "1.5h", "9d", "ls;"])
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(ValidationError):
            parse_duration(text)


class TestTheOptions:
    def test_nothing_set_is_no_limit_and_the_servers_clock(self, zoneinfo: Path) -> None:
        assert validate_options(None, None) == TimerOptions(None, None)

    def test_a_limit_and_a_zone_are_kept_as_they_are_written(self, zoneinfo: Path) -> None:
        options = validate_options("2h", "Europe/Madrid")

        assert options == TimerOptions(7200, "Europe/Madrid")

    def test_a_zone_with_an_underscore_is_fine_because_it_is_checked_against_the_database(
        self, zoneinfo: Path
    ) -> None:
        assert validate_options(None, "America/Argentina/Buenos_Aires").timezone == (
            "America/Argentina/Buenos_Aires"
        )

    @pytest.mark.parametrize(
        "zone",
        ["Mars/Olympus", "../../etc/passwd", "Europe/Madrid\nExecStart=/bin/evil", "/UTC", ""],
    )
    def test_a_zone_the_server_does_not_know_never_reaches_a_unit(
        self, zone: str, zoneinfo: Path
    ) -> None:
        if zone == "":
            assert validate_options(None, zone).timezone is None
            return
        with pytest.raises(ValidationError):
            validate_options(None, zone)

    @pytest.mark.parametrize("seconds", [59, 7 * 86400 + 1, 0, -1, True])
    def test_a_limit_out_of_range_is_refused(self, seconds: object, zoneinfo: Path) -> None:
        with pytest.raises(ValidationError):
            validate_options(seconds, None)


class TestTheText:
    def test_a_service_gets_its_limit_inside_the_fenced_block(self) -> None:
        service = "[Unit]\nDescription=x\n\n[Service]\nType=oneshot\nExecStart=/bin/true\n"
        prioritised = with_priority(service, BackupPriority())

        text = with_options(prioritised, TimerOptions(7200, None), kind="service")

        assert "TimeoutStartSec=7200\n" in text
        assert text.count("TimeoutStartSec") == 1
        again = with_options(text, TimerOptions(None, None), kind="service")
        assert "TimeoutStartSec" not in again

    def test_a_timer_gets_the_zone_after_its_calendar(self) -> None:
        timer = "[Timer]\nOnCalendar=*-*-* 02:00:00\nPersistent=true\n"

        text = with_options(timer, TimerOptions(None, "Europe/Madrid"), kind="timer")

        assert "OnCalendar=*-*-* 02:00:00 Europe/Madrid\n" in text

    def test_a_zone_is_replaced_never_added_twice(self) -> None:
        timer = "[Timer]\nOnCalendar=Mon *-*-* 02:00:00 UTC\nPersistent=true\n"

        text = with_options(timer, TimerOptions(None, "Europe/Madrid"), kind="timer")
        again = with_options(text, TimerOptions(None, "Europe/Madrid"), kind="timer")

        assert text.count("Europe/Madrid") == 1 and "UTC" not in text
        assert again == text

    def test_a_calendar_that_names_its_own_zone_keeps_it_when_none_is_asked(self) -> None:
        timer = "[Timer]\nOnCalendar=*-*-* 02:00:00 UTC\n"

        assert with_options(timer, TimerOptions(None, None), kind="timer") == timer

    def test_the_shorthands_are_never_taken_for_a_zone(self) -> None:
        timer = "[Timer]\nOnCalendar=daily\n"

        text = with_options(timer, TimerOptions(None, "UTC"), kind="timer")

        assert text == "[Timer]\nOnCalendar=daily UTC\n"


class TestTheStore:
    def test_what_is_set_is_read_back_and_replaced_as_a_whole(self, store: NoustStore) -> None:
        options = TimerOptionsStore(store)

        options.set("noust-backup-db-postgresql-shop", TimerOptions(7200, "Europe/Madrid"))
        options.set("noust-backup-db-postgresql-shop", TimerOptions(None, "UTC"))

        assert options.get("noust-backup-db-postgresql-shop") == TimerOptions(None, "UTC")

    def test_an_empty_set_forgets_the_row(self, store: NoustStore) -> None:
        options = TimerOptionsStore(store)
        options.set("u", TimerOptions(7200, None))

        options.set("u", TimerOptions(None, None))

        assert options.get("u") == TimerOptions(None, None)
        assert options.all() == {}

    def test_delete_forgets_it_too(self, store: NoustStore) -> None:
        options = TimerOptionsStore(store)
        options.set("u", TimerOptions(7200, None))

        options.delete("u")

        assert options.get("u") == TimerOptions(None, None)


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def scheduler(runner: FakeRunner, systemd_dir: Path, store: NoustStore) -> BackupScheduler:
    return BackupScheduler(verbose=False, runner=runner)


class TestInTheUnits:
    def test_a_database_policy_writes_the_limit_and_the_zone(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        scheduler.install_database_timer(
            "postgresql", "shop", "daily", options=TimerOptions(5400, "Europe/Madrid")
        )

        service = (systemd_dir / "noust-backup-db-postgresql-shop.service").read_text()
        timer = (systemd_dir / "noust-backup-db-postgresql-shop.timer").read_text()
        assert "TimeoutStartSec=5400\n" in service
        assert "OnCalendar=*-*-* 02:00:00 Europe/Madrid\n" in timer
        assert "TimeoutStartSec" not in timer

    def test_the_options_survive_a_rewrite_of_the_unit_that_does_not_mention_them(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        scheduler.install_database_timer(
            "postgresql", "shop", "daily", options=TimerOptions(5400, "UTC")
        )
        (systemd_dir / "noust-backup-db-postgresql-shop.service").write_text(
            "# Generated by Noust\n[Service]\nExecStart=/bin/true\n"
        )

        scheduler.apply_priority()

        service = (systemd_dir / "noust-backup-db-postgresql-shop.service").read_text()
        assert "TimeoutStartSec=5400\n" in service

    def test_installing_again_without_options_clears_them(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        scheduler.install_database_timer(
            "postgresql", "shop", "daily", options=TimerOptions(5400, "UTC")
        )

        scheduler.install_database_timer("postgresql", "shop", "daily")

        assert (
            "TimeoutStartSec"
            not in (systemd_dir / "noust-backup-db-postgresql-shop.service").read_text()
        )
        assert " UTC" not in (systemd_dir / "noust-backup-db-postgresql-shop.timer").read_text()

    def test_removing_the_timer_forgets_its_options(
        self, scheduler: BackupScheduler, store: NoustStore
    ) -> None:
        scheduler.install_database_timer(
            "postgresql", "shop", "daily", options=TimerOptions(5400, "UTC")
        )

        scheduler.remove_database_timer("postgresql", "shop")

        assert TimerOptionsStore(store).all() == {}

    def test_a_volume_and_a_self_backup_timer_take_them_too(
        self, scheduler: BackupScheduler, systemd_dir: Path
    ) -> None:
        scheduler.install_volume_timer(
            "shop.example.com", "daily", options=TimerOptions(3600, "UTC")
        )
        scheduler.install_self_backup_timer("daily", options=TimerOptions(None, "Europe/Madrid"))

        assert (
            "TimeoutStartSec=3600\n"
            in (systemd_dir / "noust-backup-vol-shop-example-com.service").read_text()
        )
        assert "02:00:00 Europe/Madrid\n" in (systemd_dir / "noust-backup-self.timer").read_text()
