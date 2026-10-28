# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_app_export.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.appdir.layout import env_file_for
from noust.core.exceptions import ServiceError
from noust.core.store import (
    App,
    BackupDestinationRecord,
    BackupScheduleRecord,
    Database,
    NoustStore,
    PreviewSettings,
    get_store,
)
from noust.deployers import app_export
from noust.deployers.app_export import FORMAT, CreateSpec
from noust.managers import previews
from noust.managers.cron_manager import CronJob

DOMAIN = "shop.example.com"
NEW = "store.example.org"
#: Token-shaped, built in pieces so no scanner mistakes the test for a leak.
TOKEN = "ghp_" + "abcdefghijklmnop" + "qrstuvwxyz0123456789"
STRIPE = "sk_live_" + "51Habcdefghijklmn" + "opqrstuvwxyz"


class FakeCron:
    """A cron manager that keeps jobs in memory, shaped like list_jobs answers."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def list_jobs(self) -> list[dict[str, Any]]:
        return sorted(self.jobs.values(), key=lambda job: job["name"])

    def get_job(self, name: str) -> dict[str, Any] | None:
        return self.jobs.get(name)

    def create_job(self, job: CronJob) -> CronJob:
        app = get_store().get_app(job.app_domain or "")
        directory = job.working_directory or (f"{app.app_path}/current" if app else "")
        self.jobs[job.name] = {
            "name": job.name,
            "command": job.command,
            "on_calendar": job.schedule,
            "enabled": True,
            "user": job.user or "www-data",
            "working_directory": directory,
            "app_domain": job.app_domain,
        }
        return job

    def disable_job(self, name: str) -> str:
        if name not in self.jobs:
            raise ServiceError(f"No job {name}")
        self.jobs[name]["enabled"] = False
        return f"wasm-cron-{name}.timer"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def no_sweep_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Previews install a timer when turned on; keep it off the machine."""
    monkeypatch.setattr(previews, "sync_sweep_timer", lambda **_: True)


def write_env(app: App, values: dict[str, str]) -> None:
    path = env_file_for(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))


@pytest.fixture
def cron() -> FakeCron:
    return FakeCron()


@pytest.fixture
def shop(store: NoustStore, tmp_path: Path, cron: FakeCron) -> App:
    """An application with a bit of everything."""
    app = store.create_app(
        App(
            domain=DOMAIN,
            app_type="nextjs",
            source=f"https://deploy:{TOKEN}@github.com/acme/shop.git",
            branch="main",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
            layout="releases",
            keep_releases=7,
            persistent_paths=["uploads", "storage"],
            memory_max_mb=512,
            tasks_max=256,
            env_secret_marks={"APP_NAME": False, "INTERNAL_ID": True},
            github_installation_id=42,
        )
    )
    store.set_app_health(DOMAIN, path="/healthz", expect="200-399", timeout=60)
    store.set_zero_downtime(DOMAIN, True, drain_seconds=20)
    store.set_webhook_secret(DOMAIN, "whsec_" + "never-exported")
    for name, kind in (
        (f"www.{DOMAIN}", "redirect"),
        (f"api.{DOMAIN}", "alias"),
        ("shop.example.net", "redirect"),
    ):
        store.add_domain(DOMAIN, name, kind)
    store.save_backup_destination(
        BackupDestinationRecord(name="offsite", backend="s3", settings={"bucket": "b"})
    )
    store.save_backup_schedule(
        BackupScheduleRecord(
            app_domain=DOMAIN,
            schedule="daily",
            include_databases=True,
            retention_count=7,
            retention_days=30,
            destinations=[
                {"name": "offsite", "retention_count": 14, "retention_days": None},
                {"name": "gone", "retention_count": 3, "retention_days": 10},
            ],
        )
    )
    store.save_preview_settings(
        PreviewSettings(
            app_domain=DOMAIN,
            base_domain="previews.example.com",
            max_previews=5,
            ttl_hours=48,
            allow_bots=True,
            exclude_env=["STRIPE_KEY"],
        )
    )
    store.create_database(Database(app_id=app.id, name="shop", engine="postgresql"))
    row = store.get_app(DOMAIN)
    assert row is not None
    write_env(
        row,
        {
            "APP_NAME": "Shop",
            "INTERNAL_ID": "1234",
            "STRIPE_KEY": STRIPE,
            "PUBLIC_URL": "https://cdn.example.net",
            "PORT": "3000",
        },
    )
    cron.jobs["shop-example-com-nightly"] = {
        "name": "shop-example-com-nightly",
        "command": "node scripts/nightly.js",
        "on_calendar": "*-*-* 03:00:00",
        "enabled": False,
        "user": "www-data",
        "working_directory": f"{row.app_path}/current",
        "app_domain": DOMAIN,
    }
    cron.jobs["someone-else"] = {"name": "someone-else", "app_domain": "other.example.com"}
    return row


def minimal(**app: Any) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": 1,
        "app": {"domain": DOMAIN, "app_type": "nodejs", "source": "https://x.test/r.git", **app},
    }


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, store: NoustStore) -> list[str]:
    """Stand in for the managers that touch the machine, writing what they own."""
    calls: list[str] = []

    def add_domain(app: str, name: str, kind: str, **_: Any) -> None:
        calls.append(f"add {kind} {name}")
        store.add_domain(app, name, kind)

    class Change:
        certificate_issued = True
        certificate_error = None

    def issue_certificate(app: str, **_: Any) -> Change:
        calls.append("certificate")
        return Change()

    def set_health_check(app: str, *, path: Any, expect: Any, timeout: Any) -> None:
        calls.append("health")
        store.set_app_health(app, path=path, expect=expect, timeout=timeout)

    def set_release_retention(app: str, keep: int, **_: Any) -> None:
        calls.append(f"retention {keep}")
        store.set_keep_releases(app, keep)

    class Scheduler:
        def create_schedule(self, schedule: Any) -> bool:
            calls.append(f"backup {[d['name'] for d in schedule.destinations]}")
            store.save_backup_schedule(
                BackupScheduleRecord(
                    app_domain=schedule.domain,
                    schedule=schedule.schedule,
                    include_databases=schedule.include_databases,
                    retention_count=schedule.retention_count,
                    retention_days=schedule.retention_days,
                    destinations=schedule.destinations,
                )
            )
            return True

    def set_zero_downtime(app: str, enabled: bool, *, drain_seconds: Any, **_: Any) -> None:
        calls.append("zero-downtime")
        store.set_zero_downtime(app, enabled, drain_seconds=drain_seconds)

    monkeypatch.setattr(app_export.domain_changes, "add_domain", add_domain)
    monkeypatch.setattr(app_export.domain_changes, "issue_certificate", issue_certificate)
    monkeypatch.setattr(app_export, "set_health_check", set_health_check)
    monkeypatch.setattr(app_export, "set_release_retention", set_release_retention)
    monkeypatch.setattr(app_export, "BackupScheduler", Scheduler)
    monkeypatch.setattr(app_export, "set_zero_downtime", set_zero_downtime)
    return calls


def fake_deploy(store: NoustStore, tmp_path: Path, calls: list[str]) -> Any:
    """A deploy that records the row and the .env as the real one would."""

    def deploy(spec: CreateSpec) -> None:
        calls.append(f"deploy {spec.domain}")
        store.create_app(
            App(
                domain=spec.domain,
                app_type=spec.app_type,
                source=spec.source,
                branch=spec.branch,
                port=spec.port or 3001,
                app_path=str(tmp_path / "apps" / spec.domain.replace(".", "-")),
                layout=spec.layout or "inplace",
                persistent_paths=list(spec.persistent_paths),
                memory_max_mb=spec.memory_max_mb,
                cpu_quota_percent=spec.cpu_quota_percent,
                tasks_max=spec.tasks_max,
                env_secret_marks=dict(spec.env_secret_marks),
            )
        )
        if spec.include_www:
            store.add_domain(spec.domain, f"www.{spec.domain}", "redirect")
        row = store.get_app(spec.domain)
        assert row is not None
        write_env(row, spec.env_vars)

    return deploy
