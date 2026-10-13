# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application is set to that it does not run with yet (checklist B-09).

Pinned here:

- the baseline is the configuration the application was last put to work with:
  taken when a deployment succeeds, refreshed for the areas a restart applies;
- the difference lists settings, what changed about each and what applying it
  takes, and never a secret's value (not in the answer, not in the document);
- a variable the framework bakes into the bundle, and every variable of a site
  with no process, needs a rebuild; the rest of the environment, a restart; the
  health check and the hooks, only the next deploy;
- an application with no baseline is given one, as it is, and nothing is reported.
"""

# The service manager fixtures are imported rather than replicated.

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core import app_build_store, applied_config_store
from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.core.teams import ScopedStore
from noust.core.unit_hardening_store import set_record
from noust.deployers import pending_changes
from noust.deployers.recorder import DeploymentRecorder
from noust.managers.service_manager import ServiceManager
from noust.web.api import pending as pending_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers

DOMAIN = "shop.example.com"
SECRET = "sk_live_4eC39HqLyjWDarjtT1zdp7dc"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of the test's own, holding one in-place Node application with a .env."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    (root / ".env").write_text(f"DATABASE_URL=postgres://u:p@h/db\nSTRIPE_KEY={SECRET}\n")
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", port=3100, app_path=str(root), memory_max_mb=256)
    )
    yield instance
    instance.close()
    NoustStore.reset_instance()


def env_file(store: NoustStore) -> Path:
    app = store.get_app(DOMAIN)
    assert app is not None
    return Path(app.app_path) / ".env"


def baseline(store: NoustStore) -> None:
    """Put the application to work as it is."""
    assert pending_changes.record_applied(DOMAIN, 7, store=store)


def keys(changes: pending_changes.PendingChanges) -> set[tuple[str, str, str]]:
    return {(item.area, item.key, item.change) for item in changes.items}


# ---------------------------------------------------------------------------
# The baseline
# ---------------------------------------------------------------------------


def test_an_application_with_no_baseline_is_given_one_and_nothing_is_reported(
    store: NoustStore,
) -> None:
    first = pending_changes.pending(DOMAIN, store=store)

    assert first.baseline_created and not first.pending

    env_file(store).write_text("OTHER=1\n")
    second = pending_changes.pending(DOMAIN, store=store)
    assert not second.baseline_created
    assert ("environment", "OTHER", "added") in keys(second)


def test_a_configuration_that_has_not_changed_has_nothing_pending(store: NoustStore) -> None:
    baseline(store)

    changes = pending_changes.pending(DOMAIN, store=store)

    assert not changes.pending and changes.needs is None
    assert changes.deployment_id == 7 and changes.since is not None


def test_an_unknown_application_is_refused(store: NoustStore) -> None:
    with pytest.raises(NoustError, match="not found"):
        pending_changes.pending("nobody.example.com", store=store)


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


def test_an_edited_added_and_removed_variable_are_told_apart(store: NoustStore) -> None:
    baseline(store)
    env_file(store).write_text("DATABASE_URL=postgres://u:other@h/db\nNEW_FLAG=1\n")

    changes = pending_changes.pending(DOMAIN, store=store)

    assert keys(changes) == {
        ("environment", "DATABASE_URL", "changed"),
        ("environment", "NEW_FLAG", "added"),
        ("environment", "STRIPE_KEY", "removed"),
    }
    assert {item.needs for item in changes.items} == {"restart"}
    assert changes.needs == "restart"


def test_no_value_reaches_the_answer_or_the_document(store: NoustStore) -> None:
    baseline(store)
    env_file(store).write_text(
        "DATABASE_URL=postgres://u:other@h/db\nSTRIPE_KEY=sk_live_NEWVALUE123456\n"
    )

    changes = pending_changes.pending(DOMAIN, store=store)
    answer = json.dumps(changes.to_dict())
    stored = json.dumps(applied_config_store.get(DOMAIN, store=store).sections)  # type: ignore[union-attr]

    for text in (answer, stored):
        assert SECRET not in text
        assert "NEWVALUE123456" not in text
        assert "postgres://" not in text
    assert all(item.before is None and item.after is None for item in changes.items)


def test_a_variable_baked_into_the_bundle_needs_a_rebuild(store: NoustStore) -> None:
    baseline(store)
    env_file(store).write_text(
        f"DATABASE_URL=postgres://u:p@h/db\nSTRIPE_KEY={SECRET}\nNEXT_PUBLIC_API=https://x\nPORT=1\n"
    )

    changes = pending_changes.pending(DOMAIN, store=store)

    by_key = {item.key: item.needs for item in changes.items}
    assert by_key == {"NEXT_PUBLIC_API": "rebuild", "PORT": "restart"}
    assert changes.needs == "rebuild", "the heaviest of them"


def test_every_variable_of_a_site_with_no_process_needs_a_rebuild(store: NoustStore) -> None:
    app = store.get_app(DOMAIN)
    assert app is not None
    app.app_type = "vite"
    store.update_app(app)
    baseline(store)
    env_file(store).write_text(f"STRIPE_KEY={SECRET}\nAPI=1\n")

    changes = pending_changes.pending(DOMAIN, store=store)

    assert {item.needs for item in changes.items} == {"rebuild"}


# ---------------------------------------------------------------------------
# Settings that are not secret
# ---------------------------------------------------------------------------


def test_a_limit_says_what_it_was_and_what_it_is(store: NoustStore) -> None:
    baseline(store)
    app = store.get_app(DOMAIN)
    assert app is not None
    app.memory_max_mb = 512
    app.tasks_max = 128
    store.update_app(app)

    changes = pending_changes.pending(DOMAIN, store=store)

    by_key = {item.key: item for item in changes.items}
    assert (by_key["memory_max_mb"].before, by_key["memory_max_mb"].after) == ("256", "512")
    assert by_key["memory_max_mb"].change == "changed"
    assert by_key["tasks_max"].change == "added"
    assert by_key["tasks_max"].needs == "restart"


def test_a_limit_taken_away_is_removed(store: NoustStore) -> None:
    baseline(store)
    app = store.get_app(DOMAIN)
    assert app is not None
    app.memory_max_mb = None
    store.update_app(app)

    [item] = pending_changes.pending(DOMAIN, store=store).items

    assert (item.key, item.change, item.before, item.after) == (
        "memory_max_mb",
        "removed",
        "256",
        None,
    )


def test_the_sandbox_is_a_restart(store: NoustStore) -> None:
    baseline(store)
    set_record(DOMAIN, "strict", ("/srv/a",), changed_by="ana", store=store)

    changes = pending_changes.pending(DOMAIN, store=store)

    assert {(i.area, i.key, i.needs) for i in changes.items} == {
        ("sandbox", "profile", "restart"),
        ("sandbox", "writable_paths", "restart"),
    }


def test_the_health_check_waits_for_the_next_deploy(store: NoustStore) -> None:
    baseline(store)
    store.set_app_health(DOMAIN, path="/healthz", expect="200", timeout=60)

    changes = pending_changes.pending(DOMAIN, store=store)

    assert {(i.area, i.key, i.needs) for i in changes.items} == {
        ("health", "path", "next_deploy"),
        ("health", "expect", "next_deploy"),
        ("health", "timeout", "next_deploy"),
    }
    assert changes.needs == "next_deploy"


def test_the_hooks_wait_for_the_next_deploy_and_show_no_value(store: NoustStore) -> None:
    baseline(store)
    store.set_app_hooks(DOMAIN, "pre_deploy: ['rails', 'db:migrate']\n", updated_by="ana")

    [item] = pending_changes.pending(DOMAIN, store=store).items

    assert (item.area, item.key, item.change, item.needs) == ("hooks", "", "added", "next_deploy")
    assert item.before is None and item.after is None


def test_the_build_commands_need_a_rebuild(store: NoustStore) -> None:
    baseline(store)
    app_build_store.set_document(
        DOMAIN, "build:\n  start: npm run serve\n", updated_by="ana", store=store
    )

    [item] = pending_changes.pending(DOMAIN, store=store).items

    assert (item.area, item.change, item.needs) == ("build", "added", "rebuild")


def test_the_persistent_paths_need_a_rebuild(store: NoustStore) -> None:
    baseline(store)
    app = store.get_app(DOMAIN)
    assert app is not None
    app.persistent_paths = ["uploads"]
    store.update_app(app)

    [item] = pending_changes.pending(DOMAIN, store=store).items

    assert (item.area, item.key, item.needs) == ("persistent_paths", "uploads", "rebuild")


# ---------------------------------------------------------------------------
# Applying them
# ---------------------------------------------------------------------------


def test_a_restart_applies_what_a_restart_applies_and_nothing_more(store: NoustStore) -> None:
    baseline(store)
    env_file(store).write_text("OTHER=1\n")
    app = store.get_app(DOMAIN)
    assert app is not None
    app.memory_max_mb = 1024
    store.update_app(app)
    store.set_app_health(DOMAIN, path="/healthz", expect=None, timeout=None)
    app_build_store.set_document(DOMAIN, "build:\n  start: x\n", updated_by="ana", store=store)

    assert pending_changes.mark_restarted(DOMAIN, store=store)

    changes = pending_changes.pending(DOMAIN, store=store)
    assert {item.area for item in changes.items} == {"health", "build"}
    assert changes.deployment_id == 7, "the deployment is still the last one"


def test_a_deployment_that_succeeds_takes_the_baseline(store: NoustStore) -> None:
    env_file(store).write_text("OTHER=1\n")
    recorder = DeploymentRecorder(store, DOMAIN, "cli", logger=Logger(verbose=False))
    recorder.start()

    recorder.finish_success()

    changes = pending_changes.pending(DOMAIN, store=store)
    assert not changes.baseline_created
    assert not changes.pending
    assert changes.deployment_id == recorder.deployment_id


def test_a_deployment_that_fails_leaves_the_baseline_as_it_was(store: NoustStore) -> None:
    baseline(store)
    env_file(store).write_text("OTHER=1\n")
    recorder = DeploymentRecorder(store, DOMAIN, "cli", logger=Logger(verbose=False))
    recorder.start()

    recorder.finish_failure("the build failed")

    assert ("environment", "OTHER", "added") in keys(pending_changes.pending(DOMAIN, store=store))


def test_restarting_the_unit_refreshes_the_baseline(
    store: NoustStore,
    runner: FakeRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ServiceManager.restart is the one place every restart goes through."""
    managed = tmp_path / "etc/systemd/system"
    managed.mkdir(parents=True)
    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", managed)
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (managed,))
    manager = ServiceManager()
    manager.create_service(
        name="shop-example-com", command="/usr/bin/true", working_directory=str(tmp_path)
    )
    baseline(store)
    env_file(store).write_text("OTHER=1\n")
    assert pending_changes.pending(DOMAIN, store=store).pending

    manager.restart("shop-example-com")

    assert not pending_changes.pending(DOMAIN, store=store).pending


def test_the_baseline_goes_with_the_application(store: NoustStore) -> None:
    baseline(store)

    store.delete_app(DOMAIN)

    with store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM app_applied_config")
        assert cursor.fetchone()[0] == 0


def test_the_baseline_survives_a_redeploy_writing_the_application_row(store: NoustStore) -> None:
    baseline(store)
    app = store.get_app(DOMAIN)
    assert app is not None
    app.status = "running"

    store.update_app(app)

    assert applied_config_store.get(DOMAIN, store=store) is not None


def test_an_area_the_baseline_predates_is_not_compared(store: NoustStore) -> None:
    """A baseline taken before an area existed says nothing about it."""
    baseline(store)
    known = applied_config_store.get(DOMAIN, store=store)
    assert known is not None
    sections = {name: value for name, value in known.sections.items() if name != "limits"}
    applied_config_store.put(DOMAIN, sections, deployment_id=7, store=store)
    app = store.get_app(DOMAIN)
    assert app is not None
    app.memory_max_mb = 999
    store.update_app(app)

    assert not any(
        item.area == "limits" for item in pending_changes.pending(DOMAIN, store=store).items
    )


def test_an_area_registered_by_another_part_is_compared_too(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pending_changes, "_EXTRA", [])
    state = {"workers": 1}
    pending_changes.register_section(
        pending_changes.Section(
            "processes", pending_changes.NEEDS_RESTART, lambda app: {"worker": state["workers"]}
        )
    )
    baseline(store)
    state["workers"] = 3

    [item] = pending_changes.pending(DOMAIN, store=store).items

    assert (item.area, item.key, item.before, item.after) == ("processes", "worker", "1", "3")


# ---------------------------------------------------------------------------
# The API and the command line
# ---------------------------------------------------------------------------


@pytest.fixture
def client(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(pending_api, "scoped_store", lambda: ScopedStore(store=store))
    monkeypatch.setattr(pending_changes, "get_store", lambda: store)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(pending_api.router, prefix="/api/apps")
    app.dependency_overrides[get_current_session] = lambda: {"sid": "s", "scope": "read"}
    return TestClient(app)


def test_the_api_lists_what_is_pending_with_the_counts(
    client: TestClient, store: NoustStore
) -> None:
    baseline(store)
    env_file(store).write_text(f"NEXT_PUBLIC_X=1\nPORT=2\nSTRIPE_KEY={SECRET}\nDATABASE_URL=a\n")

    body = client.get(f"/api/apps/{DOMAIN}/pending").json()

    assert body["pending"] is True and body["needs"] == "rebuild"
    assert body["counts"] == {"rebuild": 1, "restart": 2}
    assert SECRET not in json.dumps(body)


def test_the_api_says_nothing_is_pending(client: TestClient, store: NoustStore) -> None:
    baseline(store)

    body = client.get(f"/api/apps/{DOMAIN}/pending").json()

    assert body["pending"] is False and body["items"] == [] and body["counts"] == {}


def test_the_api_reads_an_unknown_application_as_absent(client: TestClient) -> None:
    assert client.get("/api/apps/nobody.example.com/pending").status_code == 404


def test_the_command_lists_the_changes_and_how_to_apply_them(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pending_changes, "get_store", lambda: store)
    baseline(store)
    env_file(store).write_text("OTHER=1\n")

    result = CliRunner().invoke(root_cli, ["--json", "app", "pending", DOMAIN])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["needs"] == "restart"
    assert {item["key"] for item in body["items"]} == {"DATABASE_URL", "STRIPE_KEY", "OTHER"}


def test_the_route_is_as_public_as_reading_the_application() -> None:
    from noust.web.permissions.routes_pending import ROUTES

    assert ROUTES[("GET", "/api/apps/{domain}/pending")] == "apps.read"
