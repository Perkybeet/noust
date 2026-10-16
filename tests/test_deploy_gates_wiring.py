# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every door that deploys asks the guard (checklist B-17, B-26).

A freeze holds the update by whichever door it comes: the command, the console's
queue, a pipeline's call, a code host's push or tag and the update the job runs.
A pause holds only what starts by itself. Going back pauses it. Rolling back is
never held. The decisions themselves are ``test_deploy_gates.py``'s; here they are
shown to be asked.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import app_rollout_store, deploy_freeze
from noust.core import webhook_deliveries as deliveries_log
from noust.core.exceptions import DeploysFrozenError, DeploysPausedError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.deployers import lifecycle
from noust.deployers.recorder import DeploymentRecorder
from noust.web.api import app_deploy
from noust.web.api import jobs as jobs_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from noust.web.api.hooks import webhook_update_job
from noust.web.jobs import Job, JobType
from tests.test_web_hooks import (
    DOMAIN,
    PUSH_MAIN,
    admin,
    app,
    client,
    fresh_delivery_cache,
    github_headers,
    queued,
    secret,
    seeded,
    store,
)

FIXTURES = (admin, app, client, fresh_delivery_cache, queued, secret, seeded, store)


def last_delivery(store: NoustStore, seeded: App) -> tuple[str, str]:
    assert seeded.id is not None
    row = deliveries_log.list_deliveries(seeded.id, limit=1)[0]
    return row.outcome, row.detail or ""


class TestUpdateAsksTheGuard:
    @pytest.fixture
    def ran(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def fake(domain: str, **kwargs: Any) -> Any:
            calls.append({"domain": domain, **kwargs})
            return SimpleNamespace(deployment_id=1)

        monkeypatch.setattr(lifecycle, "_update_app", fake)
        monkeypatch.setattr(lifecycle, "_tag_to_deploy", lambda *a, **k: None)
        return calls

    def test_a_frozen_application_is_not_updated(
        self, store: NoustStore, seeded: App, ran: list
    ) -> None:
        deploy_freeze.create("incident 412")

        with pytest.raises(DeploysFrozenError):
            lifecycle.update_app(DOMAIN)

        assert ran == []

    def test_a_person_who_says_so_updates_it_anyway(
        self, store: NoustStore, seeded: App, ran: list
    ) -> None:
        deploy_freeze.create("incident 412")

        lifecycle.update_app(DOMAIN, frozen_ok=True)

        assert len(ran) == 1

    def test_a_webhook_cannot_say_so(self, store: NoustStore, seeded: App, ran: list) -> None:
        deploy_freeze.create("incident 412")

        with pytest.raises(DeploysFrozenError):
            lifecycle.update_app(DOMAIN, trigger="webhook", frozen_ok=True)

        assert ran == []

    def test_a_pause_holds_a_webhook_and_a_schedule_but_not_a_person(
        self, store: NoustStore, seeded: App, ran: list
    ) -> None:
        app_rollout_store.pause(DOMAIN, by="rollback")

        with pytest.raises(DeploysPausedError):
            lifecycle.update_app(DOMAIN, trigger="webhook")
        with pytest.raises(DeploysPausedError):
            lifecycle.update_app(DOMAIN, automatic=True)
        lifecycle.update_app(DOMAIN)

        assert len(ran) == 1

    def test_going_back_by_rebuilding_a_commit_is_never_held(
        self, store: NoustStore, seeded: App, ran: list
    ) -> None:
        deploy_freeze.create("incident 412")
        app_rollout_store.pause(DOMAIN)
        # The rollback of an in-place application rebuilds the commit it goes back to.
        lifecycle.update_app(
            DOMAIN, commit="abc1234", rollback_of=7, schema_changed_ok=True, trigger="webhook"
        )

        assert len(ran) == 1


class TestTheQueuesRefuseBeforeAJobExists:
    def test_an_update_job_is_refused_during_a_freeze(
        self, store: NoustStore, seeded: App, queued: list
    ) -> None:
        deploy_freeze.create("incident 412")

        with pytest.raises(DeploysFrozenError):
            jobs_api.queue_update(DOMAIN, actor="ana")

        assert queued == []

    def test_a_confirmed_one_carries_the_confirmation_to_the_job(
        self, store: NoustStore, seeded: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        made: list[dict[str, Any]] = []

        class Manager:
            def create_job(self, **kwargs: Any) -> Any:
                made.append(kwargs)
                return SimpleNamespace(id="job-1")

        monkeypatch.setattr(jobs_api, "get_job_manager", lambda: Manager())
        deploy_freeze.create("incident 412")

        jobs_api.queue_update(DOMAIN, actor="ana", frozen_ok=True)

        assert made[0]["kwargs"] == {"domain": DOMAIN, "frozen_ok": True}

    def test_the_console_s_request_says_it_the_same_way(
        self, store: NoustStore, seeded: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deploy_freeze.create("incident 412")
        monkeypatch.setattr(jobs_api, "refuse_if_nothing_new", lambda domain: None)
        made: list[dict[str, Any]] = []

        class Manager:
            def create_job(self, **kwargs: Any) -> Any:
                made.append(kwargs)
                return SimpleNamespace(
                    id="job-1", to_dict=lambda: {}, status=SimpleNamespace(value="pending")
                )

        monkeypatch.setattr(jobs_api, "get_job_manager", lambda: Manager())
        with pytest.raises(DeploysFrozenError):
            jobs_api._queue_update(DOMAIN, True, {"sid": "abc"})

        jobs_api._queue_update(DOMAIN, True, {"sid": "abc"}, frozen_ok=True)

        assert len(made) == 1


class TestTheConsoleSRequest:
    @pytest.fixture
    def console(self, store: NoustStore, seeded: App, monkeypatch: pytest.MonkeyPatch) -> Any:
        made: list[dict[str, Any]] = []

        class Manager:
            def create_job(self, **kwargs: Any) -> Any:
                made.append(kwargs)
                return Job(id="job-1", type=JobType.UPDATE, name="Update", description="")

        monkeypatch.setattr(jobs_api, "get_job_manager", lambda: Manager())
        server = FastAPI()
        install_error_handlers(server)
        server.include_router(jobs_api.router, prefix="/api")
        server.dependency_overrides[get_current_session] = lambda: {"sid": "abcdef012345"}
        client = TestClient(server)
        client.made = made  # type: ignore[attr-defined]
        return client

    def test_a_freeze_is_a_409_that_says_who_froze_and_why(self, console: Any) -> None:
        deploy_freeze.create("incident 412", by="ana")

        response = console.post("/api/jobs/update", json={"domain": DOMAIN, "force": True})

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["error"] == "deploys_frozen" and "incident 412" in body["detail"]
        assert body["freeze"]["created_by"] == "ana" and body["automatic"] is False
        assert console.made == []

    def test_frozen_ok_queues_it_and_the_job_carries_the_yes(self, console: Any) -> None:
        deploy_freeze.create("incident 412")

        response = console.post(
            "/api/jobs/update", json={"domain": DOMAIN, "force": True, "frozen_ok": True}
        )

        assert response.status_code == 202, response.text
        assert console.made[0]["kwargs"]["frozen_ok"] is True

    def test_a_freeze_does_not_hide_behind_nothing_new(self, console: Any) -> None:
        deploy_freeze.create("incident 412")

        response = console.post("/api/jobs/update", json={"domain": DOMAIN})

        assert response.status_code == 409
        assert response.json()["error"] == "deploys_frozen"


class TestAPipelineSCall:
    @pytest.fixture
    def pipeline(self, store: NoustStore, seeded: App, monkeypatch: pytest.MonkeyPatch) -> Any:
        made: list[dict[str, Any]] = []

        class Manager:
            def create_job(self, **kwargs: Any) -> Any:
                made.append(kwargs)
                return SimpleNamespace(id=f"job-{len(made)}")

        monkeypatch.setattr(jobs_api, "get_job_manager", lambda: Manager())
        monkeypatch.setattr(
            jobs_api,
            "check_upstream",
            lambda domain, **_k: SimpleNamespace(has_new_commits=True, summary="new"),
        )

        def make(session: dict[str, Any]) -> TestClient:
            server = FastAPI()
            install_error_handlers(server)
            server.include_router(app_deploy.router, prefix="/api/apps")
            server.dependency_overrides[get_current_session] = lambda: session
            return TestClient(server)

        make.made = made  # type: ignore[attr-defined]
        return make

    TOKEN = {"type": "api_token", "sid": "token:ci", "scope": "deploy"}
    PERSON = {"type": "session", "sid": "abcdef012345", "scope": "admin"}

    def test_a_freeze_answers_409_not_a_success_a_pipeline_would_read(self, pipeline: Any) -> None:
        deploy_freeze.create("incident 412")

        response = pipeline(self.TOKEN).post(f"/api/apps/{DOMAIN}/deploy")

        assert response.status_code == 409
        assert response.json()["error"] == "deploys_frozen"
        assert pipeline.made == []

    def test_frozen_ok_deploys_anyway(self, pipeline: Any) -> None:
        deploy_freeze.create("incident 412")

        response = pipeline(self.TOKEN).post(f"/api/apps/{DOMAIN}/deploy", json={"frozen_ok": True})

        assert response.status_code == 202
        assert pipeline.made[0]["kwargs"]["frozen_ok"] is True

    def test_a_paused_application_answers_a_token_409_until_it_forces(self, pipeline: Any) -> None:
        app_rollout_store.pause(DOMAIN, reason="rolled back to 2026", by="rollback")

        refused = pipeline(self.TOKEN).post(f"/api/apps/{DOMAIN}/deploy")
        forced = pipeline(self.TOKEN).post(f"/api/apps/{DOMAIN}/deploy", json={"force": True})

        assert refused.status_code == 409
        assert refused.json()["error"] == "deploys_paused"
        assert "rolled back to 2026" in refused.json()["detail"]
        assert forced.status_code == 202

    def test_a_person_at_the_console_is_not_held_by_a_pause(self, pipeline: Any) -> None:
        app_rollout_store.pause(DOMAIN)

        response = pipeline(self.PERSON).post(f"/api/apps/{DOMAIN}/deploy")

        assert response.status_code == 202


class TestACodeHostSPush:
    def test_a_paused_application_deploys_nothing_and_says_why(
        self,
        client: TestClient,
        store: NoustStore,
        seeded: App,
        secret: str,
        queued: list[dict[str, Any]],
    ) -> None:
        app_rollout_store.pause(DOMAIN, reason="rolled back to 20261001", by="rollback")

        response = client.post(
            f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=github_headers(secret, PUSH_MAIN)
        )

        assert response.status_code == 200
        assert response.json()["status"] == "ignored"
        assert response.json()["reason"] == "paused"
        assert queued == []
        outcome, detail = last_delivery(store, seeded)
        assert outcome == deliveries_log.IGNORED_PAUSED
        assert "rolled back to 20261001" in detail

    def test_a_frozen_server_deploys_nothing(
        self,
        client: TestClient,
        store: NoustStore,
        seeded: App,
        secret: str,
        queued: list[dict[str, Any]],
    ) -> None:
        deploy_freeze.create("holiday")

        response = client.post(
            f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=github_headers(secret, PUSH_MAIN)
        )

        assert response.json()["reason"] == "frozen"
        assert queued == []
        assert last_delivery(store, seeded)[0] == deliveries_log.IGNORED_FROZEN

    def test_after_resuming_the_next_push_deploys(
        self,
        client: TestClient,
        store: NoustStore,
        seeded: App,
        secret: str,
        queued: list[dict[str, Any]],
    ) -> None:
        app_rollout_store.pause(DOMAIN)
        app_rollout_store.resume(DOMAIN)

        response = client.post(
            f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=github_headers(secret, PUSH_MAIN)
        )

        assert response.status_code == 202
        assert len(queued) == 1

    def test_a_job_that_waited_asks_again_when_it_runs(
        self, store: NoustStore, seeded: App
    ) -> None:
        deploy_freeze.create("began while it waited")

        result = webhook_update_job(DOMAIN, job_context=None)

        assert result["status"] == "ignored"
        assert "began while it waited" in result["reason"]


class TestGoingBackPausesTheAutomaticDeploys:
    def recorder(self, store: NoustStore) -> DeploymentRecorder:
        return DeploymentRecorder(store, DOMAIN, "cli", logger=Logger())

    def test_a_rollback_pauses_an_application_a_push_deploys(
        self, store: NoustStore, seeded: App, secret: str
    ) -> None:
        self.recorder(store).mark_previous_success_rolled_back()

        settings = app_rollout_store.get(DOMAIN)
        assert settings.paused
        assert settings.paused_by == app_rollout_store.ROLLBACK_PAUSE_ACTOR
        assert "Rolled back" in (settings.paused_reason or "")

    def test_an_application_nothing_deploys_by_itself_has_nothing_to_pause(
        self, store: NoustStore, seeded: App
    ) -> None:
        self.recorder(store).mark_previous_success_rolled_back()

        assert app_rollout_store.get(DOMAIN).paused is False

    def test_it_can_be_switched_off_for_an_application(
        self, store: NoustStore, seeded: App, secret: str
    ) -> None:
        app_rollout_store.set_pause_on_rollback(DOMAIN, False)

        self.recorder(store).mark_previous_success_rolled_back()

        assert app_rollout_store.get(DOMAIN).paused is False

    def test_an_existing_pause_keeps_its_author_and_reason(
        self, store: NoustStore, seeded: App, secret: str
    ) -> None:
        app_rollout_store.pause(DOMAIN, reason="incident 7", by="ana")

        self.recorder(store).mark_previous_success_rolled_back()

        settings = app_rollout_store.get(DOMAIN)
        assert (settings.paused_by, settings.paused_reason) == ("ana", "incident 7")

    def test_the_rebuild_path_pauses_too(self, store: NoustStore, seeded: App, secret: str) -> None:
        lifecycle._mark_replaced_rolled_back(store, DOMAIN, None, Logger())

        assert app_rollout_store.get(DOMAIN).paused

    def test_a_preview_is_never_paused(self, store: NoustStore, seeded: App) -> None:
        store.create_app(
            App(
                domain="pr-1-app.previews.example.com",
                app_type="nodejs",
                port=3010,
                app_path="/tmp/pr1",
                preview_parent=DOMAIN,
            )
        )

        lifecycle._mark_replaced_rolled_back(store, "pr-1-app.previews.example.com", None, Logger())

        assert app_rollout_store.get("pr-1-app.previews.example.com").paused is False


class TestTheRecorderPrunesToTheApplicationSNumber:
    def test_rows_beyond_the_number_are_deleted_when_a_deployment_ends(
        self, store: NoustStore, seeded: App
    ) -> None:
        app_rollout_store.set_history_keep(DOMAIN, 2)
        for _ in range(4):
            recorder = DeploymentRecorder(store, DOMAIN, "cli", logger=Logger())
            recorder.start()
            recorder.finish_success()

        assert len(store.list_deployments(DOMAIN, limit=50)) == 2
