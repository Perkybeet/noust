# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The monitor's thresholds are the operator's, and the new checks hang off its loop.

Checklist G-19 (the disk threshold, how often it is looked at and when a disk
that stays full is announced again, and the certificate window) and the wiring
of K-17, K-18, K-22 and K-23 (the watches), G-18 (the alert engine) and K-30
(the delivery sweep).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.config import Config, ConfigError
from noust.core.notifier import SweepReport
from noust.managers.cert_manager import CertificateInfo
from noust.monitor.process_monitor import DISK_ALERT_PERCENT, MonitorConfig, ProcessMonitor
from noust.monitor.watches import Watch, WatchServices


class Notifier:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.sweeps = 0
        self.report = SweepReport()

    def notify(self, event: Any) -> None:
        self.events.append(event)

    def deliver_due(self) -> SweepReport:
        self.sweeps += 1
        return self.report


class Certificates:
    def __init__(self, *certs: CertificateInfo) -> None:
        self.certs = list(certs)

    def list_certificates(self) -> list[CertificateInfo]:
        return self.certs


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield Config()
    finally:
        Config.reset_instance()


def make(tmp_path: Path, config: Config, **extra: Any) -> tuple[ProcessMonitor, Notifier, Clock]:
    notifier = Notifier()
    clock = Clock()
    monitor = ProcessMonitor(
        config=MonitorConfig(),
        event_notifier=notifier,
        cert_manager=extra.pop("certificates", Certificates()),
        cert_state_path=tmp_path / "certs.json",
        clock=clock,
        **extra,
    )
    monitor.global_config.reload = lambda: None  # type: ignore[method-assign]
    return monitor, notifier, clock


def disk(percent: float, mountpoint: str = "/") -> SimpleNamespace:
    total = 20 * 1024**3
    return SimpleNamespace(
        mountpoint=mountpoint,
        percent=percent,
        used_bytes=int(total * percent / 100),
        total_bytes=total,
        free_bytes=int(total * (100 - percent) / 100),
    )


class TestTheDiskThreshold:
    def test_the_default_is_ninety(self, tmp_path: Path, config: Config) -> None:
        monitor, notifier, _ = make(tmp_path, config)

        monitor._notify_full_disks([], [disk(88)])

        assert monitor._disk_threshold() == DISK_ALERT_PERCENT == 90.0

    def test_the_operators_threshold_decides_and_is_the_one_the_message_names(
        self, tmp_path: Path, config: Config
    ) -> None:
        config.set("monitor.disk_threshold", 85)
        monitor, notifier, _ = make(tmp_path, config)

        monitor._notify_full_disks([disk(88)], [disk(88)])

        (event,) = notifier.events
        assert event.kind == "disk_threshold"
        assert "85%" in event.summary

    @pytest.mark.parametrize("value", [0, 101, "wide", True, -5])
    def test_a_threshold_that_is_no_percentage_is_refused(self, config: Config, value: Any) -> None:
        with pytest.raises(ConfigError, match="disk_threshold"):
            config.set("monitor.disk_threshold", value)

    def test_a_hand_edited_nonsense_falls_back_to_the_default(
        self, tmp_path: Path, config: Config
    ) -> None:
        config._config["monitor"]["disk_threshold"] = "wide"
        monitor, _, _ = make(tmp_path, config)

        assert monitor._disk_threshold() == DISK_ALERT_PERCENT

    def test_a_disk_that_stays_over_is_one_message_by_default(
        self, tmp_path: Path, config: Config
    ) -> None:
        monitor, notifier, clock = make(tmp_path, config)

        monitor._notify_full_disks([disk(95)], [disk(95)])
        clock.now += 30 * 3600
        monitor._notify_full_disks([disk(96)], [disk(96)])

        assert len(notifier.events) == 1

    def test_with_renotify_hours_it_reminds(self, tmp_path: Path, config: Config) -> None:
        config.set("monitor.disk_renotify_hours", 24)
        monitor, notifier, clock = make(tmp_path, config)

        monitor._notify_full_disks([disk(95)], [disk(95)])
        clock.now += 23 * 3600
        monitor._notify_full_disks([disk(95)], [disk(95)])
        assert len(notifier.events) == 1

        clock.now += 2 * 3600
        monitor._notify_full_disks([disk(96)], [disk(96)])
        assert len(notifier.events) == 2

    def test_a_disk_that_drops_and_crosses_again_is_announced_at_once(
        self, tmp_path: Path, config: Config
    ) -> None:
        config.set("monitor.disk_renotify_hours", 24)
        monitor, notifier, _ = make(tmp_path, config)

        monitor._notify_full_disks([disk(95)], [disk(95)])
        monitor._notify_full_disks([], [disk(70)])  # recovered: announced
        monitor._notify_full_disks([disk(95)], [disk(95)])

        assert [e.code for e in notifier.events] == [
            "disk.threshold",
            "disk.recovered",
            "disk.threshold",
        ]

    def test_the_disks_are_looked_at_no_more_often_than_asked(
        self, tmp_path: Path, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config.set("monitor.disk_check_seconds", 600)
        monitor, notifier, clock = make(tmp_path, config)
        reading = SimpleNamespace(
            cpu_percent=1.0,
            memory_percent=1.0,
            process_count=1,
            disks=[disk(95)],
        )
        monkeypatch.setattr(monitor, "collect_metrics", lambda: reading)

        monitor._log_metrics()
        reading.disks = [disk(40)]  # it recovered a minute later
        clock.now += 60
        monitor._log_metrics()
        assert [e.code for e in notifier.events] == ["disk.threshold"]

        clock.now += 600
        monitor._log_metrics()
        assert [e.code for e in notifier.events] == ["disk.threshold", "disk.recovered"]

    @pytest.mark.parametrize(
        ("key", "bad"),
        [
            ("monitor.disk_check_seconds", -1),
            ("monitor.disk_renotify_hours", 5000),
            ("monitor.cert_warning_days", 0),
            ("monitor.backup_missing_days", 1000),
            ("monitor.backup_check_hours", 0),
            ("monitor.token_warning_days", -2),
            ("monitor.os_patches_check_hours", 10_000),
        ],
    )
    def test_every_new_setting_has_its_bounds(self, config: Config, key: str, bad: int) -> None:
        with pytest.raises(ConfigError):
            config.set(key, bad)


class TestTheCertificateWindow:
    def cert(self, days: int) -> CertificateInfo:
        expiry = date.today() + timedelta(days=days)
        return CertificateInfo(
            name="example.com", domains=["example.com"], expiry=expiry.isoformat()
        )

    def test_twenty_days_is_outside_the_default_fourteen(
        self, tmp_path: Path, config: Config
    ) -> None:
        monitor, notifier, _ = make(tmp_path, config, certificates=Certificates(self.cert(20)))

        monitor._check_certificates()

        assert notifier.events == []

    def test_the_operator_may_widen_it(self, tmp_path: Path, config: Config) -> None:
        config.set("monitor.cert_warning_days", 30)
        monitor, notifier, _ = make(tmp_path, config, certificates=Certificates(self.cert(20)))

        monitor._check_certificates()

        assert [e.code for e in notifier.events] == ["cert.expiring"]


class Recorder(Watch):
    name = "recorder"
    default_interval_s = 60.0

    def __init__(self) -> None:
        self.services: list[WatchServices] = []

    def check(self, services: WatchServices) -> None:
        self.services.append(services)


class TestTheLoopsNewDuties:
    def test_the_watches_run_with_the_monitors_own_services(
        self, tmp_path: Path, config: Config
    ) -> None:
        from noust.core.notification_state import NotificationState
        from noust.core.store import NoustStore

        NoustStore.reset_instance()
        try:
            store = NoustStore(tmp_path / "noust.db")
            watch = Recorder()
            monitor, notifier, clock = make(
                tmp_path,
                config,
                watches=[watch],
                notification_state=NotificationState(store, clock=lambda: 1.0),
            )

            monitor._run_watches()
            monitor._run_watches()  # the same moment: not due again

            assert len(watch.services) == 1
            services = watch.services[0]
            assert services.runner is monitor.runner
            assert services.clock is monitor._clock
            assert services.publish == monitor._publish
        finally:
            NoustStore.reset_instance()

    def test_a_store_that_cannot_be_read_does_not_stop_the_scan(
        self, tmp_path: Path, config: Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Broken:
            def get(self, *args: Any) -> Any:
                raise OSError("disk I/O error")

        monitor, _, _ = make(
            tmp_path,
            config,
            watches=[Recorder()],
            notification_state=Broken(),  # type: ignore[arg-type]
        )

        monitor._run_watches()

        assert "disk I/O error" in caplog.text

    def test_the_alert_rules_are_judged_every_scan(self, tmp_path: Path, config: Config) -> None:
        judged: list[int] = []
        engine = SimpleNamespace(
            evaluate=lambda: judged.append(1) or SimpleNamespace(fired=1, resolved=0, announced=1)
        )
        monitor, _, _ = make(tmp_path, config, alert_engine=engine)

        monitor._evaluate_alerts()
        monitor._evaluate_alerts()

        assert judged == [1, 1]

    def test_a_failing_evaluation_is_logged_not_fatal(
        self, tmp_path: Path, config: Config, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom() -> Any:
            raise OSError("metrics.db is locked")

        monitor, _, _ = make(tmp_path, config, alert_engine=SimpleNamespace(evaluate=boom))

        monitor._evaluate_alerts()

        assert "metrics.db is locked" in capsys.readouterr().out

    def test_each_scan_sweeps_the_deliveries_that_are_due(
        self, tmp_path: Path, config: Config
    ) -> None:
        monitor, notifier, _ = make(tmp_path, config)
        notifier.report = SweepReport(delivered=2, retried=1)

        monitor._deliver_due()

        assert notifier.sweeps == 1

    def test_a_notifier_that_cannot_sweep_is_left_alone(
        self, tmp_path: Path, config: Config
    ) -> None:
        monitor, notifier, _ = make(tmp_path, config)
        notifier.deliver_due = None  # type: ignore[assignment]

        monitor._deliver_due()  # nothing to call, nothing raised
