"""
Tests for scheduled reboots and shutdowns.

A reboot is not a job: it would die with the process that ran it. What can
report its end is a row written before and read after, so the tests are about
the row and about the checks that run before it is written. The one rule under
all of them is that nothing here reboots the machine because an update asked to;
an operator does, with the checks in front of them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core import paths
from noust.core.exceptions import ValidationError
from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.server.errors import PreflightError, ServerError
from noust.managers.server.power import (
    LAST_BOOT_FILE,
    PowerManager,
    clean_message,
    parse_scheduled_file,
    resolve_delay,
    unenabled_app_units,
)
from noust.managers.server.power_records import PowerRecords
from tests.server_support import make_host

NOW = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
BOOT = "d875e599869f472296c96f9b3e1b5356"


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "power.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def host(tmp_path: Path):
    root = make_host(tmp_path / "root")
    (root.boot_id).write_text("d875e599-869f-4722-96c9-6f9b3e1b5356\n")
    root.proc_uptime.write_text("12345.67 45678.90\n")
    return root


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setattr(paths, "state_dir", lambda: directory)
    return directory


def _manager(store, host, runner: FakeRunner | None = None, **kwargs) -> PowerManager:
    runner = runner or FakeRunner()
    kwargs.setdefault("apps_check", lambda r: [])
    return PowerManager(
        runner=runner,
        fs=RecordingFileSystem(),
        host=host,
        records=PowerRecords(store),
        clock=lambda: NOW,
        **kwargs,
    )


def _healthy(runner: FakeRunner) -> FakeRunner:
    runner.script(["systemctl", "is-enabled"], stdout="enabled\n")
    runner.script(["findmnt", "--verify"], stdout="Success, no errors or warnings detected\n")
    return runner


class TestWhen:
    def test_minutes_pass_through_and_zero_is_now(self) -> None:
        assert resolve_delay(in_minutes=5) == 5
        assert resolve_delay(in_minutes=0) == 0

    def test_a_moment_rounds_up_so_it_is_never_earlier_than_asked(self) -> None:
        assert resolve_delay(at=NOW + timedelta(minutes=4, seconds=1), now=NOW) == 5

    def test_a_moment_in_the_past_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="already passed"):
            resolve_delay(at=NOW - timedelta(minutes=1), now=NOW)

    def test_more_than_a_week_ahead_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="more than a week"):
            resolve_delay(in_minutes=8 * 24 * 60)

    def test_it_has_to_say_when_and_only_one_way(self) -> None:
        with pytest.raises(ValidationError):
            resolve_delay()
        with pytest.raises(ValidationError):
            resolve_delay(in_minutes=1, at=NOW)

    def test_a_wall_message_cannot_carry_an_escape_sequence(self) -> None:
        assert clean_message("hi\x1b[2Jthere\n", "default") == "hi [2Jthere"
        assert clean_message("", "default") == "default"
        assert len(clean_message("x" * 500, "d")) == 200


class TestScheduling:
    def test_a_reboot_is_scheduled_with_shutdown_and_recorded_with_its_boot(
        self, store, host
    ) -> None:
        runner = _healthy(FakeRunner())
        manager = _manager(store, host, runner)

        scheduled = manager.schedule("reboot", minutes=5, actor="yago")

        assert ("shutdown", "-r", "+5", "Reboot requested from Noust by yago") in runner.calls
        assert scheduled.action == "reboot"
        assert scheduled.requested_by == "yago"
        assert scheduled.boot_id == BOOT
        assert scheduled.scheduled_for == (NOW + timedelta(minutes=5)).isoformat()
        assert PowerRecords(store).active() is not None

    def test_a_shutdown_powers_off_rather_than_halts(self, store, host) -> None:
        runner = _healthy(FakeRunner())

        _manager(store, host, runner).schedule("poweroff", minutes=1, actor=None)

        assert runner.calls[-1][:3] == ("shutdown", "-P", "+1")

    def test_scheduling_another_replaces_the_first(self, store, host) -> None:
        manager = _manager(store, host, _healthy(FakeRunner()))
        manager.schedule("reboot", minutes=10, actor="a")
        manager.schedule("reboot", minutes=20, actor="b")

        history = PowerRecords(store).history()

        assert [row.status for row in history] == ["scheduled", "cancelled"]

    def test_nothing_but_reboot_and_poweroff_can_be_scheduled(self, store, host) -> None:
        with pytest.raises(ValidationError):
            _manager(store, host).schedule("halt", minutes=1, actor=None)

    def test_shutdown_refusing_is_an_error_with_its_output_and_leaves_no_row(
        self, store, host
    ) -> None:
        runner = _healthy(FakeRunner())
        runner.script(["shutdown"], stderr="Failed to connect to bus", exit_code=1)

        with pytest.raises(ServerError) as raised:
            _manager(store, host, runner).schedule("reboot", minutes=1, actor=None)

        assert "Failed to connect to bus" in (raised.value.output or "")
        assert PowerRecords(store).active() is None

    def test_a_warning_stops_it_until_it_is_confirmed(self, store, host) -> None:
        runner = _healthy(FakeRunner())
        manager = _manager(store, host, runner, blockers=lambda: ["deploy shop.example.com"])

        with pytest.raises(PreflightError) as raised:
            manager.schedule("reboot", minutes=1, actor=None)

        assert raised.value.blockers == ["Running now: deploy shop.example.com"]
        assert not runner.ran("shutdown")

        manager.schedule("reboot", minutes=1, actor=None, force=True)

        assert runner.ran("shutdown")

    def test_the_wall_message_is_the_one_the_caller_wrote_cleaned(self, store, host) -> None:
        runner = _healthy(FakeRunner())

        _manager(store, host, runner).schedule(
            "reboot", minutes=2, actor="yago", message="Kernel update\x07"
        )

        assert runner.calls[-1][-1] == "Kernel update"


class TestChecks:
    def _by_id(self, manager: PowerManager) -> dict:
        return {check.id: check for check in manager.checks()}

    def test_a_healthy_machine_passes_everything(self, store, host) -> None:
        runner = _healthy(FakeRunner())

        checks = self._by_id(_manager(store, host, runner))

        assert {c.status for c in checks.values()} == {"ok"}
        assert set(checks) == {"jobs", "console", "apps", "fstab", "ramdisk"}

    def test_a_console_that_is_not_a_unit_will_not_come_back(self, store, host) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "is-enabled"], stdout="disabled\n", exit_code=1)
        runner.script(["findmnt", "--verify"], stdout="Success\n")

        checks = self._by_id(_manager(store, host, runner))

        assert checks["console"].status == "warn"
        assert "noust web enable" in checks["console"].message

    def test_the_console_under_its_old_wasm_name_counts(self, store, host) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "is-enabled", "noust-web.service"], stdout="", exit_code=1)
        runner.script(["systemctl", "is-enabled", "wasm-web.service"], stdout="enabled\n")
        runner.script(["findmnt", "--verify"], stdout="Success\n")

        assert self._by_id(_manager(store, host, runner))["console"].status == "ok"

    def test_applications_that_will_stay_down_are_named(self, store, host) -> None:
        runner = _healthy(FakeRunner())
        manager = _manager(store, host, runner, apps_check=lambda r: ["shop-example-com"])

        checks = self._by_id(manager)

        assert checks["apps"].status == "warn"
        assert "shop-example-com" in checks["apps"].message

    def test_a_broken_fstab_is_shown_in_the_tools_own_words(self, store, host) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "is-enabled"], stdout="enabled\n")
        runner.script(
            ["findmnt", "--verify"],
            stdout="/dev/sdb1 does not exist\n0 parse errors, 1 errors, 0 warnings",
            exit_code=1,
        )

        checks = self._by_id(_manager(store, host, runner))

        assert checks["fstab"].status == "warn"
        assert "/dev/sdb1 does not exist" in checks["fstab"].message

    def test_a_kernel_with_no_ramdisk_will_not_boot(self, store, host) -> None:
        (host.boot / "vmlinuz-6.8.0-100-generic").write_text("")
        runner = _healthy(FakeRunner())

        checks = self._by_id(_manager(store, host, runner))

        assert checks["ramdisk"].status == "warn"
        assert "6.8.0-100-generic" in checks["ramdisk"].message

        (host.boot / "initrd.img-6.8.0-100-generic").write_text("")

        assert self._by_id(_manager(store, host, runner))["ramdisk"].status == "ok"

    def test_units_of_applications_that_are_not_enabled_are_listed(
        self, store, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.store import App

        store.create_app(App(domain="shop.example.com", app_path="/var/www/apps/shop-example-com"))
        store.create_app(App(domain="blog.example.com", app_path="/var/www/apps/blog-example-com"))
        runner = FakeRunner()
        runner.script(["systemctl", "is-enabled", "shop-example-com.service"], stdout="enabled\n")
        runner.script(
            ["systemctl", "is-enabled", "blog-example-com.service"],
            stdout="disabled\n",
            exit_code=1,
        )
        monkeypatch.setattr("noust.core.store.get_store", lambda *a, **k: store)
        monkeypatch.setattr("noust.managers.service_manager.get_store", lambda *a, **k: store)

        assert unenabled_app_units(runner) == ["blog-example-com"]


class TestStatusAndCancelling:
    def test_the_schedule_is_systemds_file_and_the_row_says_who(self, store, host) -> None:
        manager = _manager(store, host, _healthy(FakeRunner()))
        manager.schedule("reboot", minutes=5, actor="yago")
        host.shutdown_scheduled.write_text(
            "USEC=1790716408000000\nWARN_WALL=1\nMODE=reboot\nWALL_MESSAGE=Reboot requested\n"
        )

        status = manager.status()

        assert status.scheduled is not None
        assert status.scheduled.requested_by == "yago"
        assert status.mode == "reboot"
        assert status.due_at is not None and status.due_at.startswith("2026-09-29T")
        assert status.boot_id == BOOT
        assert status.uptime_seconds == 12345.67

    def test_a_row_systemd_no_longer_has_is_closed_as_lost(self, store, host) -> None:
        manager = _manager(store, host, _healthy(FakeRunner()))
        manager.schedule("reboot", minutes=5, actor="yago")

        status = manager.status()

        # Somebody ran `shutdown -c` by hand: there is nothing scheduled.
        assert status.scheduled is None
        assert PowerRecords(store).history()[0].status == "lost"

    def test_cancelling_runs_shutdown_c_and_closes_the_row(self, store, host) -> None:
        runner = _healthy(FakeRunner())
        manager = _manager(store, host, runner)
        manager.schedule("reboot", minutes=5, actor="yago")
        host.shutdown_scheduled.write_text("MODE=reboot\n")

        assert manager.cancel() is True

        assert ("shutdown", "-c") in runner.calls
        assert PowerRecords(store).history()[0].status == "cancelled"

    def test_cancelling_when_nothing_is_scheduled_says_so(self, store, host) -> None:
        assert _manager(store, host).cancel() is False

    def test_a_failure_to_cancel_is_reported_when_something_is_scheduled(self, store, host) -> None:
        runner = _healthy(FakeRunner())
        manager = _manager(store, host, runner)
        manager.schedule("reboot", minutes=5, actor="yago")
        host.shutdown_scheduled.write_text("MODE=reboot\n")
        runner.script(["shutdown", "-c"], stderr="Access denied", exit_code=1)

        with pytest.raises(ServerError) as raised:
            manager.cancel()

        assert "Access denied" in (raised.value.output or "")

    def test_the_scheduled_file_is_read(self) -> None:
        mode, due = parse_scheduled_file("USEC=1790716408000000\nMODE=poweroff\n")

        assert mode == "poweroff"
        assert due == "2026-09-29T21:13:28+00:00"
        assert parse_scheduled_file("") == (None, None)


class TestComingBack:
    def test_the_first_look_only_remembers_the_boot(self, store, host, state_dir) -> None:
        manager = _manager(store, host)

        assert manager.detect_return() is None
        assert (state_dir / LAST_BOOT_FILE).read_text().strip() == BOOT

    def test_the_same_boot_says_nothing(self, store, host, state_dir) -> None:
        (state_dir / LAST_BOOT_FILE).write_text(BOOT + "\n")

        assert _manager(store, host).detect_return() is None

    def test_a_scheduled_reboot_that_happened_is_reported_with_who_and_how_long(
        self, store, host, state_dir
    ) -> None:
        # Scheduled during the previous boot, due five minutes ago.
        records = PowerRecords(store)
        records.schedule(
            "reboot",
            (NOW - timedelta(minutes=2, seconds=10)).isoformat(),
            requested_by="yago",
            message="m",
            boot_id="0000000000000000000000000000beef",
        )
        (state_dir / LAST_BOOT_FILE).write_text("0000000000000000000000000000beef\n")
        manager = _manager(store, host)

        returned = manager.detect_return()

        assert returned is not None
        assert returned.planned is True
        assert returned.requested_by == "yago"
        assert returned.took_seconds == 130
        assert records.history()[0].status == "completed"
        assert (state_dir / LAST_BOOT_FILE).read_text().strip() == BOOT

    def test_a_reboot_nobody_scheduled_is_reported_too(self, store, host, state_dir) -> None:
        (state_dir / LAST_BOOT_FILE).write_text("0000000000000000000000000000beef\n")

        returned = _manager(store, host).detect_return()

        assert returned is not None
        assert returned.planned is False
        assert returned.requested_by is None

    def test_an_unreadable_boot_id_reports_nothing(self, store, host, state_dir) -> None:
        host.boot_id.unlink()

        assert _manager(store, host).detect_return() is None
