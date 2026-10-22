# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether an application could run on more than one server, as the server it is on can tell
(checklist B-24, B-69).

Putting an application on several servers is the central's. What decides whether a second server
would serve the same thing is on the server the application is on: the files it keeps, the
database it uses, the tasks it schedules. These tests pin what is read, what blocks and what only
warns, that no value of a variable is ever returned, and that nothing is placed or copied.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.store import App, Database, NoustStore, PreviewSettings, get_store
from noust.core.teams import ScopedStore
from noust.managers import replication
from noust.web.api import app_replication as replication_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers

DOMAIN = "shop.example.com"
REPO = "https://github.com/shop/storefront.git"


@pytest.fixture
def store() -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    yield get_store()
    NoustStore.reset_instance()


def make(store: NoustStore, tmp_path: Path, **fields: Any) -> App:
    """An application of this store, with a directory of its own."""
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True, exist_ok=True)
    defaults: dict[str, Any] = {
        "domain": DOMAIN,
        "app_type": "nodejs",
        "source": REPO,
        "layout": "releases",
        "app_path": str(root),
    }
    return store.create_app(App(**{**defaults, **fields}))


def codes(readiness: replication.Readiness) -> list[str]:
    return [finding.code for finding in readiness.findings]


def no_cron() -> list[dict[str, Any]]:
    return []


# -- An application nothing speaks against ------------------------------------------------------------


def test_a_stateless_application_from_a_repository_is_ready(
    store: NoustStore, tmp_path: Path
) -> None:
    app = make(store, tmp_path)

    verdict = replication.assess(app, cron_jobs=no_cron)

    assert verdict.ready and verdict.findings == () and not verdict.by_image


# -- What blocks ---------------------------------------------------------------------------------------


def test_files_kept_on_this_server_block_it_and_are_named(
    store: NoustStore, tmp_path: Path
) -> None:
    app = make(store, tmp_path, persistent_paths=["storage/uploads", "public/avatars"])

    verdict = replication.assess(app, cron_jobs=no_cron)

    assert not verdict.ready
    found = verdict.findings[0]
    assert (found.code, found.severity) == ("persistent_paths", "blocks")
    assert found.items == ("storage/uploads", "public/avatars")


def test_a_database_noust_runs_here_blocks_it_and_one_elsewhere_does_not(
    store: NoustStore, tmp_path: Path
) -> None:
    app = make(store, tmp_path)
    store.create_database(
        Database(app_id=app.id, name="shop", engine="postgresql", host="localhost")
    )
    store.create_database(Database(app_id=app.id, name="cache", engine="redis", host="db.internal"))

    verdict = replication.assess(app, cron_jobs=no_cron)

    local = next(f for f in verdict.findings if f.code == "local_database")
    assert local.severity == "blocks" and local.items == ("postgresql:shop",)
    assert not verdict.ready


def test_a_source_that_is_a_folder_on_this_server_blocks_it(
    store: NoustStore, tmp_path: Path
) -> None:
    app = make(store, tmp_path, source=str(tmp_path / "srv" / "src"))

    verdict = replication.assess(app, cron_jobs=no_cron)

    assert "no_repository" in codes(verdict) and not verdict.ready


def test_a_registry_image_has_no_source_to_lack_and_can_be_rolled_out_by_tag(
    store: NoustStore, tmp_path: Path
) -> None:
    app = make(store, tmp_path, app_type="container", source="ghcr.io/shop/storefront:1.4")

    verdict = replication.assess(app, cron_jobs=no_cron, compose_document=lambda: {"services": {}})

    assert verdict.ready and verdict.by_image


def test_a_site_noust_adopted_cannot_be_copied_from_its_record(
    store: NoustStore, tmp_path: Path
) -> None:
    app = make(store, tmp_path, app_type="adopted", source=str(tmp_path), layout="inplace")

    verdict = replication.assess(app, cron_jobs=no_cron)

    assert codes(verdict) == ["adopted"] and not verdict.ready


def test_a_database_the_stack_runs_blocks_it_and_named_volumes_warn(
    store: NoustStore, tmp_path: Path
) -> None:
    app = make(store, tmp_path, app_type="docker-compose", layout="inplace")
    document = {
        "services": {
            "web": {"image": "ghcr.io/shop/web:1"},
            "db": {"image": "postgres:16"},
            "cache": {"image": "redis:7"},
        },
        "volumes": {"dbdata": {}, "media": {}},
    }

    verdict = replication.assess(app, cron_jobs=no_cron, compose_document=lambda: document)

    by_code = {finding.code: finding for finding in verdict.findings}
    assert by_code["stack_database"].severity == "blocks"
    assert by_code["stack_database"].items == ("db",)  # the engines Noust itself backs up
    assert by_code["named_volumes"].severity == "warns"
    assert by_code["named_volumes"].items == ("dbdata", "media")
    assert not verdict.ready


def test_a_compose_file_that_cannot_be_read_says_nothing_about_the_stack(
    store: NoustStore, tmp_path: Path
) -> None:
    app = make(store, tmp_path, app_type="docker-compose", layout="inplace")

    verdict = replication.assess(app, cron_jobs=no_cron, compose_document=lambda: None)

    assert "stack_database" not in codes(verdict) and "named_volumes" not in codes(verdict)


# -- What only warns --------------------------------------------------------------------------------------


def test_scheduled_tasks_would_run_once_per_server(store: NoustStore, tmp_path: Path) -> None:
    app = make(store, tmp_path)
    jobs = [
        {"name": "nightly", "app_domain": DOMAIN},
        {"name": "digest", "app_domain": DOMAIN},
        {"name": "other-app", "app_domain": "other.example.com"},
        {"name": "server-wide", "app_domain": None},
    ]

    verdict = replication.assess(app, cron_jobs=lambda: jobs)

    tasks = next(f for f in verdict.findings if f.code == "scheduled_tasks")
    assert tasks.severity == "warns" and tasks.items == ("digest", "nightly")
    assert verdict.ready


def test_previews_and_an_in_place_build_warn(store: NoustStore, tmp_path: Path) -> None:
    app = make(store, tmp_path, layout="inplace")
    store.save_preview_settings(PreviewSettings(app_domain=DOMAIN, base_domain="p.example.com"))

    verdict = replication.assess(app, cron_jobs=no_cron)

    assert set(codes(verdict)) == {"in_place", "previews"}
    assert verdict.ready


def test_a_variable_that_points_at_this_machine_warns_by_name_and_never_shows_its_value(
    store: NoustStore, tmp_path: Path
) -> None:
    app = make(store, tmp_path, layout="inplace")
    (Path(app.app_path) / ".env").write_text(
        "REDIS_URL=redis://127.0.0.1:6379/0\n"
        "DATABASE_URL=postgres://db.internal/shop\n"
        "MAIL_HOST=localhost\n"
        "APP_SECRET=127.0.0.1-is-not-a-service\n"
        "PORT=3000\n"
    )

    verdict = replication.assess(app, cron_jobs=no_cron)

    local = next(f for f in verdict.findings if f.code == "local_service")
    assert local.severity == "warns" and local.items == ("MAIL_HOST", "REDIS_URL")
    assert "127.0.0.1" not in json.dumps(verdict.to_dict())


def test_what_blocks_comes_first(store: NoustStore, tmp_path: Path) -> None:
    app = make(store, tmp_path, layout="inplace", persistent_paths=["uploads"])
    store.save_preview_settings(PreviewSettings(app_domain=DOMAIN, base_domain="p.example.com"))

    verdict = replication.assess(app, cron_jobs=no_cron)

    severities = [finding.severity for finding in verdict.findings]
    assert severities == sorted(severities, key=lambda s: s != "blocks")
    assert severities[0] == "blocks"


def test_it_asks_the_machine_nothing_and_places_nothing(store: NoustStore, tmp_path: Path) -> None:
    from noust.core.runner import FakeRunner, set_runner

    fake = FakeRunner()
    set_runner(fake)
    try:
        app = make(store, tmp_path, persistent_paths=["uploads"])
        replication.assess(app, cron_jobs=no_cron)
    finally:
        set_runner(None)

    assert fake.calls == []


# -- The API and the command line --------------------------------------------------------------------------


def client(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(replication_api, "scoped_store", lambda: ScopedStore(store=store))
    monkeypatch.setattr(replication, "_scheduled_tasks", lambda domain, cron_jobs: ())
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(replication_api.router, prefix="/api/apps")
    session = {"type": "session", "sid": "s", "scope": "read", "account_id": 1, "username": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session
    return TestClient(app)


def test_the_api_answers_the_same_and_an_unknown_application_is_a_404(
    store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(store, tmp_path, persistent_paths=["uploads"])
    api = client(store, monkeypatch)

    body = api.get(f"/api/apps/{DOMAIN}/replication").json()

    assert body["ready"] is False and body["by_image"] is False
    assert body["findings"][0] == {
        "code": "persistent_paths",
        "severity": "blocks",
        "message": body["findings"][0]["message"],
        "fix": body["findings"][0]["fix"],
        "items": ["uploads"],
    }
    assert api.get("/api/apps/nope.example.com/replication").status_code == 404


def test_the_route_is_reading_an_application() -> None:
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_app_replication import ROUTES

    assert ROUTES == {("GET", "/api/apps/{domain}/replication"): Permission.APPS_READ}


def test_the_command_says_what_blocks_and_prints_json(
    store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(store, tmp_path, persistent_paths=["uploads"])
    monkeypatch.setattr(replication, "_scheduled_tasks", lambda domain, cron_jobs: ())

    text = CliRunner().invoke(root_cli, ["app", "replication", DOMAIN])
    data = CliRunner().invoke(root_cli, ["--json", "app", "replication", DOMAIN])

    assert text.exit_code == 0, text.output
    assert "something here blocks it" in text.output and "uploads" in text.output
    body = json.loads(data.output.strip().splitlines()[-1])
    assert body["ready"] is False and body["findings"][0]["code"] == "persistent_paths"


def test_the_command_for_an_unknown_application_says_so(store: NoustStore) -> None:
    result = CliRunner().invoke(root_cli, ["app", "replication", "nope.example.com"])

    assert result.exit_code != 0 and "Application not found" in str(result.exception)
