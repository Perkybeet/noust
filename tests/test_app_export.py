# Copyright (c) 2024-2026 Yago López Prado
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
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DomainConflictError, ValidationError
from noust.core.store import App, NoustStore
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
from noust.deployers.importers.base import Proposal, ProposedEnv
from tests.support.app_export import (  # noqa: F401
    DOMAIN,
    NEW,
    STRIPE,
    TOKEN,
    FakeCron,
    cron,
    engine,
    fake_deploy,
    minimal,
    no_sweep_timer,
    shop,
    store,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


# Export -----------------------------------------------------------------------


def test_export_carries_the_definition(shop: App, cron: FakeCron) -> None:
    doc = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]

    assert list(doc)[:5] == [
        "format",
        "version",
        "exported_at",
        "noust_version",
        "secrets_included",
    ]
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
        # How previews are made came with 4.0; this application was set up before it.
        "inherit_env": "all",
        "allow_env": [],
        "data_mode": "shared",
        "domain_template": None,
        "noindex": True,
        "fork_policy": "refuse",
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
        port=18080,
        health_path="/healthz",
        health_timeout=120,
        persistent_paths=["uploads"],
        databases=["postgresql"],
        env=[
            ProposedEnv(name="APP_NAME", value="Shop"),
            ProposedEnv(name="SESSION_SECRET", secret=True, generated=True),
            ProposedEnv(name="STRIPE_KEY", secret=True, required=True),
            ProposedEnv(name="OPTIONAL"),
            ProposedEnv(name="PORT", value="18080"),
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
    assert plan.create.port == 18080
    assert plan.create.persistent_paths == ("uploads",)
    assert any(step.startswith("health check /healthz") for step in plan.steps)
    assert plan.create.initial_health == ("/healthz", None, 120)
    assert [step.part for step in plan.skipped] == ["database (postgresql)"]
