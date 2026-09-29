# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for exporting an application's definition and importing it back.

The export is pinned on what it carries and, as carefully, on what it never
carries: a secret value without ``with_secrets``, the credentials in a clone
URL, a destination's keys, the webhook secret. The import is pinned on the
plan (a taken domain, a missing secret, a source without its credentials all
stop it before anything runs) and, end to end, on a round trip: an export
imported under a new domain exports back to the same document, the names
derived from the domain renamed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DomainConflictError, ServiceError, ValidationError
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
from noust.deployers.app_export import (
    FORMAT,
    VERSION,
    CreateSpec,
    apply_import,
    dumps,
    export_app,
    load_document,
    plan_import,
    plan_summary,
    proposal_document,
    report_summary,
    validate_document,
)
from noust.deployers.helpers.layout import env_file_for
from noust.deployers.importers.base import Proposal, ProposedEnv
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


# Export -----------------------------------------------------------------------


def test_export_carries_the_definition(shop: App, cron: FakeCron) -> None:
    doc = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]

    assert list(doc)[:5] == ["format", "version", "exported_at", "wasm_version", "secrets_included"]
    assert doc["format"] == FORMAT and doc["version"] == VERSION
    app = doc["app"]
    assert app["domain"] == DOMAIN
    assert app["app_type"] == "nextjs"
    assert app["source"] == "https://***@github.com/acme/shop.git"
    assert app["branch"] == "main" and app["layout"] == "releases"
    assert app["include_www"] is True
    assert app["persistent_paths"] == ["storage", "uploads"]
    assert app["keep_releases"] == 7
    assert app["limits"] == {"memory_max_mb": 512, "cpu_quota_percent": None, "tasks_max": 256}
    assert app["health"] == {"path": "/healthz", "expect": "200-399", "timeout": 60}
    assert app["zero_downtime"] == {"enabled": True, "drain_seconds": 20}
    assert doc["domains"] == {"aliases": [f"api.{DOMAIN}"], "redirects": ["shop.example.net"]}
    assert doc["env_secret_marks"] == {"APP_NAME": False, "INTERNAL_ID": True}
    assert doc["cron"] == [
        {
            "name": "shop-example-com-nightly",
            "schedule": "*-*-* 03:00:00",
            "command": "node scripts/nightly.js",
            "enabled": False,
            "user": None,
            "working_directory": None,
        }
    ]
    assert doc["backup"]["destinations"][0] == {
        "name": "offsite",
        "retention_count": 14,
        "retention_days": None,
    }
    assert doc["previews"] == {
        "base_domain": "previews.example.com",
        "max_previews": 5,
        "ttl_hours": 48,
        "allow_bots": True,
        "exclude_env": ["STRIPE_KEY"],
    }
    assert doc["github"] == {"installation_linked": True}
    assert doc["databases"] == [{"engine": "postgresql", "name": "shop"}]


def test_export_withholds_secrets_and_never_carries_wasm_credentials(
    shop: App, cron: FakeCron
) -> None:
    doc = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]
    env = doc["env"]

    assert env["STRIPE_KEY"] == {"secret": True, "value": None}
    assert env["INTERNAL_ID"] == {"secret": True, "value": None}, "marked secret"
    assert env["APP_NAME"] == {"secret": False, "value": "Shop"}
    assert env["PUBLIC_URL"]["value"] == "https://cdn.example.net"
    assert "PORT" not in env, "managed by Noust"
    text = dumps(doc)
    for leaked in (TOKEN, STRIPE, "whsec_", "bucket", "1234"):
        assert leaked not in text


def test_export_with_secrets_includes_values(shop: App, cron: FakeCron) -> None:
    doc = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    assert doc["secrets_included"] is True
    assert doc["env"]["STRIPE_KEY"] == {"secret": True, "value": STRIPE}
    assert TOKEN not in dumps(doc), "the clone URL's credentials are never exported"


def test_export_is_deterministic(shop: App, cron: FakeCron) -> None:
    first = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]
    second = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]
    first.pop("exported_at")
    second.pop("exported_at")
    assert dumps(first) == dumps(second)
    assert list(first["env"]) == sorted(first["env"])


def test_export_of_an_unknown_application(store: NoustStore) -> None:
    with pytest.raises(Exception, match="Application not found"):
        export_app("nothing.example.com", cron=FakeCron())  # type: ignore[arg-type]


def test_export_of_a_bare_application(store: NoustStore, tmp_path: Path) -> None:
    store.create_app(App(domain="bare.example.com", app_type="static", source="/srv/site"))
    doc = export_app("bare.example.com", cron=FakeCron())  # type: ignore[arg-type]
    assert doc["backup"] is None and doc["previews"] is None
    assert doc["env"] == {} and doc["cron"] == [] and doc["databases"] == []
    assert validate_document(json.loads(dumps(doc)))["app"]["domain"] == "bare.example.com"


# Validation -------------------------------------------------------------------


def minimal(**app: Any) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": 1,
        "app": {"domain": DOMAIN, "app_type": "nodejs", "source": "https://x.test/r.git", **app},
    }


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"format": "other", "version": 1, "app": {}}, "not a Noust application export"),
        ({**minimal(), "version": 2}, "this Noust reads version 1"),
        ({**minimal(), "version": "1"}, "version must be a whole number"),
        (minimal(port="3000"), "app.port must be a whole number"),
        (minimal(layout="sideways"), "app.layout must be one of"),
        ({**minimal(), "env": {"BAD NAME": {"value": "x"}}}, "is not an environment variable"),
        ({**minimal(), "cron": [{"name": "x"}]}, "cron[0].schedule is missing"),
        ({**minimal(), "backup": {"destinations": []}}, "backup.schedule is missing"),
        ({**minimal(), "env_secret_marks": {"A": "yes"}}, "env_secret_marks"),
    ],
)
def test_invalid_documents_are_refused(document: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        validate_document(document)
    assert message in caught.value.message


def test_load_document_refuses_what_is_not_json() -> None:
    with pytest.raises(ValidationError, match="not JSON"):
        load_document("{")


def test_unknown_keys_are_ignored() -> None:
    doc = validate_document({**minimal(), "future": {"x": 1}})
    assert doc["app"]["domain"] == DOMAIN


# Plan ---------------------------------------------------------------------------


def test_plan_refuses_a_domain_already_deployed(shop: App, cron: FakeCron) -> None:
    doc = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    with pytest.raises(DomainConflictError):
        plan_import(doc, source="https://github.com/acme/shop.git")


def test_plan_needs_the_source_again_when_its_credentials_were_taken_out(
    shop: App, cron: FakeCron
) -> None:
    doc = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="credentials taken out"):
        plan_import(doc, domain=NEW)


def test_plan_stops_on_missing_secrets_naming_them(shop: App, cron: FakeCron) -> None:
    doc = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as caught:
        plan_import(doc, domain=NEW, source="https://github.com/acme/shop.git")
    assert "INTERNAL_ID, STRIPE_KEY" in caught.value.message
    assert "--env-file" in caught.value.details


def test_plan_takes_secrets_given_and_names_what_it_will_skip(shop: App, cron: FakeCron) -> None:
    doc = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]
    plan = plan_import(
        doc,
        domain=NEW,
        source="https://github.com/acme/shop.git",
        env={"STRIPE_KEY": STRIPE, "INTERNAL_ID": "99"},
    )

    assert plan.domain == NEW and plan.exported_domain == DOMAIN
    assert plan.create.env_vars["STRIPE_KEY"] == STRIPE
    assert plan.create.port is None, "3000 belongs to the exported application here"
    assert plan.create.include_www and plan.create.layout == "releases"
    assert f"alias api.{NEW}" in plan.steps
    assert "redirect shop.example.net" in plan.steps
    assert (
        "cron store-example-org-nightly [*-*-* 03:00:00] as www-data in the application's "
        "directory (created disabled): node scripts/nightly.js"
    ) in plan.steps
    assert plan.confirm, "a document that creates cron jobs and previews needs a yes"
    skipped = {step.part: step.detail for step in plan.skipped}
    assert "backup destination gone" in skipped
    assert "backup destination offsite" not in skipped
    assert "database shop (postgresql)" in skipped
    assert "GitHub App installation" in skipped
    assert "port 3000" in skipped
    summary = plan_summary(plan)
    assert STRIPE not in json.dumps(summary)
    assert summary["create"]["env"] == sorted(plan.create.env_vars)


# Apply --------------------------------------------------------------------------


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


def test_an_import_round_trips_under_a_new_domain(
    shop: App, cron: FakeCron, engine: list[str], store: NoustStore, tmp_path: Path
) -> None:
    source = "https://github.com/acme/shop.git"
    before = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    plan = plan_import(before, domain=NEW, source=source)
    # The exported application holds api.shop.example.com; the new one gets
    # api.store.example.org, but shop.example.net can only belong to one.
    report = apply_import(
        plan,
        deploy=fake_deploy(store, tmp_path, engine),
        cron=cron,  # type: ignore[arg-type]
    )

    assert engine[0] == f"deploy {NEW}"
    assert engine[-1] == "zero-downtime", "zero-downtime goes last"
    assert "backup ['offsite']" in engine
    not_applied = {step.part for step in report.not_applied}
    assert "redirect shop.example.net" in not_applied
    assert "backup destination gone" in not_applied
    assert "database shop (postgresql)" in not_applied
    assert cron.jobs["store-example-org-nightly"]["enabled"] is False
    assert report_summary(report)["not_applied"]

    after = export_app(NEW, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    for doc in (before, after):
        doc.pop("exported_at")
        doc["app"].pop("port")
        doc["databases"] = []
        doc["github"] = {}
        doc["backup"]["destinations"] = [
            d for d in doc["backup"]["destinations"] if d["name"] != "gone"
        ]
        doc["domains"]["redirects"] = []
    expected = json.loads(
        dumps(before)
        .replace(DOMAIN, NEW)
        .replace("shop-example-com", "store-example-org")
        .replace("https://***@github.com/acme/shop.git", source)
    )
    assert after == expected


def test_a_failed_deploy_applies_nothing_else(shop: App, cron: FakeCron, engine: list[str]) -> None:
    doc = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    plan = plan_import(doc, domain=NEW, source="https://github.com/acme/shop.git")

    def deploy(spec: CreateSpec) -> None:
        raise app_export.NoustError("build failed")

    with pytest.raises(app_export.NoustError, match="build failed"):
        apply_import(plan, deploy=deploy, cron=cron)  # type: ignore[arg-type]
    assert engine == []


def test_a_refused_step_is_reported_and_the_rest_goes_on(
    shop: App,
    cron: FakeCron,
    engine: list[str],
    store: NoustStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise ValidationError("path refused", details="Give a path.")

    monkeypatch.setattr(app_export, "set_health_check", refuse)
    cron.jobs[f"{NEW.replace('.', '-')}-nightly"] = {"name": "taken", "app_domain": NEW}
    doc = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    plan = plan_import(doc, domain=NEW, source="https://github.com/acme/shop.git")
    report = apply_import(
        plan,
        deploy=fake_deploy(store, tmp_path, engine),
        cron=cron,  # type: ignore[arg-type]
    )

    details = {step.part: step.detail for step in report.not_applied}
    assert details["health check"] == "path refused. Give a path."
    assert "already exists" in details["cron store-example-org-nightly"]
    assert engine[-1] == "zero-downtime"


# Other platforms --------------------------------------------------------------


def test_a_proposal_becomes_a_document_the_plan_accepts(store: NoustStore) -> None:
    proposal = Proposal(
        platform="render",
        app_type="python",
        port=8080,
        health_path="/healthz",
        health_timeout=120,
        persistent_paths=["uploads"],
        databases=["postgresql"],
        env=[
            ProposedEnv(name="APP_NAME", value="Shop"),
            ProposedEnv(name="SESSION_SECRET", secret=True, generated=True),
            ProposedEnv(name="STRIPE_KEY", secret=True, required=True),
            ProposedEnv(name="OPTIONAL"),
            ProposedEnv(name="PORT", value="8080"),
        ],
    )
    doc = proposal_document(proposal, domain=NEW, source="https://github.com/acme/shop.git")
    assert validate_document(doc)
    assert "OPTIONAL" not in doc["env"] and "PORT" not in doc["env"]
    generated = doc["env"]["SESSION_SECRET"]["value"]
    assert isinstance(generated, str) and len(generated) >= 32

    with pytest.raises(ValidationError, match="STRIPE_KEY"):
        plan_import(doc)
    plan = plan_import(doc, env={"STRIPE_KEY": STRIPE})
    assert plan.create.app_type == "python"
    assert plan.create.port == 8080
    assert plan.create.persistent_paths == ("uploads",)
    assert any(step.startswith("health check /healthz") for step in plan.steps)
    assert plan.create.initial_health == ("/healthz", None, 120)
    assert [step.part for step in plan.skipped] == ["database (postgresql)"]
