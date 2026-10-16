# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Applying a manifest from the console (J-15): the route, the job, and what neither may do.

The apply runs the command line's engine on the server, calling the console's own
API with the credentials of the person who asked. These tests hold it to that:
the route asks for sudo mode and for the plan the person saw, the job goes through
the same routes as a person would (so their refusals are the apply's), and nothing
that identifies the caller or a secret variable is kept or logged.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.gateway_client import ForwardedCaller
from noust.web.jobs import Job, JobContext, JobStatus, JobType
from noust.web.manifest_jobs import apply_manifest_job
from tests.teams_support import World, build_world

SHOP = "shop.acme.example"

HEALTH = (
    f"version: 1\napplications:\n  - domain: {SHOP}\n"
    "    health: {path: /healthz, expect: '200', timeout: 30}\n"
)
NOTHING = f"version: 1\napplications:\n  - domain: {SHOP}\n"
SCHEDULED = HEALTH + "    backups: {schedule: daily, retention_count: 3}\n"


class Recorder:
    """The job manager as the route sees it: it records what is queued, and runs nothing."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.active: list[Job] = []

    def create_job(self, **kwargs: Any) -> Job:
        self.created.append(kwargs)
        return Job(
            id=f"job{len(self.created)}",
            type=kwargs["job_type"],
            name=kwargs["name"],
            description=kwargs["description"],
            metadata=kwargs["metadata"],
            actor=kwargs["actor"],
        )

    def get_active_jobs(self) -> list[Job]:
        return self.active


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    """Two teams and their people, served by the console, with the job queue recorded."""
    built = build_world(tmp_path, monkeypatch)
    recorder = Recorder()
    built.recorder = recorder  # type: ignore[attr-defined]
    monkeypatch.setattr("noust.web.api.manifest.get_job_manager", lambda: recorder)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def recorder_of(world: World) -> Recorder:
    return world.recorder  # type: ignore[attr-defined, no-any-return]


def apply(world: World, who: str | dict[str, str], text: str, **extra: Any) -> Any:
    headers = world.as_(who) if isinstance(who, str) else who
    return world.client.post(
        "/api/manifest/apply", json={"manifest": text, **extra}, headers=headers
    )


def preview_digest(world: World, who: str, text: str) -> str:
    response = world.client.post(
        "/api/manifest/plan", json={"manifest": text}, headers=world.as_(who)
    )
    assert response.status_code == 200, response.text
    return str(response.json()["digest"])


class TestTheRoute:
    def test_it_queues_the_apply_as_a_job_of_its_author(self, world: World) -> None:
        response = apply(world, "alice", HEALTH)

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["queued"] is True and body["job_id"] == "job1"
        queued = recorder_of(world).created
        assert len(queued) == 1 and queued[0]["job_type"] is JobType.MANIFEST
        assert queued[0]["actor"]
        assert isinstance(queued[0]["kwargs"]["caller"], ForwardedCaller)
        assert queued[0]["metadata"]["changes"] == 1

    def test_nothing_to_apply_queues_nothing_and_says_so(self, world: World) -> None:
        response = apply(world, "alice", NOTHING)

        assert response.status_code == 200
        assert response.json()["queued"] is False
        assert recorder_of(world).created == []

    def test_a_viewer_may_look_but_not_apply(self, world: World) -> None:
        assert apply(world, "dana", HEALTH).status_code == 403
        assert recorder_of(world).created == []

    def test_nobody_anonymous(self, world: World) -> None:
        response = world.client.post("/api/manifest/apply", json={"manifest": HEALTH})

        assert response.status_code == 401

    def test_it_asks_for_sudo_mode(self, world: World) -> None:
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("alice")
        issued = get_token_manager().create_api_token(
            "alice-no-sudo", "admin", None, owner=account, allow_elevated=False
        )

        response = apply(world, {"Authorization": f"Bearer {issued['token']}"}, HEALTH)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert recorder_of(world).created == []

    def test_it_applies_what_was_previewed(self, world: World) -> None:
        digest = preview_digest(world, "alice", HEALTH)

        response = apply(world, "alice", HEALTH, expect=digest)

        assert response.status_code == 202
        assert response.json()["digest"] == digest

    def test_a_plan_that_is_not_the_one_previewed_is_refused(self, world: World) -> None:
        response = apply(world, "alice", HEALTH, expect="0" * 64)

        assert response.status_code == 409
        assert response.json()["error"] == "plan_changed"
        assert "Preview it again" in response.json()["hint"]
        assert recorder_of(world).created == []

    def test_the_digest_is_the_plan_the_person_read_and_changes_with_it(self, world: World) -> None:
        before = preview_digest(world, "alice", HEALTH)
        assert before == preview_digest(world, "alice", HEALTH)
        domains = HEALTH + "    domains: [www.shop.acme.example]\n"

        assert before != preview_digest(world, "alice", domains)
        assert before != preview_digest(world, "alice", NOTHING)

    @pytest.mark.parametrize("digest", ["short", "0" * 65])
    def test_a_digest_that_is_not_one_is_refused_before_anything_is_read(
        self, world: World, digest: str
    ) -> None:
        response = apply(world, "alice", HEALTH, expect=digest)

        assert response.status_code in (400, 422)
        assert recorder_of(world).created == []

    def test_variables_read_from_the_servers_own_environment_are_refused(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SERVER_ONLY_SECRET", "must-not-be-read")
        text = NOTHING + "    env:\n      K: {from_env: SERVER_ONLY_SECRET}\n"

        response = apply(world, "alice", text)

        assert response.status_code == 400
        assert "SERVER_ONLY_SECRET" in response.json()["detail"]
        assert "must-not-be-read" not in response.text
        assert recorder_of(world).created == []

    def test_a_mistake_in_the_manifest_is_a_400_that_names_the_field(self, world: World) -> None:
        response = apply(
            world, "alice", "version: 1\napplications:\n  - domain: a.test\n    oops: 1\n"
        )

        assert response.status_code == 400
        assert "applications[0].oops" in response.json()["detail"]

    def test_one_apply_at_a_time(self, world: World) -> None:
        running = Job(id="busy", type=JobType.MANIFEST, name="n", description="d")
        running.status = JobStatus.RUNNING
        recorder_of(world).active.append(running)

        response = apply(world, "alice", HEALTH)

        assert response.status_code == 409
        assert response.json()["error"] == "apply_running"
        assert "busy" in response.json()["detail"]

    def test_what_the_caller_cannot_see_is_not_in_the_plan_it_applies(self, world: World) -> None:
        text = HEALTH + "  - domain: blog.globex.example\n    health: {path: /x}\n"
        from noust.manifest import RefusingResolver, parse, plan
        from noust.web.gateway_client import GatewayTransport

        with world.client:
            body = apply(world, "alice", text).json()
            caller = recorder_of(world).created[0]["kwargs"]["caller"]
            seen = plan(
                parse(text), GatewayTransport(caller), RefusingResolver(), compare_env=False
            )

        # Another team's application is invisible to alice: the plan holds only her own change.
        assert body["queued"] is True
        assert [a.target for a in seen.actions] == [SHOP]

    def test_the_audit_trail_names_the_counts_and_never_the_manifest(self, world: World) -> None:
        events: list[dict[str, Any]] = []

        def record(event: str, **fields: Any) -> None:
            events.append({"event": event, **fields})

        import noust.web.api.manifest as module

        original = module.audit_record
        module.audit_record = record  # type: ignore[assignment]
        try:
            apply(world, "alice", HEALTH + "    env:\n      API_TOKEN: very-secret-token\n")
        finally:
            module.audit_record = original  # type: ignore[assignment]

        applied = [e for e in events if e["event"] == "manifest.apply"]
        assert len(applied) == 1
        assert "very-secret-token" not in json.dumps(applied, default=str)
        assert applied[0]["details"]["actions"]["app.health"] == 1


def run_job(world: World, who: str | None = None, **overrides: Any) -> tuple[Any, Job, str]:
    """
    Run the queued job on this thread while the console's event loop is alive.

    Args:
        world: The server.
        who: Unused; the job is the one queued.
        **overrides: Replaces keyword arguments of the job.

    Returns:
        What it returned (or the error it raised), the job, and its log.
    """
    queued = recorder_of(world).created[-1]
    job = Job(id="job1", type=JobType.MANIFEST, name="n", description="d")
    context = JobContext(job, lambda _job: None)
    kwargs = {**queued["kwargs"], **overrides}
    try:
        result: Any = queued["func"](**kwargs, job_context=context)
    except Exception as exc:  # the job manager is the error boundary; the test reads what it holds
        result = exc
    return result, job, "\n".join(entry.message for entry in job.logs)


class TestAsASignedInBrowser:
    """The console's own session, not a token: it needs the CSRF token to change anything."""

    @staticmethod
    def sign_in(world: World) -> dict[str, str]:
        master = world.master["Authorization"].split()[1]
        login = world.client.post("/api/auth/login", json={"token": master})
        assert login.status_code == 200, login.text
        headers = {CSRF_HEADER_NAME: login.json()["csrf_token"]}
        elevated = world.client.post("/api/auth/elevate", json={"token": master}, headers=headers)
        assert elevated.status_code == 200, elevated.text
        return headers

    def test_a_session_applies_with_its_own_cookie_and_csrf_token(self, world: World) -> None:
        with world.client:
            headers = self.sign_in(world)
            queued = world.client.post(
                "/api/manifest/apply", json={"manifest": HEALTH}, headers=headers
            )
            assert queued.status_code == 202, queued.text
            caller = recorder_of(world).created[0]["kwargs"]["caller"]
            assert caller.token is None and caller.cookie and caller.csrf

            result, _job, _log = run_job(world)

            assert isinstance(result, dict) and result["ok"] is True, result
            app = world.store.get_app(SHOP)
            assert app is not None and app.health_path == "/healthz"

    def test_a_session_without_the_csrf_token_changes_nothing(self, world: World) -> None:
        with world.client:
            headers = self.sign_in(world)
            world.client.post("/api/manifest/apply", json={"manifest": HEALTH}, headers=headers)
            from dataclasses import replace

            stripped = replace(recorder_of(world).created[0]["kwargs"]["caller"], csrf=None)

            result, _job, _log = run_job(world, caller=stripped)

            assert isinstance(result, Exception)
            assert "CSRF" in str(result)
            app = world.store.get_app(SHOP)
            assert app is not None and app.health_path != "/healthz"


class TestTheJob:
    def test_it_makes_the_change_with_the_authors_own_credentials(self, world: World) -> None:
        with world.client:
            apply(world, "alice", HEALTH)
            result, job, log = run_job(world)

            assert isinstance(result, dict), result
            assert result["ok"] is True
            assert [o["kind"] for o in result["outcomes"]] == ["app.health"]
            app = world.store.get_app(SHOP)
            assert app is not None and app.health_path == "/healthz"
            # And a second apply has nothing left to do.
            assert apply(world, "alice", HEALTH).status_code == 200

    def test_the_server_audits_the_change_as_the_person_not_as_the_console(
        self, world: World
    ) -> None:
        with world.client:
            apply(world, "alice", HEALTH)
            run_job(world)
            trail = world.client.get("/api/audit?limit=50", headers=world.master).json()["items"]

        changes = [e for e in trail if e["resource"] == f"/api/apps/{SHOP}/health"]
        assert [e["actor"] for e in changes] == ["token:alice-token"]
        asked = [e for e in trail if e["action"] == "manifest.apply"]
        assert [e["actor"] for e in asked] == ["token:alice-token"]
        assert "healthz" not in str(asked)

    def test_a_refusal_of_the_route_stops_the_run_there_in_the_servers_words(
        self, world: World
    ) -> None:
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("alice")
        issued = get_token_manager().create_api_token(
            "alice-limited",
            "admin",
            None,
            owner=account,
            allow_elevated=True,
            permissions=["apps.read", "apps.manage", "teams.read"],
        )
        headers = {"Authorization": f"Bearer {issued['token']}"}
        with world.client:
            queued = apply(world, headers, SCHEDULED)
            assert queued.status_code == 202, queued.text
            result, job, log = run_job(world)

            assert isinstance(result, Exception)
            assert "Applying the manifest stopped at" in str(result)
            assert "apply again" in (getattr(result, "details", "") or "")
            published = job.result
            assert [o["status"] for o in published["outcomes"]] == ["applied", "failed"]
            app = world.store.get_app(SHOP)
            assert app is not None and app.health_path == "/healthz"
            assert "403" in published["outcomes"][1]["message"] or "permission" in (
                published["outcomes"][1]["message"].lower()
            )

    def test_a_secret_variable_of_the_manifest_never_reaches_the_log_or_the_result(
        self, world: World
    ) -> None:
        text = HEALTH + "    env:\n      API_TOKEN: very-secret-token-9\n"
        with world.client:
            apply(world, "alice", text)
            result, job, log = run_job(world)

            assert isinstance(result, dict), result
            assert "very-secret-token-9" not in log
            assert "very-secret-token-9" not in json.dumps(job.result)
            assert "very-secret-token-9" not in json.dumps([e.message for e in job.logs])

    def test_nothing_to_do_by_the_time_it_runs_is_a_success(self, world: World) -> None:
        with world.client:
            apply(world, "alice", HEALTH)
            run_job(world)
            result, _job, log = run_job(world)

        assert result == {"ok": True, "outcomes": [], "restart": []}
        assert "already matches" in log

    def test_the_credentials_are_not_in_the_jobs_description_or_metadata(
        self, world: World
    ) -> None:
        apply(world, "alice", HEALTH)
        queued = recorder_of(world).created[0]
        caller = queued["kwargs"]["caller"]

        text = json.dumps(
            {k: v for k, v in queued.items() if k not in ("kwargs", "func")}, default=str
        )

        assert caller.token and caller.token not in text
        assert caller.token not in repr(caller)

    def test_the_apply_job_runs_outside_the_line_and_the_line_does_not_wait_for_it(
        self,
    ) -> None:
        from noust.core.delivery_queue import ORCHESTRATION_JOB_TYPES
        from noust.web.jobs import BUILD_JOB_TYPES

        assert ORCHESTRATION_JOB_TYPES == {"manifest"}
        assert JobType.MANIFEST not in BUILD_JOB_TYPES


def test_applying_without_a_server_around_is_still_the_same_function() -> None:
    """The job's function is the one the route queues, not a copy of the engine."""
    import noust.web.api.manifest as module

    assert module.apply_manifest_job is apply_manifest_job
