# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the Overview: everything the dashboard needs, in one call.

What is defended:

- **The six key figures are counted where the data is**, not in the browser:
  applications by state, today's deployments (counted by the store, so a busy
  day is not undercounted), certificates expiring, backups against the
  applications that should have them, disk headroom with an honest forecast,
  operating system updates from the server area's manager.
- **The attention list is ranked and explains itself**: worst first, one item per
  subject however many reasons it has, each reason machine-readable with the
  system's own words verbatim and what can be done about it.
- **A section that cannot be read says so** instead of vanishing: a dashboard
  with a hole and no reason reads as "all fine".
- **The answer is the same shape a fleet's aggregator will read** from each node.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import NoustError
from noust.core.store import App, BackupScheduleRecord, JobRecord, NoustStore
from noust.managers import overview as overview_module
from noust.managers.cert_manager import CertificateInfo
from noust.managers.overview import (
    CERT_WARNING_DAYS,
    collect_overview,
    forecast_full_days,
    get_overview,
    reset_cache,
)
from noust.monitor import sampler
from noust.monitor.timeseries import MetricsStore
from noust.web.api import overview as overview_api
from noust.web.api.auth import get_current_session
from noust.web.api.overview import router as overview_router

NOW = datetime(2026, 9, 29, 15, 30, 0)
NOW_EPOCH = int(NOW.timestamp())

DAY = 86_400


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """Give the overview a store of its own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    reset_cache()
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()
        reset_cache()


@pytest.fixture(autouse=True)
def machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine whose disk is a third full."""
    monkeypatch.setattr(sampler, "resolve_apps_root", lambda root=None: "/apps")
    monkeypatch.setattr(
        sampler,
        "read_disk",
        lambda root: sampler.Usage(used=100 * 1024**3, total=300 * 1024**3, percent=33.3),
    )


def deploy(store: NoustStore, domain: str, **fields: Any) -> App:
    """Record an application the way a deploy would."""
    values: dict[str, Any] = {
        "domain": domain,
        "app_type": "nextjs",
        "source": "https://github.com/you/app",
        "port": 3000,
        "app_path": f"/var/www/apps/{domain}",
        "status": "running",
    }
    values.update(fields)
    return store.create_app(App(**values))


def unit(
    name: str, domain: str | None, active: str = "active", sub: str = "running"
) -> dict[str, str]:
    """One entry of the unit list."""
    return {"name": name, "load": "loaded", "active": active, "sub": sub, "app": domain or ""}


def finish(
    store: NoustStore, domain: str, status: str, *, when: datetime, error: str | None = None
) -> int:
    """Record a finished deployment at a given moment."""
    deployment_id = store.record_deployment_start(domain, "cli")
    with store._transaction() as cursor:
        cursor.execute(
            "UPDATE deployments SET started_at = ?, finished_at = ?, status = ?, error = ? "
            "WHERE id = ?",
            (when.isoformat(), when.isoformat(), status, error, deployment_id),
        )
    return deployment_id


def cert(name: str, days: int | None) -> CertificateInfo:
    """A certificate that expires in ``days`` days."""
    expiry = (date.today() + timedelta(days=days)).isoformat() if days is not None else None
    return CertificateInfo(name=name, domains=[name], expiry=expiry)


def collect(store: NoustStore, **collaborators: Any) -> dict[str, Any]:
    """Collect an overview with quiet defaults for whatever a test does not care about."""
    defaults: dict[str, Any] = {
        "services": lambda: [],
        "certificates": lambda: [],
        "backups": lambda: [],
        "observations": lambda: [],
        "metrics": None,
        "updates": None,
        "now": NOW,
    }
    defaults.update(collaborators)
    return collect_overview(store=store, **defaults)


# ---------------------------------------------------------------- the figures


def test_the_answer_says_which_shape_and_which_server(store: NoustStore) -> None:
    """A schema number, a timestamp and the server's name, version and role."""
    body = collect(store)

    assert body["schema"] == overview_module.SCHEMA
    assert body["server"]["role"] in ("server", "hub")
    assert body["server"]["name"] and body["server"]["version"]
    assert body["generated_at"].endswith(("+00:00", "Z"))


def test_applications_are_counted_by_state(store: NoustStore) -> None:
    """Running, failed and stopped from the unit list; a static site has no unit."""
    deploy(store, "ok.example.com")
    deploy(store, "broken.example.com")
    deploy(store, "off.example.com")
    deploy(store, "site.example.com", app_type="static", is_static=True)
    units = [
        unit("ok-example-com", "ok.example.com"),
        unit("broken-example-com", "broken.example.com", "failed", "failed"),
        unit("off-example-com", "off.example.com", "inactive", "dead"),
    ]

    body = collect(store, services=lambda: units)

    assert body["apps"] == {"running": 1, "failed": 1, "stopped": 1, "static": 1}


def test_todays_deployments_are_counted_by_the_store(store: NoustStore) -> None:
    """Since local midnight, by outcome; yesterday's do not count."""
    deploy(store, "a.example.com")
    finish(store, "a.example.com", "success", when=NOW - timedelta(hours=1))
    finish(store, "a.example.com", "success", when=NOW - timedelta(hours=2))
    finish(store, "a.example.com", "failed", when=NOW - timedelta(hours=3), error="npm ERR!")
    finish(store, "a.example.com", "rolled_back", when=NOW - timedelta(hours=4))
    finish(store, "a.example.com", "success", when=NOW - timedelta(days=1, hours=2))

    figure = collect(store)["deploys"]

    assert figure["total"] == 4
    assert (figure["succeeded"], figure["failed"], figure["rolled_back"]) == (2, 1, 1)
    assert figure["last_status"] == "success"
    assert figure["last_domain"] == "a.example.com"
    assert figure["since"].startswith("2026-09-29T00:00:00")


def test_a_busy_day_is_not_undercounted(store: NoustStore) -> None:
    """The browser counted the last 200 rows; the store counts them all."""
    deploy(store, "a.example.com")
    for minute in range(250):
        finish(store, "a.example.com", "success", when=NOW - timedelta(minutes=minute % 300))

    assert collect(store)["deploys"]["total"] == 250


def test_certificates_are_counted_by_how_long_they_have_left(store: NoustStore) -> None:
    """Expiring is inside the warning window, expired is behind us."""
    certs = [
        cert("a.com", 400),
        cert("b.com", 12),
        cert("c.com", 3),
        cert("d.com", -2),
        cert("e.com", None),
    ]

    figure = collect(store, certificates=lambda: certs)["certificates"]

    assert figure["total"] == 5
    assert figure["expiring"] == 2
    assert figure["expired"] == 1
    assert figure["next_days"] == -2
    assert figure["warning_days"] == CERT_WARNING_DAYS == 21


def test_backups_are_measured_against_the_applications_that_should_have_them(
    store: NoustStore,
) -> None:
    """Fifteen of seventeen is a sentence; the two are the scheduled ones with no backup."""
    deploy(store, "a.example.com")
    deploy(store, "b.example.com")
    deploy(store, "c.example.com")
    deploy(store, "pr-7.example.com", preview_parent="a.example.com")
    for domain in ("a.example.com", "b.example.com"):
        store.save_backup_schedule(BackupScheduleRecord(app_domain=domain, schedule="daily"))
    fresh = (NOW - timedelta(hours=5)).isoformat()
    old = (NOW - timedelta(hours=30)).isoformat()
    backups = [
        SimpleNamespace(domain="a.example.com", created_at=fresh),
        SimpleNamespace(domain="c.example.com", created_at=old),
        SimpleNamespace(domain="pr-7.example.com", created_at=fresh),
    ]
    store.save_job(
        JobRecord(
            id="j1",
            type="backup",
            name="backup b",
            status="failed",
            domain="b.example.com",
            created_at=(NOW - timedelta(hours=2)).isoformat(),
            finished_at=(NOW - timedelta(hours=2)).isoformat(),
        )
    )

    figure = collect(store, backups=lambda: backups)["backups"]

    assert figure["apps"] == 3, "a preview is not an application to protect"
    assert figure["scheduled"] == 2
    assert figure["with_backup_24h"] == 1
    assert figure["unprotected_scheduled"] == 1
    assert figure["failed_24h"] == 1
    assert figure["last_at"].startswith("2026-09-29T10:30:00")


def test_the_disk_is_the_one_the_applications_live_on(store: NoustStore) -> None:
    """The same figure the machine strip shows: used, total, percent and free."""
    figure = collect(store)["disk"]

    assert figure["used"] == 100 * 1024**3
    assert figure["total"] == 300 * 1024**3
    assert figure["free_percent"] == pytest.approx(66.7)
    assert figure["forecast_full_days"] is None
    assert figure["forecast_reason"]["code"] == "no_history"


# ------------------------------------------------------------ the disk forecast


def hourly_growth(
    metrics: MetricsStore, *, days: int, start: float, per_day: float, end: int
) -> None:
    """Write hourly disk readings growing linearly to ``end``."""
    for hour in range(days * 24):
        ts = end - (days * 24 - hour) * 3_600
        used = start + per_day * (hour / 24)
        metrics._get_connection().execute(
            "INSERT INTO consolidated (metric, resolution, ts, value, max_value) "
            "VALUES ('disk.used_bytes', 3600, ?, ?, ?)",
            (ts, used, used),
        )
    metrics._get_connection().commit()


def test_a_growing_disk_gets_an_estimate_of_when_it_fills(tmp_path: Path) -> None:
    """Ten gigabytes a day with two hundred left: twenty days."""
    metrics = MetricsStore(tmp_path / "m.db", clock=lambda: NOW_EPOCH)
    gib = 1024**3
    hourly_growth(metrics, days=7, start=30 * gib, per_day=10 * gib, end=NOW_EPOCH)
    used = 100 * gib

    days, reason = forecast_full_days(metrics, used, 300 * gib, NOW_EPOCH)

    assert reason is None
    assert days is not None and 19 <= days <= 20


def test_a_disk_that_is_not_growing_says_so(tmp_path: Path) -> None:
    """A cleanup that freed space inverts the slope; that is not 'full in never'."""
    metrics = MetricsStore(tmp_path / "m.db", clock=lambda: NOW_EPOCH)
    gib = 1024**3
    hourly_growth(metrics, days=7, start=100 * gib, per_day=-1 * gib, end=NOW_EPOCH)

    days, reason = forecast_full_days(metrics, 90 * gib, 300 * gib, NOW_EPOCH)

    assert days is None
    assert reason is not None and reason["code"] == "not_growing"


def test_a_disk_that_would_take_years_is_not_growing_for_practical_purposes(tmp_path: Path) -> None:
    """Full in 40 years is not a forecast anyone acts on."""
    metrics = MetricsStore(tmp_path / "m.db", clock=lambda: NOW_EPOCH)
    gib = 1024**3
    hourly_growth(metrics, days=7, start=100 * gib, per_day=0.05 * gib, end=NOW_EPOCH)

    days, reason = forecast_full_days(metrics, 100 * gib, 300 * gib, NOW_EPOCH)

    assert days is None and reason is not None and reason["code"] == "not_growing"


def test_two_days_of_history_is_not_enough_to_forecast(tmp_path: Path) -> None:
    """The interface says 'estimated'; the backend does not guess from two days."""
    metrics = MetricsStore(tmp_path / "m.db", clock=lambda: NOW_EPOCH)
    hourly_growth(metrics, days=2, start=1, per_day=1_000, end=NOW_EPOCH)

    days, reason = forecast_full_days(metrics, 10, 1_000_000, NOW_EPOCH)

    assert days is None and reason is not None and reason["code"] == "insufficient_history"


# ----------------------------------------------------------------- the updates


def test_without_the_server_area_updates_say_they_are_not_available(store: NoustStore) -> None:
    """The integration point is a callable; with none, the reason says why."""
    figure = collect(store, updates=None)["updates"]

    assert figure["available"] is None
    assert figure["reason"]["code"] == "not_available"


def test_updates_come_from_the_server_areas_summary(store: NoustStore) -> None:
    """Pending, security, reboot and when it was read: that area's own words."""
    summary = {
        "updates": {
            "supported": True,
            "pending": 14,
            "security": 3,
            "checked_at": "2026-09-29T14:00:00+00:00",
            "error": None,
        },
        "reboot": {"required": True},
    }

    figure = collect(store, updates=lambda: summary)["updates"]

    assert figure["available"] == 14
    assert figure["security"] == 3
    assert figure["reboot_required"] is True
    assert figure["checked_at"] == "2026-09-29T14:00:00+00:00"
    assert "reason" not in figure


def test_updates_not_known_yet_are_null_not_zero(store: NoustStore) -> None:
    """The slow probe runs in the background: 'unknown' must not read as 'none'."""
    summary = {"updates": {"supported": True, "pending": None, "security": None}, "reboot": {}}

    figure = collect(store, updates=lambda: summary)["updates"]

    assert figure["available"] is None
    assert figure["reboot_required"] is None


def test_an_unsupported_platform_says_why(store: NoustStore) -> None:
    """A transactional server or a container: the manager's own explanation."""
    summary = {
        "updates": {"supported": False, "reason": "This is a container.", "pending": None},
        "reboot": {},
    }

    figure = collect(store, updates=lambda: summary)["updates"]

    assert figure["supported"] is False
    assert figure["reason"] == {"code": "unsupported", "message": "This is a container."}


def test_a_failing_updates_probe_is_reported_not_raised(store: NoustStore) -> None:
    """The rest of the overview still answers."""

    def fail() -> dict[str, Any]:
        raise NoustError("apt is locked")

    body = collect(store, updates=fail)

    assert "apt is locked" in body["updates"]["error"]
    assert body["apps"]["running"] == 0


# ------------------------------------------------------------- what needs care


def test_the_attention_list_is_ranked_worst_first(store: NoustStore) -> None:
    """Failures before warnings, then by name."""
    for domain in ("zeta.example.com", "alpha.example.com", "beta.example.com"):
        deploy(store, domain)
    units = [
        unit("zeta-example-com", "zeta.example.com", "failed", "failed"),
        unit("alpha-example-com", "alpha.example.com", "activating", "auto-restart"),
        unit("beta-example-com", "beta.example.com"),
    ]

    items = collect(store, services=lambda: units)["attention"]

    assert [(i["title"], i["severity"]) for i in items] == [
        ("zeta.example.com", "fail"),
        ("alpha.example.com", "warn"),
    ]
    assert items[0]["reasons"][0]["code"] == "service_failed"
    assert items[1]["reasons"][0]["code"] == "service_restarting"
    assert items[0]["reasons"][0]["actions"] == ["view_log", "diagnose"]


def test_one_subject_is_one_item_with_every_reason(store: NoustStore) -> None:
    """A failed service, a failed deploy and an expiring certificate are one row."""
    deploy(store, "shop.example.com")
    finish(
        store,
        "shop.example.com",
        "failed",
        when=NOW - timedelta(hours=1),
        error="npm ERR! code ELIFECYCLE\nnpm ERR! second line",
    )
    units = [unit("shop-example-com", "shop.example.com", "failed", "failed")]

    items = collect(
        store, services=lambda: units, certificates=lambda: [cert("shop.example.com", 5)]
    )["attention"]

    assert len(items) == 1
    item = items[0]
    assert item["id"] == "app:shop.example.com"
    assert item["severity"] == "fail"
    codes = sorted(r["code"] for r in item["reasons"])
    assert codes == ["certificate_expires_in", "deploy_failed", "service_failed"]
    deploy_reason = next(r for r in item["reasons"] if r["code"] == "deploy_failed")
    assert deploy_reason["detail"] == "npm ERR! code ELIFECYCLE", "the first line, verbatim"
    assert deploy_reason["deployment_id"] is not None
    cert_reason = next(r for r in item["reasons"] if r["kind"] == "certificate")
    assert cert_reason["params"]["days"] == 5


def test_a_rolled_back_deploy_is_a_warning_not_a_failure(store: NoustStore) -> None:
    """The app is serving the previous release."""
    deploy(store, "shop.example.com")
    finish(store, "shop.example.com", "rolled_back", when=NOW - timedelta(hours=1))

    item = collect(store, services=lambda: [unit("shop-example-com", "shop.example.com")])[
        "attention"
    ][0]

    assert item["severity"] == "warn"
    assert item["reasons"][0]["code"] == "deploy_rolled_back"


def test_only_the_newest_deployment_of_an_app_counts(store: NoustStore) -> None:
    """A failure that a later success fixed is history, not a problem."""
    deploy(store, "shop.example.com")
    finish(store, "shop.example.com", "failed", when=NOW - timedelta(hours=3))
    finish(store, "shop.example.com", "success", when=NOW - timedelta(hours=1))

    items = collect(store, services=lambda: [unit("shop-example-com", "shop.example.com")])[
        "attention"
    ]

    assert items == []


def test_a_deployment_of_a_deleted_app_is_history(store: NoustStore) -> None:
    """Nothing to fix on something that no longer exists."""
    finish(store, "gone.example.com", "failed", when=NOW - timedelta(hours=1))

    assert collect(store)["attention"] == []


def test_a_certificate_is_shown_on_its_app_or_on_its_own(store: NoustStore) -> None:
    """A certificate of an application is a reason of that application."""
    deploy(store, "shop.example.com")
    certs = [cert("shop.example.com", -1), cert("orphan.example.com", 0), cert("fine.com", 90)]

    items = collect(
        store,
        services=lambda: [unit("shop-example-com", "shop.example.com")],
        certificates=lambda: certs,
    )["attention"]

    by_id = {item["id"]: item for item in items}
    assert by_id["app:shop.example.com"]["reasons"][0]["code"] == "certificate_expired"
    assert by_id["app:shop.example.com"]["severity"] == "fail"
    assert (
        by_id["certificate:orphan.example.com"]["reasons"][0]["code"] == "certificate_expires_today"
    )
    assert "certificate:fine.com" not in by_id
    assert by_id["app:shop.example.com"]["reasons"][0]["actions"] == ["renew_certificate"]


def test_a_failed_unit_that_is_not_an_applications_is_listed_by_name(store: NoustStore) -> None:
    """Noust's own units (the monitor, a cron timer) that failed."""
    deploy(store, "shop.example.com")
    units = [
        unit("shop-example-com", "shop.example.com"),
        unit("noust-monitor", None, "failed", "failed"),
        unit("noust-web", None),
    ]

    items = collect(store, services=lambda: units)["attention"]

    assert [i["id"] for i in items] == ["unit:noust-monitor"]
    assert items[0]["reasons"][0]["actions"] == ["open_service"]


def test_an_open_observation_is_a_warning_and_a_dismissed_one_is_not(store: NoustStore) -> None:
    """The monitor observes and never acts: what it noted is for a person."""
    rows = [
        {
            "id": 7,
            "process_name": "xmrig",
            "pid": 4242,
            "severity": "warning",
            "signal": "name_pattern",
            "detail": "Executable name starts with 'xmrig'.",
            "observed_at": "2026-09-29T15:00:00",
            "acknowledged": 0,
        },
        {"id": 8, "process_name": "node", "pid": 1, "acknowledged": 1},
    ]

    items = collect(store, observations=lambda: rows)["attention"]

    assert len(items) == 1
    assert items[0]["severity"] == "warn"
    assert items[0]["subject"] == {"kind": "monitor", "process": "xmrig", "pid": 4242}
    assert items[0]["reasons"][0]["params"] == {"severity": "warning", "signal": "name_pattern"}
    assert "cmdline" not in str(items[0]) and "command" not in items[0]["reasons"][0]


def test_the_list_is_cut_but_says_how_many_there_were(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hundred rows is enough to look at; the total is still true."""
    monkeypatch.setattr(overview_module, "ATTENTION_LIMIT", 3)
    certs = [cert(f"c{i}.example.com", -1) for i in range(5)]

    body = collect(store, certificates=lambda: certs)

    assert len(body["attention"]) == 3
    assert body["attention_total"] == 5


# ------------------------------------------------------------------ the rest


def test_recent_activity_is_the_jobs_newest_first(store: NoustStore) -> None:
    """A timeline: what, on which app, how it ended, who asked, when."""
    for index, (kind, status, hours) in enumerate(
        [("deploy", "completed", 5), ("backup", "failed", 1), ("cert_renew", "completed", 3)]
    ):
        moment = (NOW - timedelta(hours=hours)).isoformat()
        store.save_job(
            JobRecord(
                id=f"j{index}",
                type=kind,
                name=f"{kind} shop",
                status=status,
                domain="shop.example.com",
                actor="ana",
                created_at=moment,
                finished_at=moment,
            )
        )

    activity = collect(store)["activity"]

    assert [entry["type"] for entry in activity] == ["backup", "cert_renew", "deploy"]
    assert activity[0] == {
        "id": "job:j1",
        "type": "backup",
        "title": "backup shop",
        "status": "failed",
        "domain": "shop.example.com",
        "actor": "ana",
        "at": activity[0]["at"],
    }
    assert activity[0]["at"].startswith("2026-09-29T14:30:00")


def test_a_section_that_fails_says_so_and_the_rest_answers(store: NoustStore) -> None:
    """The tool's own words, in the section, not a 500 for the whole page."""

    def certbot_down() -> list[Any]:
        raise NoustError("certbot exited 1: another instance is running")

    body = collect(store, certificates=certbot_down)

    assert body["certificates"] == {"error": "certbot exited 1: another instance is running"}
    assert body["apps"]["running"] == 0
    assert body["deploys"]["total"] == 0
    assert body["attention"] == []


def test_a_unit_list_that_cannot_be_read_does_not_blank_the_page(store: NoustStore) -> None:
    """systemd unreachable: every tally reads zero, and the page still answers."""

    def down() -> list[dict[str, str]]:
        raise NoustError("systemctl is unavailable")

    body = collect(store, services=down)

    assert body["apps"] == {"running": 0, "failed": 0, "stopped": 0, "static": 0}


def test_history_says_whether_metrics_are_recorded(store: NoustStore) -> None:
    """So a fleet view can say 'no history' next to a thumbnail with no line."""
    status = SimpleNamespace(
        recording=False,
        host=None,
        reason=SimpleNamespace(
            to_dict=lambda: {"code": "monitor_disabled", "fix": "noust monitor enable"}
        ),
    )

    body = collect(store, recording=lambda: status)

    assert body["history"] == {
        "recording": False,
        "host": None,
        "reason": {"code": "monitor_disabled", "fix": "noust monitor enable"},
    }


def test_the_spark_is_the_last_hour_of_cpu_and_memory(store: NoustStore, tmp_path: Path) -> None:
    """Sixty one-minute means each, for a thumbnail per server."""
    metrics = MetricsStore(tmp_path / "m.db", clock=lambda: NOW_EPOCH)
    for offset in range(0, 1_800, 5):
        metrics.record_many(
            [("cpu.percent", 10.0), ("mem.used_bytes", 2048.0)], ts=NOW_EPOCH - offset
        )

    body = collect(store, metrics=metrics, spark=True)

    spark = body["spark"]
    assert spark["step"] == 60
    assert len(spark["cpu"]) == 61
    assert spark["cpu"][0] is None and spark["cpu"][-2] == 10.0
    assert spark["mem"][-2] == 2048.0
    assert spark["to"] - spark["from"] == 3_600


def test_without_a_spark_request_there_is_no_spark(store: NoustStore) -> None:
    """It is optional: the cheap call stays cheap."""
    assert "spark" not in collect(store)


# ----------------------------------------------------------------------- cache


def test_a_recent_answer_is_reused_and_a_stale_one_is_not(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The console polls; the certificate and backup reads are not free."""
    calls: list[bool] = []

    def counting(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["spark"])
        return {"n": len(calls)}

    monkeypatch.setattr(overview_module, "collect_overview", counting)

    first = get_overview()
    second = get_overview()
    with_spark = get_overview(spark=True)
    again = get_overview(spark=True)
    forced = get_overview(max_age=0)

    assert first == second == {"n": 1}
    assert with_spark == {"n": 2}, "an answer without spark cannot serve one that wants it"
    assert again == {"n": 2}
    assert forced == {"n": 3}
    assert calls == [False, True, False]


# ------------------------------------------------------------------------- API


@pytest.fixture
def client(store: NoustStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """A client for the overview router, with the collaborators faked."""
    metrics = MetricsStore(tmp_path / "metrics.db", clock=lambda: NOW_EPOCH)
    monkeypatch.setattr(overview_api.metrics_collector, "get_metrics_store", lambda: metrics)
    monkeypatch.setattr(
        overview_api.metrics_collector,
        "history_status",
        lambda: SimpleNamespace(recording=True, host="daemon", reason=None),
    )
    real = overview_module.collect_overview

    def collect_with_fakes(**kwargs: Any) -> dict[str, Any]:
        kwargs.update(
            services=lambda: [],
            certificates=lambda: [],
            backups=lambda: [],
            observations=lambda: [],
            now=NOW,
        )
        return real(store=store, **kwargs)

    monkeypatch.setattr(overview_module, "collect_overview", collect_with_fakes)
    monkeypatch.setattr(
        overview_api,
        "server_summary",
        lambda: {
            "updates": {"supported": True, "pending": 14, "security": 3, "checked_at": None},
            "reboot": {"required": True},
        },
    )
    app = FastAPI()
    app.include_router(overview_router, prefix="/api/overview")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test"}
    return TestClient(app)


def test_the_endpoint_answers_the_whole_overview(client: TestClient, store: NoustStore) -> None:
    """One call, typed: the figures, the attention list, the activity, the history."""
    deploy(store, "shop.example.com")

    response = client.get("/api/overview")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "schema",
        "generated_at",
        "server",
        "apps",
        "deploys",
        "certificates",
        "backups",
        "disk",
        "updates",
        "attention",
        "attention_total",
        "activity",
        "history",
        "spark",
    }
    assert body["schema"] == 1
    assert body["history"] == {"recording": True, "host": "daemon", "reason": None, "error": None}
    assert body["spark"] is None
    assert body["updates"]["available"] == 14
    assert body["updates"]["security"] == 3
    assert body["updates"]["reboot_required"] is True


def test_the_spark_travels_with_the_wire_name_from(client: TestClient) -> None:
    """from is a Python keyword: the model aliases it, the wire says what the API promised."""
    body = client.get("/api/overview", params={"spark": "true"}).json()

    assert set(body["spark"]) == {"cpu", "mem", "step", "from", "to", "error"}


def test_the_endpoint_demands_a_session(tmp_path: Path) -> None:
    """The overview names every application on the machine."""
    from noust.web.auth import SecurityConfig
    from noust.web.server import create_app

    app = create_app(SecurityConfig(state_dir=tmp_path / "state"))
    anonymous = TestClient(app, client=("testclient", 50000))

    assert anonymous.get("/api/overview").status_code == 401


# ---------------------------------------------------------------------------
# The cached answer is forgotten when something it shows changes
# ---------------------------------------------------------------------------


@pytest.fixture
def collections(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """Count how many times the Overview is collected, and start with nothing cached."""
    calls: list[int] = []

    def collect(**_: Any) -> dict[str, Any]:
        calls.append(1)
        return {"schema": 1, "collected": len(calls)}

    monkeypatch.setattr(overview_module, "collect_overview", collect)
    reset_cache()
    try:
        yield calls
    finally:
        reset_cache()


def test_a_successful_write_makes_the_next_read_collect_again(collections: list[int]) -> None:
    """Stopping an app, then opening the Overview, must not show it running for 15 seconds."""
    get_overview()
    get_overview()
    assert len(collections) == 1

    overview_api.invalidate_after_mutation("POST", "/api/apps/shop.example.com/stop", 200)
    get_overview()

    assert len(collections) == 2


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/api/apps", 200),
        ("POST", "/api/apps/shop.example.com/stop", 409),
        ("POST", "/hooks/deploy/shop.example.com", 200),
    ],
)
def test_reads_refusals_and_non_api_calls_keep_the_cached_answer(
    collections: list[int], method: str, path: str, status: int
) -> None:
    """Only a write that went through changes what the Overview shows."""
    get_overview()
    overview_api.invalidate_after_mutation(method, path, status)
    get_overview()

    assert len(collections) == 1


def test_a_job_that_ends_makes_the_next_read_collect_again(collections: list[int]) -> None:
    """A deploy or a backup finishes after the 202 that queued it: its end is the change."""
    from noust.web.jobs import Job, JobStatus, JobType

    job = Job(id="j1", type=JobType.DEPLOY, name="Deploy", description="", status=JobStatus.RUNNING)
    get_overview()
    overview_api.invalidate_after_job(job)
    get_overview()
    assert len(collections) == 1

    job.status = JobStatus.COMPLETED
    overview_api.invalidate_after_job(job)
    get_overview()
    assert len(collections) == 2


def test_the_console_forgets_the_overview_after_every_write_and_every_job() -> None:
    """Wired where every API call and every job transition already pass, not per endpoint."""
    import inspect

    from noust.web import server

    source = inspect.getsource(server)
    assert "invalidate_after_mutation(" in source
    assert "subscribe_all(invalidate_after_job)" in source
    assert "unsubscribe_all(invalidate_after_job)" in source
