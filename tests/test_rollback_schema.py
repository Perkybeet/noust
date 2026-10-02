# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for going back past a deployment that changed the database's schema.

Noust puts code back, never a database. A deployment whose migrating hook or
Prisma migration succeeded is marked ``schema_changed``; every way back past
it - a rollback to a deployment, the activation of an older release, the
rebuild of an earlier commit, the restore of a backup's files - asks the
operator first, through one guard. The automatic rollback after a failed gate
still happens, and says first that the schema stayed changed.
"""

# The pipeline's fixtures are imported rather than replicated, as in
# test_build_sandbox.py; pytest resolves them by name.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.store import NoustStore
from noust.deployers import lifecycle
from noust.deployers.helpers.hooks import SCHEMA_CHANGED_PREFIX
from noust.deployers.lifecycle import (
    SchemaChangedError,
    activate_release,
    require_schema_change_confirmed,
    rollback_to_deployment,
    schema_changed_between,
)
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    BROKEN_SERVER,
    DOMAIN,
    active_id,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    update,
    write_tree,
)

MIGRATES = "hooks:\n  pre_deploy:\n    - run: ./migrate.sh\n      migrates: true\n"


def migrating_tree(directory: Path, *, server: str | None = None) -> Path:
    if server is None:
        node_tree(directory)
    else:
        node_tree(directory, server=server)
    return write_tree(directory, {"noust.yaml": MIGRATES})


@pytest.fixture
def history(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    """v1 deployed, v2 migrated the schema, v3 did not: three releases, three rows."""
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    first_release = active_id(root)
    machine.git.publish(migrating_tree(tmp_path / "v2"))
    update(machine, monkeypatch)
    machine.git.publish(node_tree(tmp_path / "v3"))
    update(machine, monkeypatch)
    rows = sorted(store.list_deployments(DOMAIN), key=lambda row: row.id or 0)
    # Activations restart and probe through lifecycle's own managers.
    monkeypatch.setattr(lifecycle, "ServiceManager", lambda **kwargs: machine.services)
    monkeypatch.setattr(lifecycle, "SourceManager", lambda **kwargs: machine.git)
    monkeypatch.setattr(lifecycle, "wait_until_healthy", lambda url, **kwargs: True)
    return SimpleNamespace(rows=rows, first_release=first_release)


def test_each_deployment_knows_the_later_schema_changes(history: SimpleNamespace) -> None:
    first, second, third = history.rows

    assert [row.schema_changed for row in history.rows] == [False, True, False]
    assert schema_changed_between(history.rows) == {
        first.id: [second.id],
        second.id: [],
        third.id: [],
    }


def test_going_back_past_a_schema_change_names_it_and_asks(
    history: SimpleNamespace, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, _ = history.rows
    before = active_id(root)

    with pytest.raises(SchemaChangedError) as refusal:
        rollback_to_deployment(DOMAIN, first.id)

    assert refusal.value.deployments == [second.id]
    assert f"deployment {second.id}, which changed the database schema" in refusal.value.message
    assert "noust backup restore" in (refusal.value.details or "")
    assert refusal.value.field == "schema_changed_ok"
    assert active_id(root) == before


def test_a_yes_goes_back(history: SimpleNamespace, root: Path) -> None:
    first = history.rows[0]

    outcome = rollback_to_deployment(DOMAIN, first.id, schema_changed_ok=True)

    assert outcome.release_id == history.first_release
    assert active_id(root) == history.first_release


def test_going_back_to_after_the_change_asks_nothing(history: SimpleNamespace, root: Path) -> None:
    second = history.rows[1]

    rollback_to_deployment(DOMAIN, second.id)

    assert active_id(root) == second.release_id


def test_activating_an_older_release_asks_too(history: SimpleNamespace, root: Path) -> None:
    with pytest.raises(SchemaChangedError):
        activate_release(DOMAIN, history.first_release)

    activate_release(DOMAIN, history.first_release, schema_changed_ok=True)
    assert active_id(root) == history.first_release


def test_rebuilding_an_earlier_commit_asks_too(
    history: SimpleNamespace, machine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = history.rows[0]

    with pytest.raises(SchemaChangedError):
        update(machine, monkeypatch, commit=first.git_commit)


def test_a_backup_taken_before_the_change_asks_too(history: SimpleNamespace) -> None:
    first, second, _ = history.rows

    with pytest.raises(SchemaChangedError) as refusal:
        require_schema_change_confirmed(
            DOMAIN, target="backup x", schema_changed_ok=False, since=first.started_at
        )
    assert second.id in refusal.value.deployments

    assert require_schema_change_confirmed(
        DOMAIN, target="backup x", schema_changed_ok=True, since=first.started_at
    )


def test_the_automatic_rollback_still_happens_and_says_the_schema_changed_first(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    first = active_id(root)
    machine.git.publish(migrating_tree(tmp_path / "v2", server=BROKEN_SERVER))

    with pytest.raises(DeploymentError) as failure:
        update(machine, monkeypatch)

    assert active_id(root) == first
    assert failure.value.message.startswith(SCHEMA_CHANGED_PREFIX)
    row = store.list_deployments(DOMAIN)[0]
    assert row.status == "failed"
    assert row.schema_changed is True
    assert (row.error or "").startswith(SCHEMA_CHANGED_PREFIX)


def test_the_guard_is_the_one_lifecycle_exports() -> None:
    """Every door calls the same function; none re-implements the question."""
    assert lifecycle.require_schema_change_confirmed is require_schema_change_confirmed


def deployments_client() -> Any:
    """A client for the deployment routes alone, authenticated."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from noust.web.api import deployments
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(deployments.router, prefix="/api/deployments")
    app.include_router(deployments.app_router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session
    return TestClient(app)


class TestTheApi:
    def test_the_history_says_what_changed_the_schema_and_what_going_back_passes(
        self, history: SimpleNamespace
    ) -> None:
        first, second, _ = history.rows

        body = deployments_client().get(f"/api/deployments?domain={DOMAIN}").json()

        rows = {item["id"]: item for item in body["items"]}
        assert rows[second.id]["schema_changed"] is True
        assert rows[second.id]["hooks"][0]["run"] == "./migrate.sh"
        assert rows[first.id]["schema_changed_between"] == [second.id]
        assert rows[second.id]["schema_changed_between"] == []

    def test_a_rollback_past_a_schema_change_is_409_until_confirmed(
        self, history: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.api import deployments

        queued: list[dict[str, object]] = []

        class Jobs:
            def create_job(self, **kwargs: object) -> SimpleNamespace:
                queued.append(kwargs)
                return SimpleNamespace(
                    id="j1", status=SimpleNamespace(value="queued"), to_dict=lambda: {"id": "j1"}
                )

        monkeypatch.setattr(deployments, "get_job_manager", lambda: Jobs())
        first, second, _ = history.rows
        client = deployments_client()
        url = f"/api/apps/{DOMAIN}/deployments/{first.id}/rollback"

        refused = client.post(url)
        assert refused.status_code == 409
        detail = refused.json()
        assert detail["error"] == "schema_changed" or "schema_changed" in str(detail)
        assert str(second.id) in str(detail)
        assert queued == []

        accepted = client.post(url, json={"schema_changed_ok": True})
        assert accepted.status_code == 202, accepted.text
        [job] = queued
        assert job["kwargs"] == {
            "domain": DOMAIN,
            "deployment_id": first.id,
            "schema_changed_ok": True,
        }


def test_the_command_line_asks_with_a_flag(
    history: SimpleNamespace, root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli
    from noust.core.config import Config

    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    before = active_id(root)

    refused = CliRunner().invoke(root_cli, ["releases", "rollback", DOMAIN, history.first_release])

    assert isinstance(refused.exception, SchemaChangedError)
    assert active_id(root) == before

    accepted = CliRunner().invoke(
        root_cli, ["releases", "rollback", DOMAIN, history.first_release, "--schema-changed-ok"]
    )

    assert accepted.exit_code == 0, accepted.output
    assert active_id(root) == history.first_release
    Config.reset_instance()


def backup_before_the_change(history: SimpleNamespace) -> Any:
    """A backup taken right after the first deployment, before the schema changed."""
    from noust.managers.backup_manager import BackupMetadata

    return BackupMetadata(
        id="shop-before",
        domain=DOMAIN,
        app_name="shop",
        created_at=history.rows[0].started_at,
        size_bytes=1,
        app_type="nodejs",
        version="3.2.0",
        description="by hand",
        includes_env=True,
        includes_node_modules=False,
    )


@pytest.fixture
def backups(history: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The backup above is the only one; restoring it is recorded, never done."""
    from noust.managers.backup_manager import BackupManager

    metadata = backup_before_the_change(history)
    restored: list[str] = []
    monkeypatch.setattr(BackupManager, "get_backup", lambda self, backup_id: metadata)
    monkeypatch.setattr(BackupManager, "list_backups", lambda self, **kwargs: [metadata])
    monkeypatch.setattr(
        BackupManager, "restore", lambda self, backup_id, *a, **k: restored.append(backup_id)
    )
    return restored


def test_restoring_a_backup_from_before_the_change_asks_in_the_manager(
    history: SimpleNamespace, backups: list[str]
) -> None:
    """The guard is in RollbackManager.rollback, which every way to a backup passes."""
    from noust.managers.backup_manager import RollbackManager

    with pytest.raises(SchemaChangedError) as refusal:
        RollbackManager().rollback(DOMAIN, backup_id="shop-before")

    assert history.rows[1].id in refusal.value.deployments
    assert backups == []


def test_the_console_s_backup_rollback_job_asks_too(
    history: SimpleNamespace, backups: list[str]
) -> None:
    from noust.web.jobs import Job, JobContext, JobType, rollback_app_job

    context = JobContext(Job(id="j", type=JobType.ROLLBACK, name="r", description=""), print)

    with pytest.raises(SchemaChangedError):
        rollback_app_job(DOMAIN, backup_id="shop-before", job_context=context)
    assert backups == []


def test_the_backup_rollback_route_is_409_until_confirmed(
    history: SimpleNamespace, backups: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from noust.web.api import jobs
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers

    queued: list[dict[str, Any]] = []

    class Jobs:
        def create_job(self, **kwargs: Any) -> SimpleNamespace:
            queued.append(kwargs)
            return SimpleNamespace(
                id="j1",
                status=SimpleNamespace(value="queued"),
                to_dict=lambda: {
                    "id": "j1",
                    "type": "rollback",
                    "name": "r",
                    "description": "",
                    "status": "queued",
                    "progress": 0,
                    "total_steps": 1,
                    "current_step": "",
                    "created_at": "2026-10-02T00:00:00",
                },
            )

    monkeypatch.setattr(jobs, "get_job_manager", lambda: Jobs())
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(jobs.router, prefix="/api")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session
    client = TestClient(app)

    refused = client.post("/api/jobs/rollback", json={"domain": DOMAIN})
    assert refused.status_code == 409
    assert refused.json()["error"] == "schema_changed" or "schema_changed" in refused.text
    assert str(history.rows[1].id) in refused.text
    assert queued == []

    client.post("/api/jobs/rollback", json={"domain": DOMAIN, "schema_changed_ok": True})
    [job] = queued
    assert job["kwargs"]["schema_changed_ok"] is True
