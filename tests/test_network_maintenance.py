# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The network's hourly upkeep: DNS records nobody uses, and Cloudflare's address ranges.

What is pinned: the monitor only starts a pass when there is something to do (most servers
have no DNS account and no Cloudflare policy, and must not pay for it every hour); the pass
does both steps and one failing does not stop the other; and the monitor starts the work in
a transient unit of its own, once an hour, because its own sandbox is read-only for the
web server's files.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.dns_store import DnsAccount, DnsStore, ManagedRecord
from noust.core.exceptions import IntegrationError
from noust.core.runner import FakeRunner
from noust.core.site_policy_store import SitePolicyStore
from noust.core.store import App, NoustStore
from noust.deployers import access
from noust.managers import network_maintenance, trusted_proxies
from noust.managers.dns.records import ManagedDns, Release, ReleaseResult
from noust.managers.trusted_proxies import RangeList

APP = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(App(domain=APP, app_path="/srv/shop"))
    DnsStore(instance).save_account(DnsAccount(name="cf", kind="cloudflare"))
    yield instance
    NoustStore.reset_instance()


def fetched(days_ago: int | None) -> RangeList:
    when = None
    if days_ago is not None:
        when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return RangeList(("173.245.48.0/20",), "fetched", when)


def trust_cloudflare(store: NoustStore) -> None:
    SitePolicyStore(store).set_document(APP, '{"real_ip": "cloudflare"}', updated_by=None)


def waiting_record(store: NoustStore, *, unreferenced: bool) -> None:
    DnsStore(store).save_record(
        ManagedRecord(
            uuid="u1",
            provider="cf",
            zone_id="z",
            zone_name="example.com",
            hostname=APP,
            record_type="A",
            content="8.8.4.4",
            unreferenced_since="2026-10-13T10:00:00+00:00" if unreferenced else None,
        )
    )


# -- whether there is anything to do ----------------------------------------------


def test_a_server_with_nothing_to_keep_up_is_never_woken(store: NoustStore) -> None:
    assert network_maintenance.work_due() is False


def test_a_record_waiting_to_be_swept_is_work(store: NoustStore) -> None:
    waiting_record(store, unreferenced=True)

    assert network_maintenance.work_due() is True


def test_a_record_an_application_uses_is_not(store: NoustStore) -> None:
    waiting_record(store, unreferenced=False)

    assert network_maintenance.work_due() is False


def test_stale_ranges_are_work_only_when_a_site_trusts_them(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trusted_proxies, "current_ranges", lambda: fetched(30))
    assert network_maintenance.work_due() is False

    trust_cloudflare(store)

    assert network_maintenance.work_due() is True


def test_fresh_ranges_are_not_work(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
    trust_cloudflare(store)
    monkeypatch.setattr(trusted_proxies, "current_ranges", lambda: fetched(1))

    assert network_maintenance.work_due() is False


def test_ranges_never_fetched_are_stale(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
    trust_cloudflare(store)
    monkeypatch.setattr(trusted_proxies, "current_ranges", lambda: fetched(None))

    assert network_maintenance.work_due() is True


# -- the pass ---------------------------------------------------------------------


def test_the_pass_sweeps_and_refreshes_what_is_due(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_cloudflare(store)
    monkeypatch.setattr(trusted_proxies, "current_ranges", lambda: fetched(30))
    monkeypatch.setattr(
        ManagedDns,
        "sweep",
        lambda self, **k: [ReleaseResult("old.example.com", "A", Release.DELETED, "cf")],
    )
    refreshed = {"changed": True, "count": 22, "source": "fetched", "applied": [APP], "failed": {}}
    monkeypatch.setattr(access, "refresh_trusted_ranges", lambda **k: refreshed)

    result = network_maintenance.maintain()

    assert result.released == {"old.example.com": "deleted"}
    assert result.ranges == refreshed and result.errors == ()


def test_ranges_nothing_uses_are_not_fetched(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trusted_proxies, "current_ranges", lambda: fetched(30))
    monkeypatch.setattr(ManagedDns, "sweep", lambda self, **k: [])

    def forbidden(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("nothing trusts the ranges, so nothing is fetched")

    monkeypatch.setattr(access, "refresh_trusted_ranges", forbidden)

    assert network_maintenance.maintain().ranges is None


def test_a_step_that_fails_does_not_stop_the_other(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_cloudflare(store)
    monkeypatch.setattr(trusted_proxies, "current_ranges", lambda: fetched(30))

    def refuse(self: ManagedDns, **kwargs: Any) -> None:
        raise IntegrationError("Cloudflare is not answering")

    monkeypatch.setattr(ManagedDns, "sweep", refuse)
    refreshed = {"changed": False, "count": 22, "source": "built-in", "applied": [], "failed": {}}
    monkeypatch.setattr(access, "refresh_trusted_ranges", lambda **k: refreshed)

    result = network_maintenance.maintain()

    assert result.errors == ("The DNS sweep failed: Cloudflare is not answering",)
    assert result.ranges == refreshed


def test_ranges_that_could_not_be_fetched_are_reported(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_cloudflare(store)
    monkeypatch.setattr(trusted_proxies, "current_ranges", lambda: fetched(30))
    monkeypatch.setattr(ManagedDns, "sweep", lambda self, **k: [])

    def refuse(**kwargs: Any) -> None:
        raise IntegrationError("No route to host")

    monkeypatch.setattr(access, "refresh_trusted_ranges", refuse)

    result = network_maintenance.maintain()

    assert result.errors == ("Cloudflare's ranges were not refreshed: No route to host",)


def test_the_unit_reports_what_it_did_and_exits_one_when_a_step_failed(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        network_maintenance,
        "maintain",
        lambda: network_maintenance.Maintenance(
            released={"old.example.com": "deleted"}, errors=("The DNS sweep failed: boom",)
        ),
    )

    assert network_maintenance.main() == 1

    out = capsys.readouterr()
    assert out.out == "DNS old.example.com: deleted\n"
    assert out.err == "The DNS sweep failed: boom\n"


def test_the_work_is_started_in_a_transient_unit_of_its_own() -> None:
    argv = network_maintenance.refresh_command("/usr/bin/python3")

    assert argv[0] == "systemd-run"
    assert "--unit=noust-network-maintenance" in argv
    assert "--collect" in argv and "--no-block" in argv
    assert argv[-3:] == ["/usr/bin/python3", "-m", "noust.managers.network_maintenance"]


# -- the monitor ------------------------------------------------------------------


class TestMonitor:
    @pytest.fixture
    def monitor(self, store: NoustStore) -> Any:
        from noust.monitor.process_monitor import MonitorConfig, ProcessMonitor

        fake = FakeRunner()
        instance = ProcessMonitor(config=MonitorConfig(), runner=fake)
        instance.runner = fake
        return instance

    @staticmethod
    def started(monitor: Any) -> list[tuple[str, ...]]:
        return [call for call in monitor.runner.calls if call[0] == "systemd-run"]

    def test_with_nothing_to_do_nothing_is_started(self, monitor: Any) -> None:
        monitor._run_network_maintenance()

        assert self.started(monitor) == []

    def test_with_work_the_monitor_has_systemd_run_it(
        self, monitor: Any, store: NoustStore
    ) -> None:
        waiting_record(store, unreferenced=True)

        monitor._run_network_maintenance()

        (call,) = self.started(monitor)
        assert "--unit=noust-network-maintenance" in call

    def test_it_asks_once_an_hour(self, monitor: Any, store: NoustStore) -> None:
        waiting_record(store, unreferenced=True)

        monitor._run_network_maintenance()
        monitor._run_network_maintenance()

        assert len(self.started(monitor)) == 1
